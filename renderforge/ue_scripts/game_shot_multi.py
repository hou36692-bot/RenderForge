"""Runs inside a -game session: takes several screenshots at different exposures in one engine start.

Calibrating exposure by launching the game once per candidate costs ~2 minutes each; the level is already loaded
and lit after the first wait, so a sweep belongs in one session. Between shots the RF_PostProcess volume's f-stop
is rewritten (EV100 = log2(fstop^2) with ISO 100 and a 1 s shutter) and the scene is given time to settle, because
the post-process chain applies exposure on the following frames.

The schedule is built up front (tick -> action) instead of being advanced by a state machine: a state machine here
re-triggers its own action on every tick after the first transition, which is how the first version of this file
logged the same set_ev call hundreds of times.

env: RF_CASE, RF_W, RF_H, RF_WAIT_TICKS, RF_SHOT_PREFIX, RF_EVS="10,12,14", RF_CMD, RF_TICKS_PER_EV
"""
import os
import unreal

CASE = os.environ["RF_CASE"]
W = int(os.environ.get("RF_W", "1920"))
H = int(os.environ.get("RF_H", "1080"))
WAIT = int(os.environ.get("RF_WAIT_TICKS", "900"))
PREFIX = os.environ.get("RF_SHOT_PREFIX", "rf_sweep")
EVS = [float(x) for x in os.environ.get("RF_EVS", "10,12,14").split(",") if x.strip()]
SETTLE = int(os.environ.get("RF_TICKS_PER_EV", "60"))
state = {"t": 0}


def world():
    return unreal.load_object(None, f"/Game/RenderForge/{CASE}/Maps/Recon.Recon")


def schedule():
    """tick -> (action, argument). Exposure changes need a few frames to reach the post-process chain, so each EV
    gets a settle window before its shot and one after, keeping the shots from sharing a transition frame."""
    plan = {}
    t = WAIT
    for ev in EVS:
        plan[t] = ("ev", ev)
        plan[t + SETTLE] = ("shot", ev)
        t += SETTLE * 2
    plan[t + 60] = ("quit", None)
    return plan


PLAN = schedule()


def post_process():
    """The unbound volume the importer spawned; its settings are the only exposure control in the level."""
    for a in unreal.GameplayStatics.get_all_actors_of_class(world(), unreal.PostProcessVolume):
        if a.get_actor_label() == "RF_PostProcess":
            return a
    return None


def set_ev(ev):
    pp = post_process()
    if pp is None:
        unreal.log("RF_SWEEP no RF_PostProcess volume")
        return
    fstop = 2.0 ** (ev / 2.0)
    pp.settings.set_editor_property("depth_of_field_fstop", fstop)
    unreal.log(f"RF_SWEEP ev={ev} fstop={fstop:.3f}")


def on_tick(dt):
    state["t"] += 1
    t = state["t"]
    if t == 30:
        unreal.log("RF_SWEEP world " + str(world()))
        pcm = unreal.GameplayStatics.get_player_camera_manager(world(), 0)
        unreal.log(f"RF_SWEEP cam loc={pcm.get_camera_location()} fov={pcm.get_fov_angle()}")
    if t == WAIT - 120 and os.environ.get("RF_CMD"):
        for c in os.environ["RF_CMD"].split(";"):
            unreal.SystemLibrary.execute_console_command(world(), c)
    action = PLAN.get(t)
    if action is None:
        return
    kind, arg = action
    if kind == "ev":
        set_ev(arg)
    elif kind == "shot":
        name = f"{PREFIX}_ev{arg:g}"
        unreal.SystemLibrary.execute_console_command(world(), f"HighResShot {W}x{H} filename=\"{name}\"")
        unreal.log(f"RF_SWEEP shot {name}")
    elif kind == "quit":
        unreal.unregister_slate_post_tick_callback(handle)
        unreal.SystemLibrary.execute_console_command(world(), "quit")


handle = unreal.register_slate_post_tick_callback(on_tick)
