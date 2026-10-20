import sys
sys.path.insert(0, r"D:\HOU\RenderForge")
import numpy as np
from renderforge.solve import *
ex = Extract(sys.argv[1])
dm = {d["eid"]: d for d in ex.draws if len(d["rts"]) == 7}
def collect(multi, lim=60):
    out = []
    for eid, d in dm.items():
        pi = ex.position_attr_index(d["mesh"]); vs = ex.vsout(d)
        if pi is None or vs is None: continue
        pos = ex.mesh_attr(d["mesh"], pi); n = len(pos)
        if len(vs) % n: continue
        cnt = len(vs)//n
        if (cnt > 1) != multi: continue
        M, err = fit_local_to_clip(pos, vs[:n])
        if M is not None and err < 1e-5: out.append({"draw": d, "M": M})
        if len(out) >= lim: break
    return out
for lab, m in (("single", False), ("multi", True)):
    st = collect(m)
    rk = find_view_proj(ex, st, top=4, sample=60)
    print(lab, len(st), [(round(f,2), n) for f, n, c in rk])
