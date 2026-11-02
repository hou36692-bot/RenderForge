"""Normal RenderDoc launch/target control, independent of the post-capture step graph.

Launching requires explicit consent per request; a game profile never stores permission to inject.
No global hook, child hook, protection bypass, remote copy, or original-capture modification is used.
"""
import json
import subprocess
import uuid
from pathlib import Path

from . import rd_run


def request(case_dir, action="trigger", game=None, exe=None, cmdline=None, working_dir=None,
            target_ident=None, delay=0, timeout=60, frames=1, allow_launch=False):
    if action not in ("targets", "launch", "trigger"):
        raise ValueError("capture action must be targets, launch or trigger")
    if action == "launch" and not allow_launch:
        raise ValueError("launch requires --allow-launch for this request; no game is started")
    if not 1 <= frames <= 3:
        raise ValueError("capture-frames must be 1..3")
    if not 1 <= timeout <= 600 or not 0 <= delay < timeout:
        raise ValueError("capture timeout must be 1..600 seconds and 0 <= delay < timeout")
    out = Path(case_dir).resolve()
    if not out.name or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-" for c in out.name):
        raise ValueError("invalid case directory id")
    cp = out / "case.json"
    case = json.loads(cp.read_text(encoding="utf-8")) if cp.exists() else {}
    if game and case.get("game") and game != case["game"]:
        raise ValueError("game differs from the existing case; use a new case")
    game = game or case.get("game")
    from .toolbox import load
    games = load()[2]
    profile = games.get(game, {})
    defaults = profile.get("capture_launch") or {}
    if action == "launch":
        exe = exe or defaults.get("exe")
        if not exe or not Path(exe).is_file():
            raise ValueError("capture executable not found; supply --exe")
        exe = str(Path(exe).resolve())
        working_dir = str(Path(working_dir or Path(exe).parent).resolve())
        if not Path(working_dir).is_dir():
            raise ValueError("capture working directory does not exist")
        cmdline = defaults.get("args", "") if cmdline is None else cmdline
    prior = (case.get("capture") or {}).get("target") or {}
    target_ident = target_ident or prior.get("ident")
    if action == "trigger" and (not target_ident or not 1 <= int(target_ident) <= 65535):
        raise ValueError("trigger requires a live --target-ident (or this case's successful launch)")

    evidence = out / "capture" / uuid.uuid4().hex[:12]
    evidence.mkdir(parents=True)
    result_file = evidence / "capture_result.json"
    job = {"mode": action, "out": str(evidence), "result_file": str(result_file),
           "timeout": float(timeout), "delay": float(delay), "frames": int(frames),
           "target_ident": int(target_ident) if target_ident else None,
           "capture_template": str(evidence / "frame"), "exe": exe,
           "working_dir": working_dir, "cmdline": cmdline or "",
           "expected_target": defaults.get("target")}
    error = None
    try:
        rc = rd_run.run("rf_capture.py", job, timeout=timeout + 30)
    except (subprocess.TimeoutExpired, OSError) as e:
        rc = -1
        error = "%s: %s" % (type(e).__name__, e)
    result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {"ok": False}
    result.update(action=action, game=game, returncode=rc, evidence=str(result_file))
    if error or rc != 0:
        result["ok"] = False
        result.setdefault("error", error or "RenderDoc capture script exited with code %s" % rc)
    if result.get("ok") and action == "trigger":
        path = Path(result.get("path", ""))
        if not path.is_file():
            result.update(ok=False, error="RenderDoc reported no existing capture file")
        else:
            with path.open("rb") as f:
                if f.read(4) != b"RDOC":
                    result.update(ok=False, error="capture does not have an RDOC header")
            if result["ok"]:
                # Reference the original where RenderDoc wrote it. Never copy or rewrite it.
                case.update(source="rdc", rdc=str(path.resolve()))
    result_file.write_text(json.dumps(result, indent=1), encoding="utf-8")
    if action != "targets":
        if game:
            case["game"] = game
        record = {"action": action, "ok": result["ok"], "evidence": str(result_file)}
        if result.get("target"):
            record["target"] = result["target"]
        elif prior:
            record["target"] = prior
        if result.get("error"):
            record["error"] = result["error"]
        case["capture"] = record
        cp.write_text(json.dumps(case, indent=1, ensure_ascii=False), encoding="utf-8")
    return result
