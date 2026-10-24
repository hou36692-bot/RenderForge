# RenderForge — 工作约定（给 Claude）

目标：RenderDoc 截帧 → UE 5.8 可编辑场景。用户不熟悉工程细节，以对话驱动；说明简洁，关键进展简短汇报，别频繁汇报。
**不要声称功能完成，除非实际跑过。** ROADMAP.md 要如实更新。

## 目录
- `renderforge/` Python 包（`python -m renderforge …` 是主入口，见 `cli.py`）
- `renderforge/rd_scripts/` 在 RenderDoc 内部跑的脚本（Py3.6，仅标准库）：`qrenderdoc.exe --python`
- `renderforge/ue_scripts/` 在 UE 内部跑的脚本（Py3.11）
- `workspace/<case>/` 每个案例的生成物（survey/ export/ build/ shots/），git 忽略；**原始 .rdc 绝不复制/修改**
- `ue_project/RenderForgeUE/` UE 目标工程（Content 是生成物，git 忽略）；已启用官方 MCP 插件
- `unity_project/RenderForgeUnity/` Unity 目标工程（Assets/Editor 里是导入器与测试包构建；Library/Bundles 等生成物 git 忽略）
- `toolbox/` 工具注册表、游戏档案、工作流；`docs/` 文档
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

## 工具箱视角（先读这个）
- 这是**工具箱不是单一流水线**：先 `python -m renderforge doctor`、`plan --game <id> --target unreal|unity`，按游戏/输入类型/目标引擎选工具，别默认走 RenderDoc。
- 数据文件是权威：`toolbox/registry.json`（工具+状态）、`games.json`（游戏档案+已验证+缺口）、`recipes.json`。改完跑 `python -m renderforge docs` 重新生成 `docs/TOOLBOX.md`。**不要手改 TOOLBOX.md。**
- 状态只能用 registry 的词汇；没实际跑过不要标 verified。新验证要写 evidence 路径。
- 结构、三类工具区别、许可证/密钥政策见 `docs/ARCHITECTURE.md`。GPL 工具只能当独立进程调用；无许可证参考项目不复制代码；不保存/打印 AES 密钥；不对有反作弊的联网游戏擅自注入。
- 游戏专用逻辑放档案/独立适配器，不能影响其他游戏。

## 追加的已验证事实
- Scene Package 声明 `space`（ue/unity）；`renderforge/space.py` 负责转换（ue→unity = (Y,Z,X)×0.01）。UV 在包内一律 D3D 约定，仅 Unity 发射器翻 V。
- 3DMigoto Frame Analysis 没有 post-VS 数据 → 走 `build_model_space`（模型空间 + 游戏档案 `model_basis`）。ZZZ 角色是 X 轴向下，basis=[[0,-1,0],[1,0,0],[0,0,1]]。
- GPU 驱动的 `DrawIndexedInstancedIndirect` 参数在转储里是过期的 CPU 写入，按它截取索引会把网格撕碎，默认用整个 IB（头脸会缺）。
- UnityPy 1.25：Mesh 原始数组用 `UnityPy.helpers.MeshHelper.MeshHandler(mesh).process()`（`m_Vertices/m_IndexBuffer/m_Normals/m_UV0`）；对象读取用 `parse_as_object()`。
- Unity 批处理：新工程第一次运行只导入，需要再跑一次方法；`JsonUtility` 对缺失字段给非空默认对象（判相机要查数组长度）。
- AssetRipper 2.0 无头：`--headless --port N`，`POST /LoadFile`(path) → `POST /Export/UnityProject`(path, createSubfolder)。
- UE 官方 MCP：启用 ModelContextProtocol+ToolsetRegistry+EditorToolset，`DefaultEditorPerProjectUserSettings.ini` 设 bAutoStartServer=True，HTTP `localhost:8000/mcp` 需要 Mcp-Session-Id（`renderforge/httpmcp.py`）；`call_tool` 参数是 `toolset_name`+`tool_name`+`arguments`。
- Endfield（Unity 2021.3.34f5 IL2CPP）：资源在加密 VFS(.blc/.chk)；`globalgamemanagers` 是普通文件可读。

- 透明 pass 在主 pass 之后单独写颜色 RT（如 31658 + 31689）；`rf_extract` 的 `save_rt_inputs` 强制保存这些 RT 作为贴图。`build --extra-export <dir> --extra-pass translucent` 把第二个 extract 合并进场景（mesh id 加 `x` 前缀，各自根目录）。
- 无法用 local→clip 拟合的 billboard/粒子不要烘焙；`w` 正负混杂，除完是退化卡片。depth-only pass 与主 pass 是同一批植被代理，别合并。
- UE 透明材质实例每次导入要删除重建（否则沿用旧父级）；无 alpha 的 BC1 粒子贴图改用亮度遮罩。
- 水面：MI 链路（父级/混合模式/贴图）可在引擎内核实，但“截图里可见”必须单独验证。

## 已知限制 / 待办见 ROADMAP.md
