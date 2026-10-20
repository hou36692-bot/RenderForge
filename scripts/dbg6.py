import sys
sys.path.insert(0, r"D:\HOU\RenderForge")
import numpy as np
np.set_printoptions(suppress=True, linewidth=150)
from renderforge.solve import *
ex = Extract(sys.argv[1]); eid = int(sys.argv[2])
d = next(x for x in ex.draws if x["eid"] == eid)
pos = ex.mesh_attr(d["mesh"], ex.position_attr_index(d["mesh"])); vs = ex.vsout(d); n = len(pos)
print("verts", n, "instances", len(vs)//n, "numInst", d["numInstances"], ex.meshes[d["mesh"]]["topology"])
C = ex.cbuffer("697e1f7f7c5a32ae")[16:32].astype(np.float64).reshape(4,4); Ci = np.linalg.inv(C)
for k in (0, 5, 100):
    M, err = fit_local_to_clip(pos, vs[k*n:(k+1)*n]); W = M@Ci
    print(k, "err", err, "aff", affine_score(W), "proj", proj_score(W)); print(W.round(3))
W3 = (M@Ci)[:3,:3]
print("sv", np.linalg.svd(W3)[1], "det", np.linalg.det(W3))
P = pos[:, :3]; print("pos\n", P.round(1), "\nsv pos", np.linalg.svd(P-P.mean(0))[1])
