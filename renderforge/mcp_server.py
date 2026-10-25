"""RenderForge MCP server (stdio, standard library only).

Exposes the workbench to an agent without shell-quoting: toolbox queries (`doctor`, `plan`), case state
(`status`), the step graph (`run`, `step`), validation (`metrics`), and detached long jobs (`job_start`,
`job_status`, `job_log`). Every tool returns JSON text so a caller can branch on fields instead of parsing prose.

Run it:  python -m renderforge.mcp_server          (add to .mcp.json as a stdio server)

Deliberately narrow: no generic shell tool. Anything not exposed here is a bug in the tool surface, not something
to work around by spawning commands.
"""
import json
import sys
import traceback

from . import caseinfo, jobs, pipeline

PROTOCOL = "2024-11-05"
NAME = "renderforge"
VERSION = "0.2.0"

TOOLS = [
    {
        "name": "doctor",
        "description": "What is installed on this machine (RenderDoc, UE, Unity, unpackers, Python packages), with the "
                       "registry status of each tool. Call this first in a fresh environment.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "plan",
        "description": "For one game profile and target engine: the recommended recipes, each step's tool and its "
                       "verification status, plus the game's verified capabilities and known gaps.",
        "inputSchema": {"type": "object", "properties": {
            "game": {"type": "string", "description": "profile id from toolbox/games.json, e.g. wuwa, zzz, generic-unity"},
            "target": {"type": "string", "enum": ["unreal", "unity"], "default": "unreal"}},
            "required": ["game"]},
    },
    {
        "name": "games",
        "description": "List game profiles with engine, capture routes, source readers, verified items and gaps.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "status",
        "description": "Case status. Without a case: every case under workspace/ with the stages whose artifacts "
                       "exist and the next step. With a case: full detail (draws, meshes, instances, identity "
                       "coverage, build skips) and the inferred next command.",
        "inputSchema": {"type": "object", "properties": {
            "case": {"type": "string", "description": "case directory name under workspace/ (omit for all cases)"}}},
    },
    {
        "name": "run",
        "description": "Run a case's step graph in order, skipping steps whose artifacts already exist (pass force to "
                       "redo). Use from/to to resume. Long steps should use job_start instead so the call returns.",
        "inputSchema": {"type": "object", "properties": {
            "case": {"type": "string"},
            "from": {"type": "string", "description": "first step, e.g. export"},
            "to": {"type": "string", "description": "last step, e.g. build"},
            "force": {"type": "boolean", "default": False},
            "rdc": {"type": "string"}, "bundle": {"type": "string"}, "frame_dir": {"type": "string"},
            "game": {"type": "string"}, "index": {"type": "string"}},
            "required": ["case"]},
    },
    {
        "name": "step",
        "description": "Run one named stage of a case (survey, export, build, build-model, ingest-unity, "
                       "ingest-3dmigoto, identify, ue-import, unity-import, shot, metrics). Returns that stage's "
                       "result JSON; the outcome is also recorded in case.json under steps.",
        "inputSchema": {"type": "object", "properties": {
            "case": {"type": "string"},
            "step": {"type": "string", "enum": ["survey", "export", "build", "build-model", "ingest-unity",
                                                "ingest-3dmigoto", "identify", "ue-import", "unity-import", "shot",
                                                "metrics"]},
            "rdc": {"type": "string"}, "range": {"type": "string"}, "rt_count": {"type": "integer"},
            "extra_export": {"type": "string"}, "extra_pass": {"type": "string"},
            "bundle": {"type": "string"}, "frame_dir": {"type": "string"}, "game": {"type": "string"},
            "index": {"type": "string"}, "ids": {"type": "string"}, "shot": {"type": "string"},
            "max_instances": {"type": "integer"}, "emissive": {"type": "boolean"},
            "no_instanced_index": {"type": "boolean"}},
            "required": ["case", "step"]},
    },
    {
        "name": "metrics",
        "description": "Numeric comparison of a reconstruction screenshot against the captured reference frame: "
                       "global SSIM, MAE/RMSE, p95 error, foreground coverage error, mask IoU, and whether the shot "
                       "needed a vertical flip. Approximate (same viewpoint, different lighting), not pixel-identical.",
        "inputSchema": {"type": "object", "properties": {
            "case": {"type": "string"},
            "shot": {"type": "string", "description": "path, or a file name under shots/"}},
            "required": ["case"]},
    },
    {
        "name": "job_start",
        "description": "Start a long stage/run detached and return a job id immediately. Output goes to "
                       "workspace/_jobs/<id>.log; poll with job_status.",
        "inputSchema": {"type": "object", "properties": {
            "case": {"type": "string"},
            "step": {"type": "string", "description": "a step name, or 'run' for the whole graph"},
            "from": {"type": "string"}, "to": {"type": "string"}, "force": {"type": "boolean"},
            "extra": {"type": "object", "description": "extra CLI-style options, e.g. {\"index\": \"...\"}"}},
            "required": ["case", "step"]},
    },
    {
        "name": "job_status",
        "description": "State of a background job: running / done / failed, exit code, seconds, argv.",
        "inputSchema": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
    },
    {
        "name": "job_log",
        "description": "Tail a background job's log.",
        "inputSchema": {"type": "object", "properties": {
            "id": {"type": "string"}, "tail": {"type": "integer", "default": 60}}, "required": ["id"]},
    },
    {
        "name": "jobs",
        "description": "Recent background jobs, newest first.",
        "inputSchema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}},
    },
]

# CLI option name -> step function keyword
_STEPS = {
    "survey": pipeline.step_survey, "export": pipeline.step_export, "build": pipeline.step_build,
    "build-model": pipeline.step_build_model, "ingest-unity": pipeline.step_ingest_unity,
    "ingest-3dmigoto": pipeline.step_ingest_3dmigoto, "identify": pipeline.step_identify,
    "ue-import": pipeline.step_ue_import, "unity-import": pipeline.step_unity_import,
    "shot": pipeline.step_shot, "metrics": pipeline.step_metrics,
}
_STEP_KEYS = ("rdc", "range", "rt_count", "extra_export", "extra_pass", "bundle", "frame_dir", "game", "index",
              "ids", "shot", "max_instances", "emissive", "no_instanced_index", "ticks", "cmd")


def _call(name, args):
    if name == "doctor":
        from .toolbox import doctor
        return [{"tool": t, "ok": ok, "where": w, "version": v} for t, ok, w, v in doctor()]
    if name == "plan":
        from .toolbox import plan
        return plan(args["game"], args.get("target") or "unreal")
    if name == "games":
        from .toolbox import load
        _, _, games, _ = load()
        return {gid: {k: v for k, v in g.items() if k in
                      ("name", "engine", "engine_version", "install", "capture", "source", "verified", "gaps")}
                for gid, g in games.items()}
    if name == "status":
        if args.get("case"):
            return caseinfo.scan(args["case"])
        cases = caseinfo.scan_all()
        return {"cases": caseinfo.summary(cases)}
    if name == "run":
        kw = {k: v for k, v in args.items() if k in _STEP_KEYS and k not in ("case",)}
        return pipeline.run(args["case"], frm=args.get("from"), to=args.get("to"), force=bool(args.get("force")), **kw)
    if name == "step":
        fn = _STEPS.get(args["step"])
        if fn is None:
            raise ValueError(f"unknown step '{args['step']}'; known: {', '.join(_STEPS)}")
        kw = {k: v for k, v in args.items() if k in _STEP_KEYS}
        if "cmd" not in kw:                       # the shot step needs the console command (fov) from case.json
            kw["cmd"] = (pipeline.load_case(args["case"]).get("shot") or {}).get("console")
        return fn(args["case"], **kw)
    if name == "metrics":
        # the same function the CLI uses: it picks the default shot (newest case shot), records `also` for the other
        # engine, and writes the step outcome. Calling compare_case directly here made MCP and CLI disagree.
        return pipeline.step_metrics(args["case"], shot=args.get("shot") or args.get("shot_name"))
    if name == "job_start":
        argv = _argv_for(args)
        return jobs.start(argv, name=f"{args['case']}:{args['step']}")
    if name == "job_status":
        return jobs.status(args["id"])
    if name == "job_log":
        return {"id": args["id"], "log": jobs.log(args["id"], tail=int(args.get("tail") or 60))}
    if name == "jobs":
        return jobs.list_jobs(int(args.get("limit") or 10))
    raise ValueError(f"unknown tool '{name}'")


def _argv_for(args):
    """Turn job_start arguments back into CLI arguments (the job is a normal `python -m renderforge ...`)."""
    step = args["step"]
    argv = ["run" if step == "run" else step, args["case"]]
    for k in ("from", "to"):
        if args.get(k):
            argv += [f"--{k}", str(args[k])]
    if args.get("force"):
        argv.append("--force")
    for k, v in (args.get("extra") or {}).items():
        if isinstance(v, bool):
            if v:
                argv.append(f"--{k.replace('_', '-')}")
        elif v is not None:
            argv += [f"--{k.replace('_', '-')}", str(v)]
    return argv


def _respond(rid, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": rid}
    if error is not None:
        msg["error"] = error
    else:
        msg["result"] = result
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _text(rid, obj, is_error=False):
    _respond(rid, {"content": [{"type": "text", "text": json.dumps(obj, indent=1, ensure_ascii=False, default=str)}],
                   "isError": is_error})


def handle(msg):
    method = msg.get("method")
    rid = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        return _respond(rid, {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                              "serverInfo": {"name": NAME, "version": VERSION}})
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _respond(rid, {})
    if method == "tools/list":
        return _respond(rid, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            return _text(rid, {"ok": True, "tool": name, "result": _call(name, args)})
        except Exception as e:  # noqa: BLE001 - report the failure as tool output, not a transport error
            traceback.print_exc(file=sys.stderr)
            return _text(rid, {"ok": False, "tool": name, "error": f"{type(e).__name__}: {e}"}, is_error=True)
    if rid is not None:
        return _respond(rid, error={"code": -32601, "message": f"method not found: {method}"})
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        handle(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
