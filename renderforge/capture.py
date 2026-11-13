"""Normal RenderDoc launch/inject/target control, independent of the post-capture step graph.

Actions:
  targets       list RenderDoc-injected targets on this machine
  launch        ExecuteAndInject a program (needs allow_launch for this request; never persisted)
  trigger       capture from an injected target (an ident, or auto-discovered by name)
  watch-inject  wait for a NEW process of a given image name, inject before its graphics device exists,
                then trigger one capture (the launcher-auth route: some games refuse to start otherwise)

No global hook, protection bypass, remote copy, or modification of original captures is used. A launcher
that blocks injection simply fails; the failure is recorded and not worked around.
"""
import json
import re
import subprocess
import time
import uuid
from pathlib import Path

from . import rd_run

_ACTIONS = ("targets", "launch", "trigger", "watch-inject")


def _pids(exe_name):
    """PIDs whose image name equals `exe_name` (Toolhelp snapshot; no process is opened or modified)."""
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)), ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD), ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260)]

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    k32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.POINTER(PROCESSENTRY32W)]
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)          # TH32CS_SNAPPROCESS
    if not snap or snap == ctypes.c_void_p(-1).value:
        raise OSError("CreateToolhelp32Snapshot failed")
    out = []
    try:
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == exe_name.lower():
                out.append(int(e.th32ProcessID))
            ok = k32.Process32NextW(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)
    return out


def request(case_dir, action="trigger", game=None, exe=None, cmdline=None, working_dir=None,
            target_ident=None, delay=0, timeout=60, frames=1, allow_launch=False, process_name=None):
    if action not in _ACTIONS:
        raise ValueError("capture action must be one of: " + ", ".join(_ACTIONS))
    if action == "launch" and not allow_launch:
        raise ValueError("launch requires --allow-launch for this request; no program is started")
    if not 1 <= frames <= 3:
        raise ValueError("capture-frames must be 1..3")
    if not 0 <= delay < 3600:
        raise ValueError("capture delay must be 0..3600 seconds")
    if not 1 <= timeout <= (1800 if action == "watch-inject" else 600):
        raise ValueError("capture timeout is 1..600 s (1..1800 for watch-inject)")
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
    if action == "watch-inject":
        process_name = process_name or defaults.get("process")
        if not process_name:
            raise ValueError("watch-inject needs --process-name (or capture_launch.process in the game profile)")

    evidence = out / "capture" / uuid.uuid4().hex[:12]
    evidence.mkdir(parents=True)
    result_file = evidence / "capture_result.json"
    base = {"mode": "trigger", "out": str(evidence), "result_file": str(result_file),
            "expected_target": defaults.get("target"),
            "capture_template": str(evidence / "frame"), "exe": exe, "working_dir": working_dir,
            "cmdline": cmdline or "", "frames": int(frames), "delay": float(delay)}

    inject = None
    if action == "watch-inject":
        t0 = time.time()
        baseline = set(_pids(process_name))
        pid = None
        while time.time() - t0 < timeout:
            fresh = [p for p in _pids(process_name) if p not in baseline]
            if fresh:
                pid = fresh[0]
                break
            time.sleep(0.05)
        if pid is None:
            result = {"ok": False, "action": action, "error": "no new %s process appeared within %ss" % (process_name, timeout),
                      "waited_s": round(time.time() - t0, 1), "evidence": str(result_file)}
            result_file.write_text(json.dumps(result, indent=1), encoding="utf-8")
            _record(cp, case, game, action, result, prior)
            return result
        cmd = [str(rd_run.RD_DIR / "renderdoccmd.exe"), "inject", "--PID=%d" % pid,
               "--capture-file=%s" % (evidence / "frame")]
        t1 = time.time()
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        inject = {"pid": pid, "returncode": p.returncode, "stdout": p.stdout[-4000:], "stderr": p.stderr[-4000:],
                  "elapsed_s": round(time.time() - t1, 2), "detected_s": round(t1 - t0, 2)}
        # renderdoccmd inject exits with the new target ident, not 0, when it succeeds
        m = re.search(r"Launched as ID (\d+)", (p.stdout or "") + "\n" + (p.stderr or ""))
        ident = int(m.group(1)) if m else None
        inject["ident"] = ident
        if p.returncode != 0 and ident is None:
            result = {"ok": False, "action": action, "error": "RenderDoc injection failed (exit %s)" % p.returncode,
                      "inject": inject, "evidence": str(result_file)}
            result_file.write_text(json.dumps(result, indent=1), encoding="utf-8")
            _record(cp, case, game, action, result, prior)
            return result
        base.update(target_ident=ident, timeout=min(300.0, max(60.0, float(timeout) / 3)))

    error = None
    try:
        rc = rd_run.run("rf_capture.py", base, timeout=base["timeout"] + 60)
    except (subprocess.TimeoutExpired, OSError) as e:
        rc = -1
        error = "%s: %s" % (type(e).__name__, e)
    result = json.loads(result_file.read_text(encoding="utf-8")) if result_file.exists() else {"ok": False}
    result.update(action=action, game=game, returncode=rc, evidence=str(result_file))
    if inject is not None:
        result["inject"] = inject
    if error or rc != 0:
        result["ok"] = False
        result.setdefault("error", error or "RenderDoc capture script exited with code %s" % rc)
    if result.get("ok") and result.get("path"):
        path = Path(result["path"])
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
        _record(cp, case, game, action, result, prior)
    return result


def _record(cp, case, game, action, result, prior):
    if game:
        case["game"] = game
    record = {"action": action, "ok": result.get("ok") is True, "evidence": result.get("evidence")}
    if result.get("target"):
        record["target"] = result["target"]
    elif prior:
        record["target"] = prior
    if result.get("error"):
        record["error"] = result["error"]
    if result.get("inject"):
        record["inject"] = {k: result["inject"].get(k) for k in ("pid", "returncode", "elapsed_s", "detected_s")}
    case["capture"] = record
    cp.parent.mkdir(parents=True, exist_ok=True)
    cp.write_text(json.dumps(case, indent=1, ensure_ascii=False), encoding="utf-8")
