"""Runs inside the full Unreal Editor (RHI, off-screen). Loads the reconstructed level, points the editor viewport at
RF_Camera and saves a screenshot.

env: RF_CASE, RF_OUT (png path), RF_W, RF_H, RF_WAIT_TICKS, RF_SHADING (lit|unlit|basecolor)
"""
import os
import unreal

CASE = os.environ["RF_CASE"]
OUT = os.environ["RF_OUT"]
W = int(os.environ.get("RF_W", "1920"))
H = int(os.environ.get("RF_H", "1080"))
WAIT = int(os.environ.get("RF_WAIT_TICKS", "900"))
SHADING = os.environ.get("RF_SHADING", "lit")
state = {"t": 0, "cam": None}


def console(cmd):
    unreal.SystemLibrary.execute_console_command(unreal.EditorLevelLibrary.get_editor_world(), cmd)


def on_tick(dt):
    state["t"] += 1
    t = state["t"]
    if t == 30:
        unreal.EditorLoadingAndSavingUtils.load_map(f"/Game/RenderForge/{CASE}/Maps/Recon")
    if t == 60:
        for a in unreal.EditorLevelLibrary.get_all_level_actors():
            if a.get_actor_label() == "RF_Camera":
                state["cam"] = a
        cam = state["cam"]
        unreal.log("RF_SHOT camera " + str(cam))
        if cam is not None:
            unreal.EditorLevelLibrary.set_level_viewport_camera_info(cam.get_actor_location(), cam.get_actor_rotation())
            fov = cam.get_component_by_class(unreal.CameraComponent).get_editor_property("field_of_view")
            console(f"FOV {fov}")
            if SHADING == "unlit":
                console("viewmode unlit")
            elif SHADING == "basecolor":
                console("viewmode visualizebuffer BaseColor")
    if t == WAIT:
        unreal.AutomationLibrary.take_high_res_screenshot(W, H, OUT, state["cam"], False, False, unreal.ComparisonTolerance.LOW, "", 0.0, False)
        unreal.log("RF_SHOT requested " + OUT)
    if t >= WAIT + 200:
        unreal.unregister_slate_post_tick_callback(handle)
        unreal.SystemLibrary.quit_editor()


handle = unreal.register_slate_post_tick_callback(on_tick)
