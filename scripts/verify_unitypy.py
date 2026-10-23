import sys, json, UnityPy
from pathlib import Path
src, out = sys.argv[1], Path(sys.argv[2]); out.mkdir(parents=True, exist_ok=True)
env = UnityPy.load(src)
res = {"unitypy": UnityPy.__version__, "objects": {}, "exported": []}
for o in env.objects:
    res["objects"][o.type.name] = res["objects"].get(o.type.name, 0) + 1
    d = None
    if o.type.name == "Texture2D":
        d = o.parse_as_object() if hasattr(o, "parse_as_object") else o.read()
        d.image.save(out / f"{d.m_Name}.png"); res["exported"].append(f"{d.m_Name}.png")
    elif o.type.name == "Mesh":
        d = o.parse_as_object() if hasattr(o, "parse_as_object") else o.read()
        (out / f"{d.m_Name}.obj").write_text(d.export(), encoding="utf-8"); res["exported"].append(f"{d.m_Name}.obj")
        res["mesh"] = {"name": d.m_Name, "verts": d.m_VertexCount if hasattr(d, "m_VertexCount") else None}
    elif o.type.name == "Transform":
        d = o.parse_as_object() if hasattr(o, "parse_as_object") else o.read()
        res["transform"] = {"pos": [d.m_LocalPosition.x, d.m_LocalPosition.y, d.m_LocalPosition.z],
                            "scale": [d.m_LocalScale.x, d.m_LocalScale.y, d.m_LocalScale.z]}
print(json.dumps(res, indent=1))
