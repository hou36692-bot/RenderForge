import sys
sys.path.insert(0, r"D:\HOU\RenderForge")
import numpy as np
from renderforge.solve import *
ex = Extract(sys.argv[1])
st = fit_static_draws(ex, lambda d: len(d["rts"]) == 7)
rk = find_view_proj(ex, st, top=12)
for frac, name, C in rk:
    ws = [s["M"] @ np.linalg.inv(C) for s in st]
    sc = np.array([np.linalg.norm(w[:3,:3], axis=1) for w in ws])
    tr = np.array([w[3,:3]/w[3,3] for w in ws])
    print(f"{frac:.2f} {name}  scale med {np.median(sc,0).round(3)}  trans min {tr.min(0).round(0)} max {tr.max(0).round(0)}")
