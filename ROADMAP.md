# RenderForge 路线图（工具箱版）

状态：✅ 在真实数据上跑通　🧪 仅合成数据/初步　🟡 部分　⬜ 未开始　⚠ 已知阻塞

范围：以 **Unity 与 Unreal** 为主；**UE 5.8 与 Unity 6000.0.83f1 同为已验证目标**；游戏专用逻辑隔离在适配档案/适配器里。
完整工具清单见 [docs/TOOLBOX.md](docs/TOOLBOX.md)，结构见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 总览（按层）

```
L0 截帧获取     ██████░░░░ 60%  RenderDoc ✅ · 3DMigoto(用户数据) ✅ · Nsight/PIX ⬜
L1 摄入适配器   ███████░░░ 70%  RenderDoc→Extract ✅ · 3DMigoto→Extract ✅ · Unity包→Scene 🧪 · WWMI路线 ⬜
L2 游戏资源读取 █████░░░░░ 50%  UnityPy ✅ · AssetRipper 🧪 · CUE4Parse(用户SourceProbe) ✅ · AnimeStudio(用户) ✅ · 加密容器 ⚠
L3 对账/求解    ████████░░ 80%  VP+世界矩阵+相机 ✅ · 全库源网格索引 ✅(157k section) · per-draw 精确身份 ✅(96% 实例)
L4 场景包/空间  ████████░░ 80%  scene.json · ue↔unity 转换 ✅(数值校验)
L5 目标发射器   ███████░░░ 70%  UE 5.8 ✅ · Unity 6 ✅ · 材质底色/法线/遮罩 + 逐材质透明混合模式 🟡（12 个透明材质里 6 个 proven、5 个 judgement、1 个 skipped）
L6 编辑器控制   ████░░░░░░ 40%  UE 官方 MCP ✅(读关卡) · Unity CLI/MCP 🟡 · funplay 两个 ⬜
L7 验证         ████████░░ 80%  离屏截图+并排对比 ✅ · 数值指标(SSIM/MAE/覆盖率/IoU) ✅ · 离线回归夹具 ✅ · MCP 协议级验收 ✅ · 逐材质/逐物体差异 ⬜
L8 编排         █████████░ 90%  registry/games/recipes/doctor/plan/docs ✅ · 案例状态+可续跑步骤图 ✅ · MCP server ✅ · skill ✅(会话级验收未独立跑) · 后台作业 ✅ · 日志统一 ✅ · **本地 Web 工作台 ✅**(四页面 + 3D 资源预览)

L9 可视化        ████████░░ 80%  任务总览✅ · 工作流可视化✅ · 资源浏览器✅(缩略图+悬停3D+查看器) · 场景结果✅(不裁剪+放大) · **资源导出 ✅**(OBJ/GLB/贴图/整案例 zip) · Agent 对话未内嵌 ⬜ · 编辑器控制入口 ⬜
```

## 游戏 × 阶段 覆盖矩阵

| 游戏 | 引擎 | 截帧 | 摄入 | 源资源读取 | 对账 | UE 5.8 | Unity | 主要缺口 |
|---|---|---|---|---|---|---|---|---|
| 鸣潮 WuWa | UE4 | RenderDoc ✅ | ✅ | CUE4Parse ✅(用户) | 全库索引，96% 实例带源名 | ✅ | ✅ | 透明 pass 已合并、逐材质混合模式已分；水面可见（UV 缺失已用世界投影绕过），但水线/泡沫/折射未还原；粒子/billboard 跳过；天空用引擎大气，游戏天空未还原 |
| 绝区零 ZZZ | Unity | 3DMigoto ✅ | ✅ | AnimeStudio ✅(用户,游戏未装) | 世界矩阵+相机已接 `build-model`，58 角色实例放置 | ✅ 角色 | ✅ 角色 | 头/脸(GPU 间接绘制)；141/213 draw 无刚性矩阵（UI pass）；bind pose；参考帧是 UI 界面，指标不能当放置质量 |
| 原神 / 星铁 | Unity | 3DMigoto(未测) | 预期可用 | AnimeStudio(未测) | ⬜ | ⬜ | ⬜ | 本机无安装、无截帧：全部未验证 |
| 终末地 Endfield | Unity 2021.3 IL2CPP | 未试(有反作弊) | — | ⚠ VFS 加密，通用工具打不开 | ⬜ | ⬜ | ⬜ | 需要游戏专用源适配器；先确认是否要做 |
| 通用 Unity 包 | Unity | — | UnityPy ✅(合成) | UnityPy/AssetRipper ✅(合成) | — | ✅(合成) | ✅(合成) | 没有本机可读的商业游戏样本 |
| 通用 UE pak | UE | RenderDoc | ✅ | CUE4Parse | ✅ | ✅ | ✅ | 每个游戏要 EGame/AES/usmap |

## 推进顺序

**阶段 A（已完成）建图 + 代表性验证**
- ✅ 工具能力地图（registry 40 项、可机读、状态分级），游戏档案 7 个，工作流 5 条，`doctor/plan/docs`
- ✅ 验证：RenderDoc→UE+Unity（鸣潮世界场景）；3DMigoto→Unity+UE（绝区零角色）；Unity 包→UE+Unity（合成，矩阵与引擎真值 1.6e-7）；UE 官方 MCP 读取重建关卡；CUE4Parse 指纹精确匹配 53 个 GPU 网格
- ✅ 发现并记录：HoYo 类需要专用解包器；GPU 间接绘制使 3DMigoto 部件边界丢失；Endfield 容器加密

**阶段 B（下一步，按价值/风险排序）**
1. ✅ 对账闭环：SourceProbe `dump-index` 全库索引（41,133 包 / 157,691 section）→ per-draw 精确身份，UE/Unity 对象带源资产名；帧 3422 96.2%、帧 61250 96.8% 实例覆盖
2. 鸣潮补全（大部分完成，仍有缺口）：
   - ✅ **水面可见性已解决**。根因不是绕序/负 Z 缩放，而是数据：捕获到的水面网格没有可用 UV——它的顶点缓冲 SRV 解出来 `u` 恒为 1.0（`m0517`，逐点检查：SNORM 通道只有 13 个不同的 u 值且近似 0，UNORM 通道 ch0 全 1.0），所以材质采样到的是同一个 texel，整片渲成一块平色板。
     改法：水体 master 用世界坐标做平面投影 UV，加 fresnel 驱动的不透明度和 `WorldTile` / `Opacity` / `NormalStrength` 参数；图形变了就重建资产（旧资产曾因复用而保留旧图）。
     现状：`wuwa_bridge` 截图里水面可见（青绿色水面 + 波纹），但水线/泡沫/折射/吸收/焦散仍未重建。
   - ✅ **透明/粒子材质按材质分混合模式**：以前一个 master 对所有透明面都用贴图 alpha，把"黑底带亮点"的片渲成实心黑卡、把软片裁成硬边。现在每个材质得到 `alpha` / `red` / `luminance` / `additive` / `skipped` 之一，导入结果逐个记录证据是 **proven**（从 PS 反汇编 + D3D11 混合状态读出）还是 **judgement**。`wuwa_bridge` 的 12 个透明材质：6 proven、5 judgement、1 skipped。
     实测：ssim 0.3174 → **0.3394**，mae 0.3403 → **0.2997**，coverage_error 0.2415 → **0.1489**。
   - ✅ **光照/天空改用引擎默认**：发射的关卡现在就是一盏标准平行光（rotator 0,-50,30）+ SkyAtmosphere + 实时捕捉的 SkyLight，引擎默认曝光，**没有后处理体积**。可选覆盖仍然保留：`RF_SUN_INTENSITY` / `RF_SKY_INTENSITY` / `RF_FOG`。
     **游戏的天空仍未还原**：`build` 丢掉了捕获里的天空几何与云层（见下），关卡用的是引擎大气，不是游戏那张天。
   - 未做：粒子/billboard 仍无法定位（已跳过而不是伪造）；depth-only 植被 pass 与主 pass 是同一批，未合并。
   - ⚠ 关于 `sky_radius`：这条规则实际丢的是 2 个实例——`SM_CloudCover_01`（eid 11311，scale 160000）和一个 LightFog 网格（eid 3829）。**捕获里的天空穹顶 `m0000`（eid 2037，2159 顶点，局部半径 41,431）并没有被丢，它作为普通静态网格在场景里。** 想"换掉引擎天"需要另外处理它（见下面 Next steps）。
3. 🟡 Agent 入口（MCP + skill）：`renderforge/mcp_server.py` 暴露 `doctor, plan, games, status, run, step, metrics, job_start, job_status, job_log, jobs`（stdio，仅标准库，JSON 文本返回，失败为 `isError`），`step` 里含 `shaders`；已在 `.mcp.json` 注册为 `renderforge`，项目 skill 在 `.claude/skills/renderforge/SKILL.md`。
   **协议级验收已做**：`scripts/dev/mcp_acceptance.py` 用原始 JSON-RPC over stdio 对服务器本身跑 14 项检查（initialize / tools-list / doctor / plan / status / CLI 一致性 / step-build / metrics 一致性 / 缺图 isError / 错误后仍存活），**14/14，约 10 s**。
   **仍未做**：全新 Claude 会话只用 MCP 工具走一遍。协议测试与"会话真的能这么用"不等价。
   注意：这个验收会 `step build` 重建 `wuwa_f3765`，因此 running 之后 `status` 会报 `emit-unity` 过期（build/scene.json 比 Unity 结果新）——这是正常的，不是回归。
4. ✅ 绝区零适配器（**已接进 build**）：`renderforge/adapters/zzz.py` 的矩阵结论不变，本轮把它接进了构建路径。
   - 每 draw 世界矩阵取自 `vs-cb1[0..3]`；相机姿态取自 `vs-cb0[123..126]`（camera-to-world，其第 3 行与 `vs-cb0[126]` 的 eye 字面量**误差 0.0**）；view 取自 `vs-cb0[119..122]`。
   - 213 个 draw 里 **72 个**给出刚性世界矩阵；通过 `build-model` pick 的 **58 个角色 draw 全部有矩阵并放置**，其余 141 个是屏幕空间 UI/非刚性 pass，如实报告、不伪造。
   - 证据：draw 106 用单位基投影与转储的 2880×1368 剪影 **IoU 0.9783**（需 +632 px 的 x 平移，由 `vs-cb0[21].x == -0.44` 的屏幕空间项解释）；若再叠 `games.json` 的 `model_basis` 则降到 **0.5524**，所以这些矩阵上**不能**再乘 basis。
   - **接入方式**：游戏档案 `zzz -> placement_adapter: zzz`；`build-model` 自动调用 `solve_placement()`，把 58 个实例（世界矩阵 + `decompose` 出的 TRS）和相机写进 `build/scene.json`，并把标注副本留在 `export/extract_placed.json`。`--adapter none` 可回到单位放置；其他游戏没有这个字段，行为不变。
   - **相机**：场景用 cb3 的 3840×2160 世界帧（hfov 91.49 / vfov 60），不是角色预览的 2880×1368 / vfov 24；两个相机同姿态，`extract_placed.json` 同时保留 `camera`（preview）与 `frame_camera`。`shot` 的 `fov`/分辨率由构建记录，分辨率不再固定 1920×1080。
   - **端到端**：`ue-import` 15 meshes / 7 materials / 58 instances，4K 截图里角色直立、全身、位于画面中央；`unity-import` 同样 58 实例，Unity 截图里脸、头发、裙子、剑与贴图都完整。UE 截图偏暗是引擎默认光照 + 预览级材质，不是放置错误。
   - 指标：UE ssim 0.2368 / mae 0.3167 / coverage_error 0.2473；Unity ssim 0.2400 / mae 0.4902 / coverage_error 0.7383。**这些数字不能当作放置质量**：参考帧是 UI 角色界面，场景是从世界相机渲染的 3D 角色，前景/背景构成完全不同；UE 那张甚至被方向检测误判为 flipped。几何证据仍是 draw 106 的 RT 剪影 IoU。
   - 头脸缺失（GPU 间接绘制）、蒙皮姿态（POSITION0 = bind pose）仍未解决。
5. ✅ 材质 PBR 通道语义：**已调查，结论是否定的**。线性伴随贴图**不是** ORM：`o3.xyz = lerp(t2.rgb, 1.0, saturate(cb0[279].w))` 是被标量混向白的三通道色调；`o2.y` 是对 `R32_FLOAT` 遮罩采样乘 10 后取 `min`；着色器里没有 roughness/metallic 参数，PBR 输入来自逐材质 cbuffer 标量与 3D LUT（`t5`/`t6`）；贴图槽位在不同 draw 间不稳定。把那些图接成粗糙度等于凭空发明材质响应。
   真正的路径是解码逐材质常量块（`ps3`）。证据链见 [docs/MATERIAL_SEMANTICS.md](docs/MATERIAL_SEMANTICS.md)，不要在这里重复。
6. ✅ 验证指标 + 回归夹具：`metrics`（BT.601 亮度 8×8 均匀窗 SSIM、MAE/RMSE/p95、前景覆盖率误差、mask IoU；方向检测，不放大图像）与 `selftest`（6 项离线检查、约 4 s、无 GPU/引擎）。
   `scripts/dev/mcp_acceptance.py` 是 MCP 面的 14 项协议级检查（见第 3 项）。`selftest` 实测 6 passed；MCP 验收 14/14。

**当前实测数值**（同机位近似，非像素测试；`metrics` 主分数取最新截图，另一引擎记在 `also`）：

| 案例 | 来源 | draws / instances | UE ssim / mae / coverage_error | Unity ssim / mae |
|---|---|---|---|---|
| wuwa_61250 | RenderDoc 61250 | 1017 / 26,944 | 0.3931 / 0.2421 / 0.0771 | 0.3323 / 0.2745 |
| wuwa_bridge | RenderDoc 3422 | 681 / 19,192 | 0.3394 / 0.2997 / 0.1489 | 0.3332 / 0.2721 |
| wuwa_f3422 | RenderDoc 3422（全帧） | 1718 / 19,131 | 0.2381 / 0.4631 / 0.3737 | 0.3613 / 0.3765 |
| wuwa_f3765 | RenderDoc 3765 | 19 / 19 | 0.5321 / 0.3311 / 0.0318 | 0.7315 / 0.2070 |
| zzz_frame01 | 3DMigoto Frame01 | 213 / 58 | 0.2368 / 0.3167 / 0.2473 | 0.2400 / 0.4902（coverage 0.7383） |
| unity_synth | 合成 Unity 包 | 3 instances | 无参考帧，无法打分 | — |

`—` 只表示该引擎这一侧没有可比的数字；`also` 记录另一张截图的分数。`zzz_frame01` 这一轮 UE/Unity 两侧都打了分，但**不要拿它的 ssim 当放置质量**：参考帧是 UI 角色界面，场景是从世界相机渲染的 3D 角色，前景/背景构成不同；UE 那张被判 `flipped` 是方向检测在这种构成上的误判。
`status` 在**干净状态**下 6 个案例都是 `next=done`；跑过 `scripts/dev/mcp_acceptance.py` 之后 `wuwa_f3765` 会变成 `next=emit-unity`（验收自己重建了 build），重发射一次即恢复。

**阶段 B 工作台（已完成，独立于上面的补全项）**
- ✅ 案例状态层 `renderforge/caseinfo.py` + `status [case] [--json]`：来源（rdc/3dmigoto/unity-bundle）与 8 个阶段的产物都从磁盘推断，单案例给出数字与一条可执行的 next；
  会核对引擎工程，记录的导入已不存在时改报 stale（实际抓到 `wuwa_f3422`，本会话重新导入并重拍）。6 个案例目录（含无 `case.json` 的旧目录）都跑过；`backfill [--write]` 为旧目录补写 `case.json`（标 `inferred: true`）。
- ✅ 可续跑步骤图 `renderforge/pipeline.py` + `run <case> [--from/--to/--force]`：顺序随来源变化；产物存在即跳过（shot 只认 `case.json` 里仍存在的文件）；缺输入的步骤带原因跳过；失败停止并打印续跑命令；
  build 选项持久化在 `case.json -> build`。跑过：`run wuwa_f3765`（19 draw 旧案例；export 已在磁盘所以跳过）→ build → UE 导入 → 截图 → metrics；
  `run wuwa_f3422 --from shot`（重新导入后）；`run --from build --force` 端到端。留档的作业日志是最后一次 `run wuwa_f3765 --from build --force`：
  `workspace/_jobs/20261024-221351-007c7b.log` 显示 build → emit-ue → shot → metrics 全成功、exit 0、39 s。
- ⚠ 边界：`run` 当前覆盖 RenderDoc / 3DMigoto / Unity 源三条步骤图，但**不会交互式选择场景 pass**——它按 survey 自动选“类 G-buffer”的 pass；要别的 pass 仍需显式 `export --range/--eids`。
- `metrics` 在 `metrics.json -> also` 里同时记录两个引擎的分数（默认主分数取最新截图）；没有截帧参考的案例（`unity_synth`）无法打分。

**阶段 B 可视化工作台（本轮完成，用户已在用）**
- ✅ `python -m renderforge web`（`renderforge/web/`，标准库 `ThreadingHTTPServer` + 原生 JS，无框架无构建）→ `http://127.0.0.1:8770/`。
- ✅ 页面：**任务总览**（阶段徽章、draw/网格/实例、VP 拟合、身份覆盖、该案例参考帧缩略图、5 s 轮询）、**工作流**（按来源的步骤图、每步真实结果、单步重跑/整条运行）、**资源浏览器**（模型按源资产名分组、服务端渲染缩略图、纹理/材质、搜索）、**场景结果**（参考帧 + 各引擎截图、导入报告、SSIM/MAE/RMSE/p95/覆盖率/IoU、构建统计）。
- ✅ **3D 资源预览**：悬停缓慢旋转，点击打开轨道查看器。**全页单一 WebGL 上下文**（浏览器上限约 16，每卡一个会坏），按需借用/归还；几何走 `/api/meshbin` 直发 `pos.f32`+`idx.u32`（4 字节对齐），不转 glTF。three.js r186 内置（MIT），离线可用。
- ✅ 服务端不持有状态：一切由 `workspace/` 经 `caseinfo`/`pipeline`/`jobs`/`metrics` 派生，页面与 CLI 不一致即为 bug。
- ✅ **资源导出**（`renderforge/web/exporters.py` + `export_job.py`）：单网格 OBJ / GLB、贴图原图 PNG、整案例 zip。
  整案例 zip 含 `scene.json`（原样）、`meshes/`、`materials.mtl`（OBJ）、`textures/`、`INSTANCES.csv`（逐实例世界矩阵）、`CAMERA.json`、`REPORT.md`。
  卡片上有 OBJ/GLB 下载，查看器里也有；资源浏览器顶部按当前筛选/分组导出（`OBJ · N` / `GLB · N`），大案例走后台作业（`workspace/_exports/<id>.zip`）。
  约定：GLB 保持包的左上原点 UV（glTF 一致），OBJ 翻 V（OBJ 是左下原点）；GLB 索引在 >65535 顶点时用 uint32。
  子集导出只打包这些网格材质引用的贴图；整案例导出打包场景列出的全部贴图（WuWa 一帧约 600 MB，所以不是默认）。
- ✅ **任务管理（三种来源）**：任务总览左侧可**新建任务**，按来源填本机路径（也可自动判断）：
  RenderDoc `.rdc` 读 `RDOC` 魔数；3DMigoto 检查 FrameAnalysis 的 `log.txt` + 逐 draw 转储（有 `capture-summary.json` 时显示 draw/dispatch 次数与唯一数据量）；Unity 检查 `UnityFS/UnityWeb/UnityRaw/UnityArchive` 头。
  建目录后按来源自动启动第一步：`survey`（RenderDoc）/ `ingest-3dmigoto` / `ingest-unity` 后台作业；3DMigoto 摄入也进了步骤图（`export/extract.json` 是它的产物）。
  另有**重命名**（只改 `case.json -> title`，目录 id 不变，因为引擎资产路径与截图文件名由它派生）。
  左侧还有任务过滤框。目录 id 校验 `[A-Za-z0-9._-]`、拒绝保留名/已存在/路径穿越。
  ✅ 实测：页面自动识别 FrameAnalysis（`zzz-frame01`，20,986 个文件 / 354 次 draw-dispatch / 9.38 GB 唯一数据）与 Unity bundle（UnityFS）；
  从页面新建一个 Unity 任务后 `ingest-unity` 作业成功产出 `build/scene.json`，next 随即指向 `emit-ue`；
  走同一套 create→run 接口建 3DMigoto 任务并摄入成功：213 draws / 167 meshes / 83 textures，13 s（探针目录已删除）。
  ✅ 顺带修掉 `caseinfo._next_step` 里被提前 `return` 挡住的必需阶段分支：空案例现在会建议 survey/ingest，而不是直接报 done。
  ⚠ 没有做**大文件上传**：1–2 GB 的 .rdc 与十几 GB 的 FrameAnalysis 目录只能填本机已有路径；也没有做删除任务（有 `casectl.delete`，页面未接）。
  ⚠ 新建后自动跑的是该来源的第一步：`.rdc` 的 survey 会真的启动 qrenderdoc 分析整个截帧，大文件上要几分钟；3DMigoto 摄入要读整个转储。
- ✅ **按画面定位资产 + 多维分类**（`renderforge/web/locate.py`）：在参考帧上点击 → 射线逐三角形求交 → 按深度列出命中，
  选中后显示材质/贴图/同材质同贴图的网格，并把列表收窄到命中项。分类从“只有源资产名”扩到 6 个分组维度
  （源名 / 类别码 / 关卡目录 / 渲染方式 / 未知分组 / 屏幕占比）+ 叠加筛选（画面内、画面占比、已识别/未知）。
  实测：`wuwa_bridge` 535 网格中 248 有源名、495 在画面上；“Bri · 桥”筛出 4 个网格；未知 287 个按共用贴图分出 27/19/15…；
  “画面 >5% + 未知”叠出 67 个，导出按钮同步为 `OBJ · 67`。拾取 8–20 ms，点桥拱得 `SM_Old_Bri_01AH`。
  ⚠ 覆盖是**投影包围盒**，不是光栅化轮廓；相机是拟合的（`vp_match`），画面边缘可能差几个像素；
  没有可用相机的案例（`unity_synth`、`zzz_frame01` 的自动环绕机位）会明确拒绝定位并给出原因。
- ✅ **加载性能**（都是量过的）：`caseinfo._json` 按 (路径, 大小, mtime_ns) 缓存解析（20 MB scene.json 0.15 s → 命中 ~0）；
  缩略图落盘缓存（`workspace/_logs/webcache/thumbs`，按网格文件的 size+mtime 命名）；HTTP/1.1 keep-alive + 关 Nagle；
  API JSON 紧凑序列化 + gzip（资源列表 352 KB → 28 KB）；静态文件 ETag/304（three.js 2.1 MB 不再每次重下）、缩略图 `max-age`；
  导出压缩走 `compresslevel=6`。实测：`/api/case/<n>` 0.36–0.82 s → 10 ms；`scan_all` 0.65 s → 3.8 ms（热）；
  30 张冷缩略图 1.4 s → 93 ms，热 14 ms；三个 JSON 大文件常驻内存约 170 MB（按字节预算的 LRU）。
- ✅ 验收 `scripts/dev/web_acceptance.py` **68 项**（约 50 s）：原有 32 项，加 GLB 结构/OBJ 面数/Content-Disposition/贴图 PNG/zip 内 scene.json 原样/导出作业起停与下载/坏 id 拒绝，定位（6 点散测命中、深度单调、命中网格存在、关联有材质、坏坐标 400、无相机案例明确拒绝）、分类（已识别计数、画面内子集、类别码可解析、kind 词表），以及任务管理（截帧检查、拒绝未知输入、3DMigoto 与 Unity 的按来源/自动检查、三种来源新建与 `start` 字段、列名、重命名、目录不变、重复与路径穿越拒绝）。
- ⚠ 边界：页面截图是 ≤1600 px 副本（非截帧原始分辨率）；资源按选触发导入未接（`RF_ONLY_KIND`/`RF_HIDE_MESH` 已在导入器里）；**Agent 对话未内嵌**；编辑器控制（L6）无入口；植被散片类网格 3D 预览仍像"一堆草叶"（数据特性）。
  导出只给"所见数据"：网格是未放置的原始网格（世界变换在 `INSTANCES.csv`），材质是预览级，导出里的贴图引用按贴图文件名而不是材质槽语义。

**阶段 C（扩展）**
- D3D12 / Vulkan 截帧验证；Nsight / PIX 作为 RenderDoc 抓不到时的后备评估
- Unity 场景文件(.unity)与 prefab 变体、蒙皮网格与动画；UE 世界分区/FastGeo 布局
- 原神 / 星铁 / Endfield 各自适配器（需要你提供样本或确认可做）
- funplay-unreal-mcp / funplay-unity-mcp 试用，对比官方 MCP

## 诚实声明（当前所有“未做”）
- 只有 **WuWa(D3D11, UE)** 和 **ZZZ(3DMigoto)** 有真实数据验证；Unity 解包只在**我自己生成的包**和 Endfield 的一个标准文件上验证过。
- 源网格身份是**整网格**精确匹配：只画了某个大数组网格一个 section 的 draw 仍然未识别；源索引来自全库 `dump-index`（41,133 个 SM_ 包，其中约 11k 个在当前挂载视图里没有渲染 LOD 数据）。
- 材质是预览级，不代表原游戏着色效果；动画、蒙皮姿态、光照、天空均未还原。**天空**：关卡用的是引擎默认大气（平行光 + SkyAtmosphere + 实时 SkyLight），捕获里的天空穹顶 `m0000` 以普通静态网格在场景中，引擎天与它叠加，不是游戏那张天。
- 鸣潮透明/水一轮的如实状态：透明 pass（67 draw）独立导出并合并，73 个实例有仿射变换；水面精确识别为 `SM_Old_Wat_08AH`（`m0517`，4,004 顶点 / 7,346 三角），**可见性已解决**——根因是顶点缓冲 SRV 解出的 `u` 恒为 1.0（`has_uv: false`），材质用世界投影 UV 绕开，**不是**负 Z 缩放/绕序问题（先前那个猜测是错的，负面 Z 缩放仍在 `matrix` 里但纹理由世界坐标采样，不经过网格 UV）。
  仍未还原：水线/泡沫/折射/吸收/焦散、双/多层波纹法线、水面反射；粒子与 billboard 无可靠变换已跳过；depth-only 植被 pass 与主 pass 重复、未合并。
- 透明混合模式里 5/12 是 **judgement**（`luminance`：游戏用深度淡出遮罩，这里用贴图亮度替代），不是从着色器证明的；导入结果里逐条标了 `proven` / `judgement`，不要把它们当成同一置信度。
- ZZZ 适配器已接进 `build-model` 并端到端渲染过（UE + Unity）：58/58 个被选中的角色 draw 都有世界矩阵，画面上角色直立、全身、贴图完整。**但它只解决了“放在哪”**：141/213 个 draw 没有刚性矩阵（UI/非刚性 pass）被跳过；头/脸仍因 GPU 间接绘制缺失；蒙皮仍是 bind pose；参考帧是 UI 界面，所以 ssim/mask 数字不能说明放置好坏（几何证据是 draw 106 的 RT 剪影 IoU 0.9783）。
- `wuwa_f3765` 的 UE ssim 0.532 看着不错，但**前景覆盖率误差 0.0318 是在很小的场景上算的**：画面大部分是背景，主导背景色占优，覆盖率对"少了半个场景"不敏感；同一案例 Unity 侧 ssim 0.7315 却 coverage_error 0.7595（该图还被检测为上下颠倒）。不要用单张图的 ssim 比较案例之间的好坏。
- RenderDoc 版本：本机 1.45，上游已到 1.46；renderdoc-mcp 二进制只有 v0.3.0（内置 1.43 运行库），v0.3.1 需自行编译。
- 验证指标是**同机位近似**：相机近似求解（姿态/fov 可有几个百分点差异），UE/Unity 光照也不是游戏的，所以“重建得不错”的案例依然有明显绝对误差。
  这些数字回答的是“同一批几何大致落在同一位置吗 / 有没有东西渲染出来”，不是像素一致性。前景覆盖率用主导背景色估计，只为抓空场景/缺半边，不是分割。
  按引擎目标不同指标只做记录，尚未用它做通过/不通过的阈值判定。**SSIM 是近似量**：可用来比较同一案例改前/改后，不能跨案例、跨游戏比优劣。
- `selftest` 只覆盖本仓库已提交的夹具（unity_synth、wuwa_bridge、zzz_frame01 等）；夹具缺失时该项记为 skipped 而非失败，所以“6 passed”不代表覆盖任意新案例。
  其中 `toolbox-data` 检查 `docs/TOOLBOX.md` 是否比 toolbox 数据新；手改 TOOLBOX.md 或只改数据不重跑 `docs` 都会让它失败。
- 工作台（status/run/metrics/selftest/MCP）已在 6 个真实案例目录上运行。MCP 面已做**协议级**验收（`scripts/dev/mcp_acceptance.py` 16/16，约 38 s，原始 JSON-RPC over stdio，并含 CLI 一致性）；**“全新会话只用 MCP 工具走通”仍未做**。
- **Web 工作台**已在真实案例上运行并截屏验证：四页面均读真实数据，3D 预览与查看器、结果页放大与不裁剪均实际点开过。它能"看 + 跑流水线 + 导出资源"，但**不做编辑**；页内没有 Agent 对话；没有权限/多用户概念（绑定 `127.0.0.1`，仅供本机）。
  导出是派生不是求解：GLB/OBJ 里是捕获到的原始网格，不是源资产原文件；OBJ 的 MTL 只连底色/法线两张图，槽位语义没有从着色器翻译。
  两者不等价：协议测试证明工具能调通、与 CLI 一致，不证明一个 Agent 在真实会话里会正确选择它们。
- 工作区已清理（4.0 GB → 3.1 GB）：删掉了五个临时探针目录（`_probe_light`、`_probe_lighting`、`_probe_particles`、`_probe_water`、`_zzz_probe`）、被取代的导出（`wuwa_bridge/export_transparent{,_2,_3}`、`export_depth`）、旧构建（`wuwa_f3422/build2`、`build3`）、`-backup-*` 日志与日志归档。
  保留：每个案例的 `export`/`build`/`shots`/`survey`/`ids`/`unity_pkg`/`_scene_*_space`，以及 `_logs`（每个流水线族最新一次）和 `_jobs`。**注意**：`renderforge/adapters/zzz.py` 的 docstring 仍指向 `workspace/zzz_probe/scripts/`，那个目录已被清理——要复现 ZZZ 探针需重写脚本（适配器本身的结论在 docstring 里数值完整）。
- 游戏档案带 `coverage: active|extension`：**wuwa、zzz 是 active；genshin、hsr、endfield、generic-unity、generic-unreal 是 extension**（只有档案 + 适配器位置，什么都没验证——genshin/hsr 本机连安装和截帧都没有）。
- registry 40 个工具：verified-here 19、verified-prior 5、verified-synthetic 2、installed 1、candidate 8、reference 5。
- `status` 的“next”是启发式：它只建议不需要新输入数据的步骤，且 `identify` 只对 `SOURCE_INDEX_GAMES`（当前仅 wuwa）给出。案例目录里混有 `_jobs/`、`_logs/`、`_selftest/` 等非案例目录，扫描会跳过它们。
- 商业 Unity 游戏仍**没有**测试过：Unity 资源路线只在自建测试包（`unity_synth`）上验证。

## 下一步（按价值排序）

1. ✅ **ZZZ 适配器接进 build**（本轮完成）：游戏档案 `placement_adapter: zzz`，`build-model` 自动使用；58 个角色实例带世界矩阵与 cb3 世界相机，UE 与 Unity 都端到端渲染过。`--adapter none` 回到单位放置。剩余缺口是头/脸（GPU 间接绘制）、bind pose、141 个无刚性矩阵的 UI/非刚性 draw。
2. **天空穹顶**：`m0000`（eid 2037，2159 顶点，局部半径 41,431，`psDiscard`）本来就在场景里，只是和引擎天叠在一起。把它作为一个不受光的天空壳单独发射（或者反过来：识别出它并跳过引擎大气），比重建大气便宜，而且能换掉引擎的通用天。
   （`sky_radius` 规则丢的不是它——是云层 `SM_CloudCover_01` 和一个 LightFog 网格。）
3. **逐物体差异指标**：现在只有整帧数字，回答不了"哪栋楼不对"。按源资产名对指标分组即可。
4. **材质常量（真正的 PBR 路径）**：解码逐材质常量块（`ps3`）并映射到引擎参数；这取代 ORM 那条路，是一个项目而不是一个任务。见 [docs/MATERIAL_SEMANTICS.md](docs/MATERIAL_SEMANTICS.md)。
5. 长期项：ZZZ 头/脸（GPU 驱动间接绘制）、蒙皮网格、D3D12/Vulkan、Nsight/PIX、编辑器控制（L6）。
