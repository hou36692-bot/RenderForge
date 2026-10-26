# RenderForge — 工作约定（给 Claude）

目标：RenderDoc 截帧 → UE 5.8 可编辑场景。用户不熟悉工程细节，以对话驱动；说明简洁，关键进展简短汇报，别频繁汇报。
**不要声称功能完成，除非实际跑过。** ROADMAP.md 要如实更新。

## 目录
- `renderforge/` Python 包（`python -m renderforge …` 是主入口，见 `cli.py`）
- `renderforge/rd_scripts/` 在 RenderDoc 内部跑的脚本（Py3.6，仅标准库）：`qrenderdoc.exe --python`
- `renderforge/ue_scripts/` 在 UE 内部跑的脚本（Py3.11）
- `renderforge/adapters/` 游戏专用适配器（目前 `zzz.py`）：一个游戏一个模块，同一组接口，不被其他游戏引用
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
- **UE 离屏截图全黑，先怀疑自己手调的灯光/曝光**：曾经用 3 lux 平行光 + 后处理体积里倒置的 EV100/f-stop，截图 mean RGB (0,0,0)。
  现在的规则是**从引擎默认开始**（标准平行光 + SkyAtmosphere + SkyLight，无后处理体积），一次只改一个可量化项，改完就截一张看。
  “材质链路通了”不等于“画面上看得见”。
- **捕获的网格可能没有可用 UV**：鸣潮水面 `m0517` 的顶点缓冲 SRV 解出 `u` 恒为 1.0（SNORM 通道只有 13 个近似 0 的 u 值，UNORM ch0 全 1.0），
  材质对每个像素采样同一个 texel，整片渲成平色。平面看起来“材质坏了”时，先查 `has_uv` 与 UV 数值分布，再动材质。
- **Web 工作台只有一个 WebGL 上下文**：`renderforge/web/static/viewer.js` 全页共享一个 `WebGLRenderer`，卡片悬停借用、移开归还；
  浏览器上限约 16 个上下文，每卡一个会在十几个网格时就悄悄丢最旧的。几何走 `/api/meshbin` 直发 `pos.f32`/`idx.u32`（JSON 头补 4 字节对齐）。
- **静态文件缺失必须 404，不能回退到 index.html**：SPA 回退会让缺 `.js` 被当成 HTML 返回，浏览器报成 "MIME type text/html"，离真正原因很远。
- **flex 纵列里的图片会被压扁再被 `overflow:hidden` 裁掉**：`.shot` 必须 `flex: 0 0 auto`，否则三张图按比例裁剪（本次真实出现过）。
- **贴图语义要从着色器读，不要从贴图长得像什么推断**：`rd_scripts/rf_extract.py` 有 `shaders` 模式（反汇编指定 draw 的 PS，`vs: true` 切 VS），
  必须显式给 eids，入口是 MCP 的 `step` 或直接调 `pipeline.step_shaders`（CLI 没有 `shaders` 子命令，`run` 也没有 `--eids`）。靠它确认了 WuWa 的“ORM”贴图其实是色调伴随贴图。
- **小的 HTTP 服务不要用 `BaseHTTPRequestHandler` 的默认协议**：默认是 HTTP/1.0（每请求关连接）且开着 Nagle，
  浏览器要连 75 次拿缩略图时每次多 ~40 ms 的延迟 ACK 停顿。设 `protocol_version="HTTP/1.1"` + `disable_nagle_algorithm=True` 后，
  同一批缩略图 30 张冷渲染从 ~1.4 s 降到 92 ms。
- **`src` 里的 JSON 解析要缓存，但 `case.json` 必须 `fresh=True`**：`caseinfo._json` 按 (路径, 大小, mtime_ns) 缓存，
  一个 20 MB 的 scene.json 解析要 0.15 s、占约 40 MB；`scan` 每个请求会调两次。写后原地改的文件（`pipeline.load_case`）
  和“删目录重建同字节”的文件（`convert_scene` 后的 `ue_import_result.json`）要绕开缓存，否则拿到共享对象被改。
- **Windows 上不能用 `os.kill(pid, 0)` 探活**：Python 3.12 实测它对活进程返回成功，对已死进程**也不抛异常**（等于永远“活着”）。进程状态只按记录推进判断。
- **参考帧上的“点哪找哪”靠的是相机 + 实例矩阵，不需要深度缓冲**（`renderforge/web/locate.py`）：
  相机装配射线，先用每个实例投影包围盒筛候选，再对候选网格逐三角形求交，按距离排序。实测 535 网格的帧每次 8–20 ms。
  验证方式：点桥拱 → `SM_Old_Bri_01AH`，点树冠 → `SM_Old_Tre_01CL`；把已知变体的包围盒画回参考帧，位置对得上。
  画面正中可能**没有几何**（天空或相机近似导致的缝隙），所以验收是散点探测（6 个点至少 3 个命中），不是只点中心。
- **贴近相机的命中有毒**：`xm0023`（透明，全屏，深度 0.1 m）挡在所有点击前面，它是天空壳一类的东西。
  `pick` 用 `near_camera`（<1 m）标出来并排到末尾，**不要直接丢弃**——网格确实在那里。
- **资产分类不要从名字猜语义**：WuWa 的 `SM_<前缀>_<类别码>_<编号><后缀>` 里只有少数类别码有证据
  （`Bri` 桥 / `Tre` 树 / `Wat` 水 / `Veg`·`Gra` 植被草，证据在 `locate.CODE_LABEL` 旁注）。其余原样展示代号，不翻译。
  分组维度：源资产名 / 类别码 / 关卡目录 / 渲染方式（`m` 静态、`x` 透明 pass、`b` 烘焙——**与本案例的 kind 一一对应**）/ 未知分组 / 屏幕占比。
- **未识别网格不是噪声**：`wuwa_bridge` 535 个里 287 个没有源名，其中 260 个在画面上。它们按“共用底色贴图”再分组
  （27/19/15 个网格共一张图），这比按 mesh id 有用得多。
- **案例的“目录 id”和“显示名”必须分开**：UE 资产路径 `/Game/RenderForge/<case>`（`import_scene.py` 的 `ROOT`）、
  Unity 的 `Assets/RF/<case>`（`RFImportScene.cs`）、截图与 metrics 记录的文件名全部由目录名派生，
  **改目录名会让已有引擎导入失联**。所以重命名只写 `case.json -> title`（`caseinfo.scan` 返回 `title`，回退到目录名），
  页面同时显示目录 id。新建任务时目录 id 只允许 `[A-Za-z0-9._-]`，且不能用 `_` 开头。
- **新建任务不复制 .rdc**：截帧常有 1–2 GB，页面上只能填**本机已有路径**（`casectl.look` 先读 4 字节 `RDOC` 魔数确认），
  在 `workspace/<id>/` 下只写一个 `case.json`，然后立刻把 `survey` 作为普通后台作业启动。大文件上传没有做，也不需要做。

## 已知限制 / 待办见 ROADMAP.md
