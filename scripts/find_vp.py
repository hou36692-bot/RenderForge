import json, sys
import numpy as np
from pathlib import Path

root = Path(sys.argv[1])
ex = json.load(open(root / "extract.json"))
d = next(x for x in ex["draws"] if x["eid"] == 415)
m = ex["meshes"][d["mesh"]]
md = root / "meshes" / d["mesh"]
pa = m["attrs"][0]
pos = np.fromfile(md / pa["file"], dtype="f4").reshape(-1, pa["comps"])[:, :3].astype(np.float64)
vs = np.fromfile(root / d["postVS"]["file"], dtype="f4").reshape(-1, 4).astype(np.float64)
h = np.c_[pos, np.ones(len(pos))]
M0 = np.linalg.lstsq(h, vs, rcond=None)[0]
print("M0 (row-vec convention)\n", np.round(M0, 4))
print("pos extents", pos.min(0), pos.max(0))
cands = []
for k, cb in d["cbuffers"].items():
    if not k.startswith("vs"):
        continue
    data = np.fromfile(root / "cbuffers" / (cb["hash"] + ".bin"), dtype="f4")
    for off in range(0, len(data) - 15, 4):
        A = data[off:off + 16].reshape(4, 4).astype(np.float64)
        if not np.isfinite(A).all() or abs(np.linalg.det(A)) < 1e-12:
            continue
        for tname, B in (("A", A), ("A^T", A.T)):
            W = M0 @ np.linalg.inv(B)
            # affine check: last column ~ (0,0,0,1)
            lc = W[:, 3] / (W[3, 3] if abs(W[3, 3]) > 1e-12 else 1)
            err = np.abs(lc - [0, 0, 0, 1]).max()
            R = W[:3, :3]
            rs = np.linalg.norm(R, axis=1)
            orth = np.abs(R / rs[:, None] @ (R / rs[:, None]).T - np.eye(3)).max() if rs.min() > 0 else 9
            cands.append((err + orth, k, off * 4, tname, rs, W))
cands.sort(key=lambda c: c[0])
for c in cands[:8]:
    print(f"score {c[0]:.4g} cb {c[1]} byteoff {c[2]} {c[3]} scales {np.round(c[4],3)}")
    print(np.round(c[5], 3))
