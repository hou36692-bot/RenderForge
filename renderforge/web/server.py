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
  GET  /api/export/mesh?case=&mesh=&fmt=obj|glb        one mesh as a downloadable file
  GET  /api/export/texture?case=&tex=[&w=]              one texture (full size by default) as PNG
  GET  /api/export/case?case=&fmt=obj|glb&meshes=&textures=0   the whole case as a zip
  POST /api/case/<name>/export                         start a case zip as a background job
  GET  /file?path=<absolute>          a PNG/JPEG from inside the workspace (textures, screenshots)
  POST /api/case/<name>/run           start a step (or the whole graph) as a background job
  POST /api/case/<name>/metrics       recompute metrics (synchronous; ~1 s)
  GET  /                              the single page app
"""
import json
import mimetypes
import re
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from .. import caseinfo, jobs, pipeline
from . import casectl, exporters, locate

ROOT = Path(__file__).resolve().parent.parent.parent
WS = ROOT / "workspace"
STATIC = Path(__file__).resolve().parent / "static"

# Steps the UI may start. Anything not here is not exposed over HTTP: the tool surface stays as narrow as the
# MCP server's, and a browser tab should not be able to run arbitrary CLI commands.
RUNNABLE = {"survey", "export", "build", "build-model", "ingest-unity", "ingest-3dmigoto", "identify",
            "ue-import", "unity-import", "shot", "metrics", "shaders", "run"}


EXPORTS = ROOT / "workspace" / "_exports"


def _json(p, default=None):
    return caseinfo._json(p, default)


def _scene(name):
    """The case's Scene Package: (path, parsed) or (None, None). Cached parse, so this is ~free after the first hit."""
    p = WS / name / "build" / "scene.json"
    sc = _json(p)
    return (p.parent, sc) if sc else (None, None)


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
    # Thumbnails are served with a long max-age (scrolling must not re-request them). A rebuild replaces the mesh
    # files under the same mesh id, so the URL carries the scene's mtime: new build, new URL, no stale picture.
    try:
        ver = (WS / name / "build" / "scene.json").stat().st_mtime_ns
    except OSError:
        ver = 0
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

    # Screen geometry is optional: a case without a usable camera (a source package, a model-space 3DMigoto case)
    # gets area 0 and the UI hides the "in frame" controls instead of filtering everything away.
    try:
        loc = locate.index(name)
        area, camera_ok, camera_why = loc["mesh_area"], loc["camera_ok"], loc["camera_why"]
    except (KeyError, OSError, ValueError) as e:
        area, camera_ok, camera_why = {}, False, f"无法计算屏幕位置：{e}"
    facet = locate.facets(sc)
    # the material a mesh is mostly drawn with, and that material's base texture: the fallback key for unnamed meshes
    mat_votes = {}
    for i in instances:
        if i.get("material"):
            d = mat_votes.setdefault(i["mesh"], {})
            d[i["material"]] = d.get(i["material"], 0) + 1
    main_mat = {mid: max(d.items(), key=lambda kv: kv[1])[0] for mid, d in mat_votes.items()}
    base_of = {mid: (materials.get(mk) or {}).get("base") for mid, mk in main_mat.items()}

    rows = []
    groups = {}
    for mid, m in meshes.items():
        sn = source_name(m)
        f = facet[mid]
        a = area.get(mid, 0.0)
        rec = {
            "id": mid, "verts": m.get("verts"), "tris": m.get("tris"),
            "has_nrm": m.get("has_nrm"), "has_uv": m.get("has_uv"),
            "source": sn, "source_path": (m.get("source") or {}).get("object"),
            "lod": (m.get("source") or {}).get("lod"), "section": (m.get("source") or {}).get("section"),
            "instances": per_mesh.get(mid, {}).get("instances", 0),
            "kinds": sorted(per_mesh.get(mid, {}).get("kinds", [])),
            "thumb": f"/api/thumb/mesh?case={name}&mesh={mid}&v={ver}",
            "export": {f2: f"/api/export/mesh?case={name}&mesh={mid}&fmt={f2}" for f2 in ("obj", "glb")},
            # classification facets (naming + kinds), and how much of the reference frame it covers
            "named": f["named"], "code": f["code"], "code_label": f["code_label"], "prefix": f["prefix"],
            "level": f["level"], "kind": f["kind"],
            "material": main_mat.get(mid), "base_texture": base_of.get(mid),
            "screen": round(a, 5), "in_frame": a > 1e-4,
        }
        if not f["named"]:
            rec["unknown_group"] = locate.unknown_group(mid, m, f, area, base_of)[1]
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
                         "export": f"/api/export/texture?case={name}&tex={quote(tid, safe='')}" if f else None,
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
            "groups": sorted(groups.values(), key=lambda g: -g["instances"]),
            "camera": {"ok": camera_ok, "why": camera_why, "vp_match": sc.get("vp_match"),
                       "fov": (sc.get("camera") or {}).get("hfov_deg")},
            "facets": _facet_summary(rows, meshes)}


def _facet_summary(rows, meshes):
    """The counts behind the filter chips, computed once here so the page never has to guess.

    Codes are only listed when more than one mesh carries them; a code seen on a single mesh is still shown on that
    mesh's card, but a filter chip for it would be a one-item filter. The label is the code plus a translation only
    where the translation has evidence (see locate.CODE_LABEL)."""
    codes, levels, kinds = {}, {}, {}
    for r in rows:
        if r.get("code"):
            codes[r["code"]] = codes.get(r["code"], 0) + 1
        if r.get("level"):
            levels[r["level"]] = levels.get(r["level"], 0) + 1
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    return {
        "named": sum(1 for r in rows if r.get("named")),
        "unnamed": sum(1 for r in rows if not r.get("named")),
        "in_frame": sum(1 for r in rows if r.get("in_frame")),
        "codes": [{"code": c, "n": n, "label": locate.CODE_LABEL.get(c), "evidence": locate.CODE_EVIDENCE.get(c)}
                  for c, n in sorted(codes.items(), key=lambda kv: (-kv[1], kv[0])) if n > 1],
        "levels": [{"level": l, "n": n} for l, n in sorted(levels.items(), key=lambda kv: (-kv[1], kv[0]))],
        "kinds": [{"kind": k, "n": n} for k, n in sorted(kinds.items(), key=lambda kv: (-kv[1], kv[0]))],
    }


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
# A thumbnail is a deterministic function of (mesh files, size). It used to be cached in a dict only, so every server
# restart -- and every page load after one -- re-rendered every visible tile; the HTTP layer now keeps the PNG in
# workspace/_logs/webcache and answers a repeat request without touching numpy at all.
_THUMB_CACHE = {}
_THUMB_LOCK = threading.Lock()
_THUMB_KEEP = 4096          # a browser page shows ~40 tiles; a case has up to ~900, a few cases fit comfortably


def thumb_path(case, mesh, size=192, scene=None):
    """Disk cache path for a thumbnail, keyed on the mesh's own files so an export/build replaces it automatically."""
    sc_dir, sc = (_scene(case) if scene is None else (WS / case / "build", scene))
    info = ((sc or {}).get("meshes") or {}).get(mesh)
    if not info:
        raise KeyError(f"no mesh {mesh} in {case}")
    d = sc_dir / info["dir"]
    sig = []
    for name in ("pos.f32", "idx.u32"):
        st = (d / name).stat()
        sig.append(f"{st.st_size:x}{st.st_mtime_ns:x}")
    return WS / "_logs" / "webcache" / "thumbs" / f"{case}_{mesh}_{size}_{'_'.join(sig)}.png"


def mesh_thumbnail(case, mesh, size=192):
    """A PNG thumbnail: the mesh's own vertices projected orthographically and flat-shaded, z-sorted.

    No GPU and no 3D library: the scene package already has positions and indices as raw float32/uint32. Sampling is
    capped so a 40k-vertex mesh costs about what a 400-vertex one does.
    """
    key = (case, mesh, size)
    with _THUMB_LOCK:
        hit = _THUMB_CACHE.get(key)
    if hit is not None:
        return hit
    out_p = thumb_path(case, mesh, size)
    if out_p.is_file():
        out = out_p.read_bytes()
        with _THUMB_LOCK:
            _THUMB_CACHE[key] = out
        return out

    sc_dir, sc = _scene(case)
    if not sc:
        raise KeyError(f"no scene for {case}")
    info = (sc.get("meshes") or {}).get(mesh)
    if not info:
        raise KeyError(f"no mesh {mesh} in {case}")
    d = sc_dir / info["dir"]
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
    tz = P[:, :, depth].mean(1)
    order = np.argsort(tz)                             # far first, so near faces paint over them
    tri, shade = tri[order], shade[order]

    img = Image.new("RGB", (size, size), (32, 36, 43))
    dr = ImageDraw.Draw(img)
    pxy = np.stack([px, py], axis=1)
    for t, sh in zip(tri, shade):
        c = int(70 + 165 * sh)
        a, b, cc = pxy[t[0]], pxy[t[1]], pxy[t[2]]
        dr.polygon([(a[0], a[1]), (b[0], b[1]), (cc[0], cc[1])], fill=(c, int(c * 0.98), int(c * 0.92)))
    out = _png_bytes(img)
    try:
        out_p.parent.mkdir(parents=True, exist_ok=True)
        tmp = out_p.with_suffix(".tmp")
        tmp.write_bytes(out)
        tmp.replace(out_p)                 # atomic: two threads rendering the same tile cannot leave half a PNG
    except OSError:
        pass                                # a read-only workspace still serves from memory
    with _THUMB_LOCK:
        _THUMB_CACHE[key] = out
        if len(_THUMB_CACHE) > _THUMB_KEEP:
            for k in list(_THUMB_CACHE)[: _THUMB_KEEP // 4]:
                del _THUMB_CACHE[k]
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
    # The asset browser's per-mesh import is the existing importer with its own switch, not a new pipeline step: UE
    # builds one mesh and one material by default and hides the rest, so the job stays a plain `ue-import`.
    env = {}
    if opts.get("only_kind"):
        env["RF_ONLY_KIND"] = str(opts["only_kind"])
    if opts.get("only_mesh"):
        env["RF_ONLY_MESH"] = str(opts["only_mesh"])
    if opts.get("hide_mesh"):
        env["RF_HIDE_MESH"] = str(opts["hide_mesh"])
    if opts.get("emissive"):
        env["RF_EMISSIVE"] = "1"
    return jobs.start(argv, name=f"{name}:{step}", env=env or None)


# ---------------------------------------------------------------- export jobs (case zip)


def _export_state(jid):
    """An export job's record (started by the workbench, finished by export_job) for the UI's progress line.

    Export jobs live in workspace/_exports, not workspace/_jobs: they are a workbench action, not a pipeline step,
    and they carry their own manifest. A record that says "running" with no process alive is reported as failed."""
    if not re.fullmatch(r"[\w-]+", jid or ""):
        raise KeyError(f"bad export id: {jid}")
    rec = _json(EXPORTS / f"{jid}.json")
    if not rec:
        raise KeyError(f"no export {jid}")
    if rec.get("state") not in ("done", "failed"):
        # No pid probe: on Windows `os.kill(pid, 0)` is neither a reliable liveness test nor safe to call. A runner
        # that dies without writing a result is detected by the record simply not progressing.
        age = time.time() - (rec.get("started") or time.time())
        rec["state"] = "running" if age < 900 else "failed"
        if rec["state"] == "failed":
            rec.setdefault("error", "no result after 15 minutes; the export process probably exited without one")
    rec["seconds"] = round(time.time() - (rec.get("started") or time.time()), 1)
    return rec


def start_export(name, opts):
    """Build a case zip in a separate process, so a 500-mesh case does not block the workbench's HTTP threads.

    The child is a plain `python -m renderforge.web.export_job` reading the same workspace: same code path as the
    synchronous GET, just out of the request thread. Only whitelisted options are forwarded."""
    if not (WS / name).is_dir():
        raise KeyError(f"no such case: {name}")
    sc_dir, sc = _scene(name)
    if not sc:
        raise ValueError(f"{name} has no build/scene.json yet")
    fmt = opts.get("fmt") or "obj"
    if fmt not in ("obj", "glb"):
        raise ValueError("fmt must be obj or glb")
    meshes = opts.get("meshes") or ""
    jid = time.strftime("%Y%m%d-%H%M%S") + "-" + re.sub(r"\W", "", name)[-12:]
    EXPORTS.mkdir(parents=True, exist_ok=True)
    meta = {"id": jid, "case": name, "started": time.time(), "fmt": fmt, "meshes": meshes,
            "textures": opts.get("textures", True), "log": str(EXPORTS / f"{jid}.log"),
            "started_by": "web"}
    (EXPORTS / f"{jid}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    log = open(meta["log"], "w", encoding="utf-8")
    p = subprocess.Popen([sys.executable, "-m", "renderforge.web.export_job", jid, name], cwd=ROOT,
                         stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    meta["pid"] = p.pid
    (EXPORTS / f"{jid}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    meta["state"] = "running"
    return meta


def list_exports(limit=20):
    """Recent export jobs for this workspace, newest first (the download links stay reachable after a reload)."""
    if not EXPORTS.is_dir():
        return []
    out = []
    for p in sorted(EXPORTS.glob("*.json"), reverse=True)[:limit]:
        try:
            out.append(_export_state(p.stem))
        except (KeyError, OSError, ValueError):
            continue
    return out


def _export_file(jid):
    """The finished zip for an export job (404 until the runner has written it)."""
    if not re.fullmatch(r"[\w-]+", jid or ""):
        raise KeyError(f"bad export id: {jid}")
    p = EXPORTS / f"{jid}.zip"
    if not p.is_file():
        raise KeyError(f"export {jid} has no zip yet")
    return p


def recompute_metrics(name):
    return pipeline.step_metrics(name, shot=None)


def _disposition(filename):
    """attachment + an ASCII fallback and the UTF-8 name (captured source names are ASCII, but not by guarantee)."""
    ascii_name = re.sub(r'[^A-Za-z0-9._-]+', "_", filename) or "export.bin"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "RenderForgeWorkbench/0.1"
    # HTTP/1.0 closes the connection per request, which the browser then has to re-open for each of the ~75
    # thumbnails a page asks for; and writing headers and body as two segments with Nagle on adds a delayed-ACK
    # stall (~40 ms measured) to every small response. Both together were most of "the asset list feels slow".
    protocol_version = "HTTP/1.1"
    disable_nagle_algorithm = True

    def log_message(self, fmt, *args):   # quieter than the default: one line per request is enough
        if "/api/jobs/" in (self.path or ""):
            return
        super().log_message(fmt, *args)

    # -- helpers ----------------------------------------------------------
    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            # indent=1 is for reading with curl; over the wire it is 20% of a 350 KB asset list, so compact it
            sep = (",", ":") if self._compact else None
            body = json.dumps(body, ensure_ascii=False, indent=None if sep else 1, separators=sep).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        extra = dict(extra or {})
        # compress the text-ish payloads a browser parses (the asset list is ~350 KB, three.js is 2.1 MB)
        if (len(body) > 1400 and "gzip" in (self.headers.get("Accept-Encoding") or "")
                and (ctype.startswith(("application/json", "text/", "image/svg", "application/javascript")))):
            import gzip
            body = gzip.compress(body, 6)
            extra["Content-Encoding"] = "gzip"
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", extra.pop("Cache-Control", "no-store"))
        for k, v in extra.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    _compact = False          # set per request: API JSON is compact, human-facing error JSON keeps its indent

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
                self._compact = True
                return self._api_get(path, q)
            if path == "/file":
                return self._file(q)
            if path == "/img":
                return self._img(q)
            if path == "/export/download":
                return self._export_download(q)
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
            if path == "/api/cases":
                # create a task from a local path: the page cannot upload a multi-GB capture, but it can name one
                # that is already on this disk (where captures and dumps live). Seeding only; the first
                # survey/ingest is a normal job after. `rdc` stays accepted for the older single-source caller.
                source = body.get("source") or ("rdc" if body.get("rdc") else None)
                inp = body.get("path") or body.get("rdc") or ""
                return self._send(200, {"ok": True, **casectl.create(
                    (body.get("case") or "").strip(), inp, source=source,
                    title=body.get("title"), game=body.get("game"))})
            m = re.match(r"^/api/case/([^/]+)/(run|metrics|export|title)$", path)
            if not m:
                return self._err(404, f"no POST route {path}")
            name, action = m.group(1), m.group(2)
            if not (WS / name).is_dir():
                raise KeyError(f"no such case: {name}")
            if action == "run":
                return self._send(200, {"ok": True, "job": start_run(name, body.get("step") or "run", body)})
            if action == "export":
                return self._send(200, {"ok": True, "job": start_export(name, body)})
            if action == "title":
                # display name only: the directory id stays, so engine asset paths and recorded files stay valid
                return self._send(200, {"ok": True, **casectl.rename_title(name, body.get("title"))})
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
        if path == "/api/export/mesh":
            return self._export_mesh(q)
        if path == "/api/export/texture":
            return self._export_texture(q)
        if path == "/api/export/case":
            return self._export_case(q)
        if path == "/api/pick":
            return self._pick(q)
        if path == "/api/related":
            return self._related(q)
        if path == "/api/export/jobs":
            return self._send(200, {"jobs": list_exports(int(q.get("limit", ["20"])[0]))})
        m = re.match(r"^/api/export/([\w-]+)$", path)
        if m:
            return self._send(200, _export_state(m.group(1)))
        if path == "/api/capture":
            return self._send(200, casectl.look((q.get("path") or [""])[0],
                                                (q.get("source") or [""])[0] or None))
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
        try:
            png = thumb_path(case, mesh).read_bytes()
        except OSError:                     # not rendered yet (or the cache dir is unwritable): render and cache it
            png = mesh_thumbnail(case, mesh)
        # A thumbnail only changes when the mesh files do, so the browser may keep it without revalidating. That is
        # what makes scrolling the asset list cheap: an already-seen tile is never requested again.
        return self._send(200, png, "image/png",
                          extra={"Cache-Control": "public, max-age=86400", "X-RF-Cache": "thumb"})

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

    # -- locate on the reference frame ------------------------------------
    def _case_arg(self, q):
        case = (q.get("case") or [""])[0]
        if not case or not (WS / case).is_dir():
            raise KeyError(f"no such case: {case}")
        return case

    def _pick(self, q):
        """Ray-pick the reference frame at normalized (x, y). Returns ranked hits, not a single answer: the camera is
        solved approximately, and a click near a silhouette edge legitimately hits more than one surface."""
        case = self._case_arg(q)
        try:
            x, y = float((q.get("x") or [""])[0]), float((q.get("y") or [""])[0])
        except ValueError:
            raise ValueError("pick needs numeric x and y (normalized 0..1 on the reference frame)")
        return self._send(200, locate.pick(case, x, y))

    def _related(self, q):
        """Mesh -> material -> textures -> sibling meshes, for "what else belongs to this thing"."""
        case = self._case_arg(q)
        mesh = (q.get("mesh") or [""])[0]
        if not mesh:
            raise ValueError("related needs mesh")
        return self._send(200, locate.related(case, mesh))

    # -- export -----------------------------------------------------------
    def _case_scene(self, q):
        case = (q.get("case") or [""])[0]
        if not case or not (WS / case).is_dir():
            raise KeyError(f"no such case: {case}")
        sc_dir, sc = _scene(case)
        if not sc:
            raise ValueError(f"{case} has no build/scene.json yet")
        return case, sc_dir, sc

    def _export_mesh(self, q):
        """One mesh as OBJ (UVs V-flipped) or GLB (UVs unchanged: both are top-left origin)."""
        case, sc_dir, sc = self._case_scene(q)
        mesh = (q.get("mesh") or [""])[0]
        fmt = (q.get("fmt") or ["obj"])[0]
        if fmt not in ("obj", "glb"):
            raise ValueError("fmt must be obj or glb")
        name, data, ctype = exporters.single_mesh_export(sc_dir, sc, mesh, fmt)
        self._send(200, data, ctype, extra={"Content-Disposition": _disposition(name)})

    def _export_texture(self, q):
        """A texture exactly as the exporter would ship it: original PNG bytes, or a PNG re-encode when a variant is
        asked for (the captured files are already PNG; variant only affects the display copy under /img)."""
        case, sc_dir, sc = self._case_scene(q)
        tex = (q.get("tex") or [""])[0]
        t = (sc.get("textures") or {}).get(tex)
        if not t:
            raise KeyError(f"no texture {tex} in {case}")
        p = exporters._tex_path(sc_dir, sc, tex, WS)
        if not p:
            raise KeyError(f"texture {tex} has no readable file inside the workspace")
        self._send(200, p.read_bytes(), "image/png",
                   extra={"Content-Disposition": _disposition(f"{exporters._safe(p.stem)}.png")})

    def _export_case(self, q):
        """The whole case as a zip, synchronously. Large cases should use POST /api/case/<n>/export instead (the
        UI does), which runs this same builder in a separate process."""
        case, sc_dir, sc = self._case_scene(q)
        fmt = (q.get("fmt") or ["obj"])[0]
        if fmt not in ("obj", "glb"):
            raise ValueError("fmt must be obj or glb")
        mesh_ids = [m for m in (q.get("meshes") or [""])[0].split(",") if m] or None
        buf, man = exporters.case_zip(sc_dir, sc, case, mesh_ids=mesh_ids, fmt=fmt,
                                      include_textures=(q.get("textures") or ["1"])[0] not in ("0", "false"),
                                      workspace=WS)
        self._send(200, buf.getvalue(), "application/zip",
                   extra={"Content-Disposition": _disposition(f"{case}_{fmt}.zip"),
                          "X-RF-Export": json.dumps({k: man[k] for k in ("meshes", "textures", "bytes")})})

    def _export_download(self, q):
        p = _export_file((q.get("id") or [""])[0])
        self._send(200, p.read_bytes(), "application/zip",
                   extra={"Content-Disposition": _disposition(f"{p.stem}.zip")})

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
        """Static files with a real validator.

        The workbench used to answer every response with `no-store`, which meant three.js (2.1 MB over two modules)
        and this page's own JS were re-downloaded on every reload and every tab. They are versioned by their own
        mtime instead: a conditional request costs one 304, and a changed file gets a new ETag."""
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
        st = p.stat()
        etag = f'W/"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            return
        ctype = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                 ".js": "text/javascript; charset=utf-8", ".mjs": "text/javascript; charset=utf-8",
                 ".json": "application/json; charset=utf-8", ".svg": "image/svg+xml",
                 ".png": "image/png", ".ico": "image/x-icon"}.get(p.suffix, "application/octet-stream")
        self._send(200, p.read_bytes(), ctype, extra={"Cache-Control": "no-cache", "ETag": etag})


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
