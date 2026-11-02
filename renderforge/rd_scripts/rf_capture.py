# Runs inside qrenderdoc's Python 3.6; stdlib only, normal RenderDoc target control.
import json
import os
import sys
import time
import traceback

JOB = json.load(open(os.environ["RENDERFORGE_JOB"], encoding="utf-8"))
RESULT = JOB["result_file"]
STATE = {"ok": False, "action": JOB["mode"], "events": []}


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


def main():
    import renderdoc as rd
    mode = JOB["mode"]
    if mode == "targets":
        targets = []
        ident = 0
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
        # Never enable global/child hooking or force somebody else's control connection.
        opts.hookIntoChildren = False
        result = rd.ExecuteAndInject(JOB["exe"], JOB["working_dir"], JOB["cmdline"], [],
                                     JOB["capture_template"], opts, False)
        status = getattr(result.result, "code", result.result)
        event("launch", status=str(status), ident=result.ident)
        if status != rd.ResultCode.Succeeded or not result.ident:
            STATE["error"] = "RenderDoc launch failed: %s" % result.result
            return
        ident = result.ident

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
        event("connected", **info)
        if mode == "launch":
            STATE["ok"] = True
            return
        expected = JOB.get("expected_target")
        if expected and expected.lower() not in info["target"].lower():
            STATE["error"] = "Target is not the requested game (%s)" % expected
            return
        deadline = START + JOB["timeout"]
        trigger_at = time.monotonic() + JOB["delay"]
        triggered = False
        captures = []
        while time.monotonic() < deadline and ctrl.Connected():
            msg = ctrl.ReceiveMessage(None)
            typ = msg.type
            if typ == rd.TargetControlMessageType.Busy:
                STATE["error"] = "Target busy: %s" % ctrl.GetBusyClient()
                return
            if typ == rd.TargetControlMessageType.Disconnected:
                break
            if typ == rd.TargetControlMessageType.RegisterAPI:
                STATE["target"]["api"] = ctrl.GetAPI()
                event("api", api=ctrl.GetAPI())
            if typ == rd.TargetControlMessageType.NewCapture:
                c = msg.newCapture
                # Old captures sent during the handshake are not the requested new frame.
                if triggered and c.timestamp >= STATE["trigger_timestamp"]:
                    if not c.local:
                        STATE["error"] = "Only local capture files are supported; remote files are not copied"
                        return
                    path = os.path.abspath(c.path)
                    if os.path.isfile(path):
                        with open(path, "rb") as f:
                            if f.read(4) != b"RDOC":
                                STATE["error"] = "Capture file has no RDOC header"
                                return
                        item = {"path": path, "frame": c.frameNumber, "api": str(c.api),
                                "bytes": os.path.getsize(path), "timestamp": c.timestamp}
                        if not any(x["path"] == path for x in captures):
                            captures.append(item)
                            event("capture", **item)
                    if len(captures) >= JOB["frames"]:
                        cap = rd.OpenCaptureFile()
                        try:
                            res = cap.OpenFile(captures[0]["path"], "", None)
                            status = getattr(res, "code", res)
                            if status != rd.ResultCode.Succeeded:
                                STATE["error"] = "Captured file could not be opened: %s" % res
                                return
                            STATE.update(ok=True, captures=captures, path=captures[0]["path"],
                                         capture_file_opened=True)
                            return
                        finally:
                            cap.Shutdown()
            if not triggered and time.monotonic() >= trigger_at and ctrl.GetAPI():
                STATE["trigger_timestamp"] = int(time.time())
                ctrl.TriggerCapture(JOB["frames"])
                triggered = True
                event("trigger", frames=JOB["frames"], api=ctrl.GetAPI())
            time.sleep(0.02)
        STATE["error"] = ("Capture timed out (API=%r, requested=%s); no new frame saved" %
                          (ctrl.GetAPI(), triggered)) if ctrl.Connected() else "Target disconnected before capture"
    finally:
        ctrl.Shutdown()


START = time.monotonic()
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
