"""Local web workbench: a read-mostly HTTP surface over the same case state the CLI and MCP expose.

Why this exists: the toolbox is usable from a conversation, but "what is this case doing right now, and what did
it produce" is a visual question. The server adds no state of its own -- every response is derived from
`workspace/` (case.json, scene.json, metrics.json, shots) and from the job records, through the same functions the
CLI calls (`caseinfo.scan`, `pipeline.plan_steps`, `jobs.*`, `metrics.compare`). If the web UI and the CLI ever
disagree, the bug is here, not in the data.

Server: standard library only (`http.server.ThreadingHTTPServer`), one process, no build step, no bundler. Static
files are a handful of hand-written HTML/CSS/JS files served from `renderforge/web/static/`.

Endpoints (all JSON unless stated):
  GET  /api/cases                     every case, summary rows
  GET  /api/case/<name>               one case: stages, detail, notes, next, step plan
  GET  /api/case/<name>/steps         step graph with done/pending and the last recorded outcome
  GET  /api/case/<name>/assets        meshes/materials/textures/instances for the asset browser
  GET  /api/case/<name>/report        build report, import results, diagnostics
  GET  /api/case/<name>/metrics       metrics.json (or null)
  GET  /api/case/<name>/jobs          jobs whose argv mentions this case
  GET  /api/jobs                      recent jobs
  GET  /api/jobs/<id>                 one job's state
  GET  /api/jobs/<id>/log?tail=N      tail of a job's log
  GET  /api/thumb/mesh?case=&mesh=    PNG thumbnail rendered from the mesh's own vertices
  GET  /file?path=<absolute>          a PNG/JPEG from inside the workspace (textures, screenshots)
  POST /api/case/<name>/run           start a step (or the whole graph) as a background job
  POST /api/case/<name>/metrics       recompute metrics (synchronous; ~1 s)
  GET  /                              the single page app
"""
import json
import math
import mimetypes
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .. import caseinfo, jobs, pipeline

ROOT = Path(__file__).resolve().parent.parent.parent
WS = ROOT / "workspace"
STATIC = Path(__file__).resolve().parent / "static"

# Steps the UI may start. Anything not here is not exposed over HTTP: the tool surface stays as narrow as the
# MCP server's, and a browser tab should not be able to run arbitrary CLI commands.
RUNNABLE = {"survey", "export", "build", "build-model", "ingest-unity", "ingest-3dmigoto", "identify",
            "ue-import", "unity-import", "shot", "metrics", "shaders", "run"}


def _json(p, default=None):
    return caseinfo._json(p, default)


def _jobs_for_case(case):
    """Jobs are `python -m renderforge <argv...>`; a job belongs to a case when its argv names it."""
    out = []
    for j in jobs.list_jobs(80):
        argv = [str(a) for a in (j.get("argv") or [])]
        if case in argv:
            out.append(j)
    return out


def case_summary():
    return {"cases": caseinfo.summary(caseinfo.scan_all())}


def case_detail(name):
    d = WS / name
    if not d.is_dir():
        raise KeyError(f"no such case: {name}")
    info = caseinfo.scan(name)
    plan = pipeline.plan_steps(name)
    steps = []
    for s in plan:
        rec = None
        try:
            rec = (pipeline.load_case(name).get("steps") or {}).get(s["step"])
        except (OSError, ValueError):
            pass
        steps.append({**s, "record": rec})
    info["plan"] = steps
    info["jobs"] = _jobs_for_case(name)[:10]
    # full records, not the trimmed summary: a page that shows RMSE must not receive a 4-field projection of it
    info["metrics"] = _json(d / "metrics.json")
    info["shots"] = _shot_urls(name)
    info["reference"] = _file_url(d / "export" / "reference_backbuffer.png")
    return info


def _file_url(p):
    """Display URL for a workspace image (downscaled; see Handler._img)."""
    p = Path(p)
    return f"/img?path={p}" if p.is_file() else None


def _shot_urls(name):
    """Screenshots of a case as {file, url}; the browser never constructs filesystem paths."""
    d = WS / name / "shots"
    out = []
    if d.is_dir():
        for p in sorted(d.glob("*.png")):
            if p.name.startswith("compare"):
                continue
            out.append({"file": p.name, "url": _file_url(p)})
    if not out and (WS / name / "unity_shot.png").exists():   # zzz-era layout
        p = WS / name / "unity_shot.png"
        out.append({"file": p.name, "url": _file_url(p)})
    return out


def case_assets(name):
    """Meshes/materials/textures/instances for the asset browser, with the source-asset roll-up.

    Asset identity is the interesting index here: one captured mesh can be a section of a larger source object, and
    the same source object can back several captured meshes, so the browser groups by source name when there is one.
    """
    sc = _json(WS / name / "build" / "scene.json")
    if not sc:
        return {"ok": False, "error": "this case has no build/scene.json yet", "meshes": [], "textures": [],
                "materials": [], "groups": []}
    meshes = sc.get("meshes") or {}
    textures = sc.get("textures") or {}
    materials = sc.get("materials") or {}
    instances = sc.get("instances") or []

    per_mesh = {}
    for i in instances:
        per_mesh.setdefault(i["mesh"], {"instances": 0, "kinds": set()})
        per_mesh[i["mesh"]]["instances"] += 1
        per_mesh[i["mesh"]]["kinds"].add(i.get("kind") or "static")

    def source_name(m):
        src = m.get("source") or {}
        obj = src.get("object") or src.get("name")
        if not obj:
            return None
        return obj.split(".")[-1].split("/")[-1]

    rows = []
    groups = {}
    for mid, m in meshes.items():
        sn = source_name(m)
        rec = {
            "id": mid, "verts": m.get("verts"), "tris": m.get("tris"),
            "has_nrm": m.get("has_nrm"), "has_uv": m.get("has_uv"),
            "source": sn, "source_path": (m.get("source") or {}).get("object"),
            "lod": (m.get("source") or {}).get("lod"), "section": (m.get("source") or {}).get("section"),
            "instances": per_mesh.get(mid, {}).get("instances", 0),
            "kinds": sorted(per_mesh.get(mid, {}).get("kinds", [])),
            "thumb": f"/api/thumb/mesh?case={name}&mesh={mid}",
        }
        rows.append(rec)
        key = sn or "(unidentified)"
        groups.setdefault(key, {"name": key, "meshes": 0, "verts": 0, "tris": 0, "instances": 0})
        groups[key]["meshes"] += 1
        groups[key]["verts"] += m.get("verts") or 0
        groups[key]["tris"] += m.get("tris") or 0
        groups[key]["instances"] += rec["instances"]
    rows.sort(key=lambda r: (-(r["instances"] or 0), r["id"]))
    tex_rows = []
    for tid, t in textures.items():
        f = t.get("file")
        tex_rows.append({"id": tid, "w": t.get("w"), "h": t.get("h"), "format": t.get("format"),
                         "url": _file_url(f) if f else None,
                         "exists": bool(f) and Path(f).exists()})
    mat_rows = []
    for mk, m in materials.items():
        mat_rows.append({"id": mk, "base": m.get("base"), "normal": m.get("normal"),
                         "masked": bool(m.get("masked")), "water": bool(m.get("water")),
                         "translucent": bool(m.get("translucent")),
                         "base_url": (_file_url(textures[m["base"]]["file"])
                                      if m.get("base") in textures and textures[m["base"]].get("file") else None),
                         "normal_url": (_file_url(textures[m["normal"]]["file"])
                                        if m.get("normal") in textures and textures[m["normal"]].get("file") else None)})
    return {"ok": True, "meshes": rows, "textures": tex_rows, "materials": mat_rows,
            "groups": sorted(groups.values(), key=lambda g: -g["instances"])}


def case_report(name):
    """Everything a diagnostics page needs: what the build skipped, what the importers reported, and why."""
    d = WS / name
    sc = _json(d / "build" / "scene.json") or {}
    report = sc.get("report") or {}
    identity = sc.get("identity") or {}
    ue = _json(d / "build" / "ue_import_result.json") or _json(d / "_scene_ue_space" / "ue_import_result.json") or {}
    un = _json(d / "unity_pkg" / "unity_import_result.json") or {}
    metrics = _json(d / "metrics.json") or {}
    # metrics.json is the CLI's record; when it was never written, fall back to the tracked record in case.json
    if not metrics:
        metrics = ((caseinfo.scan(name).get("detail") or {}).get("metrics")) or {}
    diag = []
    skipped = (report.get("skipped") or 0) + (report.get("extra_skipped") or 0)
    if skipped:
        diag.append({"level": "info", "text": f"build 跳过 {skipped} 个 draw（billboard／无法拟合／无 post-VS 数据）"})
    if report.get("baked"):
        diag.append({"level": "info", "text": f"{report['baked']} 个 draw 由 clip 空间烘焙，几何冻结在捕获姿态"})
    if report.get("sky_skipped"):
        diag.append({"level": "info", "text": f"{report['sky_skipped']} 个超大网格被跳过（半径超过 sky_radius）"})
    if (sc.get("vp_match") or 0) < 0.8 and sc.get("vp"):
        diag.append({"level": "warn", "text": f"VP 拟合偏弱（{sc['vp_match']:.2f}）"})
    if identity:
        im, it = identity.get("instancesMatched"), identity.get("instances")
        if it:
            frac = (im or 0) / it
            diag.append({"level": "ok" if frac > 0.9 else "warn",
                         "text": f"源资产身份覆盖 {im}/{it} 实例（{frac:.1%}）"})
    if ue and not ue.get("ok"):
        diag.append({"level": "error", "text": f"UE 导入失败：{ue.get('error') or ue}"})
    if un and not un.get("ok"):
        diag.append({"level": "error", "text": f"Unity 导入失败：{un.get('error') or un}"})
    if metrics.get("coverage_error", 0) > 0.3:
        diag.append({"level": "warn", "text": f"覆盖率误差 {metrics['coverage_error']:.2f}：可能有整块几何缺失"})
    if not metrics:
        diag.append({"level": "info", "text": "还没有 metrics.json：对比结果尚未计算"})
    return {"report": report, "identity": identity, "vp": sc.get("vp"), "vp_match": sc.get("vp_match"),
            "ue": ue, "unity": un, "metrics": metrics, "camera": sc.get("camera"), "diagnostics": diag}


# ---------------------------------------------------------------- mesh thumbnails
_THUMB_CACHE = {}
_THUMB_LOCK = threading.Lock()


def mesh_thumbnail(case, mesh, size=192):
    """Render a wireframe-ish thumbnail by projecting the mesh's own vertices, z-sorted.

    No GPU and no 3D library: the scene package already has positions and indices as raw float32/uint32, so an
    orthographic projection of a random sample of triangles is enough to tell one asset from another in a list.
    Sampling is capped so a 40k-vertex mesh costs the same as a 400-vertex one.
    """
    key = (case, mesh, size)
    with _THUMB_LOCK:
        hit = _THUMB_CACHE.get(key)
    if hit is not None:
        return hit
    sc = _json(WS / case / "build" / "scene.json") or {}
    info = (sc.get("meshes") or {}).get(mesh)
    if not info:
        raise KeyError(f"no mesh {mesh} in {case}")
    d = WS / case / "build" / info["dir"]
    import numpy as np
    from PIL import Image, ImageDraw
    pos = np.fromfile(d / "pos.f32", dtype="<f4").reshape(-1, 3).astype(np.float64)
    idx = np.fromfile(d / "idx.u32", dtype="<u4").reshape(-1, 3)
    if len(pos) == 0 or len(idx) == 0:
        raise ValueError("empty mesh")

    # Orthographic: pick the two axes with the largest extent, and sort triangles back to front on the third.
    lo, hi = pos.min(0), pos.max(0)
    ext = hi - lo
    keep = np.argsort(ext)[::-1]
    a, b, depth = keep[0], keep[1], keep[2]
    span = max(ext[a], ext[b], 1e-6)
    cx, cy = (lo[a] + hi[a]) / 2, (lo[b] + hi[b]) / 2
    pad = size * 0.08
    # fit both axes: a flat panel is long and thin, and scaling on the longer axis alone would draw it as a line
    scale = (size - 2 * pad) / span
    px = (pos[:, a] - cx) * scale + size / 2
    py = size / 2 - (pos[:, b] - cy) * scale

    # A thumbnail only has to be recognisable: cap the triangle count so a 40k-vertex mesh costs the same as a
    # 400-vertex one. 1200 draws comfortably at 192 px.
    if len(idx) > 1200:
        sel = np.random.default_rng(0).choice(len(idx), 1200, replace=False)
        tri = idx[sel]
    else:
        tri = idx
    tz = pos[tri][:, :, depth].mean(1)
    tri = tri[np.argsort(-tz)]                     # far first, so near faces paint over them
    zs = pos[:, depth]
    zmin, zmax = float(zs.min()), float(zs.max())
    zr = max(zmax - zmin, 1e-6)

    img = Image.new("RGB", (size, size), (32, 36, 43))
    dr = ImageDraw.Draw(img)
    pxy = np.stack([px, py], axis=1)

    # Flat shading from the triangle's own normal: a wireframe is unreadable at 174 px, while a shaded solid is
    # how any asset list is normally read. The light is fixed in view space so the same asset always looks the same.
    P = pos[tri]                                       # (n, 3, 3)
    n = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
    ln = np.linalg.norm(n, axis=1)
    ln[ln < 1e-12] = 1.0
    n = n / ln[:, None]
    light = np.array([0.35, 0.55, 0.75])
    light = light / np.linalg.norm(light)
    lam = np.clip(np.abs(n @ light), 0.0, 1.0)         # abs: the capture has no consistent winding to trust
    shade = 0.30 + 0.70 * lam
    zs = pos[:, depth]
    zmin, zmax = float(zs.min()), float(zs.max())
    zr = max(zmax - zmin, 1e-6)
    tz = P[:, :, depth].mean(1)
    order = np.argsort(tz)                             # far first
    tri, shade = tri[order], shade[order]
    for t, sh in zip(tri, shade):
        c = int(70 + 165 * sh)
        a, b, cc = pxy[t[0]], pxy[t[1]], pxy[t[2]]
        dr.polygon([(a[0], a[1]), (b[0], b[1]), (cc[0], cc[1])], fill=(c, int(c * 0.98), int(c * 0.92)))
    out = _png_bytes(img)
    with _THUMB_LOCK:
        _THUMB_CACHE[key] = out
    return out


def _png_bytes(img):
    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ---------------------------------------------------------------- actions


def start_run(name, step, opts):
    """Start a step (or the whole graph) as a detached job -- the same CLI the conversation would run."""
    if step not in RUNNABLE:
        raise ValueError(f"step '{step}' is not runnable from the workbench; known: {', '.join(sorted(RUNNABLE))}")
    argv = ["run" if step == "run" else step, name]
    keymap = {"frm": "--from", "to": "--to", "index": "--index", "eids": "--eids", "range": "--range",
              "rt_count": "--rt-count", "shot": "--shot", "bundle": "--bundle", "frame_dir": "--frame-dir",
              "game": "--game", "rdc": "--rdc", "max_instances": "--max-instances"}
    for k, flag in keymap.items():
        v = opts.get(k)
        if v not in (None, "", False):
            argv += [flag, str(v)]
    for k in ("force", "emissive", "vs"):
        if opts.get(k):
            argv.append(f"--{k}")
    return jobs.start(argv, name=f"{name}:{step}")


def recompute_metrics(name):
    return pipeline.step_metrics(name, shot=None)


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "RenderForgeWorkbench/0.1"

    def log_message(self, fmt, *args):   # quieter than the default: one line per request is enough
        if "/api/jobs/" in (self.path or ""):
            return
        super().log_message(fmt, *args)

    # -- helpers ----------------------------------------------------------
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _err(self, code, msg):
        self._send(code, {"ok": False, "error": msg})

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            return {}

    # -- routing ----------------------------------------------------------
    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        path = u.path
        try:
            if path.startswith("/api/"):
                return self._api_get(path, q)
            if path == "/file":
                return self._file(q)
            if path == "/img":
                return self._img(q)
            if path == "/api/meshbin":
                return self._meshbin(q)
            return self._static(path)
        except KeyError as e:
            return self._err(404, str(e).strip("'"))
        except (ValueError, OSError) as e:
            return self._err(400, f"{type(e).__name__}: {e}")
        except Exception as e:  # noqa: BLE001 - a handler bug must not kill the server
            import traceback
            traceback.print_exc()
            return self._err(500, f"{type(e).__name__}: {e}")

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        path = u.path
        body = self._body()
        try:
            m = re.match(r"^/api/case/([^/]+)/(run|metrics)$", path)
            if not m:
                return self._err(404, f"no POST route {path}")
            name, action = m.group(1), m.group(2)
            if not (WS / name).is_dir():
                raise KeyError(f"no such case: {name}")
            if action == "run":
                return self._send(200, {"ok": True, "job": start_run(name, body.get("step") or "run", body)})
            return self._send(200, {"ok": True, "metrics": recompute_metrics(name)})
        except KeyError as e:
            return self._err(404, str(e).strip("'"))
        except ValueError as e:
            return self._err(400, str(e))
        except Exception as e:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            return self._err(500, f"{type(e).__name__}: {e}")

    def _api_get(self, path, q):
        if path == "/api/cases":
            return self._send(200, case_summary())
        if path == "/api/thumb/mesh":
            return self._thumb(q)
        if path == "/api/meshbin":
            return self._meshbin(q)
        if path == "/api/jobs":
            return self._send(200, {"jobs": jobs.list_jobs(int(q.get("limit", ["20"])[0]))})
        m = re.match(r"^/api/jobs/([^/]+)(/log)?$", path)
        if m:
            jid, want_log = m.group(1), bool(m.group(2))
            if want_log:
                return self._send(200, {"id": jid, "log": jobs.log(jid, tail=int(q.get("tail", ["120"])[0]))})
            return self._send(200, jobs.status(jid))
        m = re.match(r"^/api/case/([^/]+)(?:/(steps|assets|report|metrics|jobs))?$", path)
        if not m:
            return self._err(404, f"no route {path}")
        name, what = m.group(1), m.group(2)
        if not (WS / name).is_dir():
            raise KeyError(f"no such case: {name}")
        if what is None:
            return self._send(200, case_detail(name))
        if what == "steps":
            return self._send(200, {"case": name, "plan": case_detail(name)["plan"]})
        if what == "assets":
            return self._send(200, case_assets(name))
        if what == "report":
            return self._send(200, case_report(name))
        if what == "metrics":
            return self._send(200, {"metrics": _json(WS / name / "metrics.json")})
        if what == "jobs":
            return self._send(200, {"jobs": _jobs_for_case(name)})
        return self._err(404, f"no route {path}")

    def _thumb(self, q):
        case = (q.get("case") or [""])[0]
        mesh = (q.get("mesh") or [""])[0]
        if not case or not mesh:
            return self._err(400, "thumb needs case and mesh")
        png = mesh_thumbnail(case, mesh)
        return self._send(200, png, "image/png")

    def _meshbin(self, q):
        """Raw geometry for the in-browser 3D viewer: positions then indices, little-endian, no conversion.

        The scene package already stores exactly what a GPU wants (Nx3 float32 + uint32 triangles), so the viewer
        gets those bytes and nothing else. Serving them avoids a GLB/glTF conversion step that would only re-encode
        the same arrays, and keeps the browser's copy identical to what the UE/Unity emitters read.
        """
        case = (q.get("case") or [""])[0]
        mesh = (q.get("mesh") or [""])[0]
        if not case or not mesh:
            return self._err(400, "meshbin needs case and mesh")
        sc = _json(WS / case / "build" / "scene.json") or {}
        info = (sc.get("meshes") or {}).get(mesh)
        if not info:
            raise KeyError(f"no mesh {mesh} in {case}")
        d = WS / case / "build" / info["dir"]
        pos_p, idx_p = d / "pos.f32", d / "idx.u32"
        if not pos_p.is_file() or not idx_p.is_file():
            raise KeyError(f"mesh {mesh} has no geometry files")
        pos, idx = pos_p.read_bytes(), idx_p.read_bytes()
        nv, ni = len(pos) // 12, len(idx) // 4
        header = {"case": case, "mesh": mesh, "verts": nv, "indices": ni,
                  "has_uv": (d / "uv.f32").is_file(), "has_nrm": (d / "nrm.f32").is_file()}
        # [uint32 header length][JSON header][pos bytes][idx bytes]
        hb = json.dumps(header).encode("utf-8")
        pad = (-len(hb)) % 4                      # keep the float32/uint32 payloads 4-byte aligned
        hb += b" " * pad
        body = len(hb).to_bytes(4, "little") + hb + pos + idx
        self._send(200, body, "application/octet-stream")

    def _img(self, q):
        """A display-sized PNG (longest side <= `w`, default 1600) for a workspace image.

        Screenshots and reference frames are capture-resolution (up to 3840x2160). Sending those straight to an
        <img> makes the browser hold tens of MB per image and, in an off-screen window, often not paint them at
        all. The downscaled copy is cached on disk next to the source so repeat views cost nothing.
        """
        raw = (q.get("path") or [""])[0]
        if not raw:
            return self._err(400, "img needs ?path=")
        p = Path(raw).resolve()
        try:
            p.relative_to(WS.resolve())
        except ValueError:
            return self._err(403, "path is outside the workspace")
        if not p.is_file():
            raise KeyError(f"no such file: {p.name}")
        width = int((q.get("w") or ["1600"])[0])
        cache = WS / "_logs" / "webcache"
        cache.mkdir(parents=True, exist_ok=True)
        key = f"{p.stem}_{p.stat().st_mtime_ns}_{width}.png"
        out = cache / key
        if not out.exists():
            from PIL import Image
            with Image.open(p) as im:
                im = im.convert("RGB")
                if max(im.size) > width:
                    r = width / max(im.size)
                    im = im.resize((max(1, int(im.width * r)), max(1, int(im.height * r))), Image.LANCZOS)
                im.save(out, format="PNG", optimize=True)
        self._send(200, out.read_bytes(), "image/png")

    def _file(self, q):
        """Serve a PNG/JPEG by absolute path, restricted to the workspace.

        Textures and screenshots are recorded as absolute paths inside `workspace/`; anything outside it is refused
        rather than followed, so a malformed scene package cannot turn this into a general file reader.
        """
        raw = (q.get("path") or [""])[0]
        if not raw:
            return self._err(400, "file needs ?path=")
        p = Path(raw).resolve()
        try:
            p.relative_to(WS.resolve())
        except ValueError:
            return self._err(403, "path is outside the workspace")
        if not p.is_file():
            raise KeyError(f"no such file: {p.name}")
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        if ctype not in ("image/png", "image/jpeg", "image/webp"):
            return self._err(403, f"only images are served, not {ctype}")
        self._send(200, p.read_bytes(), ctype,
                   extra={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"})

    def _static(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = path.lstrip("/")
        if ".." in rel or rel.startswith("/"):
            return self._err(400, "bad path")
        p = STATIC / rel
        if not p.is_file():
            # Single-page app: a bare route (no file extension) falls back to index.html. A path that names a file
            # (.js/.css/.png...) and is missing must be a real 404: answering it with HTML is what made a missing
            # vendored module surface as a baffling "MIME type text/html" error in the browser instead of "not found".
            if Path(rel).suffix:
                return self._err(404, f"no such static file: {rel}")
            p = STATIC / "index.html"
            if not p.is_file():
                return self._err(404, "no static files installed")
        ctype = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
                 ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}.get(p.suffix, "application/octet-stream")
        self._send(200, p.read_bytes(), ctype,
                   extra={"Cache-Control": "no-store, must-revalidate", "Pragma": "no-cache"})


def serve(host="127.0.0.1", port=8770):
    httpd = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"RenderForge workbench: {url}  (workspace: {WS})")
    print("Ctrl-C to stop.")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    import sys
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8770
    sys.exit(serve(port=p))
