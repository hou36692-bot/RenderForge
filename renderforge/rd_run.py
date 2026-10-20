"""Run a script inside RenderDoc's embedded Python (headless) with a JSON job."""
import json
import os
import subprocess
import sys
from pathlib import Path

RD_DIR = Path(os.environ.get("RENDERDOC_DIR", r"C:\Program Files\RenderDoc"))
SCRIPTS = Path(__file__).resolve().parent / "rd_scripts"


def run(script, job, timeout=3600):
    out = Path(job["out"])
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"job_{job['mode']}.json"
    jp.write_text(json.dumps(job, indent=1), encoding="utf-8")
    env = dict(os.environ, RENDERFORGE_JOB=str(jp))
    p = subprocess.run([str(RD_DIR / "qrenderdoc.exe"), "--python", str(SCRIPTS / script)], env=env, timeout=timeout)
    return p.returncode


if __name__ == "__main__":
    mode, rdc, out = sys.argv[1:4]
    job = {"mode": mode, "rdc": rdc, "out": out}
    if len(sys.argv) > 4:
        a, b = sys.argv[4].split("-")
        job["range"] = [int(a), int(b)]
    sys.exit(run("rf_extract.py", job))
