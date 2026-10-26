"""Background jobs: run a CLI command detached, with its output and exit code on disk.

Long steps (RenderDoc export, UE import, screenshots) take minutes. A conversation should be able to start one and
keep working, so every job is a normal `python -m renderforge ...` subprocess whose stdout/stderr go to
workspace/_jobs/<id>.log and whose exit code lands in <id>.done.json. Because all state is on disk, `status` works
from a later session too (no in-process handles).
"""
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:                                    # a detached child must not hold the parent's stdout/stderr pipes open
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent
JOBS = ROOT / "workspace" / "_jobs"


def _job_id(argv):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    h = hashlib.sha1((" ".join(argv) + stamp).encode("utf-8")).hexdigest()[:6]
    return f"{stamp}-{h}"


def start(argv, name=None, env=None, cwd=None):
    """Launch `python -m renderforge <argv>` detached; returns the job record (id, log path, pid).

    `env` adds to this process's environment (the web workbench uses it for UI-only switches such as RF_ONLY_KIND).
    `cwd` defaults to the repository root, which the CLI resolves relative paths against."""
    JOBS.mkdir(parents=True, exist_ok=True)
    jid = _job_id(argv)
    meta = {"id": jid, "argv": list(argv), "name": name or argv[0], "started": time.time(),
            "log": str(JOBS / f"{jid}.log")}
    if env:
        meta["env"] = dict(env)
    (JOBS / f"{jid}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    full_env = dict(os.environ, **(env or {}))
    log = open(meta["log"], "w", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "renderforge.jobrun", jid, *argv],
                         cwd=cwd or ROOT, env=full_env, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL)
    meta["pid"] = p.pid
    (JOBS / f"{jid}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def status(jid):
    meta = json.loads((JOBS / f"{jid}.json").read_text(encoding="utf-8"))
    done_p = JOBS / f"{jid}.done.json"
    if done_p.exists():
        meta.update(json.loads(done_p.read_text(encoding="utf-8")))
        meta["state"] = "done" if meta.get("rc") == 0 else "failed"
        return meta
    # no done marker: still running if the log was touched recently, otherwise the process died without a marker
    age = time.time() - (JOBS / f"{jid}.log").stat().st_mtime if (JOBS / f"{jid}.log").exists() else 1e9
    meta["state"] = "running" if age < 30 else "unknown (process gone, no exit marker)"
    meta["seconds"] = round(time.time() - meta["started"], 1)
    return meta


def log(jid, tail=60, max_chars=8000):
    p = JOBS / f"{jid}.log"
    if not p.exists():
        return ""
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    return "\n".join(lines[-tail:])[-max_chars:]


def list_jobs(limit=20):
    if not JOBS.exists():
        return []
    out = []
    for p in sorted(JOBS.glob("*.json"), reverse=True):
        if p.name.endswith(".done.json"):
            continue
        try:
            out.append(status(p.stem))
        except (OSError, ValueError):
            continue
        if len(out) >= limit:
            break
    return out


def wait(jid, timeout=120.0, poll=1.0):
    """Block until the job finishes or the timeout passes; returns the status either way."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = status(jid)
        if st["state"] in ("done", "failed"):
            return st
        time.sleep(poll)
    st = status(jid)
    st["waited"] = round(time.time() - t0, 1)
    return st
