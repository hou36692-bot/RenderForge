import sys
sys.path.insert(0, r"D:\HOU\RenderForge")
import numpy as np
from renderforge.solve import *
ex = Extract(sys.argv[1])
dm = {d["eid"]: d for d in ex.draws if len(d["rts"]) == 7}
h = "697e1f7f7c5a32ae"
data = ex.cbuffer(h)
C = data[16:32].astype(np.float64).reshape(4, 4); Ci = np.linalg.inv(C)
res = []
for eid, d in dm.items():
    pi = ex.position_attr_index(d["mesh"]); vs = ex.vsout(d)
    if pi is None or vs is None: continue
    pos = ex.mesh_attr(d["mesh"], pi); n = len(pos)
    if len(vs) % n: continue
    for k in range(len(vs)//n):
        M, err = fit_local_to_clip(pos, vs[k*n:(k+1)*n])
        if M is None: continue
        res.append((eid, k, len(vs)//n, err, affine_score(M @ Ci)))
res = np.array(res)
for lab, m in (("single", res[:,2]==1), ("multi", res[:,2]>1)):
    r = res[m]; print(lab, len(r), "err<2e-4:", (r[:,3]<2e-4).sum(), "affine<1e-3:", (r[:,4]<1e-3).sum(), "both:", ((r[:,3]<2e-4)&(r[:,4]<1e-3)).sum())
print(res[res[:,2]>1][:10])
