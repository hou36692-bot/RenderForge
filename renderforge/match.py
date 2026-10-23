"""Match captured GPU meshes to source-asset meshes (from CUE4Parse / UnityPy / AssetRipper exports).

A source mesh set is a directory tree of `geometry_fingerprint.json` (+ lod_XX_positions.f32 / lod_XX_indices.u32); this is the
format written by the SourceProbe CUE4Parse CLI. Other tools can be adapted by writing the same two raw files per LOD.

Match levels:
  exact   same index count and vertex count, every position within `tol` (local space, same unit as the capture)
  shape   same counts and same sorted bounding-box extents (position data differs: quantised / transformed / different LOD data)
"""
import json
from pathlib import Path

import numpy as np


def load_source_lods(root):
    out = []
    for fp in Path(root).rglob("geometry_fingerprint.json"):
        d = json.loads(fp.read_text(encoding="utf-8"))
        for lod in d["lods"]:
            if lod.get("available") is False or "positionsFile" not in lod:
                continue
            out.append({"package": d["sourcePackage"], "lod": lod["lod"], "vcount": lod["vertexCount"], "icount": lod["indexCount"],
                        "dir": fp.parent, "pos": lod["positionsFile"], "idx": lod["indicesFile"]})
    return out


def match_extract(ex, sources, tol=1e-3):
    by_counts = {}
    for s in sources:
        by_counts.setdefault((s["icount"], s["vcount"]), []).append(s)
    results = {}
    for mid, m in ex.meshes.items():
        pi = ex.position_attr_index(mid)
        if pi is None:
            continue
        pos = ex.mesh_attr(mid, pi)[:, :3]
        cands = by_counts.get((m["indices"], m["verts"]), [])
        best = None
        for s in cands:
            sp = np.fromfile(s["dir"] / s["pos"], dtype="<f4").reshape(-1, 3)
            if len(sp) != len(pos):
                continue
            err = float(np.abs(sp - pos).max())
            if best is None or err < best[0]:
                best = (err, s, None)
            if err < tol:
                break
        if best and best[0] < tol:
            results[mid] = {"level": "exact", "package": best[1]["package"], "lod": best[1]["lod"], "max_err": best[0]}
        elif cands:
            ext = np.sort(np.ptp(pos, axis=0))
            for s in cands:
                sp = np.fromfile(s["dir"] / s["pos"], dtype="<f4").reshape(-1, 3)
                if len(sp) == len(pos) and np.allclose(np.sort(np.ptp(sp, axis=0)), ext, rtol=1e-2, atol=1e-2):
                    results[mid] = {"level": "shape", "package": s["package"], "lod": s["lod"], "max_err": None}
                    break
    return results
