import json, sys, numpy as np
sc = json.load(open(sys.argv[1] + "/scene.json"))
cam = sc["camera"]; print({k: np.round(v, 2).tolist() if isinstance(v, list) else v for k, v in cam.items()})
P = np.array([i["location"] for i in sc["instances"]])
print("n", len(P), "loc min", P.min(0).round(0), "max", P.max(0).round(0), "median", np.median(P, 0).round(0))
c = np.array(cam["position"]); f = np.array(cam["forward"])
d = P - c; depth = d @ f
print("frac in front", (depth > 0).mean(), "median dist", np.median(np.linalg.norm(d, axis=1)).round(0))
