# RenderForge 架构：多游戏、多引擎的图形资源重建工具箱

RenderForge 不是一条固定流水线，而是一组**可组合的工具 + 两种中立数据格式 + 游戏适配档案**。Claude Code 对话是入口：
Agent 先看 `doctor`（本机有什么）和 `plan`（这个游戏 + 这种输入 + 这个目标引擎该用哪些工具、哪里有缺口），再按需调用各层工具。
输入**不必**经过 RenderDoc：可以只有 GPU 截帧、只有游戏资源包，或者两者对照。

```
        ┌─────────────── L0 获取数据 ────────────────┐   ┌────── L2 读游戏自带资源 ──────┐
        │ RenderDoc  | 3DMigoto/XXMI | Nsight | PIX   │   │ CUE4Parse(UE)  UnityPy/AssetRipper(Unity)  │
        │ (通用截帧)   (游戏专用截帧)    (候选)  (候选) │   │ AnimeStudio等游戏专用解包器(适配器)       │
        └──────────┬──────────────────────────────────┘   └───────────────┬──────────────┘
                   │ .rdc / FrameAnalysis 目录                              │ pak / bundle / VFS
        ┌──────────▼───── L1 摄入适配器 ─────────┐                          │
        │ rd_extract (RenderDoc Python)          │                          │
        │ ingest_3dmigoto (Frame Analysis)       │                          │
        └──────────┬─────────────────────────────┘                          │
                   ▼  Extract Package (extract.json + meshes/ textures/ cbuffers/)         │
        ┌──────────────── L3 对账 ─────────────────────────────────────────▼──┐
        │ match：GPU 网格 ↔ 源资产指纹（精确到 0 误差）；坐标/相机求解 solve/build │
        └──────────┬──────────────────────────────────────────────────────────┘
                   ▼  Scene Package (scene.json：meshes/instances/materials/textures/camera，声明 space)
        ┌──────────▼──── L5 目标发射器 ────┐   ┌─ L6 编辑器控制 ──────────────┐   ┌─ L7 验证 ─┐
        │ UE 5.8 (headless python)         │   │ UE 官方 MCP / Unity CLI-MCP  │   │ 离屏截图  │
        │ Unity 6 (batchmode C#)           │   │ 对话式改场景                  │   │ 并排对比  │
        └──────────────────────────────────┘   └──────────────────────────────┘   └───────────┘
        L8 编排：toolbox/registry.json · games.json · recipes.json · CLI(doctor/plan/docs) · CLAUDE.md · 用户已有 skills
           工作台：caseinfo(status) → pipeline(run) → metrics/selftest → mcp_server/jobs
```

## 工作台：案例状态 → 步骤图 → 验证 → Agent 接口

```
caseinfo（案例状态，status） → pipeline（步骤图，run） → metrics / selftest（验证） → mcp_server / jobs（Agent 接口）
```

- **`caseinfo`**（`python -m renderforge status [case] [--json]`）：扫描 `workspace/<case>/`，来源（`rdc` / `3dmigoto` / `unity-bundle`）由 `case.json` 或 export 推断；阶段固定为
  `survey, export, build, identify, emit-ue, emit-unity, shot, metrics`，每一项只按磁盘产物判断。单案例详情给出 draw/mesh/instance 数、身份覆盖、build 跳过数，并给出一条可执行的 next 命令。
- **`pipeline`**（`python -m renderforge run <case> [--from S] [--to S] [--force]`）：步骤顺序随来源变化——rdc：survey → export → build → identify →（发射半段）；
  3dmigoto：build-model → identify →（发射半段）；unity-bundle：ingest-unity →（发射半段）。发射半段看 `case.json -> target`（默认 unreal）：
  unreal = emit-ue → shot → metrics；unity = emit-unity → metrics（Unity 导入脚本自己写 PNG，没有单独的 shot 步）。产物已存在的步骤跳过（shot 只认 `case.json -> steps.shot.file` 指向且仍在磁盘上的文件，旧截图不算）；
  缺少输入的步骤（identify 的 `--index`、survey 的 `--rdc`）带原因跳过而不是终止；其余步骤失败即停止运行并打印 `--from` 续跑命令。
  会改变结果的 build 选项（如 `--extra-export … --extra-pass translucent`）写进 `case.json -> build`，重建时复用，避免静默丢掉合并的透明 pass。
- **`metrics`**（`python -m renderforge metrics <case> [--shot <png 路径>]` → `workspace/<case>/metrics.json` + `metrics_ssim.png`）：BT.601 亮度、8×8 均匀窗（积分图）的全局 SSIM，以及 MAE、RMSE、p95 绝对误差、
  前景覆盖率误差、mask IoU；两图缩到较小尺寸，不放大。方向是测出来的（两种朝向都算，`shot_flipped` 报告真实朝向）。
  默认给最新的引擎截图打分；UE 与 Unity 两张都在时，另一张的数字记在 `metrics.json -> also`，一次只坏一个引擎也能看出来。没有 `export/reference_backbuffer.png` 的案例无法打分。
- **`selftest`**（`python -m renderforge selftest`）：6 项离线回归（空间往返、Unity 摄入 vs 引擎真值、WuWa 场景包完整性、metrics 自洽+翻转检测、案例扫描、toolbox 数据交叉引用），
  约 4 s，不需要 GPU/RenderDoc/UE/Unity；失败退出码 1。
- **`mcp_server`**（`python -m renderforge.mcp_server`，stdio，仅标准库）+ **`jobs`**：工具 `doctor, plan, games, status, run, step, metrics, job_start, job_status, job_log, jobs`；
  每个工具返回 JSON 文本，异常以 `isError` 返回而不是让服务退出。刻意不提供通用 shell 工具。

让这一层可信的两条规则：

1. **done 指磁盘上真有产物，不是记录过的标志。** 引擎侧单独核对：`case.json` 记录导入成功、但
   `ue_project/.../Content/RenderForge/<case>/Maps/Recon.umap` 或 `unity_project/.../Assets/RF/<case>/Recon.unity` 已不存在时，`status` 报 stale 并把 next 改成重新发射。
   实际抓到过 `wuwa_f3422`：记录的 UE 导入已不在工程里，本会话重新导入并重拍。
   `emit-ue` 的结果既可能在 `build/ue_import_result.json`（场景本来就是 UE 空间），也可能在 `_scene_ue_space/ue_import_result.json`
   （场景先由 `convert_scene` 转成 UE 空间，如 `unity_synth`、`zzz_frame01`）；`status` 和 `pipeline` 两处都按这个顺序查。
2. **步骤图不猜输入。** 每个成功步骤写 `case.json -> steps.<name>`（`{ok, when, run, …}`）；UE/Unity 发射与截图失败也写成 `ok: false` 并带日志路径。
   其余步骤（survey/export/build/identify/metrics）出错时抛异常，`run` 停止并打印该步骤与 `--from` 续跑命令，异常文本在输出里、不在 `case.json`。
   缺输入就带原因跳过，既不标成通过，也不连坐结束整个运行。

`job_start` 起的是同一条 CLI 的后台进程（`renderforge.jobrun` → `cli.main`），日志与退出码落在 `workspace/_jobs/<id>.log` / `<id>.done.json`，
所以后台与前台行为一致，且下一个会话（或别的 agent）不必持有进程句柄也能读到结果。

## 三类工具要分清

| 类别 | 例子 | 能力 | 在本项目的位置 |
|---|---|---|---|
| **通用截帧 / GPU 分析** | RenderDoc、Nsight、PIX、renderdoc-mcp | 拿到某一帧的 GPU 真值（网格、贴图、常量、矩阵） | L0/L1。RenderDoc 是主力；其余是 RenderDoc 抓不到时的候选 |
| **游戏专用帧数据提取** | 3DMigoto Frame Analysis(+XXMI 管理)、WWMI-Tools | 对特定游戏/反作弊环境能抓的帧；数据布局带游戏特性 | L0/L1 摄入适配器；每个游戏的差异放在 `games.json` 与适配器里 |
| **模组工具** | GIMI/SRMI/ZZMI/WWMI 导入器 | **运行时替换**游戏资源，不是提取管线 | 只当参考（缓冲布局、ini 习惯），**不进入主链路** |
| **游戏资源解包** | UnityPy、AssetRipper、AnimeStudio、CUE4Parse、FModel | 直接读包体里的原始网格/贴图/场景布局 | L2；通用解析器之外，加密/自定义容器需要“游戏源适配器” |
| **目标引擎集成** | UE Python/Interchange/官方 MCP、Unity batchmode/CLI | 写入并编辑场景 | L5/L6 |

## 两个中立数据契约

1. **Extract Package**（摄入层输出）：`extract.json` + `meshes/<id>/*.f32|idx.u32` + `textures/*.png` + `cbuffers/*.bin` (+ `srv/`)。
   与来源无关；RenderDoc 与 3DMigoto 都写这个形状。RenderDoc 来源带 `postVS`（可求解世界矩阵），3DMigoto 来源没有。
2. **Scene Package**（对账层输出，发射器输入）：`scene.json` 声明 `space`（`ue` 或 `unity`）、网格实例矩阵（行向量约定）、材质、贴图、相机；
   UV 一律 D3D 约定（左上原点），Unity 发射器自己翻 V。`renderforge/space.py` 负责 ue↔unity 转换（轴置换 + 单位）。

加一个新来源或新目标，只需要写它到这两个格式之一的适配器，不碰其他环节。

## 游戏适配档案（互不影响）

`toolbox/games.json` 每个游戏一条：引擎、打包方式、可用截帧路线、可用解包器、`model_basis` 等放置参数、**已验证项和缺口**。
档案只**选择**工具并记录事实；真正的游戏专用代码（如 ZZZ 的 VS 常量缓冲解码）将放在独立适配器模块里，不被其他游戏引用。

## 许可证与密钥政策

- GPL/AGPL 工具（FModel、AssetRipper、XXMI、WWMI-Tools、Il2CppInspectorRedux）**只作为独立进程调用**，不链接、不拷贝代码。
- 没有许可证文件的参考项目（RDC2UE、UE5_RenderDoc_To_OBJ）**只参考思路**，不复制代码。
- AES 密钥等由用户已有的工具在进程内读取（如 SourceProbe），RenderForge 不保存、不打印、不写入文档。
- 不对有反作弊的联网游戏做未经确认的注入尝试（见 Endfield 条目）。

## 状态词汇（`docs/TOOLBOX.md` 里每个工具都标）

`OK` 本工作区对真实数据跑过 · `OK*` 在我生成的合成数据上跑过 · `OK(prior)` 你之前的项目里有成功证据、本次未重跑 ·
`inst` 已安装未用 · `cand` 候选 · `ref` 仅参考。**没标 OK 的不要当成可用能力。**

## 与你已有 skills 的关系

环境里已有 `render-frame-analysis`、`render-asset-match`、`ue-render-reconstruction`、`zzz-render-reconstruction`、`ue-scene-reconstruction` 等 skill，
它们是“怎么判断/怎么验收”的方法论层；RenderForge 是它们可以直接调用的**可执行工具层**（统一目录、统一数据格式、统一命令）。后续会把 `plan/doctor` 暴露给 skill。
