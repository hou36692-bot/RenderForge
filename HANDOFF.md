# RenderForge 交接文档

项目根：`D:\HOU\RenderForge`（git 仓库，本地提交，未 push）。先读 `CLAUDE.md`（约定+已验证事实）、`ROADMAP.md`（进度）、`docs/ARCHITECTURE.md`、`docs/TOOLBOX.md`（自动生成，别手改）。

## Goal
构建以 Claude Code 对话为入口的**多游戏、多引擎图形资源重建工具箱**（不是单一 RenderDoc→UE Demo）。范围以 Unity + Unreal 为主，UE 5.8 与 Unity 6000.0.83f1 同为已验证目标。要求：建立工具能力地图、区分通用截帧/游戏专用提取/模组工具/资源解包、复用成熟项目、游戏适配器互相隔离、区分"已验证"与"理论可行"、文档和路线图如实。用户不熟悉工程细节：自主决策，说明简洁，关键进展简短汇报，不要声称没跑过的功能完成。原始 .rdc/游戏文件只读，生成物放 `workspace/<case>/`。

**本次交接的方向（用户明确要求）**：文档要与实际状态一致。本轮（2026-10-25/26）在上一轮的 CLI/工作台之上，新增了**本地可视化工作台**（本地 Web UI：任务总览 / 工作流 / 资源浏览器 / 场景结果），并按用户反馈迭代了资源预览（悬停 3D 旋转 + 内置查看器）和结果页（不再裁剪 + 点击放大）。
**之后一轮（2026-10-26 晚）**：资源浏览器加了**导出**（单网格 OBJ/GLB、贴图 PNG、整案例 zip/后台作业），并做了**加载性能优化**（JSON 解析缓存、缩略图落盘缓存、HTTP/1.1 keep-alive、gzip、ETag；数字见 ROADMAP「加载性能」）。验收脚本从 32 项扩到 63 项。
**下一会话的主线**：把 ZZZ 适配器接进 CLI 并端到端渲染（见 Next Steps 第 1 项）；Web 工作台本身已可用，不进主线，只在需要时增量改。

## Current Progress（均在本机实际跑过，除非注明）
- **黑截图已修复（已重跑验证）**：先前加的灯光 rig 是自造的 3 lux 平行光 + 手动曝光后处理体积（EV100/f-stop 关系写反了）+ movable 灯，导致**每张截图全黑（mean RGB 0,0,0）**。根因是 rig 本身而不是曝光。现在发射的关卡就是标准 UE 灯光：平行光 rotator (0,-50,30) + SkyAtmosphere + 实时捕捉 SkyLight，引擎默认曝光，**没有后处理体积**。可选覆盖仍在：`RF_SUN_INTENSITY` / `RF_SKY_INTENSITY` / `RF_FOG`。
- **水面可见（已重跑验证）**：根因是数据不是材质——水面网格 `m0517`（`SM_Old_Wat_08AH`）的顶点缓冲 SRV 解出来 `u == 1.0`（UNORM 通道 ch0 全 1.0，SNORM 通道只有 13 个近似 0 的 u 值），所以材质只采到一个 texel，整片是平色板。水体 master 改成**世界坐标平面投影 UV**，加 fresnel 驱动的不透明度和 `WorldTile`/`Opacity`/`NormalStrength` 参数，并在图形变化时**删除重建**资产（旧资产曾因复用保留旧图）。`wuwa_bridge` 截图里水面现在是青绿色带波纹。仍未做：水线/泡沫/折射/吸收/焦散、反射。
- **MCP 与 CLI 指标不一致已修**：MCP 的 `metrics` 曾直接调 `compare_case`，与 `pipeline.step_metrics` 选的截图不同、也漏掉 `also`；现在两者同一函数。`scripts/dev/mcp_acceptance.py` 有 CLI 一致性检查。
- **重建丢源身份已修**：`build` 从零重写 `scene.json`，会把所有网格静默改回 `RF_*`。`pipeline.step_build` 现在在重建后重新套用 `draw_identity.json`（`wuwa_bridge` 重建后 18,579/19,192 实例仍带源名）。
- **`status` 建议错步骤已修**：变更检测消息会列出过期步骤，但下一步逻辑取整句第一个词，把 `emit-unity` 过期报成 `ue-import`。现在按真正过期的那一步给建议。
- **`status` 看不到转换空间的 UE 导入已修**：Unity 空间场景导入前会转换，结果落在 `_scene_ue_space/ue_import_result.json`；现在两个位置都查。
- **`rf_extract.py` 新增 `shaders` 模式**：反汇编按 eid 过滤后的唯一像素（或顶点）着色器。入口三个：`shaders <case> --eids a,b`、`run <case> --from shaders --eids a,b`、MCP 的 `step`（带 `eids`/`vs`）。普通 `run` 不会自动跑它（没有 eid 列表时带原因跳过），因为它需要明确指定要看哪几个 draw。
- **透明材质逐材质混合模式**：一个 master 对所有透明面用贴图 alpha 会把"黑底带亮点"渲成实心黑卡、把软片裁成硬边。现在每个材质得到 `alpha` / `red` / `luminance` / `additive` / `skipped`，导入结果（`build/ue_import_result.json -> translucent_modes`）逐条记录 **proven**（PS 反汇编 + D3D11 混合状态）还是 **judgement**。`wuwa_bridge` 12 个透明材质：6 proven / 5 judgement / 1 skipped。
  实测：ssim 0.3174 → **0.3394**，mae 0.3403 → **0.2997**，coverage_error 0.2415 → **0.1489**。
- **ZZZ 世界放置适配器（未接 CLI、未端到端渲染）**：`renderforge/adapters/zzz.py`。世界矩阵 `vs-cb1[0..3]`；相机 `vs-cb0[123..126]`（camera-to-world，其第 3 行与 `vs-cb0[126]` 的 eye 字面量误差 **0.0**）；view `vs-cb0[119..122]`。213 draw 中 **72** 个刚性通过，其余是屏幕空间 UI，如实报告。draw 106 单位基投影 vs 转储剪影 **IoU 0.9783**（需 +632 px x 平移，等于 `vs-cb0[21].x == -0.44` × 1440），叠 `games.json` 的 `model_basis` 则 **0.5524**——**不能**再乘 basis。`workspace/zzz_frame01/export/extract_placed.json` 是适配器输出；出厂路径仍是 `build-model` 单位放置。
- **材质 PBR：否定结论**（见 `docs/MATERIAL_SEMANTICS.md`，别重复调查）：线性伴随贴图不是 ORM。`o3.xyz = lerp(t2.rgb, 1.0, saturate(cb0[279].w))` 是被标量混向白的三通道色调；`o2.y` 是对 `R32_FLOAT` 遮罩采样乘 10 后取 `min`；着色器里没有 roughness/metallic；PBR 输入来自逐材质 cbuffer 标量与 3D LUT（`t5`/`t6`）；贴图槽位跨 draw 不稳定。正路是解码 `ps3` 常量块。
- **本地可视化工作台（本轮新增，已实际跑过）**：`python -m renderforge web` → `http://127.0.0.1:8770/`。四个页面，数据全部来自 `workspace/`（和 CLI 读同一份，不另存状态）：任务总览（每案例的阶段徽章、draw/网格/实例、VP 拟合、身份覆盖、右侧挂该案例的**参考帧缩略图**、5 s 轮询）、工作流（按来源的步骤图 + 每步真实结果 + 单步重跑/整条运行）、资源浏览器（模型按源资产名分组，缩略图由顶点在服务端渲染；纹理/材质可切；搜索）、场景结果（参考帧 + 各引擎截图、导入报告、SSIM/MAE/RMSE/p95/覆盖率/IoU、构建统计）。
  - **模型预览**：悬停卡片 → 缓慢旋转的 3D 预览；点击 → 内置查看器（拖动旋转 / 滚轮缩放 / 右键平移）。**全页只有一个 WebGL 上下文**（浏览器上限约 16 个，每卡一个会坏），卡片借用/归还同一个 canvas，没有悬停时不渲染。几何走 `/api/meshbin`（直接发 `pos.f32` + `idx.u32` 字节，JSON 头补齐 4 字节对齐），不转 glTF。
  - **three.js r186 内置**在 `renderforge/web/static/vendor/three/`（MIT，含 LICENSE），离线可用；`index.html` 的 importmap 指向 `three` / `three/addons/`。
  - 验收：`scripts/dev/web_acceptance.py`，**63 项约 50 s**，含与 CLI 的数值一致（案例表/阶段/SSIM）、缩略图是真 PNG、模块依赖图闭合、缺失静态文件是真 404、`/img` 缩放、`/file` 越权拒绝、未知步骤/案例拒绝、作业生命周期、6 案例 × 5 端点渲染、结果页布局守卫；
    导出面加的是 GLB 结构（magic/chunk 长度/bufferView 边界/索引宽度/顶点数对得上 scene.json）、OBJ 顶点与面数、`Content-Disposition`、贴图是 PNG、zip 里 `scene.json` 与包内一致、导出作业起停与下载、坏 id 拒绝。
- **工作台（CLI/MCP 层，上一轮完成，仍在跑）**：
  - `renderforge/caseinfo.py` + `status [case] [--json]`：来源与 8 个阶段全按磁盘产物推断；单案例给数字与一条可执行 next；核对引擎工程（记录过导入但 map 不在了 → stale）。6 个案例目录都跑过。
  - `renderforge/pipeline.py` + `run <case> [--from S] [--to S] [--force]`：步骤顺序随来源；产物存在即跳过；缺输入带原因跳过；失败停止并打印续跑命令；build 选项（extra_export/extra_pass）持久化。
  - `renderforge/metrics.py` + `metrics`：SSIM/MAE/RMSE/p95/覆盖率误差/mask IoU/翻转检测；两个引擎的分数分别记主分与 `also`。
  - `renderforge/selftest.py` + `selftest`：6 项离线回归，约 4 s，本次 **6 passed**。
  - `renderforge/mcp_server.py`（stdio，仅标准库）+ `jobs.py`/`jobrun.py`；`.mcp.json` 注册为 `renderforge`。
  - `scripts/dev/mcp_acceptance.py`：MCP 协议级验收（原始 JSON-RPC over stdio），**14/14，约 10 s**。
- **当前实测数值**（同机位近似，最新一次 `metrics.json`）：

| 案例 | 来源 | draws / instances | UE ssim / mae / coverage_error | Unity ssim / mae |
|---|---|---|---|---|
| wuwa_61250 | RenderDoc 61250 | 1017 / 26,944 | 0.3931 / 0.2421 / 0.0771 | 0.3323 / 0.2745 |
| wuwa_bridge | RenderDoc 3422 | 681 / 19,192 | 0.3394 / 0.2997 / 0.1489 | 0.3332 / 0.2721 |
| wuwa_f3422 | RenderDoc 3422（全帧） | 1718 / 19,131 | 0.2381 / 0.4631 / 0.3737 | 0.3613 / 0.3765 |
| wuwa_f3765 | RenderDoc 3765 | 19 / 19 | 0.5321 / 0.3311 / 0.0318 | 0.7315 / 0.2070 |
| zzz_frame01 | 3DMigoto Frame01 | 213 / 58 | 0.2123 / 0.3279 / 0.1307 | —（有截图，未打分） |
| unity_synth | 合成 Unity 包 | 3 instances | 无参考帧，无法打分 | — |

  `status` 干净状态下 6 个案例 `next=done`。**跑 `mcp_acceptance.py` 之后 `wuwa_f3765` 会变 `next=emit-unity`**（表里写的是阶段名）：验收会 `step build` 重建场景，Unity 结果比它旧；`python -m renderforge unity-import wuwa_f3765` 即恢复。
- **工作区已清理（4.0 GB → 3.1 GB）**：删掉 `_probe_light`、`_probe_lighting`、`_probe_particles`、`_probe_water`、`_zzz_probe`、`wuwa_bridge/export_transparent{,_2,_3}`、`export_depth`、`wuwa_f3422/build{2,3}`、`-backup-*` 日志与日志归档。保留每个案例的 `export`/`build`/`shots`/`survey`/`ids`/`unity_pkg`/`_scene_*_space`，以及 `_logs`（每族最新）和 `_jobs`。日志现统一写在 `workspace/_logs/`，后台作业在 `workspace/_jobs/`。
- **游戏档案 `coverage`**：wuwa、zzz 为 `active`；genshin、hsr、endfield、generic-unity、generic-unreal 为 `extension`（只有档案 + 适配器位置，未验证；genshin/hsr 无本机数据）。
- **registry**：40 个工具（verified-here 19 / verified-prior 5 / verified-synthetic 2 / installed 1 / candidate 8 / reference 5）。
- **全库源网格身份**：SourceProbe `dump-index` 全库 41,133 个 `SM_` 包 → 157,691 个源 section。`identify.py` per-draw 索引 SHA-256 精确比对：帧 3422（`wuwa_bridge`）386/681 draw、27,727/28,823 实例（96.2%）；帧 61250（`wuwa_61250`）592/1017、43,336/44,757（96.8%）；`wuwa_f3422` 873/1718、49,728/52,933；`wuwa_f3765` 只有 1/19（小帧，多数几何不在全库索引里）。`wuwa_bridge` 重建后场景里 18,579/19,192 实例仍带源名。

## What Worked
- **离屏截图从引擎默认开始，只覆盖能直接量化的那一项**：平行光 + SkyAtmosphere + 实时捕捉 SkyLight + 默认曝光就能渲染出正确画面；`RF_*` 环境变量留给需要时覆盖，默认不写死任何强度。
- **怀疑材质之前先查数据**：水面"不可见"是网格没有可用 UV（SRV 解出 u 恒 1.0），不是混合、绕序或着色器的问题。做法：先把捕获到的 UV/属性打印出来（唯一值、范围），再动材质。
- **混合模式从证据来**：读 PS 反汇编 + D3D11 混合状态，逐材质判定；判不了的标 `judgement`，不假装 proven。
- **适配器的槽位靠结构性质识别，不靠猜**：ZZZ 的 camera-to-world 第 3 行与 eye 字面量误差 0.0、`V @ C == I`；世界矩阵要求旋转正交 `|R R^T - I| < 1e-3`，不满足就报错而不是输出垃圾。同一套校验顺带把屏幕空间 UI pass 筛出来。
- 无界面批处理：RenderDoc `--python`（脚本末尾 `os._exit(0)`）、`UnrealEditor.exe -game -RenderOffScreen -ExecCmds="py …"` 截图、`Unity.exe -batchmode -executeMethod`（新工程首次运行只导入，需再跑一次）。
- 求解：每 draw 最小二乘拟合 local→clip，VP 从 cbuffer 4x4 窗口挑（w 列为单位前向向量，且使多数静态 draw 的 `M@inv(VP)` 为仿射）；平面网格用 `regularize_planar`；非刚体烘焙时必须丢弃 w≤0 和超远顶点。
- UE：`duplicate_asset(Cube)` 建 StaticMesh + `build_from_static_mesh_descriptions`；ISM 通过含 ISM 组件的 Blueprint；法线靠 build settings 重算；相机用 PlayerStart + `fov`。
- 第二次导入（透明 pass）：`Extract.merge_draws` 给第二个 extract 的网格加 `x` 前缀并记录 `mesh_dir`，各 draw 用 `root_of(draw)` 解析文件，解决了同名 `m0000` 冲突。
- **图会变的 master 资产每次导入重建**：水与透明 master 重新导入时 `delete_asset` 再建，否则会沿用旧图（旧 blend mode / 旧 UV 逻辑）；实例本来每次重建。无 alpha 的 BC1 粒子贴图改用亮度遮罩；相机近 25 m 的透明效果默认隐藏（`RF_SHOW_NEAR_FX=1` 打开）。
- UnityPy 读 Mesh：`MeshHelper.MeshHandler(mesh).process()`；对象用 `parse_as_object()`。
- 用 registry 状态词汇严格区分已验证/先前证据/候选，避免夸大。
- 文档改动后跑 `python -m renderforge docs` 重新生成 `docs/TOOLBOX.md`，不要手改。
- **MCP 面用协议级测试验**：`scripts/dev/mcp_acceptance.py` 直接对 `python -m renderforge.mcp_server` 说原始 JSON-RPC，既验证协议也比对 CLI（14/14）。比"脚本化客户端逐工具调用"更接近真实调用路径。

## What Didn't Work（不要重复）
- **手调灯光 + 手动曝光 = 全黑截图（本会话最贵的教训）**。曾经为了"贴近参考帧"写了 3 lux 平行光、一个手算 EV100 的后处理体积（EV100/f-stop 关系写反了）、movable 灯；**每张离屏截图都是纯黑（mean RGB 0,0,0）**。不是曝光不够，也不是场景没导入——是 rig 本身把画面弄没了。手算曝光关系很容易错，而且一错就是全黑，没有中间反馈。
  **规则**：从引擎默认灯光开始；要改只改能直接量化的一项（如 `RF_SUN_INTENSITY`），每改一项拍一张验证，别一次叠多个手算参数。
- **"水面不可见"不要先怀疑负 Z 缩放/绕序**。那个猜测是错的：实例矩阵里确实有负的 Z 缩放（`scale [0.826, -0.826, 1.0]`），但真正原因是网格没有 UV。查证顺序应是：UV 是否存在且非退化 → 贴图是否绑上 → 混合/绕序。把"面片看起来平"当材质问题会浪费一整轮。
- **`kind: static` 的网格不一定是场景物体**：天空穹顶 `m0000` 就是普通静态实例，`build` 的 `sky_radius` 规则**没有**丢它（丢的是 `SM_CloudCover_01` 和一个 LightFog 网格）。以为"天空已被跳过"会让天空那一项的方向错。
- **单独写一个读 blend/depth state 的 RenderDoc 探针脚本会很慢**：`SetFrameEvent` 在 2GB 截帧上按升序回放；用非升序 eid 列表时会反复重放，实测 20+ 分钟无产出。查 pass 结构用 survey.json 的 RT/depth/纹理绑定即可。
- **billboard/粒子不能用 clip 空间顶点除以 w 烘焙**：`w` 有正有负，除完得到 y=0 平面上一堆退化四边形（大面积黑块遮住整个画面）。已改为跳过。
- **depth-only 植被 pass 不要合并**：714 个 draw 解出的变换退化为贴地重复卡片，且与主 pass 是同一批源资产。
- **透明材质实例/母材质不要复用旧资产**：importer 每次重建关卡，但 `MI_*` 若已存在会沿用旧父级、master 若已存在会沿用旧图。
- **别把线性伴随贴图当 ORM 接**：见 `docs/MATERIAL_SEMANTICS.md`，证据已固定；接了等于发明材质响应。
- 编辑器内 SceneCapture / 视口截图在离屏模式下黑屏或不可靠 → 用游戏模式 HighResShot。
- `create_asset(StaticMesh, None)`、`unreal.StaticMeshDescription()` 直接新建 → UE 崩溃；`StaticMeshDescription` 无法设置法线。
- 用 `DrawIndexedInstancedIndirect` 的 CPU 更新转储解析参数 → 网格被撕碎（参数是 GPU 写的）。默认用整个 IB，因此 ZZZ **头/脸缺失**（`ingest(resolve_indirect=True)` 仅供实验）。
- `gh` CLI 不存在；GitHub API 会限流 → `scripts/fetch_github_fallback.py` 用 atom feed 补。
- 提交过一次 UE/Unity 生成文件导致 `.git` 388MB，已用 soft reset + gc 清理（现约 7MB）；注意 `.gitignore` 已覆盖 Unity Library/Bundles/Assets/RF*，以及 `workspace/_logs`、`workspace/_jobs`、`workspace/_*`。
- Git Bash 里复杂 heredoc/转义容易出错，文件用 Write 工具写（本次多次遇到 `\S`/`\0` 转义问题）。
- WebFetch 调官方 UE 文档在该环境报错，改为读本地插件源码（`D:\UE\UE_5.8\Engine\Plugins\Experimental\ModelContextProtocol`）。

## 已知缺口（详见 ROADMAP.md）
- 鸣潮：透明 pass 已合并（73 实例）；**水面可见**（世界投影 UV 绕开缺失的网格 UV），但水线/泡沫/折射/吸收/焦散、多层波纹、反射未还原；粒子/billboard 未放；depth-only 植被 pass 未合并；**天空仍是引擎大气**（捕获穹顶 `m0000` 在场景里但没作为天空壳处理，云层 `SM_CloudCover_01` 被 `sky_radius` 丢弃）；蒙皮网格冻结在捕获姿态。
- 透明材质 5/12 是 `luminance` judgement（游戏用深度淡出遮罩，这里用贴图亮度替代），不是着色器证明的。
- 绝区零：适配器已实现、矩阵验证过（72/213 draws，IoU 0.9783），但**未接 CLI、未端到端渲染**；头脸缺失（GPU 间接绘制）仍未绕过；游戏本机未安装，AnimeStudio 未重跑。
- 原神/星铁：无数据，全未验证（档案 `coverage: extension`）。终末地：资源在加密 VFS，通用工具打不开，且有反作弊，未尝试注入。
- Unity 源路线没有商业游戏样本。材质仅预览级。D3D12/Vulkan 未验证。Nsight/PIX 本机未装。
- **Web 工作台的边界**：
  - 截图在页面上是服务端缩放到 ≤1600 px 的副本（`/img`），不是截帧原始 3840×2160；1:1 看到的是 1600×900。要看原始像素需要另加"原始尺寸"链接。
  - 资源浏览器的缩略图对植被/草叶这类散片网格不友好——3D 旋转不会让一堆独立草片变成"一个东西"；这是数据特性，不是渲染问题。
  - 编辑器控制（L6）没有入口：页面能看和能跑流水线，不能在 UE/Unity 里改场景。
  - 页面没有内嵌 Agent 对话；对话仍在 Claude 会话里，两边共享 `workspace/` 状态但不共享会话。
- **工作台层的真实边界**：
  - `run` 不会交互式挑场景 pass：它按 survey 自动选“类 G-buffer”的 pass；要别的 pass 仍得显式 `export --range/--eids`（然后用 `run --from build`）。
  - `status` 的 next 是启发式：只建议不需要新输入的步骤；`identify` 只对 `SOURCE_INDEX_GAMES`（目前仅 wuwa）给出。
  - metrics 只是记录，没有**通过/不通过阈值**；两个引擎的光照本来就不一样，跨版本比较时看相对变化而不是绝对值。
  - MCP 的“全新会话只用 MCP 走 doctor→plan→status→build→metrics”验收**还没做**；已做的是协议级测试（`scripts/dev/mcp_acceptance.py`，14/14）和 CLI 一致性比对。
  - 跑 `mcp_acceptance.py` 会让 `wuwa_f3765` 的 next 变成 `emit-unity`（它 `step build` 重建了场景）；`unity-import wuwa_f3765` 即可恢复。
  - `unity_synth` 夹具的 `expected.json` 没有 `RF_Prefab` 根的变换（selftest 如实标注“no ground truth”），要全覆盖得改 `RFMakeTestBundle.cs` 一并导出该实例的世界矩阵。
  - 鸣潮第二帧 `wuwa_bridge` 的 `export` 目录没有重跑过 pass 选择逻辑，只因产物在而被跳过——`run --force --from export` 会重新导出（约数十分钟）。
  - `renderforge/adapters/zzz.py` 的 docstring 指向 `workspace/zzz_probe/scripts/`，该目录已随工作区清理删除；结论与数值在 docstring 里完整，但复现探针要重写脚本。

## Next Steps（下一会话从这里开始）

1. **把 ZZZ 适配器接进 CLI**（最高价值，适配器已写好、矩阵已验证）：在 `build`/`build-model` 加 `--adapter zzz` 路径，调用 `adapters.zzz.apply()` 写出的世界矩阵与相机；然后端到端跑一次 UE 渲染，看放在世界里的场景是否对上参考剪影（适配器已验证 IoU 0.9783，但那是投影比对，不是渲染结果）。
   注意：**不要**在适配器矩阵上再乘 `games.json` 的 `model_basis`（会让 IoU 从 0.978 掉到 0.552）；适配器已经写明这一点。
2. **天空穹顶**：`m0000`（eid 2037，2159 顶点，局部半径 41,431，`psDiscard: true`）**已经在场景里**，只是作为普通 static 网格和引擎大气叠着。把它单独作为一个不受光的天空壳发射（或显式识别并跳过引擎大气），比重建大气便宜，能换掉引擎的通用天。
   ⚠ 不要按"`sky_radius` 已经丢掉了天空"来规划——它丢的是云层 `SM_CloudCover_01`（eid 11311，scale 160000）和 `SM_Ird3_Spe_01AH`（eid 3829）。
3. **逐物体差异指标**：现在只有整帧数字，回答不了"哪栋楼不对"。按源资产名（`scene.json -> instances[].source.object`）对指标分组即可；`wuwa_bridge` 已有 18,579/19,192 个带名实例。
4. **材质常量（真正的 PBR 路径）**：解码逐材质常量块（`ps3`）并映射到引擎参数；取代 ORM 那条路，是一个项目而不是一个任务。证据见 `docs/MATERIAL_SEMANTICS.md`。
5. **收尾 MCP 会话级验收**：开一个全新 Claude 会话，只用 `renderforge` MCP 工具走 `doctor → plan → status → step(build) → metrics`；协议级已经过了，这一步验的是 Agent 在真实会话里会不会正确选工具。
5b. **Web 工作台的可选增强**（用户已在用，按反馈增补）：截图"原始尺寸"链接（现在只给 ≤1600 px 副本）；资源点选后触发"只导入这个网格"
   （`POST /api/case/<n>/run` 已能带 `only_kind`/`only_mesh`/`hide_mesh` 并转成 `RF_ONLY_KIND`/`RF_ONLY_MESH`/`RF_HIDE_MESH` 传给作业，但**页面还没有按钮**，也没有端到端跑过）；页面内嵌 Agent 对话。
6. （参考）长期项：ZZZ 头/脸（GPU 驱动间接绘制）、蒙皮网格、D3D12/Vulkan、Nsight/PIX、编辑器控制（L6）。

**不要做的事**：（旧约定“不要把工作台做成 Web UI”已作废：用户明确要求本地可视化工作台，见 `renderforge/web/`；要求是**不复刻** Agent 已有的能力、不引入前端框架与构建步骤。）不要在没有实际跑过的情况下把新命令标成 OK；`docs/TOOLBOX.md` 只能由 `python -m renderforge docs` 生成；`case.json` 里的 `steps` 由步骤图写，不要手改成“ok”来绕过一次失败；不要手调灯光强度和曝光。

## 可视化工作台（本地 Web，本会话新增）

`renderforge/web/`：标准库 `ThreadingHTTPServer` + 原生 JS，无框架无构建。启动 `python -m renderforge web`（默认 `127.0.0.1:8770`）。

- `server.py`：所有响应都由 `workspace/` 经 `caseinfo.scan` / `pipeline.plan_steps` / `jobs.*` / `metrics` 派生，**不保存自己的状态**；页面与 CLI 不一致就是这里的 bug。
- 端点：`/api/cases`、`/api/case/<n>[/steps|/assets|/report|/metrics|/jobs]`、`/api/jobs[/<id>[/log]]`、`/api/thumb/mesh`、`/img`、`/file`、`POST /api/case/<n>/{run,metrics}`。
- 任务管理端点（`casectl.py`）：`GET /api/capture?path=`（读 4 字节 `RDOC` 魔数确认 + 从文件名解析帧号/时间/API + 建议目录 id）、
  `POST /api/cases`（新建：目录 id、rdc 路径、显示名；只写 `case.json`，不复制 .rdc）、`POST /api/case/<n>/title`（只改显示名）。
  `/api/cases` 的每行带 `title`（`case.json -> title`，回退到目录名）。`casectl.delete()` 存在但**页面没有接删除**。
- 定位端点（`locate.py`）：`/api/pick?case=&x=&y=`（归一化坐标，返回按深度排序的命中，含 `near_camera`）、
  `/api/related?case=&mesh=`（材质、贴图、同材质/同贴图的网格）。`/assets` 的每条网格额外带 `named/code/level/kind/material/screen/in_frame`，
  顶层带 `camera {ok, why, vp_match}` 与 `facets`（分组用的计数）。左侧分组依据可在页面上切换（源名/类别码/关卡/渲染方式/未知/占比）。
- 导出端点（`exporters.py` + `export_job.py`）：`/api/export/mesh?case=&mesh=&fmt=obj|glb`、`/api/export/texture?case=&tex=`、
  `/api/export/case?case=&fmt=&meshes=&textures=`（同步，小案例）、`POST /api/case/<n>/export` → 子进程 → `workspace/_exports/<id>.zip`、
  `/api/export/<id>`（状态）、`/api/export/jobs`（列表）、`/export/download?id=`。导出记录和 zip 都在 `workspace/_exports/`（git 忽略）；没有清理策略，攒多了手动删。
  约定：GLB 是单网格文件（position/normal/UV，>65535 顶点用 uint32 索引），UV 保持左上原点；OBJ 翻 V，附 `materials.mtl` + 引用的贴图；
  整案例 zip 另含 `scene.json` 原样、`INSTANCES.csv`、`CAMERA.json`、`REPORT.md`、`README.txt`。
- 安全边界：只允许 `RUNNABLE` 里列出的步骤；`/file` 与 `/img` 拒绝 `workspace/` 之外的路径；未知案例 404。
- 缩略图：读 `pos.f32`/`idx.u32` 做正交投影 + 平面着色（`abs(dot(N,L))`，因为捕获几何没有可信绕序），采样上限 1200 三角形，按 (case, mesh, size) 缓存。实测 40k 顶点网格 0.19 s。
- 踩过的坑：`aspect-ratio` 的缩略图放在 grid `auto` 行里高度会塌成 2 px，改用 `grid-auto-rows`；3840×2160 的参考帧直接给 `<img>` 会让离屏窗口不绘制，改走 `/img` 缩放到 1600 px。
- 验收：`scripts/dev/web_acceptance.py`，**63 项**（约 50 s，冷启动首次更久），含与 CLI 的数值一致性、越权拒绝、作业生命周期、6×5 端点渲染、结果页布局守卫、导出（GLB 结构/OBJ 面数/zip/作业/坏 id）。
- 本轮后续修复（用户反馈驱动，均已实测）：
  - **结果页三张图被裁剪**：`.shots` 是 flex 纵向容器，子项默认 `flex-shrink:1`，三张高图被压扁后由 `.shot` 的 `overflow:hidden` 裁掉上下。改为 `flex: 0 0 auto`，容器滚动。验收加了布局守卫。
  - **不能放大**：新增 `#lightbox` 对话框，点任意截图（含参考帧）按屏适配打开，再点切 1:1，`+`/`-` 切换，Esc 关闭，标题栏显示实际显示尺寸。
  - **任务总览挂参考帧**：详情页右侧显示该案例的参考帧缩略图（点击放大），无截帧的案例（`unity_synth`）显示"无截帧（Unity 资源包来源）"而非空洞。
  - 顺手修的 bug：`notes` 为空时 `pane.append(null)` 会打印字面 `null`；`/api/meshbin` 头长未对齐导致 `Float32Array` 构造失败；`/vendor/three/three.core.js` 漏拷且缺失静态文件返回 HTML 回退，浏览器报成难以定位的 "MIME type text/html"。

## 快速上手
```
cd D:\HOU\RenderForge
.venv\Scripts\python.exe -m renderforge status            # 一张表：每个案例跑到哪、下一步
.venv\Scripts\python.exe -m renderforge selftest          # 6 项离线回归，约 4 s
.venv\Scripts\python.exe -m renderforge doctor
.venv\Scripts\python.exe -m renderforge plan --game wuwa --target unity
git log --oneline -3
```
续跑 / 后台：
```
.venv\Scripts\python.exe -m renderforge run <case> --from <step>       # 产物存在就跳过，缺输入跳过并说明
.venv\Scripts\python.exe -m renderforge run <case> --force --from build
```
MCP：`.mcp.json` 里的 `renderforge`；不知道下一步就先 `status`，长步骤用 `job_start`。skill 在 `.claude/skills/renderforge/SKILL.md`。

可视化工作台：
```
.venv\Scripts\python.exe -m renderforge web        # http://127.0.0.1:8770/ ，Ctrl-C 停
.venv\Scripts\python.exe scripts/dev/web_acceptance.py   # 63 项，自起一个服务器，不占 8770
```
`.claude/launch.json` 已配好（`renderforge-workbench`，固定 8770）；端口被占时预览工具会换端口，此时页面与 `launch.json` 不一致——先确认 `netstat -ano | grep 8770` 再看。

已有案例（`status` 会实时给出）：`wuwa_bridge`（RenderDoc 帧 3422，含透明 pass 合并）、`wuwa_61250`（帧 61250）、`wuwa_f3422`（帧 3422 的早期全帧导出，1718 draw）、`wuwa_f3765`（19 draw 的小帧）、`zzz_frame01`（3DMigoto 角色）、`unity_synth`（Unity 合成包，回归夹具）。截图里程碑在 `docs/milestone_*.png`。

识别用法（`case.json -> identify.index` 已记好，`run --from identify` 可直接跑）：
```
.venv\Scripts\python.exe -m renderforge identify wuwa_61250 --index "D:\HOU\cuibo_reverse\03_source_assets\probes\geom_index_all_20261023" --apply
```

透明 pass（已验证部分；`wuwa_bridge/case.json -> build` 已记录这两个选项，`run --force --from build` 会自动带上）：
```
.venv\Scripts\python.exe -m renderforge build wuwa_bridge --extra-export workspace/wuwa_bridge/export_transparent4 --extra-pass translucent
```

读某个 draw 的着色器（CLI 没有子命令，走 MCP 的 `step`；`eids` 逗号分隔，`vs: true` 切顶点着色器）：
```
python -m renderforge.mcp_server   →  step {case: "wuwa_bridge", step: "shaders", eids: "2037,2050"}
```
输出在 `workspace/<case>/shaders/`（`shaders.json` + 反汇编文本）。

ZZZ 适配器（已写未接线；只有 `apply()` 能直接跑，产物是 `export/extract_placed.json`，不会改动 `build/scene.json`）：
```
.venv\Scripts\python.exe -c "from renderforge.adapters import zzz; print(zzz.apply(r'workspace/zzz_frame01', r'workspace/zzz_frame01/export/extract.json', r'workspace/zzz_frame01/export/extract_placed.json'))"
```
`build`/`build-model` **没有** `--adapter` 参数，`zzz_frame01` 出厂路径仍是单位放置。
