import sys, json, collections
sys.path.insert(0, r"D:\HOU\RenderForge")
from renderforge.solve import Extract
ex = Extract(sys.argv[1]); sc = json.load(open(sys.argv[2] + "/scene.json"))
noimg = {mid for mid, m in sc["meshes"].items() if not m["has_uv"]}
seen = {}
c = collections.Counter()
for d in ex.draws:
    m = ex.meshes[d["mesh"]]
    if d["mesh"] in noimg and d["mesh"] not in seen:
        seen[d["mesh"]] = d
        sig = (tuple(a["format"] for a in m["attrs"]), tuple((s["format"], s["size"] // max(1, s["elemBytes"] or 1) / m["verts"] if m["verts"] else 0) for s in d["vsSrvs"] if s["file"]))
        c[sig] += 1
for k, v in c.most_common(8): print(v, k)
print(len(noimg))
