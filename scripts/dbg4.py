import sys
sys.path.insert(0, r"D:\HOU\RenderForge")
import numpy as np
np.set_printoptions(suppress=True, linewidth=150)
from renderforge.solve import *
ex = Extract(sys.argv[1])
st = fit_static_draws(ex, lambda d: len(d["rts"]) == 7, tol=1e-5)
print(len(st))
data = ex.cbuffer("697e1f7f7c5a32ae")
for off in (0, 64, 576, 448):
  for tag in ("A","AT"):
    A = data[off//4:off//4+16].astype(np.float64).reshape(4,4)
    C = A if tag=="A" else A.T
    Ci = np.linalg.inv(C)
    sc = [(proj_score(s["M"]@Ci), affine_score(s["M"]@Ci)) for s in st[:50]]
    sc = np.array(sc)
    print(off, tag, "|f|", round(np.linalg.norm(C[:3,3]),4), "proj<1e-3:", (sc[:,0]<1e-3).sum(), "affine<2e-2:", (sc[:,1]<2e-2).sum(), "median aff", np.median(sc[:,1]))
C = data[144:160].astype(np.float64).reshape(4,4)
