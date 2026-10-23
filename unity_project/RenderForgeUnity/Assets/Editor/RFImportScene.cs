using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEditor.SceneManagement;

// Imports a RenderForge Unity package (unity_scene.json + raw mesh arrays) into the project and builds a scene.
public static class RFImportScene
{
    [Serializable] class MeshInfo { public string id; public int verts; public int indices; public bool hasNormals; public bool hasUV; }
    [Serializable] class TexInfo { public string id; public string file; public string format; }
    [Serializable] class MatInfo { public string id; public string baseTexture; public string normalTexture; public bool masked; }
    [Serializable] class InstInfo { public string mesh; public string material; public float[] m; }
    [Serializable] class CamInfo { public float[] position; public float[] forward; public float[] up; public float vfov; public float hfov; }
    [Serializable] class Pkg { public string @case; public MeshInfo[] meshes; public TexInfo[] textures; public MatInfo[] materials; public InstInfo[] instances; public CamInfo camera; }

    static float[] ReadF32(string path) { var b = File.ReadAllBytes(path); var f = new float[b.Length / 4]; Buffer.BlockCopy(b, 0, f, 0, b.Length); return f; }
    static int[] ReadI32(string path) { var b = File.ReadAllBytes(path); var f = new int[b.Length / 4]; Buffer.BlockCopy(b, 0, f, 0, b.Length); return f; }

    public static void Run()
    {
        string pkgDir = Environment.GetEnvironmentVariable("RF_UNITY_PKG");
        string result = Path.Combine(pkgDir, "unity_import_result.json");
        try { Import(pkgDir, result); }
        catch (Exception e) { File.WriteAllText(result, "{\"ok\":false,\"error\":" + JsonUtility.ToJson(e.ToString()) + "}"); Debug.LogError("RF_IMPORT_FAIL " + e); EditorApplication.Exit(2); }
    }

    static void Import(string pkgDir, string resultPath)
    {
        var pkg = JsonUtility.FromJson<Pkg>(File.ReadAllText(Path.Combine(pkgDir, "unity_scene.json")));
        string root = "Assets/RF/" + pkg.@case;
        if (AssetDatabase.IsValidFolder(root)) AssetDatabase.DeleteAsset(root);
        foreach (var d in new[] { "Assets/RF", root, root + "/Meshes", root + "/Textures", root + "/Materials" })
            if (!AssetDatabase.IsValidFolder(d)) AssetDatabase.CreateFolder(Path.GetDirectoryName(d).Replace('\\', '/'), Path.GetFileName(d));

        // textures
        AssetDatabase.StartAssetEditing();
        foreach (var t in pkg.textures)
        {
            if (string.IsNullOrEmpty(t.file) || !File.Exists(t.file)) continue;
            File.Copy(t.file, Path.Combine(root, "Textures", t.id + ".png"), true);
        }
        AssetDatabase.StopAssetEditing();
        AssetDatabase.Refresh();
        var normalIds = new HashSet<string>();
        foreach (var m in pkg.materials) if (!string.IsNullOrEmpty(m.normalTexture)) normalIds.Add(m.normalTexture);
        foreach (var t in pkg.textures)
        {
            string p = root + "/Textures/" + t.id + ".png";
            var imp = AssetImporter.GetAtPath(p) as TextureImporter;
            if (imp == null) continue;
            bool isNormal = normalIds.Contains(t.id);
            imp.textureType = isNormal ? TextureImporterType.NormalMap : TextureImporterType.Default;
            imp.sRGBTexture = !isNormal && t.format.Contains("SRGB");
            imp.SaveAndReimport();
        }

        // materials
        var mats = new Dictionary<string, Material>();
        var shader = Shader.Find("Standard");
        foreach (var m in pkg.materials)
        {
            var mat = new Material(shader) { name = m.id };
            if (!string.IsNullOrEmpty(m.baseTexture))
                mat.mainTexture = AssetDatabase.LoadAssetAtPath<Texture2D>(root + "/Textures/" + m.baseTexture + ".png");
            if (!string.IsNullOrEmpty(m.normalTexture))
            {
                mat.SetTexture("_BumpMap", AssetDatabase.LoadAssetAtPath<Texture2D>(root + "/Textures/" + m.normalTexture + ".png"));
                mat.EnableKeyword("_NORMALMAP");
            }
            if (m.masked) { mat.SetFloat("_Mode", 1); mat.SetFloat("_Cutoff", 0.5f); mat.EnableKeyword("_ALPHATEST_ON"); mat.renderQueue = 2450; }
            mat.SetFloat("_Glossiness", 0.1f);
            AssetDatabase.CreateAsset(mat, root + "/Materials/" + m.id + ".mat");
            mats[m.id] = mat;
        }

        // meshes
        var meshes = new Dictionary<string, Mesh>();
        AssetDatabase.StartAssetEditing();
        foreach (var mi in pkg.meshes)
        {
            string d = Path.Combine(pkgDir, "meshes", mi.id);
            var pos = ReadF32(Path.Combine(d, "pos.f32"));
            var v = new Vector3[pos.Length / 3];
            for (int i = 0; i < v.Length; i++) v[i] = new Vector3(pos[3 * i], pos[3 * i + 1], pos[3 * i + 2]);
            var mesh = new Mesh { name = mi.id, indexFormat = UnityEngine.Rendering.IndexFormat.UInt32 };
            mesh.vertices = v;
            mesh.triangles = ReadI32(Path.Combine(d, "idx.u32"));
            if (mi.hasUV)
            {
                var uv = ReadF32(Path.Combine(d, "uv.f32"));
                var u = new Vector2[uv.Length / 2];
                for (int i = 0; i < u.Length; i++) u[i] = new Vector2(uv[2 * i], uv[2 * i + 1]);
                mesh.uv = u;
            }
            if (mi.hasNormals)
            {
                var nr = ReadF32(Path.Combine(d, "nrm.f32"));
                var n = new Vector3[nr.Length / 3];
                for (int i = 0; i < n.Length; i++) n[i] = new Vector3(nr[3 * i], nr[3 * i + 1], nr[3 * i + 2]);
                mesh.normals = n;
            }
            else mesh.RecalculateNormals();
            mesh.RecalculateTangents();
            mesh.RecalculateBounds();
            AssetDatabase.CreateAsset(mesh, root + "/Meshes/" + mi.id + ".asset");
            meshes[mi.id] = mesh;
        }
        AssetDatabase.StopAssetEditing();

        // scene
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        var parent = new GameObject("RF_" + pkg.@case).transform;
        int n_inst = 0;
        foreach (var ii in pkg.instances)
        {
            if (!meshes.TryGetValue(ii.mesh, out var mesh)) continue;
            var go = new GameObject(ii.mesh);
            go.isStatic = true;
            var mt = new Matrix4x4();
            for (int c = 0; c < 4; c++) mt.SetColumn(c, new Vector4(ii.m[c * 4], ii.m[c * 4 + 1], ii.m[c * 4 + 2], ii.m[c * 4 + 3]));
            go.transform.SetParent(parent, false);
            go.transform.localPosition = mt.GetPosition();
            go.transform.localRotation = mt.rotation;
            go.transform.localScale = mt.lossyScale;
            go.AddComponent<MeshFilter>().sharedMesh = mesh;
            var mr = go.AddComponent<MeshRenderer>();
            if (mats.TryGetValue(ii.material, out var mat)) mr.sharedMaterial = mat;
            n_inst++;
        }
        var sun = new GameObject("RF_Sun"); var l = sun.AddComponent<Light>(); l.type = LightType.Directional; l.intensity = 1.2f;
        sun.transform.rotation = Quaternion.Euler(50, -30, 0);
        if (pkg.camera != null && pkg.camera.position != null && pkg.camera.position.Length == 3)
        {
            var cg = new GameObject("RF_Camera"); var cam = cg.AddComponent<Camera>();
            cg.transform.position = new Vector3(pkg.camera.position[0], pkg.camera.position[1], pkg.camera.position[2]);
            cg.transform.rotation = Quaternion.LookRotation(new Vector3(pkg.camera.forward[0], pkg.camera.forward[1], pkg.camera.forward[2]),
                                                           new Vector3(pkg.camera.up[0], pkg.camera.up[1], pkg.camera.up[2]));
            cam.fieldOfView = pkg.camera.vfov; cam.nearClipPlane = 0.1f; cam.farClipPlane = 5000f; cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(0.45f, 0.62f, 0.85f);
        }
        RenderSettings.ambientMode = UnityEngine.Rendering.AmbientMode.Flat;
        RenderSettings.ambientLight = new Color(0.55f, 0.55f, 0.6f);
        EditorSceneManager.SaveScene(scene, root + "/Recon.unity");
        AssetDatabase.SaveAssets();
        File.WriteAllText(resultPath, "{\"ok\":true,\"meshes\":" + meshes.Count + ",\"materials\":" + mats.Count + ",\"instances\":" + n_inst + "}");
        Debug.Log("RF_IMPORT_OK meshes=" + meshes.Count + " instances=" + n_inst);

        string shot = Environment.GetEnvironmentVariable("RF_UNITY_SHOT");
        if (!string.IsNullOrEmpty(shot) && GameObject.Find("RF_Camera") != null)
        {
            var cam = GameObject.Find("RF_Camera").GetComponent<Camera>();
            int w = 1920, h = 1080;
            var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32);
            cam.targetTexture = rt; cam.aspect = w / (float)h; cam.Render();
            RenderTexture.active = rt;
            var tex = new Texture2D(w, h, TextureFormat.RGB24, false);
            tex.ReadPixels(new Rect(0, 0, w, h), 0, 0); tex.Apply();
            File.WriteAllBytes(shot, tex.EncodeToPNG());
            Debug.Log("RF_SHOT_OK " + shot);
        }
    }
}
