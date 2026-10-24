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


def sanitize_mesh(pos, idx, uv, limit, valid=None):
    """Drop triangles with non-finite or absurdly distant vertices (unprojection of near-plane points), compact."""
    ok = np.isfinite(pos).all(1) & (np.abs(pos).max(1) < limit)
    if valid is not None:
        ok &= valid
    tri = idx.reshape(-1, 3)
    keep = ok[tri].all(1)
    tri = tri[keep]
    if len(tri) == 0:
        return None
    used = np.unique(tri)
    remap = np.full(len(pos), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return pos[used], remap[tri].reshape(-1).astype(np.uint32), (uv[used] if uv is not None else None)


def _srv_arrays(ex, draw, nverts, vmin):
    """Decode vertex-shader buffer SRVs (manual vertex fetch) into per-vertex channel arrays for this mesh.

    The buffers are usually pools shared by many meshes: element index = absolute vertex id (vmin + local index).
    The tangent buffer has exactly 2 elements per vertex (TangentX, TangentZ), which tells us the pool size, and
    the UV buffer then has an integer number of interleaved UV channels per vertex."""
    raws = []
    for sv in draw.get("vsSrvs", []):
        if not sv["file"]:
            continue
        fmt = sv["format"]
        raw = (ex.root_of(draw) / sv["file"]).read_bytes()
        if fmt == "R16G16_FLOAT":
            arr, comps = np.frombuffer(raw, dtype="<f2").astype(np.float32), 2
        elif fmt == "R32G32_FLOAT":
            arr, comps = np.frombuffer(raw, dtype="<f4"), 2
        elif fmt == "R8G8B8A8_SNORM":
            arr, comps = np.maximum(np.frombuffer(raw, dtype="i1").astype(np.float32) / 127.0, -1.0), 4
        elif fmt == "R16G16B16A16_SNORM":
            arr, comps = np.maximum(np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32767.0, -1.0), 4
        else:
            continue
        n_el = len(arr) // comps
        raws.append({"comps": comps, "fmt": fmt, "n_el": n_el, "data": arr[: n_el * comps].reshape(n_el, comps)})
    pool = None
    for r in raws:
        if r["comps"] == 4 and "SNORM" in r["fmt"] and r["n_el"] % 2 == 0:
            pool = r["n_el"] // 2
            break
    out = []
    local = np.arange(nverts)
    for r in raws:
        if pool and r["n_el"] % pool == 0:
            per = r["n_el"] // pool
        elif nverts and r["n_el"] % nverts == 0 and vmin == 0:
            per = r["n_el"] // nverts
        else:
            continue
        if (vmin + nverts) * per > r["n_el"]:
            continue
        for k in range(per):
            out.append({"comps": r["comps"], "fmt": r["fmt"], "chan": k, "per": per,
                        "data": r["data"][(vmin + local) * per + k]})
    return out


def pick_uv_normal(ex, mesh_id, pos, idx, draw=None):
    """Heuristically identify UV0 and normal among anonymous attributes / VS buffer SRVs (semantic names are stripped)."""
    attrs = ex.meshes[mesh_id]["attrs"]
    n = len(pos)
    uv = None
    cands = []
    for i, a in enumerate(attrs):
        if a["perInstance"]:
            continue
        if a["comps"] == 2 and uv is None:
            arr = ex.mesh_attr(mesh_id, i, draw)
            if np.isfinite(arr).all() and np.abs(arr).max() < 1e4:
                uv = arr
        elif a["comps"] == 4 and "SNORM" in a["format"]:
            cands.append(ex.mesh_attr(mesh_id, i, draw)[:, :3])
        elif a["comps"] == 3 and a["format"].endswith("FLOAT") and i != ex.position_attr_index(mesh_id):
            cands.append(ex.mesh_attr(mesh_id, i, draw)[:, :3])
    if draw is not None:
        vmin = ex.meshes[mesh_id].get("vmin", 0)
        for sa in _srv_arrays(ex, draw, n, vmin):
            if sa["comps"] == 2:
                if uv is None and sa["chan"] == 0 and np.isfinite(sa["data"]).all() and np.abs(sa["data"]).max() < 1e4:
                    uv = sa["data"].astype(np.float32)
            elif sa["comps"] == 4 and "SNORM" in sa["fmt"]:
                cands.append(sa["data"][:, :3])
    best, best_score = None, 0.3
    if cands and len(idx) >= 3:
        gn = vertex_normals(pos, idx)
        for arr in cands:
            ln = np.linalg.norm(arr, axis=1)
            if np.median(ln) < 0.8 or np.median(ln) > 1.2:
                continue
            nn = arr / np.maximum(ln[:, None], 1e-9)
            score = abs(float(np.mean(np.sum(nn * gn, axis=1))))
            if score > best_score:
                best, best_score = nn, score
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
    """Base colour + normal map from the pixel shader's texture bindings.

    The shader-view format is used when the export recorded it (an SRGB view of a plain UNORM texture is the
    game saying "this is colour"), and a tangent-space normal map is taken from the conventional compressed
    formats including the BC7 workaround WuWa uses instead of BC5.
    """
    def view_fmt(t):
        f = t.get("view") or t["format"]
        return t["format"] if (not isinstance(f, str) or "Swig" in f) else f
    base = normal = None
    for t in draw["textures"]:
        info = ex.data["textures"].get(t["res"])
        if not info or not info["file"] or info["dynamic"]:
            continue
        if base is None and "SRGB" in view_fmt(t) and info["w"] >= 16:
            base = t["res"]
    for t in draw["textures"]:
        info = ex.data["textures"].get(t["res"])
        if not info or not info["file"] or info["dynamic"] or t["res"] == base:
            continue
        f = view_fmt(t)
        if normal is None and (f.startswith("BC5") or info["format"] in ("BC5_TYPELESS", "BC7_TYPELESS")) and info["w"] >= 16:
            normal = t["res"]
    if base is None:
        # translucent / unlit sheets are often bound through a plain UNORM view; take the first colour-sized map
        for t in draw["textures"]:
            info = ex.data["textures"].get(t["res"])
            if not info or not info["file"] or info["dynamic"] or info["w"] < 16:
                continue
            f = view_fmt(t)
            if f.startswith(("BC1", "BC3", "BC7", "B8G8R8A8", "R8G8B8A8")) and info["format"] not in ("D24S8_TYPELESS",):
                base = t["res"]
                break
    return base, normal


def pass_kind(draw):
    """Coarse pass class: a draw that writes the scene colour plus one extra 8-bit buffer in this pass set is the
    translucent (particle / glass / water-line) batch, as opposed to the opaque G-buffer or a colour composite."""
    rt = draw.get("rtFormats") or []
    extra = draw.get("writes_extra") or []
    if rt and rt[0] == "R16G16B16A16_FLOAT" and len(extra) == 1 and extra[0].startswith("R8G8B8A8"):
        return "translucent"
    return "static"


def merge_texture_table(ex, other):
    """Make another extract's texture records resolvable through `ex` (same ids; file paths stay absolute)."""
    for k, v in other.data.get("textures", {}).items():
        ex.data["textures"].setdefault(k, v)


def build(export_dir, out_dir, draw_filter=None, tol=3e-2, affine_tol=0.15, strict_tol=1e-5, sky_radius=60000.0,
          extra_export=None, extra_pass="extra"):
    ex = Extract(export_dir)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "meshes").mkdir(parents=True)
    draws = [d for d in ex.draws if (draw_filter is None or draw_filter(d))]
    extra_draws = None
    if extra_export:
        xex = Extract(extra_export)
        for d in xex.draws:
            d["pass"] = extra_pass
        extra_draws = xex.draws
        have = {d["eid"] for d in draws}
        extra_draws = [d for d in extra_draws if d["eid"] not in have]
        ex.merge_draws(xex, kind=extra_pass)
        merge_texture_table(ex, xex)

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

    def mat_key(base, normal, masked=False, root=None):
        key = f"mat_{(base or 'none').split('::')[-1]}_{(normal or 'none').split('::')[-1]}" + ("_m" if masked and base else "")
        if key not in scene["materials"]:
            scene["materials"][key] = {"base": base, "normal": normal, "masked": bool(masked and base)}
            root = root or ex.root
            for t in (base, normal):
                if t and t not in scene["textures"]:
                    scene["textures"][t] = {"file": str(root / ex.data["textures"][t]["file"]),
                                            "w": ex.data["textures"][t]["w"], "h": ex.data["textures"][t]["h"],
                                            "format": ex.data["textures"][t]["format"]}
        return key

    # extra passes (depth-only, transparent): same camera, no influence on VP selection, never rebaked
    extra_ok = 0
    for xi, xd in enumerate(extra_draws or []):
        pi = ex.position_attr_index(xd["mesh"])
        vs_all = ex.vsout(xd)
        if pi is None or vs_all is None:
            report["extra_skipped"] = report.get("extra_skipped", 0) + 1
            continue
        pos = ex.mesh_attr(xd["mesh"], pi, xd)
        n = len(pos)
        if n == 0 or len(vs_all) % n:
            report["extra_skipped"] = report.get("extra_skipped", 0) + 1
            continue
        for k in range(len(vs_all) // n):
            vs = vs_all[k * n:(k + 1) * n]
            M, err = fit_local_to_clip(pos, vs)
            if M is None:
                continue
            W = M @ Ci
            if proj_score(W) < 1e-3:
                W, _ = regularize_planar(W, pos)
            # a fitted "world" matrix with an absurd scale means the fit absorbed clip-space w (near-plane or
            # billboard vertices); treat it like a failed fit instead of placing a 256x quad at the origin
            if W[3, 3] != 0 and np.abs(W[:3, :3] / W[3, 3]).max() > 50:
                W = None
            if W is None or err >= tol or proj_score(W) >= 1e-3:
                # Billboards / camera-facing quads: local->clip is not a rigid fit and the instance transform is not
                # recoverable this way. Baking the clip-space quad was tried and collapsed into duplicate cards at
                # the camera; an unshaded placeholder is worse than an honest gap.
                report["extra_skipped"] = report.get("extra_skipped", 0) + 1
                continue
            base, normal = classify_textures(ex, xd)
            mk = mat_key(base, normal, xd.get("psDiscard", False), root=ex.root_of(xd))
            W = W / W[3, 3]
            mid = xd["mesh"]
            if mid not in mesh_done:
                idx = np.fromfile(ex.root_of(xd) / xd.get("mesh_dir", "meshes/" + xd["mesh"]) / "idx.u32", dtype="u4")
                if len(idx) % 3:
                    report["extra_skipped"] = report.get("extra_skipped", 0) + 1
                    continue
                uv, nrm = pick_uv_normal(ex, xd["mesh"], pos[:, :3], idx, xd)
                if nrm is None:
                    nrm = vertex_normals(pos[:, :3].astype(np.float64), idx)
                write_mesh(mid, pos[:, :3], nrm, uv, idx)
                mesh_done.add(mid)
            loc, quat, scl = decompose(W)
            scene["instances"].append({"eid": xd["eid"], "inst": k, "mesh": mid, "matrix": W.flatten().tolist(),
                                       "location": loc.tolist(), "quat": quat.tolist(), "scale": scl.tolist(),
                                       "shear": float(affine_score(W)), "material": mk,
                                       "kind": xd.get("kind") or "extra",
                                       "pass": xd.get("pass")})
            extra_ok += 1
    report["extra"] = extra_ok

    units = [(dmap[eid], k, v) for (eid, k), v in fitted.items()]
    report["skipped"] = len(draws) - len({u[0]["eid"] for u in units})
    for d, k, (M, err, pos, vs) in units:
        mesh = ex.meshes[d["mesh"]]
        idx = np.fromfile(ex.root / "meshes" / d["mesh"] / "idx.u32", dtype="u4")
        if mesh["topology"].endswith("TriangleStrip") or len(idx) % 3:
            report["skipped"] += 1
            continue
        base, normal = classify_textures(ex, d)
        mk = mat_key(base, normal, d.get("psDiscard", False))
        W = M @ Ci
        if proj_score(W) < 1e-3:
            W, _ = regularize_planar(W, pos)
        ident = {"ident": d.get("ident")} if d.get("ident") else {}
        if err < tol and proj_score(W) < 1e-3:
            W = W / W[3, 3]
            if d["mesh"] not in mesh_done:
                uv, nrm = pick_uv_normal(ex, d["mesh"], pos[:, :3], idx, d)
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
                                       "shear": float(affine_score(W)), "material": mk, "kind": "static", **ident})
            report["static"] += 1
        else:
            w = vs.astype(np.float64) @ Ci
            ok = np.abs(w[:, 3]) > 1e-9
            if ok.mean() < 0.99:
                report["skipped"] += 1
                continue
            world = (w[:, :3] / np.where(ok, w[:, 3], 1)[:, None])
            bid = f"b{d['eid']}_{k}"
            uv, _ = pick_uv_normal(ex, d["mesh"], pos[:, :3], idx, d)
            clean = sanitize_mesh(world, idx, uv, 1.5e5, valid=vs[:, 3] > 1e-3)
            if clean is None:
                report["skipped"] += 1
                continue
            world, idx_b, uv_b = clean
            write_mesh(bid, world, vertex_normals(world, idx_b), uv_b, idx_b)
            scene["instances"].append({"eid": d["eid"], "inst": k, "mesh": bid, "matrix": np.eye(4).flatten().tolist(),
                                       "location": [0.0, 0.0, 0.0], "quat": [0.0, 0.0, 0.0, 1.0], "scale": [1.0, 1.0, 1.0],
                                       "material": mk, "kind": "baked", **ident})
            report["baked"] += 1

    # Water is a semantic, not a base-colour hint: the game's water mesh is shaded by a dedicated pass.
    # Mark those instances so the emitters can give them a water material instead of the preview base colour.
    def is_water_name(src):
        return bool(src) and "/Water/" in src.get("object", "")
    for inst in scene["instances"]:
        if inst.get("kind") == "static" and is_water_name(inst.get("source")):
            inst["kind"] = "water"
        if inst.get("kind") in ("water", "translucent"):
            mat = scene["materials"].get(inst.get("material"))
            if mat is not None:
                mat["water" if inst["kind"] == "water" else "translucent"] = True
    for mid, m in scene["meshes"].items():
        if is_water_name(m.get("source")):
            m["water"] = True

    scene["camera"] = camera_from_vp(C, 16 / 9)
    if scene["instances"] and not scene["camera"]:
        P = np.vstack([np.fromfile(out / scene["meshes"][i["mesh"]]["dir"] / "pos.f32", dtype="<f4").reshape(-1, 3) for i in scene["instances"]])
        lo, hi = P.min(0), P.max(0)
        c, r = (lo + hi) / 2, float(np.linalg.norm(hi - lo)) / 2
        dist = r / np.tan(np.radians(15))
        # orbit camera on the -forward axis of the source space (Unity: -Z, UE: -X), looking at the bounds centre
        back = np.array([0, 0, -1.0]) if space == "unity" else np.array([-1.0, 0, 0])
        up = np.array([0, 1.0, 0]) if space == "unity" else np.array([0, 0, 1.0])
        fwd = -back
        scene["camera"] = {"position": (c + back * dist).tolist(), "forward": fwd.tolist(), "up": up.tolist(),
                           "right": np.cross(up, fwd).tolist(), "hfov_deg": 2 * np.degrees(np.arctan(np.tan(np.radians(15)) * 16 / 9)), "vfov_deg": 30.0, "auto": True}
    scene["report"] = report
    (out / "scene.json").write_text(json.dumps(scene, indent=1), encoding="utf-8")
    return scene


def build_model_space(export_dir, out_dir, space="unity", pick=None, min_tris=1, basis=None):
    """Scene package from an extract that has no post-VS data (e.g. 3DMigoto Frame Analysis).
    Meshes are placed at identity in their own model space; `space` tells emitters which convention that is."""
    ex = Extract(export_dir)
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "meshes").mkdir(parents=True)
    scene = {"space": space, "meshes": {}, "materials": {}, "instances": [], "textures": {}, "camera": None,
             "vp": None, "vp_match": 0.0}
    done = set()
    report = {"draws": len(ex.draws), "placed": 0, "skipped": 0, "no_uv": 0}
    for d in ex.draws:
        if pick and not pick(d):
            continue
        mesh = ex.meshes[d["mesh"]]
        names = {a["name"]: i for i, a in enumerate(mesh["attrs"])}
        if "POSITION0" not in names or mesh["indices"] // 3 < min_tris:
            report["skipped"] += 1
            continue
        mid = d["mesh"]
        if mid not in done:
            pos = ex.mesh_attr(mid, names["POSITION0"])[:, :3]
            idx = np.fromfile(ex.root / "meshes" / mid / "idx.u32", dtype="u4")
            if not np.isfinite(pos).all():
                report["skipped"] += 1
                continue
            uv = ex.mesh_attr(mid, names["TEXCOORD0"])[:, :2] if "TEXCOORD0" in names and ex.meshes[mid]["attrs"][names["TEXCOORD0"]]["comps"] >= 2 else None
            nrm = ex.mesh_attr(mid, names["NORMAL0"])[:, :3] if "NORMAL0" in names else None
            if nrm is not None:
                ln = np.linalg.norm(nrm, axis=1)
                nrm = nrm / np.maximum(ln[:, None], 1e-9) if np.median(ln) > 0.5 else None
            if nrm is None:
                nrm = vertex_normals(pos.astype(np.float64), idx)
            md = out / "meshes" / mid
            md.mkdir()
            if basis is not None:
                B = np.asarray(basis, dtype=np.float64)
                pos, nrm = pos @ B, nrm @ B
            pos.astype("<f4").tofile(md / "pos.f32")
            idx.astype("<u4").tofile(md / "idx.u32")
            nrm.astype("<f4").tofile(md / "nrm.f32")
            if uv is not None:
                uv.astype("<f4").tofile(md / "uv.f32")
            else:
                report["no_uv"] += 1
            scene["meshes"][mid] = {"dir": f"meshes/{mid}", "verts": int(len(pos)), "tris": int(len(idx) // 3), "has_nrm": True, "has_uv": uv is not None}
            done.add(mid)
        base = None
        for t in d["textures"]:
            info = ex.data["textures"].get(t["res"])
            if info and info["file"] and "SRGB" in t["format"] and info["w"] >= 256:
                base = t["res"]
                break
        key = f"mat_{(base or 'none')}"
        if key not in scene["materials"]:
            scene["materials"][key] = {"base": base, "normal": None, "masked": False}
            if base and base not in scene["textures"]:
                ti = ex.data["textures"][base]
                scene["textures"][base] = {"file": str(ex.root / ti["file"]), "w": ti["w"], "h": ti["h"], "format": ti["format"]}
        W = np.eye(4)
        scene["instances"].append({"eid": d["eid"], "inst": 0, "mesh": mid, "matrix": W.flatten().tolist(), "location": [0.0, 0.0, 0.0],
                                   "quat": [0.0, 0.0, 0.0, 1.0], "scale": [1.0, 1.0, 1.0], "material": key, "kind": "model-space"})
        report["placed"] += 1
    if scene["instances"] and not scene["camera"]:
        P = np.vstack([np.fromfile(out / scene["meshes"][i["mesh"]]["dir"] / "pos.f32", dtype="<f4").reshape(-1, 3) for i in scene["instances"]])
        lo, hi = P.min(0), P.max(0)
        c, r = (lo + hi) / 2, float(np.linalg.norm(hi - lo)) / 2
        dist = r / np.tan(np.radians(15))
        # orbit camera on the -forward axis of the source space (Unity: -Z, UE: -X), looking at the bounds centre
        back = np.array([0, 0, -1.0]) if space == "unity" else np.array([-1.0, 0, 0])
        up = np.array([0, 1.0, 0]) if space == "unity" else np.array([0, 0, 1.0])
        fwd = -back
        scene["camera"] = {"position": (c + back * dist).tolist(), "forward": fwd.tolist(), "up": up.tolist(),
                           "right": np.cross(up, fwd).tolist(), "hfov_deg": 2 * np.degrees(np.arctan(np.tan(np.radians(15)) * 16 / 9)), "vfov_deg": 30.0, "auto": True}
    scene["report"] = report
    (out / "scene.json").write_text(json.dumps(scene, indent=1), encoding="utf-8")
    return scene
