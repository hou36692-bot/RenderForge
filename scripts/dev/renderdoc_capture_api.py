# Inspect the installed RenderDoc capture API; no target process is launched.
import json
import os
import sys
import traceback


def main():
    import renderdoc as rd
    names = ("ExecuteAndInject", "InjectIntoProcess", "EnumerateRemoteTargets", "CreateTargetControl", "CaptureOptions")
    data = {}
    for name in names:
        obj = getattr(rd, name, None)
        data[name] = {"available": obj is not None, "doc": getattr(obj, "__doc__", None)}
    for name in ("TargetControl", "TargetControlMessage", "NewCaptureData", "ExecuteResult", "TargetControlMessageType"):
        obj = getattr(rd, name, None)
        if obj is not None:
            data[name] = {"doc": getattr(obj, "__doc__", None), "members": [n for n in dir(obj) if not n.startswith("_")]}
            if name == "TargetControl":
                data[name]["methods"] = {n: getattr(getattr(obj, n), "__doc__", None) for n in dir(obj) if not n.startswith("_")}
    dest = os.environ["RF_CAPTURE_API_REPORT"]
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)


code = 0
try:
    main()
except Exception:
    traceback.print_exc()
    code = 1
sys.stdout.flush()
sys.stderr.flush()
os._exit(code)
