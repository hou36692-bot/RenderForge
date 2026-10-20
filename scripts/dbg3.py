import sys
sys.path.insert(0, r"D:\HOU\RenderForge")
import numpy as np
np.set_printoptions(suppress=True, linewidth=150)
from renderforge.solve import *
ex = Extract(sys.argv[1])
d = next(x for x in ex.draws if x["eid"] == 2092)
pos = ex.mesh_attr(d["mesh"], ex.position_attr_index(d["mesh"])); vs = ex.vsout(d); n = len(pos)
C = ex.cbuffer("697e1f7f7c5a32ae")[0:16].astype(np.float64).reshape(4,4); Ci = np.linalg.inv(C)
for k in range(3):
    M, err = fit_local_to_clip(pos, vs[k*n:(k+1)*n])
    print(k, err); print((M @ Ci).round(4))
print("pos range", pos.min(0), pos.max(0)); print(vs[:3], vs[n:n+3])
print([ (a["name"], a["format"], a["perInstance"], a["count"]) for a in ex.meshes[d["mesh"]]["attrs"]])
