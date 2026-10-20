"""Runs inside a -game session: waits for the scene to stream/compile, issues HighResShot, quits.
env: RF_CASE, RF_W, RF_H, RF_WAIT_TICKS, RF_SHOT_NAME"""
import os
import unreal

CASE = os.environ["RF_CASE"]
W = int(os.environ.get("RF_W", "1920"))
H = int(os.environ.get("RF_H", "1080"))
WAIT = int(os.environ.get("RF_WAIT_TICKS", "600"))
NAME = os.environ.get("RF_SHOT_NAME", "rf_shot")
state = {"t": 0}


def world():
    return unreal.load_object(None, f"/Game/RenderForge/{CASE}/Maps/Recon.Recon")


def on_tick(dt):
    state["t"] += 1
    t = state["t"]
    if t == 30:
        unreal.log("RF_GAMESHOT world " + str(world()))
    if t == WAIT - 100 and os.environ.get("RF_CMD"):
        for c in os.environ["RF_CMD"].split(";"):
            unreal.SystemLibrary.execute_console_command(world(), c)
    if t == WAIT - 50:
        try:
            pcm = unreal.GameplayStatics.get_player_camera_manager(world(), 0)
            unreal.log(f"RF_GAMESHOT cam loc={pcm.get_camera_location()} rot={pcm.get_camera_rotation()} fov={pcm.get_fov_angle()}")
            pawn = unreal.GameplayStatics.get_player_pawn(world(), 0)
            unreal.log(f"RF_GAMESHOT pawn={pawn} loc={pawn.get_actor_location() if pawn else None}")
        except Exception as e:
            unreal.log("RF_GAMESHOT camlog fail " + str(e))
    if t == WAIT:
        unreal.SystemLibrary.execute_console_command(world(), f"HighResShot {W}x{H} filename=\"{NAME}\"")
        unreal.log("RF_GAMESHOT issued")
    if t >= WAIT + 120:
        unreal.unregister_slate_post_tick_callback(handle)
        unreal.SystemLibrary.execute_console_command(world(), "quit")


handle = unreal.register_slate_post_tick_callback(on_tick)
