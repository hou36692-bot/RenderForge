"""Runs INSIDE Unreal Editor (Python 3.11). Imports a RenderForge scene package.

env: RF_SCENE=<scene.json>  RF_CASE=<name>  [RF_MAX_MESHES=N] [RF_MAX_INSTANCES=N] [RF_SKIP_TEXTURES=1]
"""
import json
import os
import struct
import time
import traceback
from array import array

import unreal

SCENE = os.environ["RF_SCENE"]
CASE = os.environ.get("RF_CASE", "case")
MAX_MESHES = int(os.environ.get("RF_MAX_MESHES", "0")) or None
MAX_INST = int(os.environ.get("RF_MAX_INSTANCES", "0")) or None
ROOT = "/Game/RenderForge/" + CASE
RESULT = os.path.join(os.path.dirname(SCENE), "ue_import_result.json")
T0 = time.time()


def log(*a):
    unreal.log("RF " + " ".join(str(x) for x in a))


def read_f32(path):
    a = array("f")
    with open(path, "rb") as f:
        a.frombytes(f.read())
    return a


def read_u32(path):
    a = array("I")
    with open(path, "rb") as f:
        a.frombytes(f.read())
    return a


tools = unreal.AssetToolsHelpers.get_asset_tools()
sm_sub = unreal.get_editor_subsystem(unreal.StaticMeshEditorSubsystem)
eal = unreal.EditorAssetLibrary
scene = json.load(open(SCENE, encoding="utf-8"))
sdir = os.path.dirname(SCENE)
res = {"textures": 0, "meshes": 0, "mesh_fail": 0, "materials": 0, "actors": 0, "instances": 0}


# ---------------- textures ----------------
def import_textures():
    tasks, names = [], {}
    for tid, t in scene["textures"].items():
        name = "T_" + tid.split("::")[-1]
        names[tid] = f"{ROOT}/Textures/{name}"
        if eal.does_asset_exist(names[tid]):
            continue
        task = unreal.AssetImportTask()
        task.set_editor_property("filename", t["file"])
        task.set_editor_property("destination_path", f"{ROOT}/Textures")
        task.set_editor_property("destination_name", name)
        task.set_editor_property("automated", True)
        task.set_editor_property("save", False)
        task.set_editor_property("replace_existing", True)
        tasks.append(task)
    if tasks and not os.environ.get("RF_SKIP_TEXTURES"):
        tools.import_asset_tasks(tasks)
    for tid, t in scene["textures"].items():
        tex = eal.load_asset(names[tid]) if eal.does_asset_exist(names[tid]) else None
        if tex is None:
            continue
        fmt = t["format"]
        if fmt.startswith("BC5"):
            tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_NORMALMAP)
            tex.set_editor_property("srgb", False)
        elif "SRGB" in fmt:
            tex.set_editor_property("srgb", True)
        else:
            tex.set_editor_property("srgb", False)
            tex.set_editor_property("compression_settings", unreal.TextureCompressionSettings.TC_MASKS)
        res["textures"] += 1
    return names


# ---------------- materials ----------------
def make_master(name, with_base, with_normal):
    path = f"{ROOT}/Materials/{name}"
    if eal.does_asset_exist(path):
        return eal.load_asset(path)
    mat = tools.create_asset(name, f"{ROOT}/Materials", unreal.Material, unreal.MaterialFactoryNew())
    mel = unreal.MaterialEditingLibrary
    mat.set_editor_property("two_sided", True)
    if with_base:
        tb = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -500, 0)
        tb.set_editor_property("parameter_name", "BaseColor")
        tb.set_editor_property("texture", unreal.load_object(None, "/Engine/EngineResources/WhiteSquareTexture.WhiteSquareTexture"))
        mel.connect_material_property(tb, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
    else:
        cv = mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -500, 0)
        cv.set_editor_property("parameter_name", "Color")
        cv.set_editor_property("default_value", unreal.LinearColor(0.5, 0.5, 0.5, 1))
        mel.connect_material_property(cv, "", unreal.MaterialProperty.MP_BASE_COLOR)
    if with_normal:
        tn = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -500, 300)
        tn.set_editor_property("parameter_name", "Normal")
        tn.set_editor_property("texture", unreal.load_object(None, "/Engine/EngineMaterials/DefaultNormal.DefaultNormal"))
        tn.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        mel.connect_material_property(tn, "RGB", unreal.MaterialProperty.MP_NORMAL)
    mel.recompile_material(mat)
    eal.save_loaded_asset(mat)
    return mat


def make_materials(tex_paths):
    masters = {
        (False, False): make_master("M_RF_Flat", False, False),
        (True, False): make_master("M_RF_Tex", True, False),
        (True, True): make_master("M_RF_TexN", True, True),
        (False, True): make_master("M_RF_FlatN", False, True),
    }
    mel = unreal.MaterialEditingLibrary
    out = {}
    for key, m in scene["materials"].items():
        has_b = bool(m["base"]) and m["base"] in tex_paths and eal.does_asset_exist(tex_paths[m["base"]])
        has_n = bool(m["normal"]) and m["normal"] in tex_paths and eal.does_asset_exist(tex_paths[m["normal"]])
        parent = masters[(has_b, has_n)]
        mi_path = f"{ROOT}/Materials/MI_{key}"
        if eal.does_asset_exist(mi_path):
            out[key] = eal.load_asset(mi_path)
            continue
        mi = tools.create_asset(f"MI_{key}", f"{ROOT}/Materials", unreal.MaterialInstanceConstant,
                                unreal.MaterialInstanceConstantFactoryNew())
        mel.set_material_instance_parent(mi, parent)
        if has_b:
            mel.set_material_instance_texture_parameter_value(mi, "BaseColor", eal.load_asset(tex_paths[m["base"]]))
        if has_n:
            mel.set_material_instance_texture_parameter_value(mi, "Normal", eal.load_asset(tex_paths[m["normal"]]))
        out[key] = mi
        res["materials"] += 1
    return out


# ---------------- meshes ----------------
def make_mesh(mid):
    info = scene["meshes"][mid]
    d = os.path.join(sdir, info["dir"])
    pos = read_f32(os.path.join(d, "pos.f32"))
    idx = read_u32(os.path.join(d, "idx.u32"))
    nrm = read_f32(os.path.join(d, "nrm.f32")) if info["has_nrm"] else None
    uv = read_f32(os.path.join(d, "uv.f32")) if info["has_uv"] else None
    n = len(pos) // 3
    path = f"{ROOT}/Meshes/SM_{mid}"
    if eal.does_asset_exist(path):
        return eal.load_asset(path)
    sm = eal.duplicate_asset("/Engine/BasicShapes/Cube", path)
    smd = sm.create_static_mesh_description(sm)
    smd.reserve_new_vertices(n)
    smd.reserve_new_vertex_instances(n)
    smd.reserve_new_triangles(len(idx) // 3)
    pg = smd.create_polygon_group()
    vis = []
    for i in range(n):
        v = smd.create_vertex()
        smd.set_vertex_position(v, unreal.Vector(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]))
        vi = smd.create_vertex_instance(v)
        if uv is not None:
            smd.set_vertex_instance_uv(vi, unreal.Vector2D(uv[2 * i], uv[2 * i + 1]), 0)
        vis.append(vi)
    for t in range(0, len(idx) - 2, 3):
        a, b, c = idx[t], idx[t + 1], idx[t + 2]
        if a == b or b == c or a == c:
            continue
        smd.create_triangle(pg, [vis[a], vis[b], vis[c]])
    sm.build_from_static_mesh_descriptions([smd])
    bs = sm_sub.get_lod_build_settings(sm, 0)
    bs.set_editor_property("recompute_normals", True)
    bs.set_editor_property("recompute_tangents", True)
    bs.set_editor_property("use_mikk_t_space", True)
    sm_sub.set_lod_build_settings(sm, 0, bs)
    return sm


def ensure_ism_blueprint():
    path = f"{ROOT}/Blueprints/BP_RF_ISM"
    if eal.does_asset_exist(path):
        return eal.load_asset(path)
    fac = unreal.BlueprintFactory()
    fac.set_editor_property("parent_class", unreal.Actor)
    bp = tools.create_asset("BP_RF_ISM", f"{ROOT}/Blueprints", unreal.Blueprint, fac)
    sub = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)
    handles = sub.k2_gather_subobject_data_for_blueprint(bp)
    params = unreal.AddNewSubobjectParams(parent_handle=handles[0], new_class=unreal.InstancedStaticMeshComponent,
                                          blueprint_context=bp)
    new_handle, fail = sub.add_new_subobject(params)
    sub.rename_subobject(new_handle, "ISM")
    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
    eal.save_loaded_asset(bp)
    return bp


def main():
    tex_paths = import_textures()
    log("textures done", res["textures"], round(time.time() - T0, 1), "s")
    mats = make_materials(tex_paths)
    log("materials done", res["materials"], round(time.time() - T0, 1), "s")

    insts = scene["instances"]
    if os.environ.get("RF_ONLY_KIND"):
        insts = [i for i in insts if i["kind"] == os.environ["RF_ONLY_KIND"]]
    used = []
    for i in insts:
        if i["mesh"] not in used:
            used.append(i["mesh"])
    if MAX_MESHES:
        used = used[:MAX_MESHES]
    meshes = {}
    for k, mid in enumerate(used):
        try:
            meshes[mid] = make_mesh(mid)
            res["meshes"] += 1
        except Exception:
            res["mesh_fail"] += 1
            log("mesh fail", mid, traceback.format_exc())
        if k % 50 == 0:
            log("meshes", k, "/", len(used), round(time.time() - T0, 1), "s")

    ism_bp = ensure_ism_blueprint()
    map_path = f"{ROOT}/Maps/Recon"
    if eal.does_asset_exist(map_path):
        eal.delete_asset(map_path)
    world = unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
    log("new_blank_map", world)
    groups = {}
    for i in insts:
        if i["mesh"] in meshes:
            groups.setdefault((i["mesh"], i["material"]), []).append(i)
    for (mid, mk), lst in groups.items():
        if MAX_INST:
            lst = lst[:MAX_INST]
        mesh = meshes[mid]
        mat = mats.get(mk)
        if len(lst) == 1:
            a = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.StaticMeshActor, unreal.Vector(0, 0, 0))
            comp = a.static_mesh_component
            comp.set_static_mesh(mesh)
            if mat:
                comp.set_material(0, mat)
            i = lst[0]
            q = unreal.Quat(*i["quat"])
            a.set_actor_transform(unreal.Transform(unreal.Vector(*i["location"]), q.rotator(), unreal.Vector(*i["scale"])), False, False)
            a.set_actor_label(f"{mid}_{i['eid']}")
            res["actors"] += 1
            res["instances"] += 1
        else:
            a = unreal.EditorLevelLibrary.spawn_actor_from_object(ism_bp, unreal.Vector(0, 0, 0))
            comp = a.get_component_by_class(unreal.InstancedStaticMeshComponent)
            comp.set_static_mesh(mesh)
            if mat:
                comp.set_material(0, mat)
            tfs = []
            for i in lst:
                q = unreal.Quat(*i["quat"])
                tfs.append(unreal.Transform(unreal.Vector(*i["location"]), q.rotator(), unreal.Vector(*i["scale"])))
            comp.add_instances(tfs, False, True)
            a.set_actor_label(f"ISM_{mid}_{mk}")
            res["actors"] += 1
            res["instances"] += len(lst)
    # basic lighting so the level is viewable
    ell = unreal.EditorLevelLibrary
    sun = ell.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 1000))
    sun.set_actor_rotation(unreal.Rotator(0, -50, 30), False)
    sun.set_actor_label("RF_Sun")
    ell.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
    sky = ell.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 500))
    sky.get_component_by_class(unreal.SkyLightComponent).set_editor_property("real_time_capture", True)
    # camera
    cam = scene.get("camera")
    if cam:
        ca = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.CameraActor, unreal.Vector(*cam["position"]))
        rot = unreal.MathLibrary.make_rot_from_xz(unreal.Vector(*cam["forward"]), unreal.Vector(*cam["up"]))
        ca.set_actor_rotation(rot, False)
        ca.get_component_by_class(unreal.CameraComponent).set_editor_property("field_of_view", cam["hfov_deg"])
        ca.set_editor_property("auto_activate_for_player", unreal.AutoReceiveInput.PLAYER0)
        ca.set_actor_label("RF_Camera")
        ps = unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.PlayerStart, unreal.Vector(*cam["position"]))
        ps.set_actor_rotation(rot, False)
        ps.set_actor_label("RF_PlayerStart")
        if os.environ.get("RF_DEBUG_CUBE"):
            fwd = ca.get_actor_forward_vector()
            log("camera fwd actual", fwd, "wanted", cam["forward"], "up actual", ca.get_actor_up_vector(), "wanted", cam["up"])
            cube = eal.load_asset("/Engine/BasicShapes/Cube")
            c = unreal.EditorLevelLibrary.spawn_actor_from_object(cube, ca.get_actor_location() + fwd * 800)
            c.set_actor_scale3d(unreal.Vector(2, 2, 2))
    log('save level', unreal.EditorLoadingAndSavingUtils.save_map(world, map_path))
    unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)


try:
    main()
    res["ok"] = True
except Exception:
    res["ok"] = False
    res["error"] = traceback.format_exc()
    log(res["error"])
res["seconds"] = round(time.time() - T0, 1)
json.dump(res, open(RESULT, "w"), indent=1)
log("DONE", json.dumps(res))
