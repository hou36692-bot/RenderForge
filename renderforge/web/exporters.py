"""Export the workbench's assets as files a person can open elsewhere.

Everything here is derived from the Scene Package alone (`build/scene.json` + the mesh files it names): no state is
invented and nothing is re-solved. The point is that the browser can hand back what it is showing -- one mesh as
OBJ/GLB, one texture as PNG, or the whole case as a zip -- instead of the data being viewable but not portable.

Conventions, because they differ between formats and get silently wrong:

  * matrices in scene.json are row-vector (`[x y z 1] @ M`), in the package's own `space` (ue: X forward, Z up,
    centimetres);
  * the package stores UVs top-left origin (D3D). glTF 2.0 also defines (0,0) at the top left, so its UVs are
    copied unchanged; OBJ/MTL are conventionally bottom-left, so V is flipped there (`1 - v`).
"""
import io
import json
import re
import struct
import zipfile
from pathlib import Path

import numpy as np

MAX_ZIP_BYTES = 2_000_000_000        # refuse to build a zip bigger than this; the caller reports why


# ---------------------------------------------------------------- reading a mesh

def mesh_files(scene_dir, scene, mesh_id):
    """(directory, info) for a mesh id, or KeyError.

    The id is looked up in scene.json rather than joined into a path: it arrives from a query string, and the scene's
    own key set is the only thing allowed to name a file."""
    info = (scene.get("meshes") or {}).get(mesh_id)
    if not info:
        raise KeyError(f"no mesh {mesh_id}")
    d = Path(scene_dir) / info["dir"]
    if not (d / "pos.f32").is_file() or not (d / "idx.u32").is_file():
        raise KeyError(f"mesh {mesh_id} has no geometry files")
    return d, info


def load_mesh(scene_dir, scene, mesh_id):
    """(pos float32 (n,3), idx uint32 (m,3), nrm or None, uv or None, info)."""
    d, info = mesh_files(scene_dir, scene, mesh_id)
    pos = np.fromfile(d / "pos.f32", dtype="<f4").reshape(-1, 3)
    idx = np.fromfile(d / "idx.u32", dtype="<u4").reshape(-1, 3)
    n = len(pos)
    if n == 0 or len(idx) == 0:
        raise ValueError(f"mesh {mesh_id} is empty")
    good = (idx < n).all(1) & (idx[:, 0] != idx[:, 1]) & (idx[:, 1] != idx[:, 2]) & (idx[:, 0] != idx[:, 2])
    idx = idx[good]
    nrm = uv = None
    if (d / "nrm.f32").is_file():
        a = np.fromfile(d / "nrm.f32", dtype="<f4")
        if len(a) == n * 3:
            nrm = a.reshape(-1, 3)
    if (d / "uv.f32").is_file():
        a = np.fromfile(d / "uv.f32", dtype="<f4")
        if len(a) == n * 2:
            uv = a.reshape(-1, 2)
    return pos, idx, nrm, uv, info


def mesh_name(info, mesh_id):
    """A filesystem-safe name that says what the asset is, not just which draw it came from."""
    src = (info.get("source") or {})
    obj = src.get("object")
    if obj:
        base = obj.split(".")[-1].split("/")[-1]
        sec = src.get("section")
        return _safe(base + (f"_S{sec}" if sec not in (None, 0) else "")) + f"_{mesh_id}"
    return _safe(mesh_id)


def _safe(s):
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("_")
    return s[:90] or "asset"


# ---------------------------------------------------------------- OBJ

def obj_bytes(pos, idx, nrm, uv, title, comments=()):
    """Wavefront OBJ text. V is flipped: the package's UVs are top-left origin, OBJ's are bottom-left."""
    out = io.StringIO()
    out.write(f"# RenderForge export -- {title}\n")
    for c in comments:
        out.write(f"# {c}\n")
    out.write(f"# {len(pos)} vertices, {len(idx)} triangles, UVs flipped to OBJ's bottom-left origin\n")
    for x, y, z in pos.tolist():
        out.write(f"v {x:.6g} {y:.6g} {z:.6g}\n")
    if uv is not None:
        for u, v in uv.tolist():
            out.write(f"vt {u:.6g} {1.0 - v:.6g}\n")
    if nrm is not None:
        for x, y, z in nrm.tolist():
            out.write(f"vn {x:.6g} {y:.6g} {z:.6g}\n")
    for a, b, c in idx.tolist():
        if uv is not None and nrm is not None:
            out.write(f"f {a + 1}/{a + 1}/{a + 1} {b + 1}/{b + 1}/{b + 1} {c + 1}/{c + 1}/{c + 1}\n")
        elif uv is not None:
            out.write(f"f {a + 1}/{a + 1} {b + 1}/{b + 1} {c + 1}/{c + 1}\n")
        else:
            out.write(f"f {a + 1} {b + 1} {c + 1}\n")
    return out.getvalue().encode("utf-8")


def mtl_bytes(name, base_file=None, normal_file=None, translucent=False, tint=(0.8, 0.8, 0.8)):
    """One MTL material. Map paths are relative (the caller copies textures next to the mesh)."""
    lines = [f"newmtl {name}", "Ka 0 0 0", f"Kd {tint[0]:.3f} {tint[1]:.3f} {tint[2]:.3f}", "Ks 0 0 0",
             f"d {0.85 if translucent else 1.0}", "illum 1"]
    if base_file:
        lines.append(f"map_Kd {base_file}")
        if translucent:
            lines.append(f"map_d {base_file}")
    if normal_file:
        lines.append(f"map_Bump {normal_file}")
    return ("\n".join(lines) + "\n").encode("utf-8")


# ---------------------------------------------------------------- GLB

def _pad4(b, fill=b"\x00"):
    return b + fill * ((-len(b)) % 4)


def glb_bytes(pos, idx, nrm, uv, name):
    """Binary glTF 2.0: one mesh, one primitive, one double-sided grey material.

    UVs are copied unchanged -- glTF's (0,0) is the top-left corner, the same as the scene package's."""
    nv = len(pos)
    pos = np.ascontiguousarray(pos, dtype="<f4")
    normal = np.ascontiguousarray(nrm, dtype="<f4") if nrm is not None else None
    tex = np.ascontiguousarray(uv, dtype="<f4") if uv is not None else None
    wide = int(idx.max()) >= 65536
    ind = np.ascontiguousarray(idx.reshape(-1), dtype="<u4" if wide else "<u2")

    blobs, views, accessors = [], [], []

    def add_accessor(arr_bytes, ctype, typ, count, target, minmax=None):
        off = sum(len(b) for b in blobs)
        pad = (-off) % 4
        if pad:
            blobs.append(b"\x00" * pad)
            off += pad
        blobs.append(arr_bytes)
        views.append({"buffer": 0, "byteOffset": off, "byteLength": len(arr_bytes), "target": target})
        acc = {"bufferView": len(views) - 1, "componentType": ctype, "count": count, "type": typ}
        if minmax:
            acc["min"], acc["max"] = minmax
        accessors.append(acc)
        return len(accessors) - 1

    attributes = {}
    pmin = pos.min(0).astype(float).tolist()
    pmax = pos.max(0).astype(float).tolist()
    attributes["POSITION"] = add_accessor(pos.tobytes(), 5126, "VEC3", nv, 34962, (pmin, pmax))
    if normal is not None:
        attributes["NORMAL"] = add_accessor(normal.tobytes(), 5126, "VEC3", nv, 34962)
    if tex is not None:
        attributes["TEXCOORD_0"] = add_accessor(tex.tobytes(), 5126, "VEC2", nv, 34962)
    attributes_idx = add_accessor(ind.tobytes(), 5125 if wide else 5123, "SCALAR", int(ind.size), 34963)

    blob = b"".join(blobs)
    doc = {
        "asset": {"version": "2.0", "generator": "RenderForge workbench"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name}],
        "meshes": [{"name": name, "primitives": [{"attributes": attributes, "indices": attributes_idx,
                                                  "material": 0}]}],
        "materials": [{"name": "rf_default", "doubleSided": True,
                       "pbrMetallicRoughness": {"baseColorFactor": [0.75, 0.78, 0.82, 1.0],
                                                "metallicFactor": 0.0, "roughnessFactor": 0.8}}],
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [{"byteLength": len(blob)}],
    }
    jb = _pad4(json.dumps(doc, separators=(",", ":")).encode("utf-8"), b" ")
    bb = _pad4(blob, b"\x00")
    total = 12 + 8 + len(jb) + 8 + len(bb)
    return (struct.pack("<III", 0x46546C67, 2, total)
            + struct.pack("<II", len(jb), 0x4E4F534A) + jb
            + struct.pack("<II", len(bb), 0x004E4942) + bb)


# ---------------------------------------------------------------- a whole case

def _tex_path(scene_dir, scene, key, workspace):
    """Absolute path of a texture, only when it is a real file inside the workspace."""
    t = (scene.get("textures") or {}).get(key) or {}
    f = t.get("file")
    if not f:
        return None
    p = Path(f)
    if not p.is_absolute():
        p = Path(scene_dir) / p
    try:
        p = p.resolve()
        p.relative_to(Path(workspace).resolve())
    except (OSError, ValueError):
        return None
    return p if p.is_file() else None


def _mesh_material(scene):
    """mesh id -> the material most of its instances use (a mesh can be drawn with more than one)."""
    counts = {}
    for i in scene.get("instances") or []:
        mk = i.get("material")
        if mk:
            d = counts.setdefault(i["mesh"], {})
            d[mk] = d.get(mk, 0) + 1
    out = {}
    for mid, d in counts.items():
        out[mid] = max(d.items(), key=lambda kv: kv[1])[0]
    return out


def case_zip(scene_dir, scene, case, mesh_ids=None, fmt="obj", include_textures=True,
             workspace=None, progress=None):
    """Build a zip of the case into a BytesIO and return (buffer, manifest).

    Contents: scene.json (verbatim), meshes/<name>.<obj|glb>, materials.mtl (OBJ only), textures/<name>.png,
    INSTANCES.csv, CAMERA.json, REPORT.md, README.txt. `mesh_ids` limits the meshes; None means all of them.
    """
    scene_dir = Path(scene_dir)
    meshes = scene.get("meshes") or {}
    ids = [m for m in (mesh_ids if mesh_ids is not None else list(meshes)) if m in meshes]
    mesh_mat = _mesh_material(scene)
    buf = io.BytesIO()
    manifest = {"case": case, "format": fmt, "meshes": 0, "textures": 0, "bytes": 0,
                "skipped": [], "files": []}
    written_tex = {}
    used_names = set()

    def utf8_zip():
        # ZipInfo timestamps are local; bytes stay deterministic enough for a cache key
        return zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True)

    with utf8_zip() as z:
        z.writestr("scene.json", (scene_dir / "scene.json").read_bytes())
        mtl_lines = []
        needed = set()          # texture keys referenced by the materials of the meshes actually exported
        for n, mid in enumerate(ids):
            try:
                pos, idx, nrm, uv, info = load_mesh(scene_dir, scene, mid)
            except (KeyError, ValueError, OSError) as e:
                manifest["skipped"].append(f"{mid}: {e}")
                continue
            name = mesh_name(info, mid)
            base, k = name, 2
            while name in used_names:                     # two captured sections can share a source name
                name = f"{base}_{k}"
                k += 1
            used_names.add(name)
            src = (info.get("source") or {}).get("object")
            comments = [f"case: {case}", f"mesh id: {mid}", f"source: {src}" if src else "source: unidentified"]
            if fmt == "glb":
                data = glb_bytes(pos, idx, nrm, uv, name)
                z.writestr(f"meshes/{name}.glb", data)
            else:
                data = obj_bytes(pos, idx, nrm, uv, name, comments)
                z.writestr(f"meshes/{name}.obj", data)
                mk = mesh_mat.get(mid)
                if mk and mk in (scene.get("materials") or {}):
                    m = scene["materials"][mk]
                    base_p = _tex_path(scene_dir, scene, m.get("base"), workspace) if include_textures else None
                    nrm_p = _tex_path(scene_dir, scene, m.get("normal"), workspace) if include_textures else None
                    needed.update(k for k, p in ((m.get("base"), base_p), (m.get("normal"), nrm_p)) if p)
                    mtl_lines.append(mtl_bytes(name, base_p and f"textures/{_safe(base_p.stem)}.png",
                                               nrm_p and f"textures/{_safe(nrm_p.stem)}.png",
                                               bool(m.get("translucent"))))
            manifest["meshes"] += 1
            if progress and n % 25 == 0:
                progress(f"{n}/{len(ids)} meshes")
        if fmt != "glb" and mtl_lines:
            z.writestr("materials.mtl", b"\n".join(mtl_lines))
        if include_textures:
            # a subset export ships the maps its meshes reference; a whole-case export ships everything the scene
            # lists (~600 MB of textures for one WuWa frame, so a full dump is deliberately not the default)
            want = list(scene.get("textures") or {}) if mesh_ids is None else sorted(needed)
            for key in want:
                p = _tex_path(scene_dir, scene, key, workspace)
                if not p:
                    continue
                arc = f"textures/{_safe(p.stem)}.png"
                if arc in written_tex:
                    continue
                z.write(p, arc)
                written_tex[arc] = key
                manifest["textures"] += 1
        z.writestr("INSTANCES.csv", _instances_csv(scene))
        z.writestr("CAMERA.json", json.dumps({"camera": scene.get("camera"), "vp": scene.get("vp"),
                                              "space": scene.get("space", "ue")}, indent=1).encode("utf-8"))
        z.writestr("REPORT.md", _report_md(scene, case, fmt))
        z.writestr("README.txt", _readme(case, fmt, include_textures).encode("utf-8"))
        manifest["files"] = sorted(i.filename for i in z.infolist())
    manifest["bytes"] = buf.tell()
    buf.seek(0)
    return buf, manifest


def _instances_csv(scene):
    rows = ["eid,instance,mesh,mesh_source,material,kind,pass,location_x,location_y,location_z,m00,m01,m02,m03,"
            "m10,m11,m12,m13,m20,m21,m22,m23,m30,m31,m32,m33"]
    meshes = scene.get("meshes") or {}
    for i in scene.get("instances") or []:
        src = ((meshes.get(i.get("mesh")) or {}).get("source") or {}).get("object") or ""
        loc = i.get("location") or ["", "", ""]
        m = i.get("matrix") or [""] * 16
        cells = [i.get("eid"), i.get("inst"), i.get("mesh"), src, i.get("material"), i.get("kind"), i.get("pass")]
        cells += list(loc) + list(m)
        rows.append(",".join(_csv(c) for c in cells))
    return ("\n".join(rows) + "\n").encode("utf-8")


def _csv(v):
    s = "" if v is None else str(v)
    return f'"{s}"' if ("," in s or '"' in s) else s


def _report_md(scene, case, fmt):
    rep = scene.get("report") or {}
    ident = scene.get("identity") or {}
    lines = [f"# {case} — RenderForge export", "",
             f"- meshes: {len(scene.get('meshes') or {})}",
             f"- instances: {len(scene.get('instances') or [])}",
             f"- materials: {len(scene.get('materials') or {})}",
             f"- textures: {len(scene.get('textures') or {})}",
             f"- space: `{scene.get('space', 'ue')}`",
             f"- VP fit: {scene.get('vp_match')}", "", "## build report", ""]
    lines += [f"- {k}: {v}" for k, v in rep.items()] or ["- (none)"]
    if ident:
        lines += ["", "## source identity", ""] + [f"- {k}: {v}" for k, v in ident.items()]
    lines += ["", "## conventions", "",
              "- matrices are row-vector: `[x y z 1] @ M`",
              f"- UVs in `.{'glb' if fmt == 'glb' else 'obj'}` keep the package's top-left origin"
              + ("" if fmt == "glb" else " but are V-flipped here, because OBJ is bottom-left")]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _readme(case, fmt, include_textures):
    uv_note = ("glTF also defines top-left, so GLB UVs are unchanged." if fmt == "glb"
               else "OBJ is bottom-left by convention, so V is flipped to 1-v in the .obj/.mtl.")
    lines = [
        f"RenderForge export -- {case}", "",
        "scene.json      the Scene Package this export was made from (row-vector matrices, space as recorded)",
        f"meshes/         one {'GLB' if fmt == 'glb' else 'OBJ'} per captured mesh, named after the source asset",
    ]
    if fmt != "glb":
        lines.append("materials.mtl   one material per mesh, pointing at textures/")
    if include_textures:
        lines.append("textures/       the base/normal maps those materials reference, as PNG")
    lines += [
        "INSTANCES.csv   every placed instance: mesh, material, kind/pass, location, 4x4 world matrix",
        "CAMERA.json     the solved camera and view-projection matrix",
        "REPORT.md       what the build did and skipped", "",
        "Notes",
        "  - Meshes are as authored, not as placed: use INSTANCES.csv for world transforms.",
        f"  - UVs keep the package's top-left (D3D) origin; {uv_note}",
        "  - Materials are preview level (base colour + normal); they are not the game's shaders.",
        "  - Skipped meshes, if any, are listed in the export result in the workbench.",
    ]
    return "\n".join(lines) + "\n"


def single_mesh_export(scene_dir, scene, mesh_id, fmt="obj"):
    """(filename, bytes, content type) for one mesh."""
    pos, idx, nrm, uv, info = load_mesh(scene_dir, scene, mesh_id)
    name = mesh_name(info, mesh_id)
    src = (info.get("source") or {}).get("object")
    comments = [f"mesh id: {mesh_id}", f"source: {src}" if src else "source: unidentified"]
    if fmt == "glb":
        return f"{name}.glb", glb_bytes(pos, idx, nrm, uv, name), "model/gltf-binary"
    return f"{name}.obj", obj_bytes(pos, idx, nrm, uv, name, comments), "text/plain; charset=utf-8"
