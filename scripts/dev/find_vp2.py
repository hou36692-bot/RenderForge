import json, sys
import numpy as np
from pathlib import Path

root = Path(sys.argv[1])
ex = json.load(open(root / "extract.json"))
draws = [d for d in ex["draws"] if d["eid"] != 415]
# collect distinct cbuffer hashes for vs slots
cbs = {}
for d in draws:
    for k, cb in d["cbuffers"].items():
        if k.startswith("vs"):
            cbs.setdefault(k, set()).add(cb["hash"])
print({k: len(v) for k, v in cbs.items()})
VS = {}
for d in draws:
    p = d["postVS"]
    VS[d["eid"]] = np.fromfile(root / p["file"], dtype="f4").reshape(-1, 4).astype(np.float64)
res = []
d0 = draws[0]
for k, hs in cbs.items():
    if len(hs) != 1:
        continue
    h = next(iter(hs))
    data = np.fromfile(root / "cbuffers" / (h + ".bin"), dtype="f4")
    for off in range(0, len(data) - 15, 4):
        A = data[off:off + 16].reshape(4, 4).astype(np.float64)
        if not np.isfinite(A).all() or abs(np.linalg.det(A)) < 1e-9:
            continue
        for tn, B in (("A", A), ("AT", A.T)):
            n3 = np.linalg.norm(B[:3, 3])
            if abs(n3 - 1) > 1e-3 or abs(B[3,3]) > 1e6 or np.linalg.cond(B) > 1e7:
                continue
            Bi = np.linalg.inv(B)
            allp = []
            ok = True
            for eid, c in VS.items():
                w = c @ Bi
                if np.abs(w[:, 3]).min() < 1e-9:
                    ok = False
                    break
                allp.append(w[:, :3] / w[:, 3:4])
            if not ok:
                continue
            P = np.vstack(allp)
            ext = P.max(0) - P.min(0)
            res.append((float(np.prod(ext + 1e-6)), k, off * 4, tn, ext, P.mean(0)))
res.sort(key=lambda r: r[0])
print(len(res), 'candidates')
for r in res[:10]:
    print(f"vol {r[0]:.4g} {r[1]} off {r[2]} {r[3]} ext {np.round(r[4],2)} center {np.round(r[5],2)}")
