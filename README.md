# RenderForge

以 Claude Code 对话为入口的**多游戏、多引擎图形资源重建工具箱**：把游戏截帧（RenderDoc / 3DMigoto）和游戏自带资源（Unity / Unreal 包体）转成可在 **Unreal Engine 5.8** 或 **Unity 6** 里查看和继续编辑的场景。

它是一组可组合的工具，不是一条固定流水线；输入不必经过 RenderDoc。

## 先看这几份文档

| 文档 | 内容 |
|---|---|
| [ROADMAP.md](ROADMAP.md) | 进度、游戏×阶段覆盖矩阵、下一步顺序、所有未完成项 |
| [docs/TOOLBOX.md](docs/TOOLBOX.md) | 工具能力地图（自动生成）：每个工具的类型/状态/许可证/最近更新/接入方式 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层结构、两种中立数据格式、三类工具的区别、许可证与密钥政策 |
| [docs/MATERIAL_SEMANTICS.md](docs/MATERIAL_SEMANTICS.md) | 鸣潮材质通道的证据链：为什么线性伴随贴图不是 ORM（否定结论，别重复调查） |
| [CLAUDE.md](CLAUDE.md) | 给 Claude 的工作约定和已验证的技术事实 |

## 目录

| 路径 | 作用 |
|---|---|
| `toolbox/` | 数据：`registry.json`（40 个工具）、`games.json`（7 个游戏档案）、`recipes.json`（5 条工作流）、`github_meta.json`（许可证/更新时间快照） |
| `renderforge/` | Python 包：CLI、摄入、求解、对账、空间转换、发射器、MCP 客户端；工作台（`caseinfo`/`pipeline`/`metrics`/`selftest`/`mcp_server`/`jobs`） |
| `renderforge/rd_scripts/` | 在 RenderDoc 内部运行的脚本（Python 3.6）；`rf_extract.py` 除导出外，还有 `shaders` 模式（反汇编指定 draw 的 PS/VS，用来读贴图槽位的语义） |
| `renderforge/ue_scripts/` | 在 UE 内部运行的脚本（Python 3.11） |
| `renderforge/adapters/` | 游戏专用适配器（目前只有 `zzz.py`：解码 VS 常量缓冲拿世界矩阵/相机）；一个游戏一个模块，同一个接口，不被别的游戏引用 |
| `unity_project/RenderForgeUnity/Assets/Editor/` | Unity 编辑器脚本（导入器、测试包构建） |
| `ue_project/RenderForgeUE/` | UE 目标工程（Content 为生成物，不入库） |
| `scripts/` | 实验/验证脚本；`scripts/dev/` 是调试脚本 |
| `workspace/<case>/` | 每个案例的生成物（git 忽略）。**原始 .rdc / 游戏文件只读，不复制不修改** |
| `tools/`、`refs/` | 下载的第三方二进制 / 参考项目 clone（git 忽略） |
| `docs/` | 文档与里程碑截图 |

## 常用命令（`.venv/Scripts/python.exe -m renderforge …`）

```
doctor                                  本机装了哪些工具
plan --game wuwa --target unity         该游戏+目标引擎该用哪些工具、有哪些缺口
docs                                    由 toolbox/*.json 重新生成 docs/TOOLBOX.md

# RenderDoc 截帧路线（也可整段用 run 跑）
survey <case> --rdc <file.rdc>          列 draw、按 RT 分组找场景 pass
export <case>                           导出网格/贴图/常量/VS SRV
build <case>                            求解世界矩阵+相机 → 场景包
match <case> --fingerprints <dir>       GPU 网格 ↔ 源资产指纹
ue-import <case> / shot <case> / compare <case>
unity-import <case>                     导入 Unity 并渲染 PNG

# 3DMigoto Frame Analysis 路线（绝区零/原神/星铁等）
ingest-3dmigoto <case> --frame-dir <FrameAnalysis-...> --game zzz
build-model <case> --game zzz

# 只有 Unity 资源包（不需要截帧）
ingest-unity <case> --bundle <file>
```

读 PS 反汇编确认贴图语义：`shaders <case> --eids 2037,2263`（`--vs` 切到顶点着色器），
或 `run <case> --from shaders --eids …`；MCP 里是 `step` 带 `eids`。普通 `run` 不会跑它（需要显式 eid 列表，会带原因跳过）。

## 工作台

单步命令之外，有一层按案例工作、可续跑、能给出数字的入口（`.venv/Scripts/python.exe -m renderforge …`）：

```
status [case] [--json]        每个案例：来源、已完成阶段、关键数字、推断的下一步（一张表看全部）
run <case> [--from S] [--to S] [--force]   按来源的步骤图运行，已有产物跳过，失败可从断点续跑
metrics <case> [--shot <png>] SSIM/MAE/RMSE/p95/覆盖率误差/IoU + 方向检测 → workspace/<case>/metrics.json
selftest                      6 项离线回归（空间往返、Unity 摄入、WuWa 场景包、指标、案例扫描、toolbox 数据），约 4 s
backfill [--write]            为没有 case.json 的旧案例目录补写（内容只来自文件已证明的事实，标 inferred）
```

`status` 的一次真实输出（2026-10-25 快照；`next` 只列当下还没做的可选步骤，做完就会变少，所以再跑时这张表不会完全一样）：

```
case         source     game      stages                                          draws    inst  next
unity_synth  unity-bund generic-unity -survey -export +build -identify +emit-ue +emit-unity +shot -metrics              3  done
wuwa_61250   rdc        wuwa      +survey +export +build +identify +emit-ue +emit-unity +shot +metrics   1017   26944  done
wuwa_bridge  rdc        wuwa      +survey +export +build +identify +emit-ue +emit-unity +shot +metrics    681   19192  done
wuwa_f3422   rdc        wuwa      +survey +export +build +identify +emit-ue +emit-unity +shot +metrics   1718   19131  done
wuwa_f3765   rdc        wuwa      +survey +export +build +identify -emit-ue +emit-unity +shot +metrics     19      19  emit-unity
zzz_frame01  3dmigoto   zzz       +survey +export +build -identify +emit-ue +emit-unity +shot +metrics    213      58  done
```

`wuwa_f3765` 这一行是跑过 `scripts/dev/mcp_acceptance.py` 之后的正常状态：验收用 `step build` 重建了 `build/scene.json`，
比 Unity 那次导入新，于是 `status` 正确地把 UE 侧报成过期（`unity-import wuwa_f3765` 即可恢复，表里的 `next` 写的是阶段名 `emit-unity`）。
干净状态下六个案例都是 `done`。

`+stage` 表示该阶段的产物已在磁盘上；`next` 是让这个案例往前走的那一步。`status` 会核对引擎工程：`case.json` 记了导入成功、但 UE 的
`Content/RenderForge/<case>/Maps/Recon.umap`（或 Unity 的 `Assets/RF/<case>/Recon.unity`）不在了，就报 stale 并把 next 改成重新发射。
`run` 的 build 选项（如 `--extra-export … --extra-pass translucent`）记在 `case.json -> build` 里，重建时复用。

`metrics` 默认给该案例**最新**的那张引擎截图打分；`--shot` 接受路径，或 `shots/` 下的文件名。两个引擎都有截图时，另一张的数字一并记在
`metrics.json -> also` 里（同一次修改可能只坏一个引擎，只记一张会看不出来）。没有 `export/reference_backbuffer.png` 的案例无法打分
（如 `unity_synth`，它是从 Unity 包构建的，没有截帧参考）。

指标是**同机位近似**比较，不是像素测试：相机是近似求解的，UE/Unity 光照也不是游戏的。当前实测（`metrics.json` 最新一次；UE 列是 ssim / mae / 前景覆盖率误差，Unity 列只记 ssim / mae）：

| 案例 | UE ssim / mae / coverage_error | Unity ssim / mae |
|---|---|---|
| wuwa_61250 | 0.393 / 0.242 / 0.077 | 0.332 / 0.275 |
| wuwa_bridge | 0.339 / 0.300 / 0.149 | 0.333 / 0.272 |
| wuwa_f3422 | 0.238 / 0.463 / 0.374 | 0.361 / 0.377 |
| wuwa_f3765 | 0.532 / 0.331 / 0.032 | 0.732 / 0.207 |
| zzz_frame01 | 0.212 / 0.328 / 0.131 | 有截图，从未打分 |

`zzz_frame01` 的截图确实上下颠倒，`shot_flipped` 检测到了；它的 0.212 是**预期值**——出厂路径仍是 `build-model` 的单位放置，
ZZZ 世界放置适配器已写好但没接进 CLI。`wuwa_f3765` 的 UE ssim 看着不错，但它画面上大部分是背景，覆盖率误差是在很小的场景上算的，
同一案例 Unity 侧 ssim 0.732 却 coverage_error 0.760 且被检出上下颠倒——**不要用单张图的 ssim 跨案例比好坏**。

MCP：`.mcp.json` 里的 `renderforge`（`python -m renderforge.mcp_server`，stdio，仅标准库），与 `renderdoc-mcp` 并列。
工具：`doctor, plan, games, status, run, step, metrics, job_start, job_status, job_log, jobs`；`step` 里含 `shaders`。均为 JSON 文本返回，失败是 `isError`。
耗时步骤用 `job_start`：它就是同一条 CLI 的后台进程，日志在 `workspace/_jobs/<id>.log`，退出码写 `<id>.done.json`，换一个会话也能读。
不提供通用 shell 工具。协议级验收用 `scripts/dev/mcp_acceptance.py`（原始 JSON-RPC over stdio，14 项，约 10 s，14/14 通过）；
**“全新 Claude 会话只用 MCP 工具走通”仍未做**——协议测试证明工具能调通且与 CLI 一致，不证明 Agent 在真实会话里会正确选工具。

## 当前能力（如实）

- ✅ 鸣潮（UE4）RenderDoc 世界场景 → UE 5.8 与 Unity，机位一致；53 个 GPU 网格与 CUE4Parse 源网格精确匹配
  - 透明 pass 合并进主场景（73 实例），透明/粒子材质**逐材质**分 `alpha`/`red`/`luminance`/`additive`/`skipped` 混合模式（`wuwa_bridge` 的 12 个里 6 proven、5 judgement、1 skipped）
  - **水面可见**：根因是捕获的水面网格没有可用 UV（SRV 解出 `u` 恒为 1.0），材质改用世界坐标投影 UV；水线/泡沫/折射/反射未还原
  - 光照用引擎默认的平行光 + SkyAtmosphere + SkyLight；**游戏的天空未还原**（引擎天和捕获的穹顶网格叠在一起）
- ✅ 绝区零 3DMigoto 帧 → 角色模型（上身/头发/鞋，贴图）进入 Unity 与 UE；头脸缺失（GPU 间接绘制，原因已查明）
  - 世界/相机适配器已写好并在矩阵层验证（72/213 draw 有刚性矩阵，剪影 IoU 0.9783），**但没接进 CLI、没端到端渲染过**——所以 `zzz_frame01` 仍是单位放置
- 🧪 Unity 资源包 → 场景包 → UE/Unity：仅合成包，层级矩阵与 Unity 真值误差 1.6e-7；商业 Unity 游戏没有样本
- ✅ UE 5.8 官方 MCP 可对话式读取重建关卡
- ✅ 工作台：`status` 覆盖 6 个案例目录、`run` 一条命令续跑步骤图、`metrics` 数值对比、`selftest` 6 项离线回归全过；
  MCP 协议级验收 14/14；**“全新会话只用 MCP”验收未做**
- ⚠ 材质仍只是预览级（底色/法线/遮罩 + 混合模式）；PBR 通道语义已调查，结论是**线性伴随贴图不是 ORM**，真正的路径是解码逐材质常量块（见 [docs/MATERIAL_SEMANTICS.md](docs/MATERIAL_SEMANTICS.md)）
- ⚠ 终末地：资源容器加密，通用工具打不开；原神/星铁：本机无数据，未验证

详见 [ROADMAP.md](ROADMAP.md)。
