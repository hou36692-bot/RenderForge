import json, sys
import numpy as np
from pathlib import Path

root = Path(sys.argv[1])
ex = json.load(open(root / "extract.json"))
for d in ex["draws"]:
    m = ex["meshes"][d["mesh"]]
    md = root / "meshes" / d["mesh"]
    pos_attr = m["attrs"][0]
    pos = np.fromfile(md / pos_attr["file"], dtype="f4").reshape(-1, pos_attr["comps"])
    p = d["postVS"]
    line = f"eid {d['eid']} mesh {d['mesh']} verts {m['verts']} idx {m['indices']} inst {d['numInstances']} attrs {len(m['attrs'])}"
    if p:
        vs = np.fromfile(root / p["file"], dtype="f4").reshape(-1, 4)
        line += f" | vsout rows {p['rows']} base {p['baseVertex']} nidx {p['numIndices']}"
        if len(vs) == len(pos):
            h = np.c_[pos[:, :3], np.ones(len(pos))]
            M, res, rk, sv = np.linalg.lstsq(h, vs, rcond=None)
            err = np.abs(h @ M - vs).max()
            line += f" | LS maxerr {err:.4g} scale {np.abs(vs).max():.3g}"
        else:
            line += " | row mismatch"
    print(line)
