import json, sys, numpy as np
root = sys.argv[1]
sc = json.load(open(root + "/scene.json"))
bad = 0; unref = 0; tot = 0
for mid, m in sc["meshes"].items():
    pos = np.fromfile(f"{root}/{m['dir']}/pos.f32", dtype="f4").reshape(-1, 3)
    idx = np.fromfile(f"{root}/{m['dir']}/idx.u32", dtype="u4")
    fin = np.isfinite(pos).all(1)
    ref = np.zeros(len(pos), bool); ref[idx] = True
    tot += 1
    if not fin.all(): bad += 1
    if not ref.all(): unref += 1
    if mid in ("m0013", "m0020") : print(mid, "nonfinite", (~fin).sum(), "of", len(pos), "unref", (~ref).sum(), "bad referenced", (~fin & ref).sum(), "max abs", np.abs(pos[fin]).max())
print("meshes", tot, "with nonfinite", bad, "with unreferenced", unref)
