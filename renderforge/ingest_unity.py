"""Source-asset route for Unity: read a UnityFS bundle / serialized file with UnityPy and write a RenderForge Scene Package
(no GPU capture involved). Handles Transform hierarchies, MeshFilter+MeshRenderer, Mesh, Material (main/bump texture), Texture2D.

Scene package space is "unity" (left-handed, Y up, metres), UVs are written in D3D convention (V flipped from Unity's).
"""
import json
import shutil
from pathlib import Path

import numpy as np
import UnityPy
from UnityPy.helpers.MeshHelper import MeshHandler


def _quat_to_mat(q):
    """Rotation matrix of a quaternion in row-vector convention (p' = p @ R)."""
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y + z * w), 2 * (x * z - y * w)],
                     [2 * (x * y - z * w), 1 - 2 * (x * x + z * z), 2 * (y * z + x * w)],
                     [2 * (x * z + y * w), 2 * (y * z - x * w), 1 - 2 * (x * x + y * y)]])


def _local_matrix(tr):
    """Row-vector 4x4 local matrix of a Unity Transform (scale, then rotation, then translation)."""
    s = np.array([tr.m_LocalScale.x, tr.m_LocalScale.y, tr.m_LocalScale.z])
    q = (tr.m_LocalRotation.x, tr.m_LocalRotation.y, tr.m_LocalRotation.z, tr.m_LocalRotation.w)
    M = np.eye(4)
    M[:3, :3] = np.diag(s) @ _quat_to_mat(q)   # _quat_to_mat already returns the row-vector form
    M[3, :3] = [tr.m_LocalPosition.x, tr.m_LocalPosition.y, tr.m_LocalPosition.z]
    return M


def ingest_bundle(bundle_path, out_dir):
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    (out / "meshes").mkdir(parents=True)
    (out / "textures").mkdir()
    env = UnityPy.load(str(bundle_path))
    objs = {(o.assets_file.name, o.path_id): o for o in env.objects}

    def rd(o):
        return o.parse_as_object() if hasattr(o, "parse_as_object") else o.read()

    def deref(ptr):
        if ptr is None or getattr(ptr, "path_id", 0) == 0:
            return None
        o = ptr.deref() if hasattr(ptr, "deref") else None
        return o

    scene = {"space": "unity", "meshes": {}, "materials": {}, "instances": [], "textures": {}, "camera": None, "vp": None, "vp_match": 0.0}
    report = {"gameobjects": 0, "placed": 0, "skipped_no_mesh": 0, "meshes": 0}
    tex_done, mesh_done = {}, {}

    def export_texture(tex_obj):
        key = (tex_obj.assets_file.name, tex_obj.path_id)
        if key in tex_done:
            return tex_done[key]
        d = rd(tex_obj)
        tid = f"tex_{len(tex_done):04d}_{d.m_Name}".replace(" ", "_")
        path = out / "textures" / f"{tid}.png"
        d.image.convert("RGBA").save(path)
        scene["textures"][tid] = {"file": str(path), "w": d.m_Width, "h": d.m_Height, "format": "R8G8B8A8_UNORM_SRGB"}
        tex_done[key] = tid
        return tid

    def export_mesh(mesh_obj):
        key = (mesh_obj.assets_file.name, mesh_obj.path_id)
        if key in mesh_done:
            return mesh_done[key]
        d = rd(mesh_obj)
        mh = MeshHandler(d)
        mh.process()
        pos = np.asarray(mh.m_Vertices, dtype=np.float32).reshape(mh.m_VertexCount, -1)[:, :3]
        idx = np.asarray(mh.m_IndexBuffer, dtype=np.uint32) if getattr(mh, "m_IndexBuffer", None) is not None else None
        if idx is None or len(idx) == 0:
            return None
        mid = f"u{len(mesh_done):04d}"
        md = out / "meshes" / mid
        md.mkdir()
        pos.astype("<f4").tofile(md / "pos.f32")
        idx.astype("<u4").tofile(md / "idx.u32")
        has_n = mh.m_Normals is not None and len(mh.m_Normals) == len(pos)
        if has_n:
            np.asarray(mh.m_Normals, dtype=np.float32).reshape(len(pos), -1)[:, :3].astype("<f4").tofile(md / "nrm.f32")
        has_uv = mh.m_UV0 is not None and len(mh.m_UV0) == len(pos)
        if has_uv:
            uv = np.asarray(mh.m_UV0, dtype=np.float32).reshape(len(pos), -1)[:, :2].copy()
            uv[:, 1] = 1.0 - uv[:, 1]   # Unity bottom-left -> D3D top-left
            uv.astype("<f4").tofile(md / "uv.f32")
        scene["meshes"][mid] = {"dir": f"meshes/{mid}", "verts": int(len(pos)), "tris": int(len(idx) // 3), "has_nrm": bool(has_n), "has_uv": bool(has_uv), "source": {"name": d.m_Name}}
        mesh_done[key] = mid
        report["meshes"] += 1
        return mid

    mats = {}

    def export_material(mat_obj):
        if mat_obj is None:
            return "mat_none"
        key = (mat_obj.assets_file.name, mat_obj.path_id)
        if key in mats:
            return mats[key]
        d = rd(mat_obj)
        base = normal = None
        try:
            for name, tex in d.m_SavedProperties.m_TexEnvs:
                t = deref(tex.m_Texture)
                if t is None or t.type.name != "Texture2D":
                    continue
                if name == "_MainTex" and base is None:
                    base = export_texture(t)
                elif name == "_BumpMap":
                    normal = export_texture(t)
        except Exception:  # noqa: BLE001
            pass
        mid = f"mat_{len(mats):03d}_{d.m_Name}".replace(" ", "_")
        scene["materials"][mid] = {"base": base, "normal": normal, "masked": False}
        mats[key] = mid
        return mid

    # world matrices by Transform path
    transforms = {}
    for (fname, pid), o in objs.items():
        if o.type.name == "Transform":
            transforms[(fname, pid)] = rd(o)

    def world(key):
        tr = transforms[key]
        M = _local_matrix(tr)
        parent = deref(tr.m_Father) if hasattr(tr, "m_Father") else None
        if parent is not None and (parent.assets_file.name, parent.path_id) in transforms:
            return M @ world((parent.assets_file.name, parent.path_id))
        return M

    for (fname, pid), o in objs.items():
        if o.type.name != "GameObject":
            continue
        report["gameobjects"] += 1
        go = rd(o)
        mf = mr = tr_key = None
        for comp in go.m_Component:
            c = deref(comp.component) if hasattr(comp, "component") else deref(comp[1] if isinstance(comp, tuple) else comp)
            if c is None:
                continue
            if c.type.name == "MeshFilter":
                mf = rd(c)
            elif c.type.name in ("MeshRenderer", "SkinnedMeshRenderer"):
                mr = rd(c)
            elif c.type.name == "Transform":
                tr_key = (c.assets_file.name, c.path_id)
        if mf is None or mr is None or tr_key is None:
            report["skipped_no_mesh"] += 1
            continue
        mesh_o = deref(mf.m_Mesh)
        mid = export_mesh(mesh_o) if mesh_o is not None else None
        if mid is None:
            report["skipped_no_mesh"] += 1
            continue
        mat_o = deref(mr.m_Materials[0]) if mr.m_Materials else None
        W = world(tr_key)
        scene["instances"].append({"eid": pid, "inst": 0, "mesh": mid, "matrix": W.flatten().tolist(), "location": W[3, :3].tolist(),
                                   "material": export_material(mat_o), "kind": "unity-source", "name": go.m_Name})
        report["placed"] += 1
    scene["report"] = report
    (out / "scene.json").write_text(json.dumps(scene, indent=1), encoding="utf-8")
    return scene
