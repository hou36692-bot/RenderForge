import json, sys, collections
s = json.load(open(sys.argv[1]))
rows = s["draws"]
print("draws", len(rows))
groups = collections.OrderedDict()
for d in rows:
    key = (tuple(d["rtFormats"]), d["depth"], tuple(d["rtSize"] or ()))
    groups.setdefault(key, []).append(d)
for k, v in groups.items():
    ids = [d["eid"] for d in v]
    print(f"RT{len(k[0])} {k[0][:3]} size={k[2]} depth={k[1]} n={len(v)} eids {ids[0]}..{ids[-1]} idx_sum={sum(d['numIndices'] for d in v)} vin={len(v[0]['vin'])} tex={len(v[0]['psTextures'])}")
