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
L7 验证         ███░░░░░░░ 30%  离屏截图+并排对比 ✅ · 像素指标/回归测试 ⬜
L8 编排         █████░░░░░ 50%  registry/games/recipes/doctor/plan/docs ✅ · MCP 封装/skill 接入 ⬜
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
- ✅ 工具能力地图（registry 36 项、可机读、状态分级），游戏档案 7 个，工作流 5 条，`doctor/plan/docs`
- ✅ 验证：RenderDoc→UE+Unity（鸣潮世界场景）；3DMigoto→Unity+UE（绝区零角色）；Unity 包→UE+Unity（合成，矩阵与引擎真值 1.6e-7）；UE 官方 MCP 读取重建关卡；CUE4Parse 指纹精确匹配 53 个 GPU 网格
- ✅ 发现并记录：HoYo 类需要专用解包器；GPU 间接绘制使 3DMigoto 部件边界丢失；Endfield 容器加密

**阶段 B（下一步，按价值/风险排序）**
1. ✅ 对账闭环：SourceProbe `dump-index` 全库索引（41,133 包 / 157,691 section）→ per-draw 精确身份，UE/Unity 对象带源资产名；帧 3422 96.2%、帧 61250 96.8% 实例覆盖
2. 鸣潮补全（部分完成，见下）：透明 pass 已导出并合并 73 个实例、水面材质已挂接但画面不可见；粒子/billboard 无法定位已跳过；**未突破**：水面可见性、水线/泡沫/折射/吸收/焦散、天空与光照、仅靠源资源的场景版本（SourceProbe layout → Scene Package）
3. Agent 入口：把 `doctor/plan/survey/export/build/…` 封成 RenderForge MCP + skill，让 Claude 原生调用，不靠拼命令行
4. 绝区零适配器：VS 常量缓冲解码得到世界矩阵/相机；评估头脸缺失的绕过办法
5. 材质：PBR 通道语义（ORM/自发光），Unity URP 与 UE 材质模板，按游戏档案配置
6. 验证指标：SSIM/像素差 + 小型回归夹具，防止改一处坏一片

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
