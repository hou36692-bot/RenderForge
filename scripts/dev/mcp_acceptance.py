#!/usr/bin/env python
"""Acceptance test for the RenderForge agent surface (ROADMAP stage B item 3).

The criterion is: a fresh session can do doctor -> plan -> status -> build -> metrics using ONLY the MCP tools, with
output identical to the CLI. A Claude session cannot load a server registered after it started, so this test speaks
raw JSON-RPC over stdio to `python -m renderforge.mcp_server` itself: same process, same protocol, no `renderforge.*`
imports here. That is the only way to check the surface rather than the library behind it.

Run:  python scripts/dev/mcp_acceptance.py        (exit 0 = every check passed)

Deterministic side effects, safe to repeat: `step build` regenerates workspace/wuwa_f3765/build/scene.json from the
existing export, and the metrics calls rewrite workspace/wuwa_f3765/metrics.json; the CLI parity runs also bump
`steps.metrics` in case.json (five fields are deterministic, only `when`/`run`/`seconds` move). The last CLI call
leaves metrics.json in the CLI's canonical form, so a second run starts from the same state.
"""
import json
import os
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASE = "wuwa_f3765"
TIMEOUT = 300                     # per call; a hung server must fail the run instead of hanging it
TOOLS = ["doctor", "plan", "games", "status", "run", "step", "metrics",
         "job_start", "job_status", "job_log", "jobs"]

_results = []


def check(name, ok, detail=""):
    _results.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))
    return bool(ok)


def _env():
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def first_json(text):
    """First JSON object in CLI stdout (some commands print a line after the document)."""
    i = text.find("{")
    if i < 0:
        raise RuntimeError(f"no JSON object in CLI output: {text[:200]!r}")
    obj, _ = json.JSONDecoder().raw_decode(text[i:])
    return obj


def cli(*args, timeout=TIMEOUT):
    r = subprocess.run([sys.executable, "-m", "renderforge", *args], cwd=str(ROOT), env=_env(),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if r.returncode != 0:
        raise RuntimeError(f"CLI {' '.join(args)} failed rc={r.returncode}: {r.stderr[-400:]}")
    return first_json(r.stdout)


def same_num(a, b):
    """Agreement at the precision the tools print (six significant digits)."""
    try:
        return abs(float(a) - float(b)) <= 5e-7
    except (TypeError, ValueError):
        return a == b


def row_diff(mcp_rows, cli_rows):
    """Empty string when the two case tables are identical, else the first differing field, with both values."""
    if not isinstance(mcp_rows, list) or not isinstance(cli_rows, list):
        return f"missing case list (mcp={type(mcp_rows).__name__}, cli={type(cli_rows).__name__})"
    if mcp_rows == cli_rows:
        return ""
    if len(mcp_rows) != len(cli_rows):
        return f"case count mcp={len(mcp_rows)} cli={len(cli_rows)}"
    for x, y in zip(mcp_rows, cli_rows):
        if x != y:
            keys = sorted(k for k in set(x) | set(y) if x.get(k) != y.get(k))
            vals = ", ".join(f"{k}: mcp={x.get(k)!r} cli={y.get(k)!r}" for k in keys)
            return f"{x.get('case')}: {vals}"
    return "rows differ"


class Mcp:
    """Minimal stdio MCP client: one process, newline-delimited JSON-RPC, a deadline on every read."""

    def __init__(self):
        self.p = subprocess.Popen([sys.executable, "-m", "renderforge.mcp_server"], cwd=str(ROOT), env=_env(),
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace")
        self.q = queue.Queue()
        self.err = []
        self.n = 0
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read_out(self):
        for line in self.p.stdout:
            self.q.put(line)
        self.q.put(None)                       # EOF marker

    def _read_err(self):
        for line in self.p.stderr:
            self.err.append(line.rstrip())
            del self.err[:-40]

    def tail(self):
        return " | ".join(self.err[-3:]) or "(no stderr)"

    def alive(self):
        return self.p.poll() is None

    def _send(self, msg):
        if not self.alive():
            raise RuntimeError(f"server exited (rc={self.p.returncode}); stderr: {self.tail()}")
        self.p.stdin.write(json.dumps(msg) + "\n")
        self.p.stdin.flush()

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def request(self, method, params=None, timeout=TIMEOUT):
        self.n += 1
        rid = self.n
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                raise TimeoutError(f"{method} (id {rid}) unanswered after {timeout}s; stderr: {self.tail()}")
            try:
                line = self.q.get(timeout=remain)
            except queue.Empty:
                raise TimeoutError(f"{method} (id {rid}) unanswered after {timeout}s; stderr: {self.tail()}")
            if line is None:
                raise RuntimeError(f"server closed stdout during {method}; stderr: {self.tail()}")
            try:
                reply = json.loads(line)
            except ValueError:
                continue                       # not protocol traffic; ignore
            if reply.get("id") == rid:
                if "error" in reply:
                    raise RuntimeError(f"{method} transport error: {reply['error']}")
                return reply

    def tool(self, name, args=None, timeout=TIMEOUT):
        """-> (the tool's `result` payload, isError). The envelope is {ok, tool, result} or {ok, tool, error}."""
        res = self.request("tools/call", {"name": name, "arguments": args or {}}, timeout)["result"]
        text = "".join(c.get("text", "") for c in res.get("content") or [] if c.get("type") == "text")
        try:
            env = json.loads(text)
        except ValueError:
            raise RuntimeError(f"{name}: tool result is not JSON text: {text[:200]!r}")
        payload = env.get("result") if env.get("ok") else env
        return payload, bool(res.get("isError"))

    def close(self):
        try:
            if self.p.stdin and not self.p.stdin.closed:
                self.p.stdin.close()           # EOF ends the server's read loop
        except OSError:
            pass
        try:
            self.p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.p.kill()
            self.p.wait(timeout=10)


def run_checks(mcp):
    init = mcp.request("initialize", {"protocolVersion": "2024-11-05"})["result"]
    info = init.get("serverInfo") or {}
    check("initialize", init.get("protocolVersion") == "2024-11-05" and "tools" in (init.get("capabilities") or {}),
          f"protocol={init.get('protocolVersion')} server={info.get('name')} {info.get('version')}")
    mcp.notify("notifications/initialized")

    names = [t["name"] for t in mcp.request("tools/list")["result"]["tools"]]
    check("tools/list", len(names) == len(TOOLS) and set(names) == set(TOOLS),
          f"{len(names)} tools: {', '.join(names)}")

    # 1. doctor
    doc, err = mcp.tool("doctor")
    rows = doc if isinstance(doc, list) else []
    shape = bool(rows) and all(isinstance(r, dict) and set(r) == {"tool", "ok", "where", "version"} for r in rows)
    check("doctor", not err and shape,
          f"{len(rows)} tools, {sum(1 for r in rows if r.get('ok'))} ok" if shape else f"unexpected shape: {str(doc)[:160]}")

    # 2. plan
    plan, err = mcp.tool("plan", {"game": "wuwa", "target": "unreal"})
    ok = not err and isinstance(plan, list) and bool(plan) and all(isinstance(x, str) for x in plan) \
        and plan[0].startswith("Game:")
    check("plan", ok, plan[0] if ok else f"unexpected output: {str(plan)[:160]}")

    # 3. status, all cases -- then compare with the CLI immediately, before later steps mutate anything.
    # The case count is not pinned: workspace/ is shared with other agents, so the CLI's own list is the oracle.
    st, err = mcp.tool("status")
    cases = st.get("cases") if isinstance(st, dict) else None
    names = [r.get("case") for r in cases] if isinstance(cases, list) else []
    ok = not err and isinstance(cases, list) and bool(cases) and CASE in names and all(
        {"case", "source", "stages", "next"} <= set(r) for r in cases)
    check("status-all", ok, f"{len(names)} cases: {', '.join(map(str, names))}" if ok
          else f"unexpected: {str(st)[:160]}")

    cli_cases = cli("status", "--json").get("cases")
    diff = row_diff(cases, cli_cases)
    check("cli-parity-status", diff == "",
          f"{len(names)} cases identical" if diff == "" else diff)

    # 4. status, one case
    det, err = mcp.tool("status", {"case": CASE})
    nxt = (det or {}).get("next") if isinstance(det, dict) else None
    bld = ((det or {}).get("detail") or {}).get("build") or {}
    ok = not err and isinstance(nxt, dict) and "step" in nxt
    check("status-case", ok, f"next.step={nxt.get('step')!r} build vp_match={bld.get('vp_match')!r}")

    # 5. build (rebuilds the 19-draw case from the same export)
    b, err = mcp.tool("step", {"case": CASE, "step": "build"}, timeout=180)
    ok = not err and isinstance(b, dict) and "vp_match" in b
    check("step-build", ok, f"vp_match={b.get('vp_match')!r} meshes={b.get('meshes')} "
                            f"instances={b.get('instances')} report={b.get('report')}")

    # 6. metrics
    met, err = mcp.tool("metrics", {"case": CASE}, timeout=120)
    ssim, comp = (met or {}).get("ssim"), (met or {}).get("compared")
    ok = not err and isinstance(ssim, (int, float)) and 0.0 <= float(ssim) <= 1.0 and bool(comp)
    check("metrics", ok, f"ssim={ssim!r} compared={comp!r}")

    # CLI parity, part 1: score the same shot so only the comparison is under test
    cli_same = cli("metrics", CASE, "--shot", comp, timeout=180)
    ok = same_num(cli_same.get("ssim"), ssim) and cli_same.get("compared") == comp
    check("cli-parity-metrics-same-shot", ok,
          f"mcp ssim={ssim!r} compared={comp!r} | cli ssim={cli_same.get('ssim')!r} "
          f"compared={cli_same.get('compared')!r}")

    # CLI parity, part 2: the documented equivalent command, defaults and all. A difference here is a real
    # difference between the surfaces (shot selection / record keeping), not just float noise.
    cli_met = cli("metrics", CASE, timeout=180)
    ok = same_num(cli_met.get("ssim"), ssim) and cli_met.get("compared") == comp
    check("cli-parity-metrics", ok,
          f"mcp ssim={ssim!r} compared={comp!r} | cli ssim={cli_met.get('ssim')!r} "
          f"compared={cli_met.get('compared')!r}")

    # 7. run identify only; skipped is legitimate (index missing, or the artifact already exists)
    runp, err = mcp.tool("run", {"case": CASE, "from": "identify", "to": "identify"}, timeout=180)
    ran = [s.get("step") for s in (runp.get("ran") or [])] if isinstance(runp, dict) else []
    skipped = [f"{s.get('step')} ({s.get('why')})" for s in (runp.get("skipped") or [])] \
        if isinstance(runp, dict) else []
    ok = not err and isinstance(runp, dict) and "stopped" in runp and runp.get("stopped") is None
    check("run-identify", ok, f"ran={ran} skipped={skipped} stopped={runp.get('stopped')!r}"
          if isinstance(runp, dict) else f"unexpected: {str(runp)[:160]}")

    # 8. a failing tool call is reported as isError and does not kill the server
    bad = "rf_acceptance_missing_shot.png"
    ep, eerr = mcp.tool("metrics", {"case": CASE, "shot": bad}, timeout=60)
    msg = ep.get("error", "") if isinstance(ep, dict) else str(ep)
    check("metrics-missing-shot-isError", eerr and bad in msg,
          f"isError={eerr} error={msg[:160]!r}")

    pong = mcp.request("ping", {}, timeout=30)
    st2, err2 = mcp.tool("status", timeout=60)
    n2 = len(st2.get("cases") or []) if isinstance(st2, dict) else 0
    check("survives-tool-error", pong.get("result") == {} and not err2 and n2 == len(names) and mcp.alive(),
          f"ping=ok status_cases={n2} server_alive={mcp.alive()}")


def main():
    t0 = time.monotonic()
    mcp = Mcp()
    try:
        run_checks(mcp)
    except Exception as e:  # noqa: BLE001 - any transport/shape failure fails the acceptance run, with the reason
        check("chain", False, f"{type(e).__name__}: {e}")
    finally:
        mcp.close()

    passed = sum(1 for _, ok in _results if ok)
    for name, ok in _results:
        if not ok:
            print(f"  failed: {name}")
    print(f"\n{passed}/{len(_results)} checks passed in {time.monotonic() - t0:.1f}s")
    return 0 if passed == len(_results) else 1


if __name__ == "__main__":
    sys.exit(main())
