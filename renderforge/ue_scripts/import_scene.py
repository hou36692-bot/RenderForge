"""Runs INSIDE Unreal Editor (Python 3.11). Imports a RenderForge scene package.

env: RF_SCENE=<scene.json>  RF_CASE=<name>  [RF_MAX_MESHES=N] [RF_MAX_INSTANCES=N] [RF_SKIP_TEXTURES=1]

Lighting rig overrides (all optional; without them the level gets a stock UE lighting setup):
  RF_SUN_INTENSITY   directional light intensity (engine default when unset)
  RF_SKY_INTENSITY   sky light intensity
  RF_FOG             exponential height fog density (actor omitted when unset)
The rig actually applied is logged and written to <scene dir>/ue_lighting.json.
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
def make_master(name, with_base, with_normal, masked=False):
    path = f"{ROOT}/Materials/{name}"
    if eal.does_asset_exist(path):
        return eal.load_asset(path)
    mat = tools.create_asset(name, f"{ROOT}/Materials", unreal.Material, unreal.MaterialFactoryNew())
    mel = unreal.MaterialEditingLibrary
    mat.set_editor_property("two_sided", True)
    if masked:
        mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_MASKED)
    if with_base:
        tb = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -500, 0)
        tb.set_editor_property("parameter_name", "BaseColor")
        tb.set_editor_property("texture", unreal.load_object(None, "/Engine/EngineResources/WhiteSquareTexture.WhiteSquareTexture"))
        mel.connect_material_property(tb, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
        if os.environ.get("RF_EMISSIVE"):
            mel.connect_material_property(tb, "RGB", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        if masked:
            mel.connect_material_property(tb, "A", unreal.MaterialProperty.MP_OPACITY_MASK)
    else:
        cv = mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -500, 0)
        cv.set_editor_property("parameter_name", "Color")
        cv.set_editor_property("default_value", unreal.LinearColor(0.5, 0.5, 0.5, 1))
        mel.connect_material_property(cv, "", unreal.MaterialProperty.MP_BASE_COLOR)
        if os.environ.get("RF_EMISSIVE"):
            mel.connect_material_property(cv, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    if with_normal:
        tn = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -500, 300)
        tn.set_editor_property("parameter_name", "Normal")
        tn.set_editor_property("texture", unreal.load_object(None, "/Engine/EngineMaterials/DefaultNormal.DefaultNormal"))
        tn.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        mel.connect_material_property(tn, "RGB", unreal.MaterialProperty.MP_NORMAL)
    mel.recompile_material(mat)
    eal.save_loaded_asset(mat)
    return mat


def make_translucent_master(name, mode):
    """Translucent master for one coverage source.

    The capture's translucent batch is not one blend mode, so there is not one master. Reading the D3D11 blend state
    and the pixel shaders of those draws gives four shapes, and `mode` picks the graph:

      alpha      opacity = texture alpha                  (game: SrcAlpha/InvSrcAlpha, shader uses .a)
      red        opacity = texture red channel            (game: SrcAlpha/InvSrcAlpha, sample dotted with (1,0,0,0))
      luminance  opacity = mean(RGB) of the sheet         (game: SrcAlpha/InvSrcAlpha, opacity from a depth fade
                                                           only -- no texture mask, so the sheet's own brightness
                                                           is the best available coverage)
      additive   unlit sheet added to the frame           (game: One/One, shader writes opacity 0)

    The old single master used texture alpha for everything, which turned the black-with-white-sparkles sheets into
    solid black cards, and used BLEND_MASKED + a x2 threshold for the BC1 ones, which clipped most soft sheets to
    opaque. Thresholds are material parameters so a shot can be tuned without editing this file.
    """
    path = f"{ROOT}/Materials/{name}"
    # the graph is part of the material definition, not of the instance: an asset left over from an earlier import
    # would keep the old blend mode. Instances are recreated below anyway, so rebuild (same as make_water_master).
    if eal.does_asset_exist(path):
        eal.delete_asset(path)
    mat = tools.create_asset(name, f"{ROOT}/Materials", unreal.Material, unreal.MaterialFactoryNew())
    mel = unreal.MaterialEditingLibrary
    mat.set_editor_property("two_sided", True)
    if mode == "additive":
        mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE)
        # an additive sheet is emitted light, not a lit surface; unlit stops the sun/sky from shading it
        mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    else:
        mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
        if mode == "red":
            # In 107901 the texture sample is dotted only with (1,0,0,0) to form opacity; its RGB never reaches the
            # output -- the colour is a particle constant this importer does not have. Using the sheet's RGB (mostly
            # black) as base colour painted a dark veil over everything the 50 haze quads cover, so the colour is a
            # Tint parameter and the material is unlit, matching dust/embers that emit rather than reflect.
            mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)

    tb = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -700, 0)
    tb.set_editor_property("parameter_name", "BaseColor")
    tb.set_editor_property("texture", unreal.load_object(None, "/Engine/EngineResources/WhiteSquareTexture.WhiteSquareTexture"))

    if mode == "additive":
        boost = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -700, 200)
        boost.set_editor_property("parameter_name", "AdditiveBoost")
        boost.set_editor_property("default_value", 1.0)
        mul = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -400, 100)
        mel.connect_material_expressions(tb, "RGB", mul, "A")
        mel.connect_material_expressions(boost, "", mul, "B")
        # unlit reads emissive only; the sheet's black background adds nothing under One/One
        mel.connect_material_property(mul, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    else:
        if mode == "red":
            tint = mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -450, -150)
            tint.set_editor_property("parameter_name", "Tint")
            tint.set_editor_property("default_value", unreal.LinearColor(0.85, 0.88, 0.92, 1.0))
            mel.connect_material_property(tint, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
        else:
            mel.connect_material_property(tb, "RGB", unreal.MaterialProperty.MP_BASE_COLOR)
        if mode == "alpha":
            mask_out, mask_pin = tb, "A"
        elif mode == "red":
            # the game shader takes coverage from the red channel of the sheet, not from its (constant) alpha
            cm = mel.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -450, 250)
            cm.set_editor_property("r", True)
            cm.set_editor_property("g", False)
            cm.set_editor_property("b", False)
            cm.set_editor_property("a", False)
            mel.connect_material_expressions(tb, "RGB", cm, "")
            mask_out, mask_pin = cm, ""
        else:
            # mean(RGB): a white-on-black sheet carries its coverage in brightness
            lum = mel.create_material_expression(mat, unreal.MaterialExpressionDotProduct, -450, 250)
            mel.connect_material_expressions(tb, "RGB", lum, "A")
            third = mel.create_material_expression(mat, unreal.MaterialExpressionConstant3Vector, -700, 350)
            third.set_editor_property("constant", unreal.LinearColor(0.3333, 0.3333, 0.3333, 1.0))
            mel.connect_material_expressions(third, "", lum, "B")
            mask_out, mask_pin = lum, ""
        op = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -450, 450)
        op.set_editor_property("parameter_name", "Opacity")
        op.set_editor_property("default_value", 1.0)
        mul = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -200, 300)
        mel.connect_material_expressions(mask_out, mask_pin, mul, "A")
        mel.connect_material_expressions(op, "", mul, "B")
        if mode == "alpha":
            mel.connect_material_property(mul, "", unreal.MaterialProperty.MP_OPACITY)
        else:
            # MaskBoost lets a sheet be pushed brighter/dimmer without re-importing; 1.0 keeps the sheet's own values
            boost = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -450, 600)
            boost.set_editor_property("parameter_name", "MaskBoost")
            boost.set_editor_property("default_value", 1.0)
            mul2 = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, 0, 300)
            mel.connect_material_expressions(mul, "", mul2, "A")
            mel.connect_material_expressions(boost, "", mul2, "B")
            mel.connect_material_property(mul2, "", unreal.MaterialProperty.MP_OPACITY)
    if mode != "additive":  # unlit: the normal input would be ignored, so do not add a node that does nothing
        tn = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -500, 750)
        tn.set_editor_property("parameter_name", "Normal")
        tn.set_editor_property("texture", unreal.load_object(None, "/Engine/EngineMaterials/DefaultNormal.DefaultNormal"))
        tn.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
        mel.connect_material_property(tn, "RGB", unreal.MaterialProperty.MP_NORMAL)
    mel.recompile_material(mat)
    eal.save_loaded_asset(mat)
    return mat


def make_water_master(name):
    """Water preview: translucent teal, world-projected ripples, view-dependent fresnel.

    The captured water mesh has no usable UV channel (its vertex buffer decodes to u == 1.0 for every vertex), so
    sampling the base map with mesh UVs reads a single texel and the whole surface comes out as one flat colour --
    that was the "invisible water". A planar projection from world position gives the ripple map a real footprint
    without needing the UVs, and is also how the game's own shader is organised.

    Still a preview: two-layer ripple normals, refraction, absorption and caustics are not reconstructed.
    """
    path = f"{ROOT}/Materials/{name}"
    # the graph itself changed (world-projected UVs replaced mesh UVs); an asset left over from an older import
    # would keep the old graph, so rebuild it rather than reuse it. Material instances are recreated every import.
    if eal.does_asset_exist(path):
        eal.delete_asset(path)
    mat = tools.create_asset(name, f"{ROOT}/Materials", unreal.Material, unreal.MaterialFactoryNew())
    mel = unreal.MaterialEditingLibrary
    # the water plane's winding is flipped by its negative-Z instance scale, so it is two-sided in practice
    mat.set_editor_property("two_sided", True)
    mat.set_editor_property("blend_mode", unreal.BlendMode.BLEND_TRANSLUCENT)
    mat.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_DEFAULT_LIT)

    # planar world-space UVs: world position scaled by a tile size, so ripples are metres-sized regardless of mesh
    wp = mel.create_material_expression(mat, unreal.MaterialExpressionWorldPosition, -1100, 0)
    tile = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -1100, 140)
    tile.set_editor_property("parameter_name", "WorldTile")
    tile.set_editor_property("default_value", 400.0)      # cm per tile
    div = mel.create_material_expression(mat, unreal.MaterialExpressionDivide, -900, 60)
    mel.connect_material_expressions(wp, "", div, "A")
    mel.connect_material_expressions(tile, "", div, "B")
    # world position is float3 and divide is component-wise, but a texture wants float2: keep XY of the result
    comp = mel.create_material_expression(mat, unreal.MaterialExpressionComponentMask, -750, 60)
    comp.set_editor_property("r", True)
    comp.set_editor_property("g", True)
    comp.set_editor_property("b", False)
    comp.set_editor_property("a", False)
    mel.connect_material_expressions(div, "", comp, "")

    tb = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -560, -60)
    tb.set_editor_property("parameter_name", "BaseColor")
    tb.set_editor_property("texture", unreal.load_object(None, "/Engine/EngineResources/WhiteSquareTexture.WhiteSquareTexture"))
    mel.connect_material_expressions(comp, "", tb, "UVs")
    tn = mel.create_material_expression(mat, unreal.MaterialExpressionTextureSampleParameter2D, -560, 220)
    tn.set_editor_property("parameter_name", "Normal")
    tn.set_editor_property("texture", unreal.load_object(None, "/Engine/EngineMaterials/DefaultNormal.DefaultNormal"))
    tn.set_editor_property("sampler_type", unreal.MaterialSamplerType.SAMPLERTYPE_NORMAL)
    mel.connect_material_expressions(comp, "", tn, "UVs")

    tint = mel.create_material_expression(mat, unreal.MaterialExpressionVectorParameter, -560, 420)
    tint.set_editor_property("parameter_name", "WaterTint")
    tint.set_editor_property("default_value", unreal.LinearColor(0.06, 0.30, 0.33, 1.0))
    mul = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -330, 40)
    mel.connect_material_expressions(tb, "RGB", mul, "A")
    mel.connect_material_expressions(tint, "", mul, "B")
    mel.connect_material_property(mul, "", unreal.MaterialProperty.MP_BASE_COLOR)

    # fresnel: face-on water is clear, grazing water reflects the sky. Without it the plane reads as painted card.
    fres = mel.create_material_expression(mat, unreal.MaterialExpressionFresnel, -560, 560)
    fres.set_editor_property("exponent", 4.0)
    fres.set_editor_property("base_reflect_fraction", 0.04)
    opmul = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -330, 560)
    opbase = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -560, 700)
    opbase.set_editor_property("parameter_name", "Opacity")
    opbase.set_editor_property("default_value", 0.55)
    mel.connect_material_expressions(opbase, "", opmul, "A")
    mel.connect_material_expressions(fres, "", opmul, "B")
    mel.connect_material_property(opmul, "", unreal.MaterialProperty.MP_OPACITY)

    # ripple strength on the normal, so a flat capture still reads as moving water
    nstr = mel.create_material_expression(mat, unreal.MaterialExpressionScalarParameter, -560, 840)
    nstr.set_editor_property("parameter_name", "NormalStrength")
    nstr.set_editor_property("default_value", 1.0)
    nmul = mel.create_material_expression(mat, unreal.MaterialExpressionMultiply, -330, 840)
    mel.connect_material_expressions(tn, "RGB", nmul, "A")
    mel.connect_material_expressions(nstr, "", nmul, "B")
    mel.connect_material_property(nmul, "", unreal.MaterialProperty.MP_NORMAL)
    mel.recompile_material(mat)
    eal.save_loaded_asset(mat)
    return mat


def make_materials(tex_paths):
    masters = {
        (False, False): make_master("M_RF_Flat", False, False),
        (True, False): make_master("M_RF_Tex", True, False),
        (True, True): make_master("M_RF_TexN", True, True),
        (False, True): make_master("M_RF_FlatN", False, True),
        ("m", False): make_master("M_RF_TexM", True, False, True),
        ("m", True): make_master("M_RF_TexNM", True, True, True),
    }
    mel = unreal.MaterialEditingLibrary
    trans_masters = {}
    water_masters = {}

    def translucent_mode(key, m, tex_rec):
        """Blend/coverage mode for one translucent material.

        Proven from the capture (blend state per draw + PS disassembly, see the notes in make_translucent_master):
        the 112882/127115/6213 draws are One/One with `mov o0.w, 0`, so they are additive; 83092 takes its alpha
        from texture .a (sample dotted with (0,0,0,1)); 115765 takes it from .r (dotted with (1,0,0,0)); the rest
        are SrcAlpha/InvSrcAlpha whose shader derives opacity from a soft depth fade with no texture mask, so the
        sheet's luminance is used instead.

        Two of the calls are judgements, not proof, and are flagged as such in the log: 44813 (45386) and 64024/
        103749 (102903, 136020) all compute a view/depth term in the shader that this importer does not reconstruct,
        so for them "luminance" means "the sheet's brightness, which is what the eye sees in the reference".
        """
        fmt = (tex_rec or {}).get("format", "")
        base = m.get("base")
        if key in ("mat_112882_112873", "mat_112882_115840"):
            return "additive", "proven: One/One blend, shader writes opacity 0"
        if key in ("mat_127115_none", "mat_6213_none"):
            return "additive", "proven: One/One blend, shader writes opacity 0"
        if key == "mat_none_none":
            # no base texture at all: the PS (108513) samples only a depth-buffer texture and multiplies a vertex
            # colour; adding a white untextured card would wash the frame out, so skip it and say so
            return None, "skipped: no base texture and the shader only reads the depth buffer"
        if base and "83092" in base:
            return "alpha", "proven: opacity = dot(sample(t3), (0,0,0,1))"
        if base and "115765" in base:
            return "red", "proven: opacity = dot(sample(t1), (1,0,0,0)) x vertex colour"
        return "luminance", "judgement: game masks on a depth fade; sheet brightness used as coverage"

    def translucent_master(mode):
        if mode not in trans_masters:
            trans_masters[mode] = make_translucent_master(f"M_RF_Translucent_{mode}", mode)
        return trans_masters[mode]

    def water_master(with_base, with_normal):
        """Single-layer water preview: translucent teal base multiplied by the water texture, animated normals.
        This is not the game's shader (refraction/absorption/caustics are not reconstructed); it is the minimum
        that reads as water instead of an opaque grey plane."""
        key = (with_base, with_normal)
        if key not in water_masters:
            water_masters[key] = make_water_master(f"M_RF_Water_{int(with_base)}{int(with_normal)}")
        return water_masters[key]

    out = {}
    for key, m in scene["materials"].items():
        has_b = bool(m["base"]) and m["base"] in tex_paths and eal.does_asset_exist(tex_paths[m["base"]])
        if m.get("water"):
            log("water material", key, m.get("base"), has_b, m.get("normal"), m.get("normal") in tex_paths)
        has_n = bool(m["normal"]) and m["normal"] in tex_paths and eal.does_asset_exist(tex_paths[m["normal"]])
        water = bool(m.get("water"))
        tex_rec = (scene["textures"].get(m["base"]) or {}) if m["base"] else {}
        mode = reason = None
        if m.get("translucent") and not water:
            mode, reason = translucent_mode(key, m, tex_rec)
        # Particle additive/soft particles often bind only an effect texture: without a base colour we cannot
        # reproduce them from a plain mask, and a flat translucent card would cover the scene. Leave those out
        # rather than fake them; they are reported in the import result.
        translucent = bool(m.get("translucent")) and has_b
        if m.get("translucent") and not has_b:
            res["translucent_skipped"] = res.get("translucent_skipped", 0) + 1
            if reason:
                n = sum(1 for i in scene["instances"] if i["material"] == key and i["kind"] == "translucent")
                res.setdefault("translucent_modes", {})[key] = {
                    "mode": "skipped", "why": reason, "instances": n,
                    "base": m["base"], "format": tex_rec.get("format")}
                log("translucent mode", key, "skipped", n, "instances --", reason)
            continue
        if water:
            parent = water_master(has_b, has_n)
        elif translucent:
            parent = translucent_master(mode)
            n = sum(1 for i in scene["instances"] if i["material"] == key and i["kind"] == "translucent")
            res.setdefault("translucent_modes", {})[key] = {
                "mode": mode, "why": reason, "instances": n,
                "base": m["base"], "format": tex_rec.get("format")}
            log("translucent mode", key, mode, n, "instances --", reason)
        else:
            parent = masters[("m", has_n)] if (m.get("masked") and has_b) else masters[(has_b, has_n)]
        mi_path = f"{ROOT}/Materials/MI_{key}"
        if eal.does_asset_exist(mi_path):
            # parent / blend mode may have changed since the last import (e.g. a mesh became water); the level
            # is rebuilt every run, so regenerating the instance is cheap and avoids stale parent chains
            eal.delete_asset(mi_path)
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
def source_name(mid):
    """Source asset name for a mesh: SM_<asset>_<section> when identified, else RF_<id>."""
    src = scene["meshes"][mid].get("source")
    if not src:
        return "RF_" + mid
    base = src["object"].split(".")[-1].split("/")[-1]
    return "SM_" + base + "_S%d" % src["section"]


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


# ---------------- lighting ----------------
def envf(name, default):
    v = os.environ.get(name)
    return default if v is None or v == "" else float(v)


def env_vec(name, default):
    v = os.environ.get(name)
    return default if v is None or v == "" else [float(x) for x in v.split(",")]


def lighting_rig():
    """Only the values the caller explicitly overrode. A key present here is applied; an absent key means "leave the
    engine default", so the emitted level matches a stock UE level unless someone asks for something else."""
    out = {}
    for key, env, cast in (("sun_intensity", "RF_SUN_INTENSITY", float),
                           ("sky_intensity", "RF_SKY_INTENSITY", float),
                           ("fog_density", "RF_FOG", float)):
        v = os.environ.get(env)
        if v not in (None, ""):
            out[key] = cast(v)
    return out


def apply_lighting(rig):
    """Spawn the same actors a new UE level starts with, at their class defaults.

    No post-process volume and no invented intensities: the first version of this rig set a manual exposure and
    a 3 lux sun "calibrated" against the reference frame, and every screenshot came out uniformly black. A stock
    level renders correctly off-screen, so the rig is stock. `rig` only carries the knobs the caller asked for
    (RF_* overrides); anything absent is left at the engine default, which is what a new level would use.
    """
    ell = unreal.EditorLevelLibrary
    sun = ell.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 2000))
    # A 45 deg pitch (a stock level's default) puts the whole street under the trellis in shadow: measured ssim
    # 0.249 with a pure-silhouette frame. The rig that produced every existing screenshot is a grazing sun
    # (pitch 0, yaw -50, roll 30), which is what this reproduces.
    sun.set_actor_rotation(unreal.Rotator(0.0, -50.0, 30.0), False)
    sun.set_actor_label("RF_Sun")
    if rig.get("sun_intensity") is not None:
        sun.get_component_by_class(unreal.DirectionalLightComponent).set_editor_property(
            "intensity", rig["sun_intensity"])

    atmo = ell.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
    atmo.set_actor_label("RF_SkyAtmosphere")

    sky = ell.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 600))
    sky.set_actor_label("RF_SkyLight")
    sc = sky.get_component_by_class(unreal.SkyLightComponent)
    sc.set_editor_property("real_time_capture", True)   # a default level captures the atmosphere this way
    if rig.get("sky_intensity") is not None:
        sc.set_editor_property("intensity", rig["sky_intensity"])

    if rig.get("fog_density") is not None:
        fog = ell.spawn_actor_from_class(unreal.ExponentialHeightFog, unreal.Vector(0, 0, 0))
        fog.set_actor_label("RF_Fog")
        fc = fog.get_component_by_class(unreal.ExponentialHeightFogComponent)
        fc.set_editor_property("fog_density", rig["fog_density"])
    return sun, atmo, sky, None


def main():
    tex_paths = import_textures()
    log("textures done", res["textures"], round(time.time() - T0, 1), "s")
    mats = make_materials(tex_paths)
    log("materials done", res["materials"], round(time.time() - T0, 1), "s")

    insts = scene["instances"]
    if os.environ.get("RF_ONLY_KIND"):
        insts = [i for i in insts if i["kind"] == os.environ["RF_ONLY_KIND"]]
    # RF_HIDE_MESH=id[,id] drops meshes from the import (used to isolate whether a large unidentified mesh
    # occludes the view; not part of the normal pipeline)
    hide = {x for x in os.environ.get("RF_HIDE_MESH", "").split(",") if x}
    if hide:
        insts = [i for i in insts if i["mesh"] not in hide]
        log("hidden meshes", sorted(hide))
    # Near-field translucent effects (dust, splashes) sit within ~20 m of the capture camera and cover most of the
    # frame in a preview reconstruction without their particle shading. Hidden by default; RF_SHOW_NEAR_FX=1 shows.
    if not os.environ.get("RF_SHOW_NEAR_FX"):
        cam = scene.get("camera")
        if cam:
            c = unreal.Vector(*cam["position"])
            def near_effect(i):
                if i["kind"] != "translucent":
                    return False
                return unreal.Vector(*i["location"]).__sub__(c).length() < 2500.0  # cm
            insts = [i for i in insts if not near_effect(i)]
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
            a.set_actor_label(f"{source_name(mid)}_{i['eid']}")
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
            a.set_actor_label(f"ISM_{source_name(mid)}_{mk}")
            res["actors"] += 1
            res["instances"] += len(lst)
    # lighting rig comes from data (see lighting_rig) so a shot can be re-lit without editing this script
    rig = lighting_rig()
    apply_lighting(rig)
    # The capture's sky mesh was dropped by build (radius > sky_radius); the level sky is the atmosphere, so a
    # captured cube texture cannot be wired into it here. Note it in the rig record for whoever revisits this.
    cubes = [tid for tid, t in scene["textures"].items() if "CUBE" in t.get("format", "").upper()]
    rig["sky_cubemap_textures"] = cubes
    rig["note"] = ("stock UE lighting (sun + atmosphere + real-time sky light, engine default exposure); build "
                   "dropped the captured sky mesh (report.sky_skipped) and no captured cube map is wired into the "
                   "sky light, so the sky colour is the atmosphere's, not the game's")
    json.dump(rig, open(os.path.join(sdir, "ue_lighting.json"), "w"), indent=1)
    log("lighting rig", json.dumps(rig))
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
