"""Scene-space conversion between engines' conventions (all left-handed here).

  ue    : X forward, Y right, Z up, centimetres, UV origin top-left
  unity : X right,  Y up,   Z forward, metres,  UV origin bottom-left   (GPU/D3D data from a Unity game is in this space)

Row-vector convention: p_dst = p_src @ Q.  UE->Unity is the permutation (Y, Z, X) * 0.01.
"""
import json
import shutil
from pathlib import Path

import numpy as np

_PERM = np.array([[0, 0, 1], [1, 0, 0], [0, 1, 0]], dtype=np.float64)   # ue -> unity
Q = {("ue", "unity"): _PERM * 0.01, ("unity", "ue"): np.linalg.inv(_PERM * 0.01)}


def matrix(W, q):
    W = np.asarray(W, dtype=np.float64).reshape(4, 4)
    out = np.eye(4)
    out[:3, :3] = np.linalg.inv(q) @ W[:3, :3] @ q
    out[3, :3] = W[3, :3] @ q
    return out


def convert_scene(scene_dir, out_dir, dst):
    """Write a copy of a scene package expressed in `dst` space. Normals are rotated only (no scale)."""
    scene_dir, out = Path(scene_dir), Path(out_dir)
    sc = json.loads((scene_dir / "scene.json").read_text(encoding="utf-8"))
    src = sc.get("space", "ue")
    if src == dst:
        return scene_dir
    q = Q[(src, dst)]
    rot = q / np.abs(q).max()
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(scene_dir, out)
    for mid, m in sc["meshes"].items():
        d = out / m["dir"]
        np.fromfile(d / "pos.f32", dtype="<f4").reshape(-1, 3).astype(np.float64).__matmul__(q).astype("<f4").tofile(d / "pos.f32")
        if (d / "nrm.f32").exists():
            np.fromfile(d / "nrm.f32", dtype="<f4").reshape(-1, 3).astype(np.float64).__matmul__(rot).astype("<f4").tofile(d / "nrm.f32")
    from .solve import decompose
    for i in sc["instances"]:
        W = matrix(i["matrix"], q)
        i["matrix"] = W.flatten().tolist()
        loc, quat, scl = decompose(W)
        i["location"], i["quat"], i["scale"] = loc.tolist(), quat.tolist(), scl.tolist()
    if sc.get("camera"):
        c = sc["camera"]
        c["position"] = (np.array(c["position"]) @ q).tolist()
        for k in ("forward", "up", "right"):
            v = np.array(c[k]) @ rot
            c[k] = (v / np.linalg.norm(v)).tolist()
    sc["space"] = dst
    (out / "scene.json").write_text(json.dumps(sc, indent=1), encoding="utf-8")
    return out
