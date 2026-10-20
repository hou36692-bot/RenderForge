import unreal
ell = unreal.EditorLevelLibrary
world = unreal.EditorLoadingAndSavingUtils.new_blank_map(False)
cube = unreal.EditorAssetLibrary.load_asset("/Engine/BasicShapes/Cube")
c = ell.spawn_actor_from_object(cube, unreal.Vector(800, 0, 0))
c.set_actor_scale3d(unreal.Vector(3, 3, 3))
sun = ell.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 1000))
sun.set_actor_rotation(unreal.Rotator(0, -50, 30), False)
ell.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0))
sky = ell.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 500))
ps = ell.spawn_actor_from_class(unreal.PlayerStart, unreal.Vector(0, 0, 100))
unreal.log("RF_TEST saved " + str(unreal.EditorLoadingAndSavingUtils.save_map(world, "/Game/RenderForge/_test/Maps/Recon")))
