# RenderForge 工具能力地图

> 由 `python -m renderforge docs` 从 `toolbox/registry.json`、`games.json`、`recipes.json` 生成，请改数据文件而不是本文件。

状态说明：**OK** = 本工作区对真实数据实际跑过；**OK\*** = 在我生成的合成数据上跑过；**OK(prior)** = 你之前的项目里有成功证据，本次未重跑；**inst** = 本机已安装但未使用；**cand** = 候选，未安装；**ref** = 只作参考。


## capture — L0 Acquire GPU / frame data from a running game

| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |
|---|---|---|---|---|---|---|---|
| [RenderDoc](https://github.com/baldurk/renderdoc) | general-capture | OK | direct | MIT | 2026-10-22 | v1.46 | Installed 1.45; upstream is at v1.46. Python API is the batch extraction workhorse. Cannot capture titles whose anti-cheat blocks DLL injection (commonly reported for HoYo titles; not tested here) - use the 3DMigoto route for those. |
| [RenderDoc MCP (JiaboLi)](https://github.com/JiaboLi-GitHub/renderdoc-mcp) | gpu-analysis-mcp | OK | direct | MIT | 2026-06-09 | v0.3.1 | Best for interactive Q&A about a frame. v0.3.1 has no Windows binary release (source only, needs build against RenderDoc source); v0.3.0 binary bundles RenderDoc 1.43. Bulk extraction is faster/more complete via our own rd_extract script (vertex SRVs, instancing, cbuffers). |
| [3DMigoto (Frame Analysis)](https://github.com/bo3b/3Dmigoto) | game-capture | OK(prior) | adapter | - | 2026-08-19 | Pre-Release beta build f | Capture mechanism behind GIMI/SRMI/ZZMI/WWMI. A Frame Analysis dump contains PRE-vertex-shader buffers (no post-VS clip positions) so the generic transform solver does not apply; ingest emits model-space meshes and needs a per-game placement hook. |
| [XXMI Launcher](https://github.com/SpectrumQT/XXMI-Launcher) | game-capture | OK(prior) | manual-gui | GPL | 2026-10-16 | v2.4.9 | Per-game 3DMigoto packages + injection management. Upstream is far ahead (2.4.9). Capture procedure (Hunting, F8 per frame) is written in the user's toolkit README. Capture tool, not an extractor and not a mod tool by itself. |
| [NVIDIA Nsight Graphics](https://developer.nvidia.com/nsight-graphics) | general-capture | cand | manual-gui | proprietary freeware | - | - | Fallback when RenderDoc cannot capture (e.g. DX12 + ray tracing). No known scripted resource export comparable to RenderDoc's Python API, so treat as visual/shader debugging, not as an extraction source. |
| [PIX on Windows](https://devblogs.microsoft.com/pix/introduction/) | general-capture | cand | manual-gui | proprietary freeware | - | - | D3D12-only fallback for titles RenderDoc mishandles. Same caveat as Nsight: export automation unverified. |
| [RenderDoc contrib extensions](https://github.com/baldurk/renderdoc-contrib) | reference | ref | reference-only | MIT | 2026-10-19 | - | Community UI extensions; useful as examples of the extension API. Not needed for headless extraction. |
| [GIMI / SRMI (Genshin / HSR model importers)](https://github.com/SilentNightSound/GI-Model-Importer) | mod-tool | ref | reference-only | GPL-3.0 | 2026-09-04 | v7.0 | MOD tools: they replace assets at runtime. Not an extraction pipeline. Value for us: documented per-game buffer/shader conventions and 3DMigoto config. SR-Model-Importer is stale (2023) and superseded by XXMI's SRMI package. |
| [3DMigoto usage guide (t1ktak)](https://github.com/t1ktak/3dmigoto/blob/main/Guides/UsageInstructions.md) | reference | ref | reference-only | - | - | - | Documentation for the frame-analysis workflow. |

## ingest — L1 Turn a capture into the neutral Extract Package

| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |
|---|---|---|---|---|---|---|---|
| RenderForge rd_extract (RenderDoc -> Extract Package) | renderforge-core | OK | direct | - | - | - | D3D11 verified; D3D12/Vulkan code paths use the same API but are unverified. Modes: survey / export / ids. export options: --no-instanced-index, save_rt_inputs (force-save render-target inputs as PNG, e.g. scene colour for a translucent pass), per-draw ownIndices when a mesh shares its index buffer. |
| RenderForge 3DMigoto Frame Analysis ingest | renderforge-core | OK | direct | - | - | - | Parses log.txt + per-draw .buf/.txt/.dds. GPU-driven DrawIndexedInstancedIndirect (ZZZ characters) has GPU-written args that are not in the dump: resolving them from CPU-side update dumps was tried and shreds meshes, so the whole dumped index buffer is used (head/face parts of the ZZZ test character come out missing/merged). Known limitation, root cause identified. |
| RenderForge Unity-source ingest (UnityPy -> Scene Package) | renderforge-core | OK* | direct | - | - | - | Source-only route: no GPU capture involved. Handles Transform hierarchy, MeshFilter+MeshRenderer, Mesh (raw arrays via UnityPy MeshHandler), Material main/bump textures. Not yet exercised on a commercial game (none with readable bundles available locally); skinned meshes, LODGroups, prefab variants and scene files are not handled. |
| [WWMI-Tools (Blender add-on)](https://github.com/SpectrumQT/WWMI-Tools) | game-frame-extractor | cand | adapter | GPL | 2026-10-03 | v1.8.0 | Game-specific: reads WuWa frame dumps into Blender with Kuro's buffer layouts (also does mod export). Use its layout knowledge/ output as an alternative WuWa ingest; do NOT treat as generic. Blender is installed locally (D:\Blender) but this add-on was not run. |

## source — L2 Read the game's own asset files (pak / bundle / VFS)

| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |
|---|---|---|---|---|---|---|---|
| [UnityPy](https://github.com/K0lb3/UnityPy) | unity-parser | OK | direct | MIT | 2026-10-05 | PyPI | First choice for scripted reading of standard Unity data. Game-specific encryption (custom bundle headers / VFS) must be undone before it can open the data. |
| [AssetRipper](https://github.com/AssetRipper/AssetRipper) | unity-parser | OK* | wrapped | GPL-3.0 | 2026-10-20 | - | Reconstructs a whole Unity project (prefabs, scenes, materials) - best for scene-level recovery when data is readable. GPL-3.0: run it as a separate process (as done here), do not link. |
| [AnimeStudio (ZZZStudio family)](https://github.com/Escartem/AnimeStudio) | unity-game-parser | OK(prior) | adapter | MIT | 2026-10-09 | - | NOT in the original candidate list but required: HoYo games use custom block formats that generic Unity tools cannot read. This is the model for a per-game 'source adapter'. ZZZ itself is not installed on this machine now, so it was not re-run. |
| [CUE4Parse](https://github.com/FabianFG/CUE4Parse) | unreal-parser | OK(prior) | wrapped | Apache-2.0 | 2026-10-21 | v1.2.2.202610 | The right batch tool for Unreal source assets. Needs the game's AES key (user's SourceProbe reads it from an existing local file; RenderForge never stores or prints keys) and sometimes .usmap mappings. |
| SourceProbe (user's CUE4Parse CLI) | local-adapter | OK(prior) | adapter | - | - | - | Owned by the user's WuWa project; referenced by path, not copied. Writes only under that project's 03_source_assets / 99_scratch. RenderForge added the batch `dump-index` command (per-section hashes + per-LOD index bytes); 41,133 SM_ packages -> 157,691 sections in ~25 min. |
| [FModel](https://github.com/4sval/FModel) | unreal-browser | inst | manual-gui | GPL-3.0 | 2026-10-18 | aug-2026 | Interactive browser/exporter built on CUE4Parse. Use to explore a game by hand and to learn paths/keys; do batch work through CUE4Parse directly. GPL-3.0 - separate process only. |
| [AssetStudio (Razviar update branch)](https://github.com/Razviar/assetstudio) | unity-parser | cand | wrapped | MIT | 2025-11-27 | - | Maintained successor of Perfare/AssetStudio (archived 2022). CLI export of meshes/FBX/textures. Not run; UnityPy + AssetRipper cover the same ground for now. |
| [UABEA (Unity Asset Bundle Extractor Avalonia)](https://github.com/nesrak1/UABEA) | unity-editor | cand | manual-gui | - | 2026-05-11 | eighth release | Bundle inspection/patching GUI. Cannot be scripted; keep as the human fallback for odd bundles. |
| [Il2CppDumper / Il2CppInspectorRedux](https://github.com/Perfare/Il2CppDumper) | il2cpp-metadata | cand | wrapped | MIT | 2024-08-18 | - | Recovers type/field layouts from global-metadata.dat + GameAssembly.dll so MonoBehaviour data (scene settings, custom components) can be decoded. Il2CppDumper is stale (2024); LukeFZ/Il2CppInspectorRedux (AGPL-3.0, 2026) is the maintained alternative. Needed for Endfield-class games; not run. |

## match — L3 Reconcile captured data with source assets

| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |
|---|---|---|---|---|---|---|---|
| RenderForge asset identity (whole-library) | renderforge-core | OK | direct | - | - | - | Per-draw exact ordered-index SHA-256 against a SourceProbe dump-index of source LOD sections. Full-mesh only: a draw whose index list is a sub-range of a larger source section stays unmatched (no approximation). Duplicate index tokens across draws resolved to one source in this data. |

## scene — L4 Neutral Scene Package (interchange format)

| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |
|---|---|---|---|---|---|---|---|
| RenderForge solver / scene builder | renderforge-core | OK | direct | - | - | - | Requires post-VS data (RenderDoc). 3DMigoto path uses build_model_space (identity placement + optional basis). Extra pass merge (--extra-export/--extra-pass) adds a second extract with its own root; billboard/camera-facing quads are rejected (not rigid) rather than baked. |
| RenderForge space converter (ue <-> unity) | renderforge-core | OK | direct | - | - | - | Handles axis permutation + unit scale, instance matrices, normals, camera. Winding is preserved (both are left-handed). UV V-flip is done by the Unity emitter only. |

## emit — L5 Write scene into a target engine / DCC

| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |
|---|---|---|---|---|---|---|---|
| [UE Python Editor Scripting](https://dev.epicgames.com/documentation/en-us/unreal-engine/scripting-the-unreal-editor-using-python) | engine-scripting | OK | direct | - | - | - | Verified pitfalls: StaticMeshDescription cannot set normals (rely on the LOD build settings recompute_normals/tangents); create a StaticMesh by duplicating /Engine/BasicShapes/Cube then build_from_static_mesh_descriptions; ISM needs a Blueprint with an ISM component; screenshots must use game mode (-game -RenderOffScreen), not SceneCapture in the editor. |
| [UE Interchange](https://dev.epicgames.com/documentation/unreal-engine/interchange-framework-in-unreal-engine) | engine-import | OK | direct | - | - | - | Textures verified. Mesh import from glTF/FBX (e.g. CUE4Parse GLB output) not yet exercised: current emitter builds StaticMeshes directly from raw arrays, which avoids FBX/glTF round trips but loses nothing we need yet. |
| Unity Editor batchmode + Unity CLI | engine-scripting | OK | direct | - | - | - | First run of a fresh project only imports; run the method twice (or open once) so scripts compile. |
| RenderForge UE emitter (import_scene.py) | renderforge-core | OK | direct | - | - | - | Accepts scene packages from any source space: converts Unity-space packages to UE space first (renderforge/space.py; vertex round-trip error <1e-6 on a synthetic hierarchy). Material kinds: opaque masked, translucent (alpha), translucent-luminance (BC1 sheets without alpha), water preview (translucent teal tint + captured normal). Near-camera translucent FX hidden by default. |
| RenderForge Unity emitter (emit_unity.py + RFImportScene.cs) | renderforge-core | OK | direct | - | - | - |  |
| [RDC2UE](https://github.com/GuoRen0830/RDC2UE) | reference-implementation | ref | reference-only | - | 2026-07-03 | - | No licence file: do not copy code. Useful ideas: manual ViewProj entry as fallback when auto-detect fails; shader transcription via DXBC decompiler + spirv-cross for material recovery. Game-specific by its own statement. |
| [UE5_RenderDoc_To_OBJ](https://github.com/PowerSerg10/UE5_RenderDoc_To_OBJ) | reference-implementation | ref | reference-only | - | 2026-03-26 | - | CSV mesh export -> OBJ with UE5 view-buffer reconstruction. No licence file. Our solver (fit local->clip, pick VP from cbuffers) covers the same ground generically. |

## control — L6 Drive the target editor interactively (MCP / scripting)

| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |
|---|---|---|---|---|---|---|---|
| [Unreal MCP (official, UE 5.8)](https://dev.epicgames.com/documentation/unreal-engine/unreal-mcp-in-unreal-editor?application_version=5.8) | engine-mcp | OK | direct | - | - | - | Enable plugins ModelContextProtocol + ToolsetRegistry + EditorToolset and set bAutoStartServer in DefaultEditorPerProjectUserSettings.ini (done in ue_project). Experimental / NoRedist plugin. Gives a chat-driven way to inspect and edit the reconstructed level. |
| [funplay-unreal-mcp](https://github.com/FunplayAI/funplay-unreal-mcp) | engine-mcp | cand | direct | MIT | 2026-06-23 | - | Alternative to the official MCP (execute_python, PIE automation). Not installed: the official server already covers editing, and headless python covers batch import. Revisit if the official toolsets lack screenshot / PIE control. |
| [funplay-unity-mcp](https://github.com/FunplayAI/funplay-unity-mcp) | engine-mcp | cand | direct | MIT | 2026-10-08 | - | Chat-driven Unity editing. Not installed. The user's environment already exposes a Unity CLI / MCP ('unity' server) and Unity.exe batchmode is verified here. |

## validate — L7 Render and compare against the reference frame

| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |
|---|---|---|---|---|---|---|---|
| RenderForge screenshot / compare harness | renderforge-core | OK | direct | - | - | - | UE: game-mode HighResShot off-screen. Unity: camera render in the import method. Comparison is side-by-side, not a pixel metric yet. |
| RenderForge numeric comparison (SSIM / error / coverage) | renderforge-core | OK | direct | - | - | - | Approximate same-viewpoint comparison, not pixel-identical. SSIM on BT.601 luminance (integral images), MAE/RMSE/p95, foreground-coverage error, mask IoU. ~1.1 s per 1080p pair. |
| RenderForge offline regression fixture | renderforge-core | OK | direct | - | - | - | No GPU / RenderDoc / UE / Unity needed; covers committed fixtures only. Exit 1 on failure. |

## orchestrate — L8 Registry, game profiles, recipes, planner, skills

| 工具 | 类型 | 状态 | 接入方式 | 许可证 | 最近更新 | 版本 | 要点 |
|---|---|---|---|---|---|---|---|
| RenderForge workbench (case status + resumable step graph) | renderforge-core | OK | direct | - | - | - | A stage is done only when its artifact exists on disk; a recorded import is cross-checked against the engine project. Steps that need input not on disk (identify index, capture path) are skipped with the reason. Build options that change the result (extra_export/extra_pass) persist in case.json and are reused on rebuild. |
| RenderForge MCP server (stdio) + background jobs | renderforge-core | OK | direct | - | - | - | Standard library only; tools are thin wrappers over the CLI so output is identical. job_start writes workspace/_jobs/<id>.log plus an exit marker, so a later session can still read the outcome. Not yet exercised from a fresh Claude session through the harness. |
| RenderForge workbench asset export (OBJ / GLB / texture / case zip) | renderforge-core | OK | direct | - | - | - | Derived from build/scene.json alone: no re-solving, no invented geometry. GLB is a single mesh per file with positional/normal/UV accessors (uint32 indices above 65535 vertices) and keeps the package top-left UV origin; OBJ flips V because OBJ is bottom-left. Whole-case zips ship scene.json verbatim, INSTANCES.csv, CAMERA.json, REPORT.md and the textures the exported meshes reference. Large cases run as a child process (renderforge.web.export_job) so the HTTP threads stay free. Materials are preview level, not the game shaders. |

## 游戏适配档案

| 游戏 | 覆盖 | 引擎 | 截帧路线 | 资源读取 | 已验证 | 缺口 |
|---|---|---|---|---|---|---|
| Wuthering Waves (`wuwa`) | active | unreal | renderdoc, 3dmigoto | user-sourceprobe, cue4parse, fmodel | RenderDoc D3D11 capture -> UE 5.8 and Unity 6 scene (bridge scene, 19k instances, camera matched)；whole-library SourceProbe dump-index (41,133 SM_ packages / 157,691 sections) -> 96% of scene instances carry their source asset name in UE and Unity；second capture (frame 61250) reconstructed end-to-end: VP fit 0.96, UE import 851 meshes / 26,944 instances；translucent pass exported separately and merged: 67 draws, 73 instances placed with affine transforms；water draw identified exactly (SM_Old_Wat_08AH LOD0 section0) and given a translucent water material in UE (linkage verified by asset inspection)；identify reproduces 6 independently established frame-61250 source matches (same object / LOD / section)；SourceProbe selftest | water does not read visually in the UE game-mode screenshot yet; the mesh/material are linked but the plane is not visible (cause not isolated; winding vs negative-Z instance scale is the leading candidate)；particle / billboard translucent draws cannot be placed by local->clip fit; they are skipped rather than faked；depth-only pass (leaf interaction proxies) duplicates main-pass foliage with degenerate transforms; not merged；no water-line / foam / refraction / absorption / caustics; water is a flat tinted translucent preview；sky + lighting not reconstructed；skinned/wind meshes baked at capture pose；instanced draws that use one section of a larger array mesh stay unmatched (whole-mesh identity only)；~11k SM_ packages have no rendered LOD data in the mounted pak view and cannot be indexed；key handling stays inside the user's SourceProbe |
| Zenless Zone Zero (`zzz`) | active | unity | 3dmigoto | animestudio, assetripper, unitypy | 3DMigoto Frame01 (user's, 1.9GB) ingested: 213 draws, 167 meshes, 83 textures decoded, 6 s；character body/hair/shoes rendered upright and textured in Unity and in UE (model space, unity->ue conversion exercised)；user's earlier UE reconstruction used AnimeStudio exports (verified-prior) | head/face parts missing: ZZZ draws characters via GPU-driven indirect draws whose arguments are not in the dump；world/camera placement needs ZZZ VS cbuffer decoding (not done)；skinning pose is whatever was dumped；AnimeStudio cannot be re-run: game not installed |
| Genshin Impact (`genshin`) | extension | unity | 3dmigoto | animestudio | 无 | no local install or captures: everything here is untested；ingest should work if layouts match (same 3DMigoto format); unverified |
| Honkai: Star Rail (`hsr`) | extension | unity | 3dmigoto | animestudio | 无 | no local install or captures: untested |
| Arknights: Endfield (`endfield`) | extension | unity | renderdoc | unitypy, il2cppdumper | UnityPy opens globalgamemanagers (real file) and reports the Unity version | asset containers unreadable without game-specific decryption (no generic tool handles them)；capture route untested；this is the clearest example of a game that needs its own source adapter |
| Unity game with standard UnityFS bundles / data files (`generic-unity`) | extension | unity | renderdoc, 3dmigoto | unitypy, assetripper, assetstudio-razviar | UnityPy + AssetRipper read a Unity 6 AssetBundle built here (synthetic)；Unity-source route: bundle -> Scene Package -> Unity + UE, hierarchy transforms match Unity ground truth to 1.6e-7 | no commercial game with plain bundles was available locally |
| Unreal game with pak / IoStore (`generic-unreal`) | extension | unreal | renderdoc | cue4parse, fmodel | WuWa path above | each game needs EGame version, AES key and sometimes usmap |

## 工作流（recipes）

### R1-rdc-to-scene: RenderDoc capture -> Scene Package -> target engine
1. survey draws and pick the scene pass — `rf-extract`　`python -m renderforge survey <case> --rdc <file.rdc>`
1. export meshes / textures / cbuffers / VS SRVs — `rf-extract`　`python -m renderforge export <case>`
1. solve world matrices + camera — `rf-solve-build`　`python -m renderforge build <case>`
1. (optional) identify source assets — `rf-match, cue4parse, unitypy`　`python -m renderforge match <case> --fingerprints <dir>`
1. emit into engine — `rf-emit-ue, rf-emit-unity`　`python -m renderforge ue-import|unity-import <case>`
1. render + compare — `rf-shot`　`python -m renderforge shot|compare <case>`

### R2-3dmigoto-to-scene: 3DMigoto Frame Analysis dump -> model-space Scene Package -> target engine
1. capture frames (user action in game) — `xxmi-launcher, 3dmigoto`　`manual: Hunting + F8 per frame`
1. ingest dump into Extract Package — `rf-ingest-3dmigoto`　`python -m renderforge ingest-3dmigoto <case> --frame-dir <FrameAnalysis-...>`
1. build model-space scene (game profile supplies basis / filters) — `rf-solve-build`　`python -m renderforge build-model <case> --game <id>`
1. emit into engine — `rf-emit-ue, rf-emit-unity`　`python -m renderforge ue-import|unity-import <case>`

> 注意：No post-VS positions in a Frame Analysis dump: placement of world geometry needs a game adapter that decodes the VS constant buffers. Character / prop extraction in model space works generically.

### R3-unreal-source-assets: Unreal pak/IoStore -> source meshes/textures/world layout
1. mount game, index files — `user-sourceprobe, cue4parse`　`SourceProbe index|query`
1. export meshes with fingerprints — `user-sourceprobe`　`SourceProbe mesh|fingerprint`
1. match against captured meshes — `rf-match`　`python -m renderforge match <case> --fingerprints <dir>`
1. world/actor layout (instances without GPU capture) — `user-sourceprobe`　`SourceProbe layout`

### R4-unity-source-assets: Unity bundles/data files -> source meshes/textures/prefabs
1. if container is custom/encrypted: use the game's source adapter first — `animestudio`　`game adapter (see games.json)`
1. scripted extraction of textures / meshes / transforms — `unitypy`　`python scripts/verify_unitypy.py <bundle> <out>`
1. whole-project reconstruction (prefabs, materials, scenes) — `assetripper`　`AssetRipper.GUI.Free --headless --port N; POST /LoadFile, /Export/UnityProject`
1. bundle -> Scene Package directly (no GPU capture) — `rf-ingest-unity`　`python -m renderforge ingest-unity <case> --bundle <file>`
1. IL2CPP type info for MonoBehaviours — `il2cppdumper`　`Il2CppDumper GameAssembly.dll global-metadata.dat`
1. emit into engine — `rf-emit-ue, rf-emit-unity`　`python -m renderforge ue-import|unity-import <case>`

### R5-chat-edit: Interactive editing of the result from Claude Code
1. Unreal: start editor with Unreal MCP enabled, talk to http://localhost:8000/mcp — `ue-official-mcp`　`renderforge.httpmcp.HttpMcp`
1. Unity: unity CLI / MCP, or batchmode methods — `unity-batchmode, funplay-unity-mcp`　`unity CLI`
