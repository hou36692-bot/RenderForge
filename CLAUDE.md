# RenderForge — 工作约定（给 Claude）

目标：RenderDoc 截帧 → UE 5.8 可编辑场景。用户不熟悉工程细节，以对话驱动；说明简洁，关键进展简短汇报，别频繁汇报。
**不要声称功能完成，除非实际跑过。** ROADMAP.md 要如实更新。

## 目录
- `renderforge/` Python 包（`python -m renderforge …` 是主入口，见 `cli.py`）
- `renderforge/rd_scripts/` 在 RenderDoc 内部跑的脚本（Py3.6，仅标准库）：`qrenderdoc.exe --python`
- `renderforge/ue_scripts/` 在 UE 内部跑的脚本（Py3.11）
- `workspace/<case>/` 每个案例的生成物（survey/ export/ build/ shots/），git 忽略；**原始 .rdc 绝不复制/修改**
- `ue_project/RenderForgeUE/` UE 目标工程（Content 是生成物，git 忽略）
- `tools/renderdoc-mcp/` 对话式分析用 MCP；`refs/` 参考项目（RDC2UE 无许可证，只借鉴思路不复制代码）
- 用 `.venv/Scripts/python.exe`（uv, 3.12）。git 提交需 `-c user.name=… -c user.email=…`，提交信息结尾加 Co-Authored-By 行。

## 流水线
`survey`（列 draw、按 RT 分组找场景 pass）→ `export`（顶点/索引/postVS/cbuffer/贴图/VS SRV）→ `build`（求解世界矩阵+相机，写 scene.json）→ `ue-import`（无头 UE 建网格/材质/关卡）→ `shot`（无头游戏模式截图，对比 `export/reference_backbuffer.png`）。

## 已验证的关键事实（别重复踩坑）
- RenderDoc 1.45 的 `qrenderdoc.exe --python script.py` 可无界面运行完整 Python API；脚本末尾必须 `os._exit(0)`。
- GetPostVSData 对实例化 draw 会把所有实例首尾相连（行数 = 顶点数 × 实例数）。
- 通用变换求解：对每个 draw 用最小二乘拟合 local→clip（行向量约定 `[p,1] @ M`）；VP 矩阵从 cbuffer 的 4x4 窗口里挑：w 列是单位前向向量 + 对大多数静态 draw 使 `M @ inv(VP)` 为仿射。平面网格(卡片/贴花)需 `regularize_planar`。
- 蒙皮/风动等非刚体 draw 回退为"烘焙"：postVS 位置经 `inv(VP)` 反投影；必须丢弃 w<=0 或超远的顶点，否则巨大包围盒会让 UE 整个画面渲染异常。
- 很多 UE 游戏用 manual vertex fetch：UV/切线在 **VS 的 buffer SRV** 里，而不在顶点输入里（UV: R16G16_FLOAT，N 通道交错；切线: R8G8B8A8_SNORM，每顶点 2 个 = TangentX, TangentZ）。
- UE Python 的 `StaticMeshDescription` 不能设置法线，只能靠 build settings 的 recompute_normals/tangents。
- UE 里创建 StaticMesh：`duplicate_asset("/Engine/BasicShapes/Cube")` 后 `build_from_static_mesh_descriptions`；`create_asset(…, None)` 会崩溃。ISM 需通过 Blueprint(含 ISM 组件) 生成。
- UE 无头截图：`UnrealEditor.exe … -game -RenderOffScreen -ExecCmds="py game_shot.py"`；世界上下文用 `unreal.load_object(None, "/Game/…/Recon.Recon")`；相机用 PlayerStart + `fov N`。编辑器内 SceneCapture/视口截图在离屏模式下不可靠。
- UnrealEditor-Cmd 的 `-ExecutePythonScript` 脚本结束就退出；需要 tick 的用 `UnrealEditor.exe -ExecCmds="py …"`。

## 已知限制 / 待办见 ROADMAP.md
