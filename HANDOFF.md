# RenderForge 交接文档

项目根：`D:\HOU\RenderForge`（git 仓库，本地提交，未 push）。先读 `CLAUDE.md`（约定+已验证事实）、`ROADMAP.md`（进度）、`docs/ARCHITECTURE.md`、`docs/TOOLBOX.md`（自动生成，别手改）。

## Goal
构建以 Claude Code 对话为入口的**多游戏、多引擎图形资源重建工具箱**（不是单一 RenderDoc→UE Demo）。范围以 Unity + Unreal 为主，UE 5.8 与 Unity 6000.0.83f1 同为已验证目标。要求：建立工具能力地图、区分通用截帧/游戏专用提取/模组工具/资源解包、复用成熟项目、游戏适配器互相隔离、区分"已验证"与"理论可行"、文档和路线图如实。用户不熟悉工程细节：自主决策，说明简洁，关键进展简短汇报，不要声称没跑过的功能完成。原始 .rdc/游戏文件只读，生成物放 `workspace/<case>/`。

## Current Progress（均在本机实际跑过，除非注明）
- **全库源网格身份（2026-10-24，阶段 B 第 1 项已完成）**：给用户的 SourceProbe 加了 `dump-index`（批量 section 哈希 + 每 LOD 索引字节）。全库 41,133 个 `SM_` 包 → 157,691 个源 section（约 25 分钟，2.8GB 索引，11,206 个包无渲染 LOD 数据）。RenderForge 侧 `renderforge/identify.py`：per-draw 有序索引 SHA-256 精确比对。帧 3422：386/681 draw、27,727/28,823 实例（96.2%）；帧 61250：592/1017 draw、43,336/44,757 实例（96.8%）。`ue-import`/`unity-import` 对象与网格以源资产名命名（`SM_Old_Des_08CS_S0`），未识别保留 `RF_<id>`。`rf_extract` 的 `ids` 模式（无几何导出）+ `--no-instanced-index`（默认关；实例化整 IB 方案在 3422 上验证过，61250 上会丢实例化 draw）。
- **第二帧端到端**：`workspace/wuwa_61250`（RenderDoc frame 61250，新 scene pass 1017 draws / 26,805 实例）：VP 拟合 0.96、UE 导入 851 网格 / 26,944 实例、截图与原帧对齐（`shots/compare.png`）。
- **工具箱数据层**：`toolbox/registry.json`（36 个工具，状态词汇 verified-here / verified-synthetic / verified-prior / installed / candidate / reference）、`games.json`（7 个游戏档案，含已验证项和缺口）、`recipes.json`（5 条工作流）、`github_meta.json`（许可证/更新时间快照）。CLI：`python -m renderforge doctor | plan --game X --target unreal|unity | docs`。
- **RenderDoc 路线**（鸣潮 D3D11，2GB 截帧）：`survey/export/build/ue-import/shot/compare/unity-import`，681 个 GBuffer draw → 19k 实例；VP 自动识别（97% 吻合）；相机在 UE 和 Unity 中都与原帧一致；VS buffer SRV 解 UV/切线；实例化 draw；masked 材质。`rf_extract.py` 在 `qrenderdoc.exe --python` 内无界面运行。
- **匹配**：`renderforge/match.py` + 用户已有 CUE4Parse 指纹（`D:\HOU\cuibo_reverse\03_source_assets\probes`，892 LOD/282 包）→ 522 个 GPU 网格中 53 个**精确匹配（误差 0）**。
- **3DMigoto 路线**（绝区零 Frame01，用户已有的 1.9GB 转储）：`ingest_3dmigoto.py` → Extract Package（213 draw/167 网格/83 贴图，6 秒）；`build-model --game zzz` 用 `model_basis` 放正；角色身体/头发/鞋带贴图在 Unity 和 UE 中可见。
- **Unity 资源路线**：`ingest_unity.py`（UnityPy）。仅在**我自己生成的合成包**上验证：层级世界矩阵与 Unity `localToWorldMatrix` 误差 1.6e-7，进入 UE 与 Unity 均成功。AssetRipper 2.0 无头 HTTP 导出同一合成包成功。
- **空间转换**：`space.py`（ue↔unity，数值校验顶点往返 <1e-6）。
- **UE 官方 MCP**：已在 `ue_project` 启用并读取重建关卡（`renderforge/httpmcp.py`，localhost:8000/mcp）。
- 文档（README/ROADMAP/ARCHITECTURE/TOOLBOX/CLAUDE）已更新；最后提交 `f33b78e` 之后的工作区改动是本交接文档与 .gitignore 微调。

## What Worked
- 无界面批处理：RenderDoc `--python`（脚本末尾 `os._exit(0)`）、`UnrealEditor.exe -game -RenderOffScreen -ExecCmds="py …"` 截图、`Unity.exe -batchmode -executeMethod`（新工程首次运行只导入，需再跑一次）。
- 求解：每 draw 最小二乘拟合 local→clip，VP 从 cbuffer 4x4 窗口挑（w 列为单位前向向量，且使多数静态 draw 的 `M@inv(VP)` 为仿射）；平面网格用 `regularize_planar`；非刚体烘焙时必须丢弃 w≤0 和超远顶点。
- UE：`duplicate_asset(Cube)` 建 StaticMesh + `build_from_static_mesh_descriptions`；ISM 通过含 ISM 组件的 Blueprint；法线靠 build settings 重算；相机用 PlayerStart + `fov`。
- UnityPy 读 Mesh：`MeshHelper.MeshHandler(mesh).process()`；对象用 `parse_as_object()`。
- 用 registry 状态词汇严格区分已验证/先前证据/候选，避免夸大。

## What Didn't Work（不要重复）
- 编辑器内 SceneCapture / 视口截图在离屏模式下黑屏或不可靠 → 用游戏模式 HighResShot。
- `create_asset(StaticMesh, None)`、`unreal.StaticMeshDescription()` 直接新建 → UE 崩溃；`StaticMeshDescription` 无法设置法线。
- 用 `DrawIndexedInstancedIndirect` 的 CPU 更新转储解析参数 → 网格被撕碎（参数是 GPU 写的）。默认用整个 IB，因此 ZZZ **头/脸缺失**（`ingest(resolve_indirect=True)` 仅供实验）。
- `gh` CLI 不存在；GitHub API 会限流 → `scripts/fetch_github_fallback.py` 用 atom feed 补。
- 提交过一次 UE/Unity 生成文件导致 `.git` 388MB，已用 soft reset + gc 清理（现 7MB）；注意 `.gitignore` 已覆盖 Unity Library/Bundles/Assets/RF*。
- Git Bash 里复杂 heredoc/转义容易出错，文件用 Write 工具写。
- WebFetch 调官方 UE 文档在该环境报错，改为读本地插件源码（`D:\UE\UE_5.8\Engine\Plugins\Experimental\ModelContextProtocol`）。

## 已知缺口（详见 ROADMAP.md）
鸣潮：透明/水/植被 pass、天空与光照未还原；蒙皮网格冻结在捕获姿态。ZZZ：头脸缺失、世界/相机放置未做（需解码 VS 常量缓冲）；ZZZ 游戏本机未安装，AnimeStudio 未重跑。原神/星铁：无数据，全未验证。终末地：资源在加密 VFS，通用工具打不开，且有反作弊，未尝试注入。Unity 源路线没有商业游戏样本。材质仅预览级。D3D12/Vulkan 未验证。Nsight/PIX 本机未装。

## Next Steps（ROADMAP 阶段 B）
1. ~~批量源网格指纹索引~~ ✅（见上）。后续：把身份用于**替换为源网格 + 源材质**（当前只用源名，几何仍是捕获网格）；数组网格 section 级匹配；UnityPy 侧做同样的批量索引。
2. 鸣潮补全透明/水/植被 pass、天空光照；并用 SourceProbe `layout` 出一份仅靠源资源的场景包。
3. 把 doctor/plan/survey/export/build… 封装成 **RenderForge MCP + skill**，让 Claude 原生调用。
4. 绝区零适配器：解码 VS cbuffer 得世界矩阵/相机；想办法恢复头脸。
5. 材质 PBR 通道语义（Unity URP / UE 模板，按游戏档案配置）。
6. 验证指标（SSIM/像素差）+ 小型回归夹具。
7. 视用户意愿：原神/星铁/终末地适配器（需样本或确认）；试 funplay 两个 MCP；Nsight/PIX 评估。

## 快速上手
```
cd D:\HOU\RenderForge
.venv\Scripts\python.exe -m renderforge doctor
.venv\Scripts\python.exe -m renderforge plan --game wuwa --target unity
```
已有案例：`workspace/wuwa_bridge`（RenderDoc 帧 3422）、`workspace/wuwa_61250`（RenderDoc 帧 61250）、`workspace/zzz_frame01`（3DMigoto）、`workspace/unity_synth`（Unity 合成包）。截图里程碑在 `docs/milestone_*.png`。

识别用法：
```
.venv\Scripts\python.exe -m renderforge identify wuwa_61250 --index "D:\HOU\cuibo_reverse\03_source_assets\probes\geom_index_all_20261023" --apply
```
