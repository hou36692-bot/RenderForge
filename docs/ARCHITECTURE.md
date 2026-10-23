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
```

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
