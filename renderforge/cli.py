"""RenderForge command line. A toolbox, not one pipeline:

  doctor                          what is installed here
  plan --game wuwa --target unity which tools / recipe, and the known gaps
  docs                            regenerate docs/TOOLBOX.md from toolbox/*.json
  ingest-3dmigoto <case> --frame-dir <FrameAnalysis-...> [--game zzz]
  build-model <case> [--game zzz]      model-space scene (no post-VS data)
  ingest-unity <case> --bundle <file>  Unity bundle / serialized file -> Scene Package (no GPU capture)
  match <case> --fingerprints <dir>    identify captured meshes among source meshes
  identify <case> --index <dir> [--ids ids.json] [--apply]  exact whole-library source identification
  unity-import <case>                  emit + import into headless Unity, render a PNG

Workbench commands:

  shaders <case> --eids a,b,c [--vs]  disassemble the unique pixel (or vertex) shaders of those draws
  status [case]                       every case: source, stages done, key numbers, inferred next step
  status --json                       the same, machine-readable
  run <case> [--from S --to S]        run the case's step graph, skipping steps whose artifacts exist
  metrics <case> [--shot name.png]    SSIM / MAE / coverage against export/reference_backbuffer.png
  backfill [--write]                  write case.json for legacy case directories from what the files prove
  selftest                            offline regression checks on committed fixtures (no GPU / engine needed)
  web [--port 8770] [--host 127.0.0.1]  local visual workbench in a browser (stdlib http.server, no build step)

RenderDoc route, per case:  survey -> export -> build -> ue-import -> shot

  python -m renderforge survey  <case> --rdc <file.rdc>
  python -m renderforge export  <case> [--range A-B | --rt-count N]
  python -m renderforge build   <case> [--rt-count N]
  python -m renderforge ue-import <case> [--max-instances N]
  python -m renderforge shot    <case> [--cmd "viewmode ..."]
  python -m renderforge run     <case> --rdc <file.rdc>        (everything, auto-picks the main scene pass)

Cases live in workspace/<case>/ ; original .rdc files are only read, never copied or modified.
"""
import argparse
import collections
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WS = ROOT / "workspace"


def case_dir(name):
    return WS / name


def load_case(name):
    p = case_dir(name) / "case.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save_case(name, data):
    p = case_dir(name) / "case.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def ue_name(case):
    return re.sub(r"[^A-Za-z0-9_]", "_", case)


def cmd_survey(a):
    from . import rd_run
    c = load_case(a.case)
    if a.rdc:
        c["rdc"] = str(Path(a.rdc).resolve())
    c.setdefault("source", "rdc")
    save_case(a.case, c)
    out = case_dir(a.case) / "survey"
    rc = rd_run.run("rf_extract.py", {"mode": "survey", "rdc": c["rdc"], "out": str(out)})
    groups = scene_passes(out / "survey.json")
    print(f"survey done (rc={rc}); candidate scene passes:")
    for i, g in enumerate(groups[:6]):
        print(f"  [{i}] RT x{g['rt_count']} {g['rt_formats'][:3]} {g['size']} draws={g['n']} eids {g['first']}..{g['last']} idx={g['indices']}")
    return 0


def scene_passes(survey_path):
    s = json.loads(Path(survey_path).read_text(encoding="utf-8"))
    groups = collections.OrderedDict()
    for d in s["draws"]:
        if not d["rts"] or not d["rtSize"]:
            continue
        key = (tuple(d["rtFormats"]), tuple(d["rtSize"]), d["depth"])
        groups.setdefault(key, []).append(d)
    out = []
    for (fmts, size, depth), v in groups.items():
        out.append({"rt_count": len(fmts), "rt_formats": list(fmts), "size": list(size), "n": len(v),
                    "first": v[0]["eid"], "last": v[-1]["eid"], "indices": sum(x["numIndices"] for x in v),
                    "eids": [x["eid"] for x in v]})
    # a G-buffer-like pass: several render targets at (near) full resolution, with many draws
    out.sort(key=lambda g: (g["rt_count"] >= 3 and g["size"][0] >= 1000, g["indices"]), reverse=True)
    return out


def cmd_export(a):
    from . import rd_run
    c = load_case(a.case)
    out = case_dir(a.case) / "export"
    job = {"mode": "export", "rdc": c["rdc"], "out": str(out)}
    if a.range:
        lo, hi = a.range.split("-")
        job["range"] = [int(lo), int(hi)]
    else:
        groups = scene_passes(case_dir(a.case) / "survey" / "survey.json")
        g = next((x for x in groups if a.rt_count is None or x["rt_count"] == a.rt_count), groups[0])
        job["eids"] = g["eids"]
        c["scene_pass"] = {"rt_count": g["rt_count"], "size": g["size"], "draws": g["n"]}
        save_case(a.case, c)
        print(f"auto-selected pass: RT x{g['rt_count']} {g['size']} draws={g['n']}")
    if a.no_instanced_index:
        job["no_instanced_index"] = True
    if out.exists():
        shutil.rmtree(out)
    rc = rd_run.run("rf_extract.py", job)
    print("export rc", rc, "->", out)
    return 0


def cmd_build(a):
    from .build import build
    c = load_case(a.case)
    rtc = a.rt_count or (c.get("scene_pass") or {}).get("rt_count")
    filt = (lambda d: len(d["rts"]) == rtc) if rtc else None
    sc = build(case_dir(a.case) / "export", case_dir(a.case) / "build", filt,
               extra_export=a.extra_export, extra_pass=a.extra_pass)
    print("VP:", sc["vp"], "match", round(sc["vp_match"], 2))
    print("report:", sc["report"], "| meshes", len(sc["meshes"]), "materials", len(sc["materials"]), "textures", len(sc["textures"]))
    return 0


def cmd_ue_import(a):
    from .ue_run import run
    from .space import convert_scene
    scene_dir = convert_scene(case_dir(a.case) / "build", case_dir(a.case) / "_scene_ue_space", "ue")
    extra = {"RF_SCENE": str(Path(scene_dir) / "scene.json"), "RF_CASE": ue_name(a.case)}
    if a.max_instances:
        extra["RF_MAX_INSTANCES"] = str(a.max_instances)
    if a.emissive:
        extra["RF_EMISSIVE"] = "1"
    rc, logp = run("import_scene.py", extra, log=f"ue_import_{a.case}.log")
    res = json.loads((Path(scene_dir) / "ue_import_result.json").read_text())
    print("ue import rc", rc, res)
    return 0 if res.get("ok") else 1


def cmd_shot(a):
    from .ue_run import run_game_shot
    name = f"{a.case}_ue"
    rc, logp = run_game_shot(ue_name(a.case), name, wait_ticks=a.ticks, console=a.cmd or f"fov {load_case(a.case).get('hfov', 100)}")
    src = ROOT / "ue_project" / "RenderForgeUE" / "Saved" / "Screenshots" / "WindowsEditor" / f"{name}.png"
    dst = case_dir(a.case) / "shots"
    dst.mkdir(exist_ok=True)
    if src.exists():
        shutil.copy(src, dst / src.name)
        print("shot ->", dst / src.name)
    else:
        print("shot failed, see", logp)
    return 0


def cmd_compare(a):
    from PIL import Image
    d = case_dir(a.case)
    ref = d / "export" / "reference_backbuffer.png"
    ue = d / "shots" / f"{a.case}_ue.png"
    if not (ref.exists() and ue.exists()):
        print("need", ref, "and", ue)
        return 1
    A, B = Image.open(ref).convert("RGB"), Image.open(ue).convert("RGB")
    h = 720
    A = A.resize((int(A.width * h / A.height), h))
    B = B.resize((int(B.width * h / B.height), h))
    out = Image.new("RGB", (A.width + B.width, h))
    out.paste(A, (0, 0))
    out.paste(B, (A.width, 0))
    dst = d / "shots" / "compare.png"
    out.save(dst)
    print("compare ->", dst, "(left: RenderDoc frame, right: UE)")
    return 0


def cmd_doctor(a):
    from .toolbox import doctor
    for name, ok, where, ver in doctor():
        print(f"{'OK ' if ok else 'MISSING'} {name:24} {ver:14} {where}")
    return 0


def cmd_plan(a):
    from .toolbox import plan
    print(chr(10).join(plan(a.game, a.target or "unreal")))
    return 0


def cmd_docs(a):
    from .toolbox import docs
    print("wrote docs/TOOLBOX.md,", docs(), "lines")
    return 0


def cmd_ingest_3dmigoto(a):
    from .ingest_3dmigoto import ingest
    c = load_case(a.case)
    c.update({"source": "3dmigoto", "frame_dir": str(Path(a.frame_dir).resolve())})
    if a.game:
        c["game"] = a.game
    save_case(a.case, c)
    ref = Path(a.frame_dir).resolve().parent / "final-output.png"
    r = ingest(a.frame_dir, case_dir(a.case) / "export", reference_png=ref)
    print("ingest:", r)
    return 0


def cmd_build_model(a):
    import numpy as np
    from .build import build_model_space
    from .solve import Extract
    c = load_case(a.case)
    games = json.loads((ROOT / "toolbox" / "games.json").read_text(encoding="utf-8"))["games"]
    g = games.get(a.game or c.get("game", ""), {})
    ex = Extract(case_dir(a.case) / "export")
    max_ext = a.max_extent

    def pick(d):
        m = ex.meshes[d["mesh"]]
        names = {x["name"]: i for i, x in enumerate(m["attrs"])}
        if "POSITION0" not in names:
            return False
        p = ex.mesh_attr(d["mesh"], names["POSITION0"])[:, :3]
        return float(np.ptp(p, axis=0).max()) < max_ext and m["indices"] // 3 >= a.min_tris

    sc = build_model_space(case_dir(a.case) / "export", case_dir(a.case) / "build", g.get("source_space", "unity"), pick=pick, basis=g.get("model_basis"))
    print("build-model:", sc["report"], "meshes", len(sc["meshes"]), "materials", len(sc["materials"]))
    return 0


def cmd_unity_import(a):
    from .emit_unity import emit
    from .unity_run import run
    pkg = case_dir(a.case) / "unity_pkg"
    print("emit:", emit(case_dir(a.case) / "build", pkg, ue_name(a.case)))
    shots = case_dir(a.case) / "shots"
    shots.mkdir(exist_ok=True)
    rc, logp = run("RFImportScene.Run", {"RF_UNITY_PKG": str(pkg), "RF_UNITY_SHOT": str(shots / f"{a.case}_unity.png")}, log=f"unity_import_{a.case}.log", graphics=True)
    print("unity rc", rc, (pkg / "unity_import_result.json").read_text() if (pkg / "unity_import_result.json").exists() else "no result")
    return rc


def cmd_match(a):
    from .match import load_source_lods, match_extract
    from .solve import Extract
    src = load_source_lods(a.fingerprints)
    ex = Extract(case_dir(a.case) / "export")
    res = match_extract(ex, src)
    (case_dir(a.case) / "asset_match.json").write_text(json.dumps(res, indent=1), encoding="utf-8")
    sp = case_dir(a.case) / "build" / "scene.json"
    if sp.exists():
        sc = json.loads(sp.read_text(encoding="utf-8"))
        for mid, r in res.items():
            if mid in sc["meshes"]:
                sc["meshes"][mid]["source"] = {"package": r["package"], "lod": r["lod"], "level": r["level"]}
        sp.write_text(json.dumps(sc, indent=1), encoding="utf-8")
    exact = sum(1 for r in res.values() if r["level"] == "exact")
    print(f"source LODs {len(src)}; captured meshes {len(ex.meshes)}; matched {len(res)} (exact {exact})")
    return 0


def cmd_identify(a):
    from .identify import identify, identify_draws, apply, apply_draws
    if a.ids:
        res = identify_draws(a.ids, a.index)
        p = case_dir(a.case) / "draw_identity.json"
        p.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"source sections {res['sourceSections']}; draws {res['drawsMatched']}/{res['draws']} matched; "
              f"instances {res['instancesMatched']}/{res['instances']}; unique source objects {res['uniqueSourceObjects']} -> {p}")
        sp = case_dir(a.case) / "build" / "scene.json"
        if sp.exists() and a.apply:
            print("scene.json draw identity:", apply_draws(sp, res))
        return 0
    if not a.index:
        print("identify needs --index <SourceProbe dump-index dir>, or --ids <ids.json> with --index")
        return 1
    from .identify import identify_export, apply_draws
    res = identify_export(case_dir(a.case) / "export", a.index)
    p = case_dir(a.case) / "draw_identity.json"
    p.write_text(json.dumps(res, indent=1), encoding="utf-8")
    print(f"source sections {res['sourceSections']}; draws {res['drawsMatched']}/{res['draws']} matched; "
          f"instances {res['instancesMatched']}/{res['instances']}; unique source objects {res['uniqueSourceObjects']} -> {p}")
    sp = case_dir(a.case) / "build" / "scene.json"
    if sp.exists() and a.apply:
        print("scene.json draw identity:", apply_draws(sp, res))
    return 0


def cmd_ingest_unity(a):
    from .ingest_unity import ingest_bundle
    c = load_case(a.case)
    c.update({"source": "unity-bundle", "bundle": str(Path(a.bundle).resolve())})
    save_case(a.case, c)
    sc = ingest_bundle(a.bundle, case_dir(a.case) / "build")
    print("ingest-unity:", sc["report"], "meshes", len(sc["meshes"]), "materials", len(sc["materials"]), "textures", len(sc["textures"]))
    return 0


def cmd_run(a):
    from . import pipeline
    res = pipeline.run(a.case, frm=a.from_step, to=a.to_step, force=a.force,
                       rdc=a.rdc, range=a.range, rt_count=a.rt_count, target=a.target,
                       extra_export=a.extra_export, extra_pass=a.extra_pass,
                       max_instances=a.max_instances, cmd=a.cmd, ticks=a.ticks, index=a.index,
                       ids=a.ids, no_instanced_index=a.no_instanced_index, emissive=a.emissive,
                       eids=a.eids, vs=a.vs,
                       max_extent=a.max_extent, min_tris=a.min_tris, bundle=a.bundle, frame_dir=a.frame_dir,
                       game=a.game)
    print(f"steps ran: {[r['step'] for r in res['ran']] or 'none'}")
    for r in res["ran"]:
        print(f"  + {r['step']}: {r['result']}")
    for s in res["skipped"]:
        print(f"  - {s['step']}: {s['why']}")
    if res["stopped"]:
        print(f"STOPPED at {res['stopped']['step']}: {res['stopped']['error']}")
        print(f"resume with: python -m renderforge run {a.case} --from {res['stopped']['step']}")
        return 1
    return 0


def cmd_shaders(a):
    from . import pipeline
    if not a.eids and not a.vs:
        print("shaders needs --eids <id,id,...> (the draw list to disassemble) or --vs")
        return 1
    res = pipeline.step_shaders(a.case, eids=a.eids, vs=a.vs)
    print(f"shaders rc={res['rc']} unique={res['unique']} -> {res['out']}")
    return 0 if res["rc"] == 0 else 1


def cmd_status(a):
    from . import caseinfo
    if a.case:
        info = caseinfo.scan(a.case)
        if a.json:
            print(json.dumps(info, indent=1, ensure_ascii=False))
            return 0
        rows = caseinfo.summary([info])
        print(chr(10).join(caseinfo.format_table(rows)))
        for k, v in info["detail"].items():
            print(f"  {k}: {json.dumps({x: y for x, y in v.items() if x != 'report'}, ensure_ascii=False)}")
        if (info["detail"].get("build") or {}).get("report"):
            print(f"  build report: {json.dumps(info['detail']['build']['report'], ensure_ascii=False)}")
        for n in info["notes"]:
            print(f"  ! {n}")
        print(f"next: {info['next']['step'] or 'nothing'}  --  {info['next']['why']}")
        if info["next"]["cmd"]:
            print(f"      {info['next']['cmd'].replace('{case}', a.case)}")
        return 0
    cases = caseinfo.scan_all()
    rows = caseinfo.summary(cases)
    if a.json:
        print(json.dumps({"cases": rows, "full": cases}, indent=1, ensure_ascii=False))
        return 0
    print(chr(10).join(caseinfo.format_table(rows)))
    print("\nlegend: +stage = artifact present; next = the one step that moves the case forward")
    return 0


def cmd_metrics(a):
    from . import pipeline
    # one implementation for the CLI, the step graph and the MCP tool: both engine shots get scored and recorded
    res = pipeline.step_metrics(a.case, shot=a.shot)
    print(json.dumps(res, indent=1, ensure_ascii=False))
    print("->", case_dir(a.case) / "metrics.json")
    return 0


def cmd_web(a):
    """Start the local workbench. Blocks until Ctrl-C; prints the URL to open."""
    from .web.server import serve
    return serve(a.host or "127.0.0.1", a.port or 8770)


def cmd_selftest(a):
    from .selftest import run
    res = run(verbose=True)
    return 0 if res["ok"] else 1


def cmd_backfill(a):
    from . import caseinfo
    made = caseinfo.backfill(write=a.write)
    if not made:
        print("no legacy case directories need a case.json")
    for name, case in made:
        print(f"{'wrote' if a.write else 'would write'} workspace/{name}/case.json: {json.dumps(case, ensure_ascii=False)}")
    if made and not a.write:
        print("\nre-run with --write to create them (nothing else is touched)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="renderforge")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("survey", cmd_survey), ("export", cmd_export), ("build", cmd_build), ("ue-import", cmd_ue_import),
                     ("shot", cmd_shot), ("compare", cmd_compare), ("run", cmd_run),
                     ("doctor", cmd_doctor), ("plan", cmd_plan), ("docs", cmd_docs), ("ingest-3dmigoto", cmd_ingest_3dmigoto),
                     ("build-model", cmd_build_model), ("ingest-unity", cmd_ingest_unity), ("unity-import", cmd_unity_import), ("match", cmd_match),
                     ("identify", cmd_identify), ("status", cmd_status), ("metrics", cmd_metrics), ("backfill", cmd_backfill), ("shaders", cmd_shaders),
                     ("selftest", cmd_selftest), ("web", cmd_web)):
        p = sub.add_parser(name)
        p.add_argument("case", nargs="?", default="")
        p.add_argument("--game")
        p.add_argument("--target", help="target engine (default: the case's target, else unreal)")
        p.add_argument("--frame-dir")
        p.add_argument("--bundle")
        p.add_argument("--fingerprints")
        p.add_argument("--index")
        p.add_argument("--ids")
        p.add_argument("--apply", action="store_true")
        p.add_argument("--max-extent", type=float, default=3.0)
        p.add_argument("--min-tris", type=int, default=100)
        p.add_argument("--rdc")
        p.add_argument("--range")
        p.add_argument("--rt-count", type=int)
        p.add_argument("--max-instances", type=int)
        p.add_argument("--extra-export", help="second extract (e.g. depth-only pass) merged into the build")
        p.add_argument("--extra-pass", default="extra", help="pass label recorded on merged instances")
        p.add_argument("--no-instanced-index", action="store_true",
                       help="instanced draws: skip the canonical index buffer (range/GPU-written), keep vertex range")
        p.add_argument("--emissive", action="store_true", help="albedo preview: base color also drives emissive")
        p.add_argument("--cmd")
        p.add_argument("--ticks", type=int, default=900)
        p.add_argument("--json", action="store_true", help="status: machine-readable output")
        p.add_argument("--shot", help="metrics: screenshot to compare (path, or a name under shots/)")
        p.add_argument("--eids", help="shaders: comma-separated draw event ids to disassemble")
        p.add_argument("--vs", action="store_true", help="shaders: dump the vertex shader instead of the pixel shader")
        p.add_argument("--from", dest="from_step", help="run: first step to run")
        p.add_argument("--to", dest="to_step", help="run: last step to run")
        p.add_argument("--force", action="store_true", help="run: re-run steps whose artifacts already exist")
        p.add_argument("--write", action="store_true", help="backfill: actually write case.json")
        p.add_argument("--port", type=int, default=8770, help="web: port for the local workbench")
        p.add_argument("--host", default="127.0.0.1", help="web: bind address (127.0.0.1 keeps it local)")
        p.set_defaults(fn=fn)
    a = ap.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
