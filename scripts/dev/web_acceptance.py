"""Acceptance test for the local web workbench, at the HTTP surface.

The workbench's whole claim is "what the browser shows is what the CLI would print". So the checks are not
"the endpoint returns 200" but "the endpoint's numbers equal the CLI's numbers", plus the safety boundaries
(paths outside the workspace refused, unknown steps refused) and the one thing a UI can break that a CLI cannot:
a page that renders but shows nothing.

Runs its own server on a free port, so it is safe alongside a workbench you already have open.
"""
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
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


def request(base, path, timeout=60, accept_encoding="gzip"):
    """(status, headers, body) with gzip decoded, so byte-level checks see the payload the page sees."""
    req = urllib.request.Request(base + path, headers={"Accept-Encoding": accept_encoding,
                                                       "User-Agent": "rf-acceptance"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read()
        if (r.headers.get("Content-Encoding") or "") == "gzip":
            import gzip
            body = gzip.decompress(body)
        return r.status, r.headers, body


def get(base, path, timeout=60):
    st, h, body = request(base, path, timeout=timeout)
    return st, (h.get("Content-Type") or ""), body


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

        # 7b. export: what comes back must be openable, not just "200 with bytes". GLB is checked structurally
        # (magic, chunk lengths, bufferViews inside the buffer, index width vs vertex count); OBJ by counting the
        # faces its own header promises; the texture by decoding it.
        # the largest mesh, not the first: it exercises the uint32 index path and the OBJ writer at real size
        mesh_rec = max(assets["meshes"], key=lambda m: m.get("verts") or 0)
        mid = mesh_rec["id"]
        st, ct, glb = get(base, f"/api/export/mesh?case={name}&mesh={mid}&fmt=glb")
        bad = []
        if ct != "model/gltf-binary" or glb[:4] != b"glTF":
            bad.append("not a glb")
        else:
            magic, ver, total = struct.unpack("<III", glb[:12])
            jlen, jtype = struct.unpack("<II", glb[12:20])
            doc = json.loads(glb[20:20 + jlen])
            blen, btype = struct.unpack("<II", glb[20 + jlen:28 + jlen])
            if total != len(glb) or 28 + jlen + blen != len(glb):
                bad.append(f"lengths {total}/{len(glb)} and {28 + jlen + blen}")
            if jtype != 0x4E4F534A or btype != 0x004E4942:
                bad.append("chunk types")
            acc, views = doc["accessors"], doc["bufferViews"]
            for v in views:
                if v["byteOffset"] + v["byteLength"] > doc["buffers"][0]["byteLength"]:
                    bad.append("bufferView outside buffer")
            prim = doc["meshes"][0]["primitives"][0]
            nv = acc[prim["attributes"]["POSITION"]]["count"]
            ni = acc[prim["indices"]]["count"]
            if nv != mesh_rec["verts"]:
                bad.append(f"glb verts {nv} vs scene {mesh_rec['verts']}")
            want_type = 5125 if nv > 65535 else 5123
            if acc[prim["indices"]]["componentType"] != want_type:
                bad.append(f"index type for {nv} verts")
            if ni != mesh_rec["tris"] * 3:
                bad.append(f"glb indices {ni} vs tris {mesh_rec['tris']}*3")
        check("export-glb-well-formed", not bad, "; ".join(bad) or f"{mesh_rec['verts']}v {len(glb) // 1024} KB")

        st, ct, obj = get(base, f"/api/export/mesh?case={name}&mesh={mid}&fmt=obj")
        text = obj.decode("utf-8", "replace")
        nv_obj = sum(1 for ln in text.splitlines() if ln.startswith("v "))
        nf_obj = sum(1 for ln in text.splitlines() if ln.startswith("f "))
        check("export-obj-faces", st == 200 and nv_obj == mesh_rec["verts"] and nf_obj == mesh_rec["tris"],
              f"{nv_obj} v, {nf_obj} f vs scene {mesh_rec['verts']}v {mesh_rec['tris']}f")
        # a browser only treats the click-to-download links as downloads when the response says so
        st, h, _ = request(base, f"/api/export/mesh?case={name}&mesh={mid}&fmt=obj")
        disp = h.get("Content-Disposition") or ""
        check("export-sends-content-disposition", st == 200 and "attachment" in disp and f"{mid}" in disp,
              disp[:80] or "header missing")

        tex_rec = next((t for t in assets["textures"] if t.get("export")), None)
        if tex_rec:
            st, ct, png = get(base, tex_rec["export"])
            check("export-texture-is-png", st == 200 and png[:8] == b"\x89PNG\r\n\x1a\n", f"{len(png) // 1024} KB")

        # the synchronous case zip: a real archive whose manifest counts match the scene
        st, ct, zbytes = get(base, f"/api/export/case?case={name}&fmt=obj&meshes={mid}&textures=0", timeout=300)
        import io
        import zipfile
        z = zipfile.ZipFile(io.BytesIO(zbytes))
        names = z.namelist()
        # the exporter names a mesh after its source asset (plus its id), which is the point of the export
        named = any(n.startswith("meshes/") and mid in n for n in names)
        check("export-case-zip", ct == "application/zip" and z.testzip() is None and named,
              f"{len(names)} files, {len(zbytes) // 1024} KB")
        check("export-case-zip-scene-json", json.loads(z.read("scene.json"))["meshes"].keys() == sc["meshes"].keys(),
              "scene.json is the package's own, verbatim")

        # the async job the buttons use: start, wait, then download the zip it wrote
        code, body = post(base, f"/api/case/{name}/export",
                          {"fmt": "glb", "meshes": mid, "textures": False})
        ejid = (body.get("job") or {}).get("id")
        check("export-job-starts", code == 200 and bool(ejid), f"export {ejid}")
        if ejid:
            for _ in range(180):
                est = get_json(base, f"/api/export/{ejid}")
                if est.get("state") in ("done", "failed"):
                    break
                time.sleep(0.5)
            check("export-job-finishes", est.get("state") == "done",
                  f"state={est.get('state')} {est.get('error') or ''} {est.get('seconds')}s")
            st, ct, z2 = get(base, f"/export/download?id={ejid}", timeout=300)
            check("export-job-downloads", st == 200 and z2[:2] == b"PK" and ct == "application/zip",
                  f"{len(z2) // 1024} KB")
            try:
                get(base, "/export/download?id=..%2F..%2Fpyproject")
                check("export-download-refuses-bad-id", False, "bad id served")
            except urllib.error.HTTPError as e:
                check("export-download-refuses-bad-id", e.code in (400, 404), f"HTTP {e.code}")

        # 7c. locating on the reference frame: the served geometry must project the way the page assumes, and the
        # facets the filters use must agree with scene.json (no invented categories, no invented coverage)
        cam = assets.get("camera") or {}
        facets = assets.get("facets") or {}
        check("assets-camera-reports-why", "ok" in cam and (cam["ok"] or cam.get("why")),
              f"ok={cam.get('ok')} vp_match={cam.get('vp_match')}")
        rows_by_id = {m["id"]: m for m in assets["meshes"]}
        check("assets-named-count", facets.get("named") == sum(1 for m in assets["meshes"] if m.get("named")),
              f"{facets.get('named')} named, {facets.get('unnamed')} unnamed")
        check("assets-in-frame-subset", 0 < facets.get("in_frame", 0) <= len(assets["meshes"]),
              f"{facets.get('in_frame')} of {len(assets['meshes'])} on screen")
        # every filter chip the page can show must map to rows that actually exist
        bad = [c["code"] for c in facets.get("codes", [])
               if not any(m.get("code") == c["code"] for m in assets["meshes"])]
        check("assets-codes-resolve", not bad, ", ".join(bad) or f"{len(facets.get('codes', []))} codes")
        kinds = {m["kind"] for m in assets["meshes"]}
        check("assets-kinds-known", kinds <= {"static", "translucent", "baked", "water"}, str(sorted(kinds)))

        if cam.get("ok"):
            # A pick must hit *somewhere* on a scene, but "the centre of the frame" is not guaranteed to be geometry
            # (it can be sky, or a spot the approximate camera puts between two objects). So probe a spread of points
            # and require that the service answers coherently on all of them and hits on at least a few.
            probes = [(0.2, 0.4), (0.5, 0.7), (0.7, 0.6), (0.3, 0.5), (0.8, 0.5), (0.5, 0.3)]
            got, misses = [], 0
            for px, py in probes:
                pk = get_json(base, f"/api/pick?case={name}&x={px}&y={py}")
                if pk.get("ok") and pk.get("hits"):
                    got.append(pk)
                elif pk.get("ok"):
                    misses += 1
            ok_shape = all(isinstance(pk.get("hits"), list) and all(
                {"mesh", "depth_m", "kind", "near_camera"} <= set(h0) for h0 in pk["hits"]) for pk in got)
            check("pick-probes", ok_shape and len(got) >= 3,
                  f"{len(got)} of {len(probes)} probes hit geometry, {misses} landed on nothing")
            pk = max(got, key=lambda p: len(p["hits"])) if got else {"hits": []}
            depths = [h0["depth_m"] for h0 in pk["hits"] if not h0["near_camera"]]
            check("pick-depths-monotone", depths == sorted(depths), f"{[round(d, 1) for d in depths[:4]]}")
            check("pick-meshes-exist", all(h0["mesh"] in rows_by_id for h0 in pk["hits"]), "all hits are scene meshes")
            # a pick that names a mesh must link it back to its material and textures -- that is the whole flow
            real = next((h0 for h0 in pk["hits"] if not h0["near_camera"]), None)
            if real:
                rel = get_json(base, f"/api/related?case={name}&mesh={real['mesh']}")
                check("related-has-links", rel.get("mesh") == real["mesh"] and bool(rel.get("materials")),
                      f"{len(rel.get('materials') or [])} materials, {len(rel.get('textures') or [])} textures")
            try:
                get(base, f"/api/pick?case={name}&x=abc&y=0.5")
                check("pick-rejects-bad-coords", False, "accepted a non-numeric x")
            except urllib.error.HTTPError as e:
                check("pick-rejects-bad-coords", e.code == 400, f"HTTP {e.code}")
        # a case whose camera cannot locate must say so instead of returning confident hits
        for c in cases:
            a2 = get_json(base, f"/api/case/{c['case']}/assets")
            if a2.get("ok") and (a2.get("camera") or {}).get("ok") is False:
                pk2 = get_json(base, f"/api/pick?case={c['case']}&x=0.5&y=0.5")
                check("pick-refuses-without-camera", pk2.get("ok") is False and bool(pk2.get("why")),
                      f"{c['case']}: {pk2.get('why')}")
                break

        # 7d. task management: a capture can be inspected and a case created from it, and a display name can be set.
        # Uses its own scratch case and removes it, so the real cases are untouched (this test must stay rerunnable).
        probe = "rf-acceptance-probe"
        real_rdc = None
        for c in cases:
            cj = ROOT / "workspace" / c["case"] / "case.json"
            try:
                rec = json.loads(cj.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            p = rec.get("rdc")
            if p and Path(p).is_file():
                real_rdc = p
                break
        if real_rdc:
            cap = get_json(base, f"/api/capture?path={urllib.parse.quote(real_rdc)}")
            check("capture-inspect", cap.get("ok") is True and cap.get("capture", {}).get("frame"),
                  f"frame {cap.get('capture', {}).get('frame')} size {cap.get('size')}")
            bad = get_json(base, f"/api/capture?path={urllib.parse.quote(str(ROOT / 'pyproject.toml'))}")
            check("capture-refuses-non-rdc", bad.get("ok") is False and "rdc" in (bad.get("why") or "").lower(),
                  bad.get("why") or "")
            try:
                code, body = post(base, "/api/cases", {"case": probe, "rdc": real_rdc, "title": "验收测试"})
                made = code == 200 and body.get("ok")
                check("case-create", made, f"{probe} -> {code}")
                if made:
                    lst = get_json(base, "/api/cases")["cases"]
                    row = next((c for c in lst if c["case"] == probe), None)
                    check("case-create-listed", bool(row) and row.get("title") == "验收测试",
                          f"title={row.get('title') if row else None}")
                    code, body = post(base, f"/api/case/{probe}/title", {"title": "改名后"})
                    lst = get_json(base, "/api/cases")["cases"]
                    row = next((c for c in lst if c["case"] == probe), None)
                    check("case-rename", code == 200 and row and row.get("title") == "改名后",
                          f"title={row.get('title') if row else None}")
                    # the directory id must not change: engine asset paths are built from it
                    check("case-rename-keeps-dir", (ROOT / "workspace" / probe).is_dir(),
                          "directory untouched")
                    code, body = post(base, "/api/case/probe-does-not-exist/title", {"title": "x"})
                    check("case-rename-unknown-404", code == 404, f"HTTP {code}")
            finally:
                import shutil
                shutil.rmtree(ROOT / "workspace" / probe, ignore_errors=True)
            check("case-create-duplicate-refused",
                  post(base, "/api/cases", {"case": name, "rdc": real_rdc})[0] == 400,
                  "existing case refused")
            check("case-create-bad-id-refused",
                  post(base, "/api/cases", {"case": "../evil", "rdc": real_rdc})[0] == 400, "path traversal refused")
        page = get(base, "/")[2].decode("utf-8", "replace")
        check("new-task-ui-present", 'id="btn-new-case"' in page and 'id="newcase"' in page and 'id="rename"' in page,
              "new task + rename markup")

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
