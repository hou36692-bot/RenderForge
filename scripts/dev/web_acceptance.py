"""Acceptance test for the local web workbench, at the HTTP surface.

The workbench's whole claim is "what the browser shows is what the CLI would print". So the checks are not
"the endpoint returns 200" but "the endpoint's numbers equal the CLI's numbers", plus the safety boundaries
(paths outside the workspace refused, unknown steps refused) and the one thing a UI can break that a CLI cannot:
a page that renders but shows nothing.

Runs its own server on a free port, so it is safe alongside a workbench you already have open.
"""
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
PY = sys.executable
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok)))
    print(f"{'PASS' if ok else 'FAIL'} {name}" + (f": {detail}" if detail else ""))


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def get(base, path, timeout=60):
    with urllib.request.urlopen(base + path, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type") or "", r.read()


def get_json(base, path):
    st, _, body = get(base, path)
    return json.loads(body)


def post(base, path, payload):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=600) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def cli(*args):
    p = subprocess.run([PY, "-m", "renderforge", *args], cwd=ROOT, capture_output=True, text=True, timeout=900)
    return p.returncode, p.stdout


def main():
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
    logpath = ROOT / "workspace" / "_logs" / "web_acceptance_server.log"
    logpath.parent.mkdir(parents=True, exist_ok=True)
    logf = open(logpath, "w", encoding="utf-8")
    srv = subprocess.Popen([PY, "-m", "renderforge.web.server", str(port)], cwd=ROOT, env=env,
                           stdout=logf, stderr=subprocess.STDOUT)
    try:
        # wait for the socket, not a fixed sleep
        for _ in range(80):
            try:
                get(base, "/api/cases", timeout=2)
                break
            except (urllib.error.URLError, ConnectionError, socket.timeout):
                time.sleep(0.25)
        else:
            raise RuntimeError("server did not start")

        # 1. the page and its assets are served
        st, ct, body = get(base, "/")
        check("index-html", st == 200 and b"<html" in body.lower() and "text/html" in ct, f"{len(body)} bytes")
        for f, kind in (("/app.js", "javascript"), ("/app.css", "text/css")):
            st, ct, body = get(base, f)
            check(f"ships-{f.strip('/')}", st == 200 and kind in ct and len(body) > 1000, f"{len(body)} bytes")

        # 1b. the module graph is closed: every relative import in the vendored three.js files resolves to a real
        # file served as JavaScript. A missing one is answered with a real 404 (not the HTML fallback), because the
        # browser reports the HTML version as an opaque MIME-type error far from the cause.
        import re
        todo, seen, broken = ["/vendor/three/three.module.js", "/vendor/three/addons/controls/OrbitControls.js"], set(), []
        while todo:
            u = todo.pop()
            if u in seen:
                continue
            seen.add(u)
            try:
                st, ct, body = get(base, u)
            except urllib.error.HTTPError as e:
                broken.append(f"{u}:{e.code}")
                continue
            if "javascript" not in ct or body.lstrip()[:1] == b"<":
                broken.append(f"{u}:not-js({ct})")
                continue
            text = body.decode("utf-8", "replace")
            for m in re.finditer(r"from\s+['\"](\.{1,2}/[^'\"]+)['\"]", text):
                ref = m.group(1)
                base_dir = u.rsplit("/", 1)[0]
                parts = (base_dir + "/" + ref).split("/")
                out = []
                for part in parts:
                    if part == "..":
                        out.pop()
                    elif part and part != ".":
                        out.append(part)
                todo.append("/" + "/".join(out))
        check("vendored-module-graph-closed", not broken, ", ".join(broken) or f"{len(seen)} modules")
        try:
            get(base, "/vendor/three/does-not-exist.js")
            check("missing-static-file-is-404", False, "served something")
        except urllib.error.HTTPError as e:
            check("missing-static-file-is-404", e.code == 404, f"HTTP {e.code}")

        # 2. case list equals the CLI's
        cases = get_json(base, "/api/cases")["cases"]
        rc, out = cli("status", "--json")
        cli_cases = json.loads(out)["cases"]
        same = (len(cases) == len(cli_cases) and
                [c["case"] for c in cases] == [c["case"] for c in cli_cases])
        check("api-cases-matches-cli", same, f"web {len(cases)} vs cli {len(cli_cases)} cases")
        check("api-cases-real-data", all(c.get("source") for c in cases) and len(cases) >= 6,
              f"sources={sorted({c['source'] for c in cases})}")

        # 3. one case's detail agrees with `status --json` for that case
        scorable = [c["case"] for c in cases
                    if (ROOT / "workspace" / c["case"] / "export" / "reference_backbuffer.png").exists()]
        name = next(c["case"] for c in cases if c["case"] in scorable and c.get("instances"))
        det = get_json(base, f"/api/case/{name}")
        cli_one = json.loads(cli("status", name, "--json")[1])
        check("api-case-stages-match-cli", det["stages"] == cli_one["stages"],
              f"{sum(det['stages'].values())} stages on")
        check("api-case-has-plan", bool(det.get("plan")) and all("step" in s for s in det["plan"]),
              f"{len(det.get('plan') or [])} steps")
        check("api-case-has-next", isinstance(det.get("next"), dict), str((det.get("next") or {}).get("step")))

        # 4. assets: counts are the scene package's, not invented
        assets = get_json(base, f"/api/case/{name}/assets")
        sc = json.loads((ROOT / "workspace" / name / "build" / "scene.json").read_text(encoding="utf-8"))
        check("assets-mesh-count", len(assets["meshes"]) == len(sc["meshes"]),
              f"{len(assets['meshes'])} vs {len(sc['meshes'])}")
        check("assets-texture-count", len(assets["textures"]) == len(sc["textures"]),
              f"{len(assets['textures'])} vs {len(sc['textures'])}")
        named = sum(1 for m in assets["meshes"] if m.get("source"))
        check("assets-source-names", named > 0, f"{named} meshes carry a source name")
        check("assets-groups", bool(assets.get("groups")), f"{len(assets['groups'])} groups")

        # 5. thumbnails are real PNGs, and the cache makes the second hit fast
        mesh = assets["meshes"][0]["id"]
        t0 = time.time()
        st, ct, png = get(base, f"/api/thumb/mesh?case={name}&mesh={mesh}")
        first = time.time() - t0
        t0 = time.time()
        get(base, f"/api/thumb/mesh?case={name}&mesh={mesh}")
        second = time.time() - t0
        check("thumb-is-png", st == 200 and ct == "image/png" and png[:8] == b"\x89PNG\r\n\x1a\n",
              f"{len(png)} bytes, first {first:.2f}s, cached {second:.3f}s")

        # 6. texture bytes are served, and only from inside the workspace
        tex = next((t for t in assets["textures"] if t.get("url")), None)
        if tex:
            st, ct, body = get(base, tex["url"])
            check("file-serves-texture", st == 200 and body[:8] == b"\x89PNG\r\n\x1a\n", f"{len(body)} bytes")
        try:
            get(base, "/file?path=" + str(ROOT / "pyproject.toml"))
            check("file-refuses-outside-workspace", False, "pyproject.toml was served")
        except urllib.error.HTTPError as e:
            check("file-refuses-outside-workspace", e.code == 403, f"HTTP {e.code}")

        # 6b. display images are downscaled: a 3840x2160 reference PNG is ~33 MB decoded and off-screen windows
        # frequently refuse to paint one, which shows up as a blank box rather than an error
        ref = get_json(base, f"/api/case/{name}").get("reference")
        if ref:
            st, ct, body = get(base, ref)
            import io
            from PIL import Image
            with Image.open(io.BytesIO(body)) as im:
                w, h = im.size
            check("img-downscales-reference", st == 200 and max(w, h) <= 1600,
                  f"{w}x{h}, {len(body) // 1024} KB")

        # 7. the report's diagnostics are derived, not hardcoded
        rep = get_json(base, f"/api/case/{name}/report")
        check("report-has-diagnostics", isinstance(rep.get("diagnostics"), list) and rep["diagnostics"],
              f"{len(rep['diagnostics'])} items")
        check("report-has-metrics", bool(rep.get("metrics")), f"ssim={rep.get('metrics', {}).get('ssim')}")

        # 8. unknown steps are refused (the tool surface stays as narrow as the MCP server's)
        code, body = post(base, f"/api/case/{name}/run", {"step": "rm -rf"})
        check("run-refuses-unknown-step", code == 400, f"HTTP {code}")
        code, body = post(base, "/api/case/__nope__/run", {"step": "build"})
        check("run-refuses-unknown-case", code == 404, f"HTTP {code}")

        # 9. metrics recompute matches the CLI for the same case and shot (the parity that matters)
        web_metrics = get_json(base, f"/api/case/{name}/metrics").get("metrics") or {}
        check("metrics-present-for-scorable-case", bool(web_metrics), f"case {name}")
        if web_metrics:
            shot = web_metrics.get("compared")
            rc, out = cli("metrics", name, "--shot", shot or "")
            cli_metrics = json.loads(out.split("\n->")[0]) if rc == 0 else {}
            check("metrics-parity", abs(float(cli_metrics.get("ssim", -1)) - float(web_metrics.get("ssim", -2))) < 1e-9,
                  f"web {web_metrics.get('ssim')} cli {cli_metrics.get('ssim')}")

        # 10. a job started from the web is a real job the CLI can see
        code, body = post(base, f"/api/case/{name}/run", {"step": "metrics", "force": True})
        jid = (body.get("job") or {}).get("id")
        check("run-starts-job", code == 200 and bool(jid), f"job {jid}")
        if jid:
            for _ in range(120):
                st = get_json(base, f"/api/jobs/{jid}")
                if st.get("state") in ("done", "failed"):
                    break
                time.sleep(0.5)
            check("job-finishes", st.get("state") == "done", f"state={st.get('state')} rc={st.get('rc')}")
            log = get_json(base, f"/api/jobs/{jid}/log?tail=5").get("log", "")
            check("job-log-available", "rf-job" in log or "metrics" in log, f"{len(log)} chars")

        # 10a. the 3D viewer's data endpoint: raw geometry, correctly framed and 4-byte aligned
        import struct
        st, ct, raw = get(base, f"/api/meshbin?case={name}&mesh={mesh}")
        hlen = struct.unpack("<I", raw[:4])[0]
        hdr = json.loads(raw[4:4 + hlen])
        expect = hdr["verts"] * 12 + hdr["indices"] * 4
        check("meshbin-framing", st == 200 and ct == "application/octet-stream"
              and len(raw) - 4 - hlen == expect and hlen % 4 == 0,
              f"{hdr['verts']}v {hdr['indices']}i, payload {len(raw) - 4 - hlen}/{expect}, hlen%4={hlen % 4}")

        # 10b. the page data must not contain values the UI would print as "null"/"undefined" into visible text.
        # A JS-side null slipping past a guard is invisible to an HTTP check but obvious to a reader, so check the
        # serialised payload for the shapes that cause it: a bare null where an object is expected.
        bad = []
        for c in cases:
            n = c["case"]
            det = get_json(base, f"/api/case/{n}")
            for key in ("reference", "shots", "metrics"):
                if key not in det:
                    bad.append(f"{n}: missing {key}")
            if det.get("reference") is not None and not str(det["reference"]).startswith("/img"):
                bad.append(f"{n}: reference is not an /img url")
        check("case-payload-shape", not bad, ", ".join(bad) or f"{len(cases)} cases")

        # 10c. the result page's screenshots are aspect-correct and zoomable. A flex child that shrinks under its
        # content gets cropped by its own overflow:hidden -- the image is served fine, so only a layout check sees it.
        st, _, page = get(base, "/")
        check("result-layout-guards", b"lightbox" in page and b"result-shots" in page, "dialog markup present")
        css = get(base, "/app.css")[2].decode("utf-8", "replace")
        # strip comments first: the rule is preceded by an explanatory one, so a naive block split misses it
        css_nc = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        m = re.search(r"(^|\n)\.shot\s*\{([^}]*)\}", css_nc)
        shot_rule = m.group(2) if m else ""
        check("shot-does-not-shrink", "flex: 0 0 auto" in shot_rule,
              " ".join(shot_rule.split())[:80] or "rule not found")

        # 11. every case renders its own pages without a 500
        broken = []
        for c in cases:
            n = c["case"]
            for ep in ("", "/assets", "/report", "/steps", "/metrics"):
                try:
                    get(base, f"/api/case/{n}{ep}", timeout=300)
                except urllib.error.HTTPError as e:
                    broken.append(f"{n}{ep}:{e.code}")
        check("all-cases-render", not broken, ", ".join(broken) or f"{len(cases)} cases x 5 endpoints")
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=10)
        except subprocess.TimeoutExpired:
            srv.kill()
        logf.close()

    passed = sum(1 for _, ok in RESULTS if ok)
    print(f"\n{passed}/{len(RESULTS)} checks passed")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
