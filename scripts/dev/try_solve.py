import sys, time
sys.path.insert(0, r"D:\HOU\RenderForge")
import numpy as np
from collections import Counter
from renderforge.solve import *

ex = Extract(sys.argv[1])
gb = lambda d: len(d["rts"]) == 7
sel = [d for d in ex.draws if gb(d)]
print("gbuffer draws", len(sel), "of", len(ex.draws))
t = time.time()
st = fit_static_draws(ex, gb)
print("static-fit draws", len(st), "time", round(time.time() - t, 1))
print("errs", np.percentile([s["err"] for s in st], [50, 90, 100]) if st else None)
t = time.time()
rk = find_view_proj(ex, st)
print("VP search", round(time.time() - t, 1))
for frac, name, C in rk:
    print(f"{frac:.2f} {name}")
if rk:
    print(np.round(rk[0][2], 5))
