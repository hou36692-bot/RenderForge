import sys, json; sys.path.insert(0, r"D:\HOU\RenderForge")
import numpy as np
from renderforge.build import build_model_space
from renderforge.solve import Extract
from renderforge.emit_unity import emit
W = r"D:\HOU\RenderForge\workspace\zzz_frame01"
ex = Extract(W + r"\export")
def is_char(d):
    m = ex.meshes[d["mesh"]]
    names = {a["name"]: i for i, a in enumerate(m["attrs"])}
    p = ex.mesh_attr(d["mesh"], names["POSITION0"])[:, :3]
    return np.ptp(p, axis=0).max() < 3 and m["indices"] // 3 >= 100
B = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
sc = build_model_space(W + r"\export", W + r"\build", "unity", pick=is_char, basis=B)
print(sc["report"], sc["camera"]["position"])
print(emit(W + r"\build", W + r"\unity_pkg", "zzz_frame01"))
