using System.IO;
using System.Text;
using UnityEditor;
using UnityEngine;

// Builds a small synthetic AssetBundle (asymmetric mesh + texture + material + hierarchy prefab) used to verify Unity unpack tools,
// and writes the engine's own world matrices to Bundles/expected.json as ground truth for the round-trip check.
public static class RFMakeTestBundle
{
    static Mesh Wedge()
    {
        var m = new Mesh { name = "RF_Wedge" };
        m.vertices = new[] { new Vector3(0, 0, 0), new Vector3(2, 0, 0), new Vector3(0, 1, 0), new Vector3(0, 0, 3), new Vector3(0.5f, 0.2f, 0.1f) };
        m.uv = new[] { new Vector2(0, 0), new Vector2(1, 0), new Vector2(0, 0.25f), new Vector2(1, 1), new Vector2(0.5f, 0.75f) };
        m.triangles = new[] { 0, 2, 1, 0, 1, 3, 0, 3, 2, 1, 2, 3, 4, 1, 2 };
        m.RecalculateNormals(); m.RecalculateBounds();
        return m;
    }

    public static void Run()
    {
        Directory.CreateDirectory("Assets/RFTest");
        var tex = new Texture2D(64, 64, TextureFormat.RGBA32, false);
        for (int y = 0; y < 64; y++)
            for (int x = 0; x < 64; x++)
                tex.SetPixel(x, y, ((x / 8 + y / 8) % 2 == 0) ? new Color(0.9f, 0.3f, 0.1f) : new Color(0.1f, 0.4f, 0.9f));
        tex.Apply();
        File.WriteAllBytes("Assets/RFTest/checker.png", tex.EncodeToPNG());
        AssetDatabase.ImportAsset("Assets/RFTest/checker.png");

        var cubeGo = GameObject.CreatePrimitive(PrimitiveType.Cube);
        var cube = Object.Instantiate(cubeGo.GetComponent<MeshFilter>().sharedMesh); cube.name = "RF_Cube";
        Object.DestroyImmediate(cubeGo);
        AssetDatabase.CreateAsset(cube, "Assets/RFTest/RF_Cube.asset");
        var wedge = Wedge();
        AssetDatabase.CreateAsset(wedge, "Assets/RFTest/RF_Wedge.asset");
        var mat = new Material(Shader.Find("Standard")) { name = "RF_Mat" };
        mat.mainTexture = AssetDatabase.LoadAssetAtPath<Texture2D>("Assets/RFTest/checker.png");
        AssetDatabase.CreateAsset(mat, "Assets/RFTest/RF_Mat.mat");

        var root = new GameObject("RF_Parent");
        root.transform.position = new Vector3(5, 0, 0); root.transform.rotation = Quaternion.Euler(0, 90, 0);
        var a = new GameObject("RF_ChildCube"); a.transform.SetParent(root.transform, false);
        a.transform.localPosition = new Vector3(0, 1, 0); a.transform.localRotation = Quaternion.Euler(10, 20, 30); a.transform.localScale = new Vector3(2, 1, 0.5f);
        a.AddComponent<MeshFilter>().sharedMesh = cube; a.AddComponent<MeshRenderer>().sharedMaterial = mat;
        var b = new GameObject("RF_ChildWedge"); b.transform.SetParent(a.transform, false);
        b.transform.localPosition = new Vector3(1, 0.5f, -2); b.transform.localRotation = Quaternion.Euler(0, 45, 0); b.transform.localScale = new Vector3(1, 3, 1);
        b.AddComponent<MeshFilter>().sharedMesh = wedge; b.AddComponent<MeshRenderer>().sharedMaterial = mat;
        PrefabUtility.SaveAsPrefabAsset(root, "Assets/RFTest/RF_Parent.prefab");

        var sb = new StringBuilder("{");
        bool first = true;
        foreach (var t in root.GetComponentsInChildren<Transform>())
        {
            var m = t.localToWorldMatrix;
            if (!first) sb.Append(","); first = false;
            sb.Append("\"" + t.name + "\":[");
            for (int r = 0; r < 4; r++) for (int c = 0; c < 4; c++) sb.Append(m[r, c].ToString("R", System.Globalization.CultureInfo.InvariantCulture) + (r == 3 && c == 3 ? "" : ","));
            sb.Append("]");
        }
        sb.Append("}");
        Directory.CreateDirectory("Bundles");
        File.WriteAllText("Bundles/expected.json", sb.ToString());
        Object.DestroyImmediate(root);
        AssetDatabase.SaveAssets();

        foreach (var p in new[] { "RF_Cube.asset", "RF_Wedge.asset", "RF_Mat.mat", "checker.png", "RF_Parent.prefab" })
            AssetImporter.GetAtPath("Assets/RFTest/" + p).assetBundleName = "rftest";
        var man = BuildPipeline.BuildAssetBundles("Bundles", BuildAssetBundleOptions.ChunkBasedCompression, BuildTarget.StandaloneWindows64);
        Debug.Log("RF_BUNDLE built " + (man != null));
    }
}
