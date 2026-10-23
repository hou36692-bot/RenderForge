"""Emit a Unity-ready package from a RenderForge scene package.

The scene package is first converted to Unity space (see space.py); UVs in a scene package are in D3D convention
(origin top-left), Unity wants bottom-left, so V is flipped here.
"""
import json
import shutil
from pathlib import Path

import numpy as np

def _auto_camera(scene_dir, sc):
    """Frame the world-space bounds of all instances from the -Z side (Unity space)."""
    pts = []
    for i in sc["instances"]:
        m = sc["meshes"].get(i["mesh"])
        if not m:
            continue
        p = np.fromfile(scene_dir / m["dir"] / "pos.f32", dtype="<f4").reshape(-1, 3)
        lo, hi = p.min(0), p.max(0)
        corners = np.array([[x, y, z, 1] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        pts.append((corners @ np.asarray(i["matrix"]).reshape(4, 4))[:, :3])
    if not pts:
        return None
    P = np.vstack(pts)
    c, r = (P.min(0) + P.max(0)) / 2, float(np.linalg.norm(P.max(0) - P.min(0))) / 2
    dist = max(r, 1e-3) / np.tan(np.radians(15))
    return {"position": (c + np.array([0, 0, -dist])).tolist(), "forward": [0, 0, 1], "up": [0, 1, 0], "vfov_deg": 30.0, "hfov_deg": 50.0}


def emit(scene_dir, out_dir, case):
    from .space import convert_scene
    out = Path(out_dir)
    scene_dir = Path(convert_scene(scene_dir, out.parent / "_scene_unity_space", "unity"))
    sc = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
    if out.exists():
        shutil.rmtree(out)
    (out / "meshes").mkdir(parents=True)
    meshes = []
    for mid, m in sc["meshes"].items():
        d = scene_dir / m["dir"]
        pos = np.fromfile(d / "pos.f32", dtype="<f4").reshape(-1, 3).astype(np.float64) 
        idx = np.fromfile(d / "idx.u32", dtype="<u4")
        (out / "meshes" / mid).mkdir()
        pos.astype("<f4").tofile(out / "meshes" / mid / "pos.f32")
        idx.tofile(out / "meshes" / mid / "idx.u32")
        has_n = (d / "nrm.f32").exists()
        has_uv = (d / "uv.f32").exists()
        if has_n:
            n = np.fromfile(d / "nrm.f32", dtype="<f4").reshape(-1, 3).astype(np.float64)
            n.astype("<f4").tofile(out / "meshes" / mid / "nrm.f32")
        if has_uv:
            uv = np.fromfile(d / "uv.f32", dtype="<f4").reshape(-1, 2).copy()
            uv[:, 1] = 1.0 - uv[:, 1]
            uv.astype("<f4").tofile(out / "meshes" / mid / "uv.f32")
        meshes.append({"id": mid, "verts": int(len(pos)), "indices": int(len(idx)), "hasNormals": has_n, "hasUV": has_uv})
    tex = [{"id": k.replace("::", "_"), "file": v["file"], "format": v["format"]} for k, v in sc["textures"].items()]
    tex_ids = {k: k.replace("::", "_") for k in sc["textures"]}
    mats = [{"id": k, "baseTexture": tex_ids.get(m["base"], "") if m["base"] else "",
             "normalTexture": tex_ids.get(m["normal"], "") if m["normal"] else "",
             "masked": bool(m.get("masked"))} for k, m in sc["materials"].items()]
    inst = [{"mesh": i["mesh"], "material": i["material"], "m": np.asarray(i["matrix"]).round(7).tolist()}
            for i in sc["instances"]]
    cam = sc.get("camera") or _auto_camera(scene_dir, sc)
    cam_u = None
    if cam:
        f = np.array(cam["forward"]) 
        u = np.array(cam["up"]) 
        cam_u = {"position": cam["position"], "forward": (f / np.linalg.norm(f)).tolist(),
                 "up": (u / np.linalg.norm(u)).tolist(), "vfov": cam["vfov_deg"], "hfov": cam["hfov_deg"]}
    pkg = {"case": case, "meshes": meshes, "textures": tex, "materials": mats, "instances": inst, "camera": cam_u,
           "texture_root": str(scene_dir.parent / "export")}
    (out / "unity_scene.json").write_text(json.dumps(pkg), encoding="utf-8")
    return {"meshes": len(meshes), "textures": len(tex), "materials": len(mats), "instances": len(inst)}
