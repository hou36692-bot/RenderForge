"""Run a static C# method inside headless Unity (batchmode) against the RenderForge Unity project."""
import os
import subprocess
import sys
from pathlib import Path

UNITY = Path(os.environ.get("UNITY_EXE", r"D:\Unity\60000.0.83f1\Editor\Unity.exe"))
PROJECT = Path(__file__).resolve().parent.parent / "unity_project" / "RenderForgeUnity"


def run(method, env_extra=None, log="unity_run.log", timeout=3600, graphics=False):
    logp = Path(__file__).resolve().parent.parent / "workspace" / log
    logp.parent.mkdir(exist_ok=True)
    cmd = [str(UNITY), "-batchmode", "-quit", "-projectPath", str(PROJECT), "-executeMethod", method, "-logFile", str(logp)]
    if not graphics:
        cmd.insert(2, "-nographics")
    p = subprocess.run(cmd, env=dict(os.environ, **(env_extra or {})), timeout=timeout)
    return p.returncode, logp


if __name__ == "__main__":
    rc, logp = run(sys.argv[1])
    print("exit", rc, "log", logp)
