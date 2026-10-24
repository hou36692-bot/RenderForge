# RenderForge 交接文档

项目根：`D:\HOU\RenderForge`（git 仓库，本地提交，未 push）。先读 `CLAUDE.md`（约定+已验证事实）、`ROADMAP.md`（进度）、`docs/ARCHITECTURE.md`、`docs/TOOLBOX.md`（自动生成，别手改）。

## Goal
构建以 Claude Code 对话为入口的**多游戏、多引擎图形资源重建工具箱**（不是单一 RenderDoc→UE Demo）。范围以 Unity + Unreal 为主，UE 5.8 与 Unity 6000.0.83f1 同为已验证目标。要求：建立工具能力地图、区分通用截帧/游戏专用提取/模组工具/资源解包、复用成熟项目、游戏适配器互相隔离、区分"已验证"与"理论可行"、文档和路线图如实。用户不熟悉工程细节：自主决策，说明简洁，关键进展简短汇报，不要声称没跑过的功能完成。原始 .rdc/游戏文件只读，生成物放 `workspace/<case>/`。

**本次交接的方向（用户明确要求）**：下一会话**跳过 ROADMAP 阶段 B 第 2 项**（鸣潮透明/水/天空补全，当前卡在水面可见性），继续推进整体路线图，**完善“工作台”相关功能**——即把已有的分散命令/数据整理成可发现、可驱动、可验收的工作台：案例状态一览、流水线可续跑、验证指标（SSIM/像素差）、以及把 `doctor/plan/…` 暴露给 Claude 原生调用（MCP + skill）。这是 ROADMAP 阶段 B 的第 3、6 项，也是“工具箱”体验的核心。

## Current Progress（均在本机实际跑过，除非注明）
- **工作台层（本会话，ROADMAP 阶段 B 第 3、6 项 + 独立工作台小节）**：
  - `renderforge/caseinfo.py` + `status [case] [--json]`：扫描 `workspace/*/`，来源与 8 个阶段（survey/export/build/identify/emit-ue/emit-unity/shot/metrics）全按磁盘产物推断；单案例给数字与一条可执行 next；**核对引擎工程**（`case.json` 记了导入成功但 UE map / Unity scene 已不在 → 报 stale 并把 next 改成重新发射）。6 个案例目录全部跑过（含 3 个没有 case.json 的旧目录）；`backfill [--write]` 可补写 `case.json`（标 `inferred: true`）。
  - `renderforge/pipeline.py` + `run <case> [--from S] [--to S] [--force]`：步骤顺序随来源（rdc / 3dmigoto / unity-bundle）；产物存在即跳过（shot 只认 `case.json` 里记录且仍在磁盘的文件）；缺输入的步骤带原因跳过；失败停止并把错误写进 `case.json -> steps`，同时打印 `--from` 续跑命令；build 选项（extra_export/extra_pass）持久化并复用。**实际跑过**：`run wuwa_f3765`（导出已在磁盘，build→UE 导入→截图→metrics）、`run wuwa_f3422 --from shot`（重新导入后）、后台 `run wuwa_f3765 --from build --force` 39 s 全绿。
  - `renderforge/metrics.py` + `metrics [--shot]`：BT.601 亮度 8×8 均匀窗 SSIM（积分图）、MAE/RMSE/p95、前景覆盖率误差、mask IoU、方向自动检测；写 `workspace/<case>/metrics.json` + `metrics_ssim.png`。两个引擎都有截图时另一张记在 `also`。约 1.1 s / 1080p。
  - `renderforge/selftest.py` + `selftest`：6 项离线回归（空间往返 / Unity 摄入 vs 引擎真值 / WuWa 场景包完整性 / metrics 自洽+翻转 / 案例扫描 / toolbox 数据交叉引用），约 4 s，6 passed。
  - `renderforge/mcp_server.py`（stdio，仅标准库）+ `jobs.py`/`jobrun.py`：工具 `doctor, plan, games, status, run, step, metrics, job_start, job_status, job_log, jobs`；已注册进 `.mcp.json`（server `renderforge`）。后台作业就是同一条 CLI，日志/退出码落 `workspace/_jobs/`。**已用脚本化 stdio 客户端逐工具调用过；尚未在全新会话里只用 MCP 走完整流程**（ROADMAP 里按这个口径如实记录）。
  - 项目 skill：`.claude/skills/renderforge/SKILL.md`（何时用哪个工具、状态词汇、别夸大的规则）。
  - 本次实测指标（同机位近似）：wuwa_bridge UE 0.322 / Unity 0.349；wuwa_61250 UE 0.393 / Unity 0.332；wuwa_f3422 UE 0.238 / Unity 0.361；wuwa_f3765 UE 0.533 / Unity 0.731；zzz_frame01 UE 0.212（预期低，无世界放置）。zzz_frame01 的截图确实上下颠倒，检测为 `shot_flipped: true`。
  - 案例状态：6 个目录现在 **next 都是 done**（本次补跑了 wuwa_f3422 的 UE/Unity/identify、wuwa_61250/wuwa_f3765 的 Unity 发射与 metrics、wuwa_f3765 的 identify）。
- **工具箱数据层**：`toolbox/registry.json`（**40** 个工具：原 36 + `rf-workbench`、`rf-metrics`、`rf-selftest`、`rf-mcp`）、`games.json`（7 个游戏档案）、`recipes.json`（5 条工作流）、`github_meta.json`。`docs/TOOLBOX.md` 已用 `python -m renderforge docs` 重新生成。
- **全库源网格身份（阶段 B 第 1 项已完成）**：SourceProbe `dump-index` 全库 41,133 个 `SM_` 包 → 157,691 个源 section。`identify.py` per-draw 索引 SHA-256 精确比对：帧 3422 386/681 draw、96.2% 实例；帧 61250 592/1017、96.8%；旧目录 `wuwa_f3422` 873/1718 draw、49,728/52,933 实例（本次补跑）；`wuwa_f3765` 只有 1/19（小帧，多数几何不在全库索引里）。

## What Worked
- 无界面批处理：RenderDoc `--python`（脚本末尾 `os._exit(0)`）、`UnrealEditor.exe -game -RenderOffScreen -ExecCmds="py …"` 截图、`Unity.exe -batchmode -executeMethod`（新工程首次运行只导入，需再跑一次）。
- 求解：每 draw 最小二乘拟合 local→clip，VP 从 cbuffer 4x4 窗口挑（w 列为单位前向向量，且使多数静态 draw 的 `M@inv(VP)` 为仿射）；平面网格用 `regularize_planar`；非刚体烘焙时必须丢弃 w≤0 和超远顶点。
- UE：`duplicate_asset(Cube)` 建 StaticMesh + `build_from_static_mesh_descriptions`；ISM 通过含 ISM 组件的 Blueprint；法线靠 build settings 重算；相机用 PlayerStart + `fov`。
- 第二次导入（透明 pass）：`Extract.merge_draws` 给第二个 extract 的网格加 `x` 前缀并记录 `mesh_dir`，各 draw 用 `root_of(draw)` 解析文件，解决了同名 `m0000` 冲突。
- UE 材质实例每次导入删除重建；无 alpha 的 BC1 粒子贴图改用亮度遮罩；相机近 25 m 的透明效果默认隐藏（`RF_SHOW_NEAR_FX=1` 打开）。
- UnityPy 读 Mesh：`MeshHelper.MeshHandler(mesh).process()`；对象用 `parse_as_object()`。
- 用 registry 状态词汇严格区分已验证/先前证据/候选，避免夸大。
- 文档改动后跑 `python -m renderforge docs` 重新生成 `docs/TOOLBOX.md`，不要手改。

## What Didn't Work（不要重复）
- **单独写一个读 blend/depth state 的 RenderDoc 探针脚本会很慢**：`SetFrameEvent` 在 2GB 截帧上按升序回放；用非升序 eid 列表时会反复重放，实测 20+ 分钟无产出。查 pass 结构用 survey.json 的 RT/depth/纹理绑定即可。
- **billboard/粒子不能用 clip 空间顶点除以 w 烘焙**：`w` 有正有负，除完得到 y=0 平面上一堆退化四边形（大面积黑块遮住整个画面）。已改为跳过。
- **depth-only 植被 pass 不要合并**：714 个 draw 解出的变换退化为贴地重复卡片，且与主 pass 是同一批源资产。
- **透明材质实例不要复用旧资产**：importer 每次重建关卡，但 `MI_*` 若已存在会沿用旧父级；已改为每次导入删除并重建实例（master 用固定名保留）。
- 编辑器内 SceneCapture / 视口截图在离屏模式下黑屏或不可靠 → 用游戏模式 HighResShot。
- `create_asset(StaticMesh, None)`、`unreal.StaticMeshDescription()` 直接新建 → UE 崩溃；`StaticMeshDescription` 无法设置法线。
- 用 `DrawIndexedInstancedIndirect` 的 CPU 更新转储解析参数 → 网格被撕碎（参数是 GPU 写的）。默认用整个 IB，因此 ZZZ **头/脸缺失**（`ingest(resolve_indirect=True)` 仅供实验）。
- `gh` CLI 不存在；GitHub API 会限流 → `scripts/fetch_github_fallback.py` 用 atom feed 补。
- 提交过一次 UE/Unity 生成文件导致 `.git` 388MB，已用 soft reset + gc 清理（现约 7MB）；注意 `.gitignore` 已覆盖 Unity Library/Bundles/Assets/RF*。
- Git Bash 里复杂 heredoc/转义容易出错，文件用 Write 工具写（本次多次遇到 `\S`/`\0` 转义问题）。
- WebFetch 调官方 UE 文档在该环境报错，改为读本地插件源码（`D:\UE\UE_5.8\Engine\Plugins\Experimental\ModelContextProtocol`）。

## 已知缺口（详见 ROADMAP.md）
- 鸣潮：透明 pass 已部分合并（73 实例），水面材质链路通了但画面不可见（首选怀疑负 Z 缩放导致绕序翻转，**未验证**）；粒子/billboard 未放；depth-only 植被 pass 未合并；水线/泡沫/折射/吸收/焦散、天空与光照未还原；蒙皮网格冻结在捕获姿态。
- 绝区零：头脸缺失、世界/相机放置未做（需解码 VS 常量缓冲）；游戏本机未安装，AnimeStudio 未重跑。
- 原神/星铁：无数据，全未验证。终末地：资源在加密 VFS，通用工具打不开，且有反作弊，未尝试注入。
- Unity 源路线没有商业游戏样本。材质仅预览级。D3D12/Vulkan 未验证。Nsight/PIX 本机未装。
- **工作台层的真实边界（本会话做完后剩下的）**：
  - `run` 不会交互式挑场景 pass：它按 survey 自动选“类 G-buffer”的 pass；要别的 pass 仍得显式 `export --range/--eids`（然后用 `run --from build`）。
  - `status` 的 next 是启发式：只建议不需要新输入的步骤；`identify` 只对 `SOURCE_INDEX_GAMES`（目前仅 wuwa）给出。
  - metrics 只是记录，没有**通过/不通过阈值**；两个引擎的光照本来就不一样，跨版本比较时看相对变化而不是绝对值。
  - MCP 的“全新会话只用 MCP 走 doctor→plan→status→build→metrics”验收**还没做**；已做的是脚本化 stdio 客户端逐工具调用（含 `job_start` 后台跑完一个案例）。
  - `unity_synth` 夹具的 `expected.json` 没有 `RF_Prefab` 根的变换（selftest 如实标注“no ground truth”），要全覆盖得改 `RFMakeTestBundle.cs` 一并导出该实例的世界矩阵。
  - 鸣潮第二帧 `wuwa_bridge` 的 `export` 目录没有重跑过 pass 选择逻辑，只因产物在而被跳过——`run --force --from export` 会重新导出（约数十分钟）。

## Next Steps（下一会话从这里开始）

**工作台已就位，下一会话回到内容主线**。建议顺序：

1. **验证 MCP 原生入口（半小时，闭环上一项的验收）**：开一个全新会话，只用 `renderforge` MCP 工具走 `doctor → plan → status → step(build)`（或 `run --from build --force`）`→ metrics`，确认输出与 CLI 一致；把结果写进 ROADMAP 第 3 项的验收行。
2. **阶段 B 第 4 项：绝区零适配器**（价值最高的内容缺口）：解码 ZZZ 的 VS 常量缓冲拿到世界矩阵/相机，先让 `build` 能走真实变换路径（而不是 `build-model` 的单位放置）；再评估头脸缺失（GPU 间接绘制）的绕过。`games.json` 的 zzz 档案里已有 `model_basis` 与缺口描述，新代码放独立适配器模块。
3. **阶段 B 第 5 项：材质 PBR 通道语义**：按游戏档案配 ORM/自发光通道；先在 `wuwa_bridge` 上把 `classify_textures` 的结果与源贴图命名核对，再落到 UE 材质模板与 Unity 标准着色器。
4. **阶段 B 第 2 项（用户上次要求跳过，仍未解决）**：水面可见性（负 Z 缩放/绕序首选怀疑，可用 `status` + `metrics` 的 `mask_iou` 量化改动的收益）。
5. （参考）阶段 C：D3D12/Vulkan、Unity 场景文件、原神/星铁/终末地适配器。

**不要做的事**：不要把工作台做成 Web UI；不要在没有实际跑过的情况下把新命令标成 OK；`docs/TOOLBOX.md` 只能由 `python -m renderforge docs` 生成；`case.json` 里的 `steps` 由步骤图写，不要手改成“ok”来绕过一次失败。

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

已有案例（`status` 会实时给出）：`wuwa_bridge`（RenderDoc 帧 3422，含透明 pass 合并）、`wuwa_61250`（帧 61250）、`wuwa_f3422`（帧 3422 的早期全帧导出，1718 draw）、`wuwa_f3765`（19 draw 的小帧）、`zzz_frame01`（3DMigoto 角色）、`unity_synth`（Unity 合成包，回归夹具）。截图里程碑在 `docs/milestone_*.png`。

识别用法（`case.json -> identify.index` 已记好，`run --from identify` 可直接跑）：
```
.venv\Scripts\python.exe -m renderforge identify wuwa_61250 --index "D:\HOU\cuibo_reverse\03_source_assets\probes\geom_index_all_20261023" --apply
```

透明 pass（已验证部分；`wuwa_bridge/case.json -> build` 已记录这两个选项，`run --force --from build` 会自动带上）：
```
.venv\Scripts\python.exe -m renderforge build wuwa_bridge --extra-export workspace/wuwa_bridge/export_transparent4 --extra-pass translucent
```
