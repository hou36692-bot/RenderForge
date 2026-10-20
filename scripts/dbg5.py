import sys, json, collections
sc = json.load(open(sys.argv[1] + "/scene.json"))
c = collections.Counter(); per = collections.Counter()
for i in sc["instances"]:
    c[i["kind"]] += 1; per[(i["kind"], i["eid"])] += 1
print(c)
top = per.most_common(8); print(top)
print("static unique meshes", len({i["mesh"] for i in sc["instances"] if i["kind"]=="static"}))
tris = sum(sc["meshes"][i["mesh"]]["tris"] for i in sc["instances"]); print("total tris placed", tris)
bk = [i for i in sc["instances"] if i["kind"]=="baked"]
print("baked draws", len({i["eid"] for i in bk}), "baked tris", sum(sc["meshes"][i["mesh"]]["tris"] for i in bk))
