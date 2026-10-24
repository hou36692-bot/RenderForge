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
L5 目标发射器   ███████░░░ 70%  UE 5.8 ✅ · Unity 6 ✅ · 材质仅底色/法线/遮罩 🟡
L6 编辑器控制   ████░░░░░░ 40%  UE 官方 MCP ✅(读关卡) · Unity CLI/MCP 🟡 · funplay 两个 ⬜
L7 验证         ███████░░░ 70%  离屏截图+并排对比 ✅ · 数值指标(SSIM/MAE/覆盖率/IoU) ✅ · 离线回归夹具 ✅ · 逐材质/逐物体差异 ⬜
L8 编排         ████████░░ 80%  registry/games/recipes/doctor/plan/docs ✅ · 案例状态+可续跑步骤图 ✅ · MCP server ✅ · skill ✅(验收未独立跑) · 后台作业 ✅
```

## 游戏 × 阶段 覆盖矩阵

| 游戏 | 引擎 | 截帧 | 摄入 | 源资源读取 | 对账 | UE 5.8 | Unity | 主要缺口 |
|---|---|---|---|---|---|---|---|---|
| 鸣潮 WuWa | UE4 | RenderDoc ✅ | ✅ | CUE4Parse ✅(用户) | 全库索引，96% 实例带源名 | ✅ | ✅ | 透明 pass 部分合并；水面材质已挂但截图不可见；粒子/billboard 跳过；天空光照未做 |
| 绝区零 ZZZ | Unity | 3DMigoto ✅ | ✅ | AnimeStudio ✅(用户,游戏未装) | ⬜ | ✅ 角色 | ✅ 角色 | 头/脸(GPU 间接绘制)、世界放置 |
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
2. 鸣潮补全（部分完成，见下）：透明 pass 已导出并合并 73 个实例、水面材质已挂接但画面不可见；粒子/billboard 无法定位已跳过；**未突破**：水面可见性、水线/泡沫/折射/吸收/焦散、天空与光照、仅靠源资源的场景版本（SourceProbe layout → Scene Package）
3. ✅ Agent 入口（MCP + skill）：`renderforge/mcp_server.py` 暴露 `doctor, plan, games, status, run, step, metrics, job_start, job_status, job_log, jobs`（stdio，仅标准库，JSON 文本返回，失败为 `isError`）；
   已在 `.mcp.json` 注册为 `renderforge`。项目 skill `.claude/skills/renderforge/SKILL.md` 已写好。验收口径：用一个**全新的 Claude 会话**只通过 MCP 工具走 doctor → plan → status → build → metrics ——
   **尚未这样跑过**；本会话只是用脚本化的 stdio 客户端逐个调用过工具（`status` 返回完整案例表、`job_start` 起后台任务成功），两者不等价。
4. 绝区零适配器：VS 常量缓冲解码得到世界矩阵/相机；评估头脸缺失的绕过办法
5. 材质：PBR 通道语义（ORM/自发光），Unity URP 与 UE 材质模板，按游戏档案配置
6. ✅ 验证指标 + 回归夹具：`metrics`（BT.601 亮度 8×8 均匀窗 SSIM、MAE/RMSE/p95、前景覆盖率误差、mask IoU；方向检测，不放大图像）与 `selftest`（6 项离线检查、约 4 s、无 GPU/引擎）。
   实测（同机位近似，非像素测试，约 1.1 s / 1080p）：wuwa_bridge UE ssim 0.322 / MAE 0.350 / 覆盖率误差 0.262；wuwa_bridge Unity 0.349 / 0.224 / 0.175；
   wuwa_61250 UE 0.393 / 0.242 / 0.077；wuwa_f3765 UE 0.533 / 0.331 / 0.032；zzz_frame01 UE 0.212 / 0.328 / 0.131（预期偏低：ZZZ 尚无世界放置）。
   `zzz_frame01` 的截图确为上下颠倒，检测结果为 flipped。selftest 数值：空间往返顶点误差 0 / 矩阵 4.4e-16；Unity 摄入 vs 引擎真值 1.6e-7；WuWa 场景包 vp_match 0.973、535 网格、19,192 实例。

**阶段 B 工作台（已完成，独立于上面的补全项）**
- ✅ 案例状态层 `renderforge/caseinfo.py` + `status [case] [--json]`：来源（rdc/3dmigoto/unity-bundle）与 8 个阶段的产物都从磁盘推断，单案例给出数字与一条可执行的 next；
  会核对引擎工程，记录的导入已不存在时改报 stale（实际抓到 `wuwa_f3422`，本会话重新导入并重拍）。6 个案例目录（含无 `case.json` 的旧目录）都跑过；`backfill [--write]` 为旧目录补写 `case.json`（标 `inferred: true`）。
- ✅ 可续跑步骤图 `renderforge/pipeline.py` + `run <case> [--from/--to/--force]`：顺序随来源变化；产物存在即跳过（shot 只认 `case.json` 里仍存在的文件）；缺输入的步骤带原因跳过；失败停止并打印续跑命令；
  build 选项持久化在 `case.json -> build`。跑过：`run wuwa_f3765`（19 draw 旧案例；export 已在磁盘所以跳过）→ build → UE 导入 → 截图 → metrics；
  `run wuwa_f3422 --from shot`（重新导入后）；`run --from build --force` 端到端。留档的作业日志是最后一次 `run wuwa_f3765 --from build --force`：
  `workspace/_jobs/20261024-221351-007c7b.log` 显示 build → emit-ue → shot → metrics 全成功、exit 0、39 s。
- ⚠ 边界：`run` 当前覆盖 RenderDoc / 3DMigoto / Unity 源三条步骤图，但**不会交互式选择场景 pass**——它按 survey 自动选“类 G-buffer”的 pass；要别的 pass 仍需显式 `export --range/--eids`。

**阶段 C（扩展）**
- D3D12 / Vulkan 截帧验证；Nsight / PIX 作为 RenderDoc 抓不到时的后备评估
- Unity 场景文件(.unity)与 prefab 变体、蒙皮网格与动画；UE 世界分区/FastGeo 布局
- 原神 / 星铁 / Endfield 各自适配器（需要你提供样本或确认可做）
- funplay-unreal-mcp / funplay-unity-mcp 试用，对比官方 MCP

## 诚实声明（当前所有“未做”）
- 只有 **WuWa(D3D11, UE)** 和 **ZZZ(3DMigoto)** 有真实数据验证；Unity 解包只在**我自己生成的包**和 Endfield 的一个标准文件上验证过。
- 源网格身份是**整网格**精确匹配：只画了某个大数组网格一个 section 的 draw 仍然未识别；源索引来自全库 `dump-index`（41,133 个 SM_ 包，其中约 11k 个在当前挂载视图里没有渲染 LOD 数据）。
- 材质是预览级，不代表原游戏着色效果；动画、蒙皮姿态、光照、天空均未还原。
- 鸣潮透明/水一轮的如实状态：透明 pass（67 draw）独立导出并合并，73 个实例有仿射变换；水面精确识别为 `SM_Old_Wat_08AH` 且 UE 材质链路已核实，但游戏模式截图里水面仍不可见（未定位，首选怀疑是负 Z 缩放导致绕序翻转）；粒子与 billboard 无可靠变换已跳过；depth-only 植被 pass 与主 pass 重复、未合并。
- RenderDoc 版本：本机 1.45，上游已到 1.46；renderdoc-mcp 二进制只有 v0.3.0（内置 1.43 运行库），v0.3.1 需自行编译。
- 验证指标是**同机位近似**：相机近似求解（姿态/fov 可有几个百分点差异），UE/Unity 光照也不是游戏的，所以“重建得不错”的案例依然有明显绝对误差。
  这些数字回答的是“同一批几何大致落在同一位置吗 / 有没有东西渲染出来”，不是像素一致性。前景覆盖率用主导背景色估计，只为抓空场景/缺半边，不是分割。
  按引擎目标不同指标只做记录，尚未用它做通过/不通过的阈值判定。
- `selftest` 只覆盖本仓库已提交的夹具（unity_synth、wuwa_bridge、zzz_frame01 等）；夹具缺失时该项记为 skipped 而非失败，所以“6 passed”不代表覆盖任意新案例。
  其中 `toolbox-data` 检查 `docs/TOOLBOX.md` 是否比 toolbox 数据新；手改 TOOLBOX.md 或只改数据不重跑 `docs` 都会让它失败。
- 工作台（status/run/metrics/selftest/MCP）已在 6 个真实案例目录上运行，但 MCP 的“全新会话只用 MCP 工具走通”验收**尚未做**；目前只是脚本化 stdio 客户端逐工具调用。
- `status` 的“next”是启发式：它只建议不需要新输入数据的步骤，且 `identify` 只对 `SOURCE_INDEX_GAMES`（当前仅 wuwa）给出。案例目录里混有 `_jobs/`、`_selftest/` 等非案例目录，扫描会跳过它们。
- 商业 Unity 游戏仍**没有**测试过：Unity 资源路线只在自建测试包（`unity_synth`）上验证。
