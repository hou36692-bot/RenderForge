# Runs inside qrenderdoc's Python 3.6; stdlib only, normal RenderDoc target control.
# job.json: {"mode": "targets"|"launch"|"trigger", "result_file": "...", "timeout": s, "delay": s, "frames": n,
#            "target_ident": int|None, "expected_target": str, "exe"/"working_dir"/"cmdline"/"capture_template"}
import json
import os
import sys
import time
import traceback

JOB = json.load(open(os.environ["RENDERFORGE_JOB"], encoding="utf-8"))
RESULT = JOB["result_file"]
STATE = {"ok": False, "action": JOB["mode"], "events": []}
START = time.monotonic()


def save():
    tmp = RESULT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(STATE, f, indent=1)
    os.replace(tmp, RESULT)


def event(kind, **detail):
    STATE["events"].append(dict(kind=kind, elapsed=round(time.monotonic() - START, 3), **detail))
    save()


def target_info(ctrl, ident):
    return {"ident": ident, "pid": ctrl.GetPID(), "target": ctrl.GetTarget(), "api": ctrl.GetAPI()}


def discover(rd, expected, deadline):
    """Find an injected target whose name matches `expected`; only ident + name are read (no capture)."""
    while time.monotonic() < deadline:
        ident = rd.EnumerateRemoteTargets("", 0)
        while ident:
            ctrl = rd.CreateTargetControl("", ident, "RenderForgeDiscover", False)
            if ctrl is not None:
                try:
                    name = ctrl.GetTarget()
                finally:
                    ctrl.Shutdown()
                event("target_found", ident=ident, target=name)
                if not expected or expected.lower() in (name or "").lower():
                    return ident
            ident = rd.EnumerateRemoteTargets("", ident)
        time.sleep(0.2)
    return None


def main():
    import renderdoc as rd
    mode = JOB["mode"]
    if mode == "targets":
        targets, ident = [], 0
        while time.monotonic() - START < JOB["timeout"]:
            ident = rd.EnumerateRemoteTargets("", ident)
            if not ident:
                break
            ctrl = rd.CreateTargetControl("", ident, "RenderForge", False)
            if ctrl is None:
                targets.append({"ident": ident, "busy": True})
                continue
            try:
                targets.append(target_info(ctrl, ident))
            finally:
                ctrl.Shutdown()
        STATE.update(ok=True, targets=targets)
        return

    ident = JOB.get("target_ident")
    if mode == "launch":
        opts = rd.CaptureOptions()
        opts.hookIntoChildren = False
        result = rd.ExecuteAndInject(JOB["exe"], JOB["working_dir"], JOB["cmdline"], [],
                                     JOB["capture_template"], opts, False)
        status = getattr(result.result, "code", result.result)
        event("launch", status=str(status), ident=result.ident)
        if status != rd.ResultCode.Succeeded or not result.ident:
            STATE["error"] = "RenderDoc launch failed: %s" % result.result
            return
        ident = result.ident
        STATE["launched"] = True
    elif not ident:
        ident = discover(rd, JOB.get("expected_target"), START + JOB["timeout"])
        if not ident:
            STATE["error"] = "No RenderDoc-injected target matching %r appeared within %ss" % (
                JOB.get("expected_target"), JOB["timeout"])
            return
        event("discovered", ident=ident)

    ctrl = rd.CreateTargetControl("", ident, "RenderForge", False)
    if ctrl is None:
        STATE["error"] = "Cannot connect to target %s (absent or controlled by another client)" % ident
        return
    try:
        if not ctrl.Connected():
            STATE["error"] = "RenderDoc target is disconnected"
            return
        info = target_info(ctrl, ident)
        STATE["target"] = info
        STATE["target_ident"] = ident
        event("connected", **info)
        if mode == "launch":
            STATE["ok"] = True
            return
        expected = JOB.get("expected_target")
        if expected and expected.lower() not in (info["target"] or "").lower():
            STATE["error"] = "Target is not the requested game (%s)" % expected
            return
        deadline = START + JOB["timeout"]
        captures = []
        triggered = False
        api_since = None
        api = info["api"]
        while time.monotonic() < deadline and ctrl.Connected():
            msg = ctrl.ReceiveMessage(None)
            typ = msg.type
            if typ == rd.TargetControlMessageType.Busy:
                STATE["error"] = "Target busy: %s" % ctrl.GetBusyClient()
                return
            if typ == rd.TargetControlMessageType.Disconnected:
                break
            if not api:
                api = ctrl.GetAPI()
                if api:
                    api_since = time.monotonic()
                    STATE["target"]["api"] = api
                    event("api", api=api, delay=JOB["delay"])
            if typ == rd.TargetControlMessageType.NewCapture:
                c = msg.newCapture
                if triggered or api_since is not None:
                    if not c.local:
                        STATE["error"] = "Only local capture files are supported; remote files are not copied"
                        return
                    path = os.path.abspath(c.path)
                    if not os.path.isfile(path):
                        STATE["error"] = "RenderDoc reported a capture path that does not exist"
                        return
                    with open(path, "rb") as f:
                        if f.read(4) != b"RDOC":
                            STATE["error"] = "Capture file has no RDOC header"
                            return
                    item = {"path": path, "frame": c.frameNumber, "api": str(c.api),
                            "bytes": os.path.getsize(path), "timestamp": c.timestamp}
                    if not any(x["path"] == path for x in captures):
                        captures.append(item)
                        event("capture", **item)
                    cap = rd.OpenCaptureFile()
                    try:
                        res = cap.OpenFile(captures[0]["path"], "", None)
                        status = getattr(res, "code", res)
                        if status != rd.ResultCode.Succeeded:
                            STATE["error"] = "Captured file could not be opened: %s" % res
                            return
                    finally:
                        cap.Shutdown()
                    STATE.update(ok=True, captures=captures, path=captures[0]["path"], capture_file_opened=True)
                    return
            if api and not triggered and api_since is not None and time.monotonic() >= api_since + JOB["delay"]:
                ctrl.TriggerCapture(JOB["frames"])
                triggered = True
                event("trigger", frames=JOB["frames"], api=api)
            time.sleep(0.02)
        STATE["error"] = ("Capture timed out after API %r (triggered=%s); no capture saved" % (api, triggered)
                          if ctrl.Connected() else "Target disconnected before capture (API %r, triggered=%s)" % (api, triggered))
    finally:
        ctrl.Shutdown()


code = 0
try:
    save()
    main()
    code = 0 if STATE.get("ok") else 1
except Exception as e:
    STATE["error"] = "%s: %s" % (type(e).__name__, e)
    STATE["traceback"] = traceback.format_exc()
    code = 1
finally:
    STATE["elapsed"] = round(time.monotonic() - START, 3)
    save()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
