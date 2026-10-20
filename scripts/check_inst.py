import json, sys, numpy as np
sc = json.load(open(sys.argv[1] + "/scene.json"))
I = sc["instances"]
loc = np.array([i["location"] for i in I]); sca = np.array([i["scale"] for i in I]); q = np.array([i["quat"] for i in I])
print("nonfinite loc", (~np.isfinite(loc)).any(1).sum(), "scale", (~np.isfinite(sca)).any(1).sum(), "quat", (~np.isfinite(q)).any(1).sum())
print("loc abs max", np.abs(loc[np.isfinite(loc).all(1)]).max(), "scale min/max", np.nanmin(sca), np.nanmax(sca))
big = [(i["eid"], i["mesh"], i["scale"], i["location"]) for i in I if not np.isfinite(i["scale"]).all() or np.abs(i["scale"]).max() > 1000 or np.abs(i["scale"]).min() < 1e-4 or np.abs(i["location"]).max() > 5e5]
print(len(big), big[:5])
print("det neg:", sum(1 for i in I if np.prod(i["scale"]) < 0))
# mesh extents
ms = []
for mid, m in sc["meshes"].items():
    pos = np.fromfile(f"{sys.argv[1]}/{m['dir']}/pos.f32", dtype="f4").reshape(-1, 3)
    ms.append((np.abs(pos).max(), mid))
ms.sort(reverse=True); print("largest meshes", ms[:6])
