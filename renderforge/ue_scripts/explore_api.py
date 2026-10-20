import unreal
print("CLS", [x for x in dir(unreal) if ("MeshDescription" in x or "MeshOperations" in x or "GeometryScript" in x and "Mesh" in x and "Normal" in x)][:40])
print("SMEL", [x for x in dir(unreal.StaticMeshEditorSubsystem) if not x.startswith("_")][:60])
print("GSN", [x for x in dir(unreal) if "GeometryScriptLibrary_MeshNormals" in x or "GeometryScriptLibrary_MeshBasicEdit" in x or "GeometryScriptLibrary_StaticMesh" in x])
