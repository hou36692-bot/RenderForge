import sys, json, collections
ex = json.load(open(sys.argv[1] + "/extract.json"))
gb = [d for d in ex["draws"] if len(d["rts"]) == 7]
c = collections.Counter(tuple(t["format"] for t in d["textures"]) for d in gb)
for k, v in c.most_common(8): print(v, k)
d = gb[5]
print(d["eid"], d["numInstances"], [(t["slot"], t["res"], t["format"], ex["textures"][t["res"]]["w"], ex["textures"][t["res"]]["file"]) for t in d["textures"]])
print(ex["meshes"][d["mesh"]]["attrs"])
print({k: (v["size"]) for k, v in d["cbuffers"].items()})
print(sum(1 for t in ex["textures"].values() if t and t["dynamic"]), "dynamic textures;", sum(1 for t in ex["textures"].values() if t and t["file"]), "saved")
