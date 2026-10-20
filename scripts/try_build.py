import sys, time
sys.path.insert(0, r"D:\HOU\RenderForge")
from renderforge.build import build
t = time.time()
sc = build(sys.argv[1], sys.argv[2], lambda d: len(d["rts"]) == 7)
print("vp", sc["vp"], sc["vp_match"])
print("report", sc["report"], "meshes", len(sc["meshes"]), "materials", len(sc["materials"]), "textures", len(sc["textures"]))
print("camera", {k: [round(x, 3) for x in v] if isinstance(v, list) else round(v, 2) for k, v in sc["camera"].items()})
print(round(time.time() - t, 1), "s")
