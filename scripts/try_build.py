import sys, time
sys.path.insert(0, r"D:\HOU\RenderForge")
from renderforge.build import build
t = time.time()
sc = build(sys.argv[1], sys.argv[2], lambda d: len(d["rts"]) == 7)
print("vp", sc["vp"], round(sc["vp_match"], 2))
print("report", sc["report"], "meshes", len(sc["meshes"]), "with_uv", sum(m["has_uv"] for m in sc["meshes"].values()), "with_nrm", sum(m["has_nrm"] for m in sc["meshes"].values()), "materials", len(sc["materials"]), "textures", len(sc["textures"]))
print(round(time.time() - t, 1), "s")
