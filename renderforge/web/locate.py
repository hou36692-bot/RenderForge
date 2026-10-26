"""Screen space over a Scene Package: what is under a pixel of the reference frame, and how much of the frame an
asset covers.

Everything here is derived from `build/scene.json` (camera pose + per-instance matrices + mesh geometry). Nothing is
re-solved and nothing is rendered, so the answers are exact for the geometry that is in the scene and honest about
what is not:

  * the camera is the one `build` solved. It is approximate by construction (a few percent of pose/fov), so a click
    returns a short ranked list of hits with their depths instead of pretending the first one is certain;
  * coverage is the *projected bounding box* of an instance clipped to the frame, not a rasterised silhouette. A
    long thin branch can cover a lot of box and few pixels; the filters are labelled 包围盒 accordingly;
  * a case whose camera was never solved (a source-package case) or is a stand-in orbit camera (`auto: true`, e.g.
    a model-space 3DMigoto case) reports `ok: false`, and the workbench hides the picker rather than pointing a
    confident-looking ray into a camera that has nothing to do with the reference frame.

Space: the package's own. Positions, matrices and the camera are all in the same space, so projection works the
same in `ue` (centimetres) and `unity` (metres); depths are converted to metres for display.
"""
import json
import re
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
WS = ROOT / "workspace"

_INDEX = OrderedDict()          # (case, scene mtime_ns) -> index
_LOCK = threading.Lock()
_KEEP = 4

# Abbreviations read off the asset names. Only ones with evidence are translated; the rest are shown as written,
# because guessing what `Sto` or `Sig` stands for would put a plausible-but-wrong word in front of the user.
#   Bri -> bridge: the picked arch at frame centre resolved to SM_Old_Bri_01AH and sits exactly on the bridge.
#   Tre -> tree:   picking a wisteria canopy resolves to SM_Old_Tre_01CL.
#   Wat -> water:  SM_Old_Wat_08AH is the documented water mesh (ROADMAP).
#   Veg -> vegetation, Gra -> grass: those meshes live under .../Foliage/ (also ROADMAP's vegetation pass).
CODE_LABEL = {"Bri": "桥", "Tre": "树", "Wat": "水", "Veg": "植被", "Gra": "草"}
CODE_EVIDENCE = {"Bri": "点选桥拱命中 SM_Old_Bri_01AH", "Tre": "点选树冠命中 SM_Old_Tre_01CL",
                 "Wat": "SM_Old_Wat_08AH 是已记录的水面网格", "Veg": "位于 .../Foliage/ 目录",
                 "Gra": "位于 .../Foliage/ 目录"}


def _scene(case):
    p = WS / case / "build" / "scene.json"
    try:
        st = p.stat()
    except OSError:
        return None, None, 0
    try:
        return p.parent, json.loads(p.read_text(encoding="utf-8")), st.st_mtime_ns
    except (OSError, ValueError):
        return None, None, 0


def camera_usable(scene):
    """Why the picker cannot be offered, or None when it can."""
    cam = (scene or {}).get("camera")
    if not cam:
        return "该案例没有解出相机（没有可用于投影的参考机位）"
    if cam.get("auto"):
        return "该案例的相机是自动环绕机位，不对应参考帧，无法在画面上定位"
    if not (scene.get("vp_match") or 0) > 0:
        return "该案例没有拟合出视投影矩阵（vp_match 为 0），无法在画面上定位"
    return None


# ---------------------------------------------------------------- naming facets

def facets(scene):
    """mesh id -> {code, prefix, level, kind, named, unknown_group}. Pure naming/kinds; no geometry."""
    meshes = scene.get("meshes") or {}
    kinds = {}
    for i in scene.get("instances") or []:
        kinds.setdefault(i.get("mesh"), set()).add(i.get("kind") or "static")
    out = {}
    for mid, m in meshes.items():
        src = m.get("source") or {}
        obj = src.get("object")
        named = bool(obj)
        code = prefix = level = None
        if named:
            base = obj.split(".")[-1].split("/")[-1]
            parts = base.split("_")
            if len(parts) >= 3 and parts[0] == "SM":
                prefix, code = parts[1], parts[2]
            elif len(parts) >= 2:
                prefix = parts[1]
            seg = obj.split("/")
            if "Levels" in seg:
                k = seg.index("Levels")
                level = seg[k + 1] if len(seg) > k + 1 else None
        ks = sorted(kinds.get(mid) or ["static"])
        kind = "water" if "water" in ks else ("translucent" if "translucent" in ks
                                              else ("baked" if "baked" in ks else "static"))
        out[mid] = {"named": named, "code": code, "prefix": prefix, "level": level, "kind": kind,
                    "code_label": CODE_LABEL.get(code)}
    return out


def unknown_group(mid, m, facet, screen_area, base_by_material):
    """A sub-group for an unidentified mesh: what other unidentified meshes share its main texture, else its size."""
    base = base_by_material.get(mid)
    if base:
        return ("texture", base)
    a = screen_area.get(mid) or 0.0
    if a > 0.05:
        return ("large", "大件（>5% 画面）")
    if a > 0.01:
        return ("mid", "中件（1–5% 画面）")
    return ("other", "其他")


# ---------------------------------------------------------------- geometry index

def _mesh_bounds(base_dir, scene):
    ids, lo, hi, tri = [], [], [], []
    for mid, m in (scene.get("meshes") or {}).items():
        p = Path(base_dir) / m["dir"] / "pos.f32"
        try:
            pos = np.fromfile(p, dtype="<f4").reshape(-1, 3)
        except (OSError, ValueError):
            continue
        if not len(pos):
            continue
        ids.append(mid)
        lo.append(pos.min(0))
        hi.append(pos.max(0))
        tri.append(m.get("tris") or 0)
    if not ids:
        return [], np.zeros((0, 3), np.float64), np.zeros((0, 3), np.float64), np.zeros(0, np.int64)
    return ids, np.array(lo, np.float64), np.array(hi, np.float64), np.array(tri, np.int64)


def _camera_rays(cam, aspect):
    """Unit ray for normalized image coordinates (0,0 top-left .. 1,1 bottom-right)."""
    kx = 1.0 / np.tan(np.radians(cam["hfov_deg"]) / 2.0)
    ky = 1.0 / np.tan(np.radians(cam["vfov_deg"]) / 2.0)

    def ray(x, y):
        d = (np.array(cam["forward"], np.float64)
             + ((x * 2 - 1) / kx) * np.array(cam["right"], np.float64)
             - ((y * 2 - 1) / ky) * np.array(cam["up"], np.float64))
        return d / np.linalg.norm(d)

    return ray


def _screen_boxes(base_dir, scene, ids, lo, hi, midx, cam):
    """Per-instance projected bbox (x0,y0,x1,y1), nearest depth, and clipped bbox area in [0,1]^2."""
    insts = scene.get("instances") or []
    if not insts or not ids:
        n = len(insts)
        return np.zeros((n, 5)), np.zeros(n)
    pos = np.array(cam["position"], np.float64)
    f = np.array(cam["forward"], np.float64)
    r = np.array(cam["right"], np.float64)
    u = np.array(cam["up"], np.float64)
    kx = 1.0 / np.tan(np.radians(cam["hfov_deg"]) / 2.0)
    ky = 1.0 / np.tan(np.radians(cam["vfov_deg"]) / 2.0)
    corners = np.array([[a, b, c] for a in (0, 1) for b in (0, 1) for c in (0, 1)], np.float64)
    keep = [k for k, i in enumerate(insts) if i.get("mesh") in midx]
    M = np.array([insts[k]["matrix"] for k in keep], np.float64).reshape(-1, 4, 4)
    mi = np.array([midx[insts[k]["mesh"]] for k in keep], np.int64)
    L, H = lo[mi][:, None, :], hi[mi][:, None, :]
    C = L + (H - L) * corners[None]                                    # (n, 8, 3) local
    W = np.concatenate([C, np.ones(C.shape[:2] + (1,))], 2) @ M        # row-vector convention
    d = W[:, :, :3] - pos
    z = d @ f
    x = d @ r
    y = d @ u
    ok = z > 1e-6
    safe = np.where(ok, z, 1.0)
    sx = np.where(ok, 0.5 + 0.5 * x * kx / safe, np.nan)
    sy = np.where(ok, 0.5 - 0.5 * y * ky / safe, np.nan)
    with np.errstate(invalid="ignore"), np.testing.suppress_warnings() as sup:
        sup.filter(RuntimeWarning)          # instances entirely behind the camera: all corners NaN by construction
        box = np.stack([np.nanmin(sx, 1), np.nanmin(sy, 1), np.nanmax(sx, 1), np.nanmax(sy, 1),
                        np.where(ok, z, np.inf).min(1)], 1)
    box[~np.isfinite(box).all(1)] = [0, 0, 0, 0, np.inf]
    w = np.clip(np.minimum(box[:, 2], 1) - np.maximum(box[:, 0], 0), 0, 1)
    h = np.clip(np.minimum(box[:, 3], 1) - np.maximum(box[:, 1], 0), 0, 1)
    area = np.where(np.isfinite(box[:, 4]), w * h, 0.0)
    out = np.zeros((len(insts), 5))
    out[np.array(keep, np.int64)] = box
    full = np.zeros(len(insts))
    full[np.array(keep, np.int64)] = area
    return out, full


def index(case):
    """Cached geometry/screen index for a case. Cheap after the first call (~0.25 s for 535 meshes).

    The cache is checked on a stat before the scene is parsed: an index hit must not pay the 0.15 s JSON parse that
    building it was supposed to save."""
    p = WS / case / "build" / "scene.json"
    try:
        mtime = p.stat().st_mtime_ns
    except OSError:
        raise KeyError(f"no build/scene.json for {case}")
    key = (case, mtime)
    with _LOCK:
        hit = _INDEX.get(key)
        if hit is not None:
            _INDEX.move_to_end(key)
            return hit
    base_dir, scene, mtime = _scene(case)
    if scene is None:
        raise KeyError(f"no build/scene.json for {case}")
    ids, lo, hi, tri = _mesh_bounds(base_dir, scene)
    midx = {m: k for k, m in enumerate(ids)}
    cam = scene.get("camera") or {}
    usable = camera_usable(scene) is None
    if usable:
        boxes, area = _screen_boxes(base_dir, scene, ids, lo, hi, midx, cam)
    else:
        boxes, area = np.zeros((len(scene.get("instances") or []), 5)), np.zeros(len(scene.get("instances") or []))
    per_mesh = {}
    for k, i in enumerate(scene.get("instances") or []):
        if k < len(area):
            per_mesh[i["mesh"]] = max(per_mesh.get(i["mesh"], 0.0), float(area[k]))
    entry = {"case": case, "scene": scene, "base": base_dir, "ids": ids, "idx": midx, "lo": lo, "hi": hi,
             "tris": tri, "boxes": boxes, "area": area, "mesh_area": per_mesh, "camera_ok": usable,
             "camera_why": camera_usable(scene), "aspect": 16 / 9}
    with _LOCK:
        _INDEX[key] = entry
        _INDEX.move_to_end(key)
        while len(_INDEX) > _KEEP:
            _INDEX.popitem(last=False)
    return entry


def mesh_screen(case):
    """mesh id -> largest clipped bbox area of any of its instances (0..1 of the frame)."""
    return dict(index(case)["mesh_area"])


# ---------------------------------------------------------------- pick

_TRI_CACHE = {}
_TRI_LOCK = threading.Lock()


def _mesh_tris(base_dir, scene, mid):
    with _TRI_LOCK:
        hit = _TRI_CACHE.get((str(base_dir), mid))
    if hit is not None:
        return hit
    d = Path(base_dir) / scene["meshes"][mid]["dir"]
    pos = np.fromfile(d / "pos.f32", dtype="<f4").reshape(-1, 3).astype(np.float64)
    idx = np.fromfile(d / "idx.u32", dtype="<u4").reshape(-1, 3)
    good = (idx < len(pos)).all(1)
    idx = idx[good]
    with _TRI_LOCK:
        _TRI_CACHE[(str(base_dir), mid)] = (pos, idx)
    return pos, idx


def pick(case, x, y, limit=8, max_candidates=400, near_m=1.0):
    """Ranked hits under a normalized image point.

    Returns {ok, why, x, y, hits: [{mesh, eid, inst, depth_m, kind, source, material, textures, screen_area,
    near_camera}]}. Instances whose projected bbox contains the point are the only candidates (the bbox is a
    superset of the mesh), then each is intersected triangle by triangle and ranked by distance from the camera.

    `near_camera` marks hits closer than `near_m`: a full-screen sheet at 10 cm is a camera effect (typically the
    reconstructed sky dome), not the thing the user pointed at, and the UI sorts it out of the way instead of
    hiding it -- the mesh really is there."""
    entry = index(case)
    scene = entry["scene"]
    if not entry["camera_ok"]:
        return {"ok": False, "why": entry["camera_why"], "hits": []}
    x, y = float(min(max(x, 0.0), 1.0)), float(min(max(y, 0.0), 1.0))
    cam = scene["camera"]
    pos = np.array(cam["position"], np.float64)
    ray = _camera_rays(cam, entry["aspect"])(x, y)
    boxes = entry["boxes"]
    if not len(boxes):
        return {"ok": False, "why": "该案例没有实例", "hits": []}
    # a tolerant box test: the camera pose is approximate, so a click on a silhouette edge may sit just outside
    m = 0.01
    cand = np.where((boxes[:, 0] <= x + m) & (boxes[:, 2] >= x - m)
                    & (boxes[:, 1] <= y + m) & (boxes[:, 3] >= y - m))[0]
    cand = cand[np.argsort(boxes[cand, 4])][:max_candidates]
    insts = scene["instances"]
    hits = []
    for k in cand:
        i = insts[k]
        if i["mesh"] not in entry["idx"]:
            continue
        try:
            P, I = _mesh_tris(entry["base"], scene, i["mesh"])
        except (OSError, KeyError, ValueError):
            continue
        if not len(I):
            continue
        M = np.array(i["matrix"], np.float64).reshape(4, 4)
        V = (np.concatenate([P, np.ones((len(P), 1))], 1) @ M)[:, :3]
        A, B, C = V[I[:, 0]], V[I[:, 1]], V[I[:, 2]]
        e1, e2 = B - A, C - A
        pv = np.cross(ray, e2)
        det = np.einsum("ij,ij->i", e1, pv)
        ok = np.abs(det) > 1e-9
        if not ok.any():
            continue
        inv = 1.0 / det[ok]
        tv = pos - A[ok]
        uu = np.einsum("ij,ij->i", tv, pv[ok]) * inv
        qv = np.cross(tv, e1[ok])
        vv = (qv @ ray) * inv
        t = np.einsum("ij,ij->i", e2[ok], qv) * inv
        hit = (uu >= 0) & (vv >= 0) & (uu + vv <= 1) & (t > 1e-4)
        if hit.any():
            hits.append((float(t[hit].min()), int(k)))
    hits.sort()
    scale = 0.01 if scene.get("space", "ue") == "ue" else 1.0     # cm -> m for ue
    out, seen = [], set()
    materials = scene.get("materials") or {}
    for dist, k in hits:
        i = insts[k]
        if i["mesh"] in seen:                     # one row per mesh: its other instances are the same asset
            continue
        seen.add(i["mesh"])
        mk = i.get("material")
        mat = materials.get(mk) or {}
        out.append({"mesh": i["mesh"], "eid": i.get("eid"), "inst": i.get("inst"),
                    "depth_m": round(dist * scale, 2), "kind": i.get("kind") or "static",
                    "near_camera": bool(dist * scale < near_m),
                    "source": ((scene["meshes"].get(i["mesh"]) or {}).get("source") or {}).get("object"),
                    "material": mk, "textures": [t for t in (mat.get("base"), mat.get("normal")) if t],
                    "screen_area": round(float(entry["mesh_area"].get(i["mesh"], 0.0)), 4)})
        if len(out) >= limit:
            break
    return {"ok": True, "why": None, "x": round(x, 4), "y": round(y, 4), "hits": out,
            "candidates": int(len(cand)), "camera": {"vp_match": scene.get("vp_match")}}


def related(case, mesh):
    """Everything reachable from one mesh: its instances, material(s), the textures those use, and the sibling
    meshes that share them. This is the "and what else belongs to it" question a locator has to answer."""
    entry = index(case)
    scene = entry["scene"]
    meshes = scene.get("meshes") or {}
    materials = scene.get("materials") or {}
    textures = scene.get("textures") or {}
    if mesh not in meshes:
        raise KeyError(f"no mesh {mesh}")
    insts = [i for i in (scene.get("instances") or []) if i.get("mesh") == mesh]
    mats = sorted({i.get("material") for i in insts if i.get("material")})
    tex = []
    for mk in mats:
        m = materials.get(mk) or {}
        for slot in ("base", "normal"):
            t = m.get(slot)
            if t and t not in tex:
                tex.append(t)
    sib_mat = {i["mesh"] for i in (scene.get("instances") or []) if i.get("material") in mats} - {mesh}
    sib_tex = set()
    for other, m in materials.items():
        if any(m.get(slot) in tex for slot in ("base", "normal")):
            sib_tex |= {i["mesh"] for i in (scene.get("instances") or []) if i.get("material") == other}
    sib_tex -= {mesh}
    return {"mesh": mesh, "instances": len(insts), "materials": mats, "textures": tex,
            "texture_info": {t: {"w": textures.get(t, {}).get("w"), "h": textures.get(t, {}).get("h"),
                                 "format": textures.get(t, {}).get("format")} for t in tex},
            "same_material": sorted(sib_mat), "same_texture": sorted(sib_tex - sib_mat),
            "screen_area": round(float(entry["mesh_area"].get(mesh, 0.0)), 4),
            "source": (meshes[mesh].get("source") or {}).get("object")}
