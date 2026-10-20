import sys, collections
sys.path.insert(0, r"D:\HOU\RenderForge")
import numpy as np
from renderforge.solve import Extract
ex = Extract(sys.argv[1])
c = collections.Counter(); ex_by = {}
for d in ex.draws:
    if len(d["rts"]) != 7: continue
    pi = ex.position_attr_index(d["mesh"]); vs = ex.vsout(d)
    if pi is None: r = "nopos"
    elif vs is None: r = "novs"
    else:
        pos = ex.mesh_attr(d["mesh"], pi)
        if len(pos) != len(vs): r = "lenmismatch"
        elif d["numInstances"] > 1: r = "instanced"
        else: r = "ok"
    c[r] += 1; ex_by.setdefault(r, d)
print(c)
for r, d in ex_by.items():
    print(r, d["eid"], d["numInstances"], ex.meshes[d["mesh"]]["attrs"][:3] if r!="novs" else "", d["postVS"])
