"""Build a UE-importable scene package from a RenderDoc export.

scene package layout (out_dir):
  scene.json                 meshes / materials / instances / camera / report
  meshes/<id>/{pos.f32,nrm.f32,uv.f32,idx.u32}   raw little-endian (pos/nrm: Nx3 f32, uv: Nx2 f32, idx: u32)
Textures are referenced by absolute path into the export directory (never copied or modified).
"""
import json
import shutil
from pathlib import Path

import numpy as np

from .solve import Extract, fit_local_to_clip, affine_score, proj_score, vp_candidates, regularize_planar, decompose


def vertex_normals(pos, idx):
    tri = idx.reshape(-1, 3)
    p0, p1, p2 = pos[tri[:, 0]], pos[tri[:, 1]], pos[tri[:, 2]]
    fn = np.cross(p1 - p0, p2 - p0)
    n = np.zeros_like(pos)
    for k in range(3):
        np.add.at(n, tri[:, k], fn)
    l = np.linalg.norm(n, axis=1, keepdims=True)
    return n / np.maximum(l, 1e-20)


def sanitize_mesh(pos, idx, uv, limit):
    """Drop triangles with non-finite or absurdly distant vertices (unprojection of near-plane points), compact."""
    ok = np.isfinite(pos).all(1) & (np.abs(pos).max(1) < limit)
    tri = idx.reshape(-1, 3)
    keep = ok[tri].all(1)
    tri = tri[keep]
    if len(tri) == 0:
        return None
    used = np.unique(tri)
    remap = np.full(len(pos), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return pos[used], remap[tri].reshape(-1).astype(np.uint32), (uv[used] if uv is not None else None)


def pick_uv_normal(ex, mesh_id, pos, idx):
    """Heuristically identify UV0 and normal among anonymous attributes (names are stripped in many games)."""
    attrs = ex.meshes[mesh_id]["attrs"]
    uv = None
    cands = []
    for i, a in enumerate(attrs):
        if a["perInstance"]:
            continue
        arr = None
        if a["comps"] == 2 and uv is None:
            arr = ex.mesh_attr(mesh_id, i)
            if np.isfinite(arr).all() and np.abs(arr).max() < 1e4:
                uv = arr
        elif a["comps"] == 4 and "SNORM" in a["format"]:
            cands.append(i)
        elif a["comps"] == 3 and a["format"].endswith("FLOAT") and i != ex.position_attr_index(mesh_id):
            cands.append(i)
    best, best_score = None, 0.3
    if cands and len(idx) >= 3:
        gn = vertex_normals(pos, idx)
        for i in cands:
            arr = ex.mesh_attr(mesh_id, i)[:, :3]
            ln = np.linalg.norm(arr, axis=1)
            if np.median(ln) < 0.8 or np.median(ln) > 1.2:
                continue
            score = float(np.mean(np.sum(arr / np.maximum(ln[:, None], 1e-9) * gn, axis=1)))
            if score > best_score:
                best, best_score = arr / np.maximum(ln[:, None], 1e-9), score
    return uv, best


def camera_from_vp(C, aspect):
    """Camera pose from a row-vector-convention view-projection matrix (translated-world space)."""
    f = C[:3, 3]
    k = np.linalg.norm(f)
    f = f / k
    up = C[:3, 1] - f * np.dot(C[:3, 1], f)
    up /= np.linalg.norm(up)
    right = np.cross(up, f)          # left-handed
    # camera position: left null vector of C (homogeneous)
    u, s, vt = np.linalg.svd(C.T)
    h = vt[-1]
    pos = h[:3] / h[3] if abs(h[3]) > 1e-12 else np.zeros(3)
    kx = np.linalg.norm(C[:3, 0]) / k
    ky = np.linalg.norm(C[:3, 1]) / k
    return {"position": pos.tolist(), "forward": f.tolist(), "up": up.tolist(), "right": right.tolist(),
            "hfov_deg": float(np.degrees(2 * np.arctan(1.0 / kx))), "vfov_deg": float(np.degrees(2 * np.arctan(1.0 / ky)))}


def choose_view_proj(ex, statics, sample=80):
    sub = statics[:: max(1, len(statics) // sample)]
    best = []
    for name, C in vp_candidates(ex, [s["draw"] for s in statics]):
        Ci = np.linalg.inv(C)
        ws = [s["M"] @ Ci for s in sub]
        good = [w for w in ws if affine_score(w) < 2e-2]
        if not good:
            continue
        tr = np.median([np.linalg.norm(w[3, :3] / w[3, 3]) for w in good])
        best.append((len(good) / len(sub), -tr, name, C))
    best.sort(key=lambda b: (-round(b[0], 2), -b[1]))
    return best[0] if best else None


def classify_textures(ex, draw):
    base = normal = None
    for t in draw["textures"]:
        info = ex.data["textures"].get(t["res"])
        if not info or not info["file"] or info["dynamic"]:
            continue
        f = t["format"]
        if base is None and "SRGB" in f and info["w"] >= 16:
            base = t["res"]
        elif normal is None and f.startswith("BC5") and info["w"] >= 16:
            normal = t["res"]
    return base, normal


def build(export_dir, out_dir, draw_filter=None, tol=3e-2, affine_tol=0.15, strict_tol=1e-5, sky_radius=60000.0):
    ex = Extract(export_dir)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "meshes").mkdir(parents=True)
    draws = [d for d in ex.draws if (draw_filter is None or draw_filter(d))]

    dmap = {d['eid']: d for d in draws}
    # 1. fit local->clip per draw instance (post-VS buffer holds all instances back to back)
    fitted = {}
    for d in draws:
        pi = ex.position_attr_index(d["mesh"])
        vs_all = ex.vsout(d)
        if pi is None or vs_all is None:
            continue
        pos = ex.mesh_attr(d["mesh"], pi)
        n = len(pos)
        if n == 0 or len(vs_all) % n:
            continue
        for k in range(len(vs_all) // n):
            vs = vs_all[k * n:(k + 1) * n]
            M, err = fit_local_to_clip(pos, vs)
            if M is not None:
                fitted[(d["eid"], k)] = (M, err, pos, vs)
    statics = [{"draw": d, "M": v[0]} for (eid, k), v in fitted.items() if v[1] < strict_tol and k == 0
               for d in [dmap[eid]] if d["numInstances"] <= 1]
    if len(statics) < 5:
        statics = [{"draw": d, "M": v[0]} for (eid, k), v in fitted.items() if v[1] < strict_tol
                   for d in [dmap[eid]]]
    if not statics:
        raise RuntimeError("no static meshes could be fitted; cannot determine view-projection")
    ch = choose_view_proj(ex, statics)
    if ch is None:
        raise RuntimeError("no view-projection candidate found")
    frac, _, vp_name, C = ch
    Ci = np.linalg.inv(C)

    scene = {"vp": vp_name, "vp_match": frac, "meshes": {}, "materials": {}, "instances": [], "textures": {}}
    report = {"draws": len(draws), "static": 0, "baked": 0, "skipped": 0}
    mesh_done = set()

    def write_mesh(mid, pos, nrm, uv, idx):
        d = out / "meshes" / mid
        d.mkdir(exist_ok=True)
        pos.astype("<f4").tofile(d / "pos.f32")
        idx.astype("<u4").tofile(d / "idx.u32")
        if nrm is not None:
            nrm.astype("<f4").tofile(d / "nrm.f32")
        if uv is not None:
            uv.astype("<f4").tofile(d / "uv.f32")
        scene["meshes"][mid] = {"dir": f"meshes/{mid}", "verts": int(len(pos)), "tris": int(len(idx) // 3),
                                "has_nrm": nrm is not None, "has_uv": uv is not None}

    def mat_key(base, normal):
        key = f"mat_{(base or 'none').split('::')[-1]}_{(normal or 'none').split('::')[-1]}"
        if key not in scene["materials"]:
            scene["materials"][key] = {"base": base, "normal": normal}
            for t in (base, normal):
                if t and t not in scene["textures"]:
                    scene["textures"][t] = {"file": str(ex.root / ex.data["textures"][t]["file"]),
                                            "w": ex.data["textures"][t]["w"], "h": ex.data["textures"][t]["h"],
                                            "format": ex.data["textures"][t]["format"]}
        return key

    units = [(dmap[eid], k, v) for (eid, k), v in fitted.items()]
    report["skipped"] = len(draws) - len({u[0]["eid"] for u in units})
    for d, k, (M, err, pos, vs) in units:
        mesh = ex.meshes[d["mesh"]]
        idx = np.fromfile(ex.root / "meshes" / d["mesh"] / "idx.u32", dtype="u4")
        if mesh["topology"].endswith("TriangleStrip") or len(idx) % 3:
            report["skipped"] += 1
            continue
        base, normal = classify_textures(ex, d)
        mk = mat_key(base, normal)
        W = M @ Ci
        if proj_score(W) < 1e-3:
            W, _ = regularize_planar(W, pos)
        if err < tol and proj_score(W) < 1e-3:
            W = W / W[3, 3]
            if d["mesh"] not in mesh_done:
                uv, nrm = pick_uv_normal(ex, d["mesh"], pos[:, :3], idx)
                if nrm is None:
                    nrm = vertex_normals(pos[:, :3].astype(np.float64), idx)
                write_mesh(d["mesh"], pos[:, :3], nrm, uv, idx)
                mesh_done.add(d["mesh"])
            loc, quat, scl = decompose(W)
            radius = float(np.abs(pos[:, :3]).max() * np.abs(scl).max())
            if radius > sky_radius:
                report["sky_skipped"] = report.get("sky_skipped", 0) + 1
                continue
            scene["instances"].append({"eid": d["eid"], "inst": k, "mesh": d["mesh"], "matrix": W.flatten().tolist(),
                                       "location": loc.tolist(), "quat": quat.tolist(), "scale": scl.tolist(),
                                       "shear": float(affine_score(W)), "material": mk, "kind": "static"})
            report["static"] += 1
        else:
            w = vs.astype(np.float64) @ Ci
            ok = np.abs(w[:, 3]) > 1e-9
            if ok.mean() < 0.99:
                report["skipped"] += 1
                continue
            world = (w[:, :3] / np.where(ok, w[:, 3], 1)[:, None])
            bid = f"b{d['eid']}_{k}"
            uv, _ = pick_uv_normal(ex, d["mesh"], pos[:, :3], idx)
            clean = sanitize_mesh(world, idx, uv, 5e5)
            if clean is None:
                report["skipped"] += 1
                continue
            world, idx_b, uv_b = clean
            write_mesh(bid, world, vertex_normals(world, idx_b), uv_b, idx_b)
            scene["instances"].append({"eid": d["eid"], "inst": k, "mesh": bid, "matrix": np.eye(4).flatten().tolist(),
                                       "location": [0.0, 0.0, 0.0], "quat": [0.0, 0.0, 0.0, 1.0], "scale": [1.0, 1.0, 1.0],
                                       "material": mk, "kind": "baked"})
            report["baked"] += 1

    scene["camera"] = camera_from_vp(C, 16 / 9)
    scene["report"] = report
    (out / "scene.json").write_text(json.dumps(scene, indent=1), encoding="utf-8")
    return scene
