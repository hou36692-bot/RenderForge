import os, unreal
CASE = os.environ["RF_CASE"]
state = {"t": 0}
def on_tick(dt):
    state["t"] += 1
    t = state["t"]
    if t == 30:
        unreal.EditorLoadingAndSavingUtils.load_map(f"/Game/RenderForge/{CASE}/Maps/Recon")
    if t == 120:
        acts = unreal.EditorLevelLibrary.get_all_level_actors()
        from collections import Counter
        unreal.log("RF_INSPECT classes " + str(Counter(a.get_class().get_name() for a in acts)))
        for a in acts:
            n = a.get_class().get_name()
            if n in ("StaticMeshActor", "CameraActor", "PlayerStart") :
                c = a.get_component_by_class(unreal.StaticMeshComponent) if n == "StaticMeshActor" else None
                sm = c.static_mesh if c else None
                unreal.log(f"RF_INSPECT {n} {a.get_actor_label()} loc={a.get_actor_location()} mesh={sm.get_name() if sm else None} verts={sm.get_num_vertices(0) if sm else None}")
        unreal.unregister_slate_post_tick_callback(handle)
        unreal.SystemLibrary.quit_editor()
handle = unreal.register_slate_post_tick_callback(on_tick)
