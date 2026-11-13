# 接手 RenderForge（新会话 / 新电脑先读这里）

> 迁移基线：2026-11-13，`main` 分支。`git log -1 --oneline` 应能看到
> `Merge feat/endfield-support into main` 之后的提交。
> 阅读顺序：本文件（5 分钟）→ `CLAUDE.md`（约定 + 已验证事实）→ `HANDOFF.md`（详细工作记录）
> → `ROADMAP.md`（进度与下一步）→ `docs/ARCHITECTURE.md`（结构）。

## 0. 30 秒自检

```bash
uv sync                          # 按 pyproject.toml 建 .venv（Python 3.12）
.venv/Scripts/python.exe -m renderforge doctor
.venv/Scripts/python.exe -m renderforge selftest
.venv/Scripts/python.exe -m renderforge status
```

`selftest` 的目标是 **6 passed, 0 failed**。`doctor` 里标 `MISSING` 的是本机没装的
外部工具，不影响纯 Python 的离线检查；要用对应路线时再装（见第 5 节）。

## 1. 项目是什么

RenderForge 是**多游戏、多引擎的图形资源重建工具箱**，不是单一固定流水线：

```
截帧（RenderDoc .rdc / 3DMigoto FrameAnalysis / Unity bundle）
  → Extract Package（顶点/索引/postVS/cbuffer/贴图/着色器证据）
  → Scene Package（求解世界矩阵 + 近似相机，scene.json）
  → UE 5.8 或 Unity 6 可编辑场景
  → 离屏截图 + 对照参考帧的数值指标
```

关键分层：

| 层 | 内容 |
|---|---|
| `renderforge/` | Python 包；`python -m renderforge …` 是主入口（`cli.py`） |
| `renderforge/rd_scripts/` | 在 RenderDoc 内部跑的脚本（Py3.6，仅标准库） |
| `renderforge/ue_scripts/` | 在 UE 内部跑的脚本（Py3.11） |
| `renderforge/adapters/` | 游戏专用适配器（当前只有 `zzz.py`），游戏之间互不引用 |
| `renderforge/web/` | 本地 Web 工作台（标准库 HTTP + 原生 JS，无构建） |
| `workspace/<case>/` | 每个案例的生成物（survey/export/build/shots/…），git 忽略 |
| `ue_project/RenderForgeUE/` | UE 目标工程；只提交 `.uproject` 和 `Config/` |
| `unity_project/RenderForgeUnity/` | Unity 目标工程；只提交 `Assets/Editor/` 等源文件 |
| `toolbox/` | 工具注册表、游戏档案、工作流（数据是权威） |
| `docs/` | 架构、材料语义、自动生成的工具表、发布说明 |

## 2. 当前进度（以磁盘产物为准，不靠口头声明）

截至迁移日，`status` 在原始大工作区里是 6 个案例 `next=done`，另有
`endfield_pilot` 是一个**捕获失败**的案例（不是未完成的重建）。

| 案例 | 来源 | draws / instances | UE ssim / mae / coverage_error | Unity ssim / mae |
|---|---|---|---|---|
| `wuwa_61250` | RenderDoc 61250 | 1017 / 26,944 | 0.3931 / 0.2421 / 0.0771 | 0.3323 / 0.2745 |
| `wuwa_bridge` | RenderDoc 3422 | 681 / 19,192 | 0.3394 / 0.2997 / 0.1489 | 0.3332 / 0.2721 |
| `wuwa_f3422` | RenderDoc 3422（全帧） | 1718 / 19,131 | 0.2381 / 0.4631 / 0.3737 | 0.3613 / 0.3765 |
| `wuwa_f3765` | RenderDoc 3765 | 19 / 19 | 0.5321 / 0.3311 / 0.0318 | 0.7315 / 0.2070 |
| `zzz_frame01` | 3DMigoto Frame01 | 213 / 58 | 0.2368 / 0.3167 / 0.2473 | 0.2400 / 0.4902 |
| `unity_synth` | 合成 Unity 包 | 3 instances | 无参考帧，无法打分 | — |

**不要把这些数字当像素一致性**：相机是近似求解，灯光不是游戏的，覆盖率是启发式。
`zzz_frame01` 的参考帧是 UI 界面，ssim/mask 不能说明角色放置质量；它的几何证据是
draw 106 的 RT 剪影 IoU 0.9783。SSIM 只能比同一案例的改前/改后，不能跨案例比优劣。

### 最近一轮（2026-10-29）新增

- **Endfield 捕获路线**：`capture --capture-action watch-inject` 等新进程出现后在图形设备
  初始化前注入 RenderDoc，再触发一次捕获。实测注入成功，但 **AntiCheatExpert 拒绝运行游戏**
  （“检测到不兼容程序 RenderDoc”）。**没有绕过，也不应尝试绕过**。详见
  `docs/ENDFIELD_RESEARCH.md` 和 `toolbox/games.json -> endfield.capture_launch`。
- 这次改动已在 2026-11-13 提交并合并进 `main`。

## 3. 已经成立的事实（不要重复踩坑）

完整清单在 `CLAUDE.md` 的“已验证的关键事实”和 `HANDOFF.md` 的 `What Didn't Work`。
最贵的几条：

1. **离屏截图全黑，先怀疑自己手调的灯光/曝光。** 从引擎默认开始（标准平行光 +
   SkyAtmosphere + SkyLight，无后处理体积），一次只改一个可量化项，改完就截一张。
2. **贴图语义从着色器读，不从“贴图长得像什么”推断。** 线性伴随贴图不是 ORM。
   入口：`python -m renderforge shaders <case> --eids …`（或 MCP `step`）。
3. **平面看起来“材质坏了”，先查 UV 是否存在且非退化。** 鸣潮水面 `m0517` 的 `u` 恒为 1.0，
   不是绕序/负 Z 缩放问题。
4. **billboard/粒子不要用 postVS 除以 w 烘焙**；`w` 正负混杂，会得到退化卡片。
5. **捕获网格的身份分类不要从名字猜语义**；只有少数类别码有证据（`Bri`/`Tre`/`Wat`/`Veg` 等）。
6. **UE 资产/关卡是生成物**：重新导入会重建；材质母资产/实例要删除重建，否则沿用旧图。
7. **Windows 上不要用 `os.kill(pid, 0)` 探活**（3.12 对死进程也返回成功）。

## 4. 下一步（按价值排序，来自 ROADMAP）

1. **天空穹顶**：捕获里的 `m0000`（eid 2037，2159 顶点，局部半径 41,431）就在场景里，
   和引擎大气叠加。把它作为不受光天空壳单独发射，或识别并跳过引擎大气。
2. **逐物体差异指标**：现在只有整帧数字，按源资产名分组才能回答“哪栋楼不对”。
3. **材质常量（真正的 PBR 路径）**：解码逐材质 `ps3` 常量块，替代 ORM 那条已否定的路。
   见 `docs/MATERIAL_SEMANTICS.md`。
4. **ZZZ 缺口**：头/脸（GPU 间接绘制）、bind pose、141/213 个无刚性矩阵的 UI/非刚性 draw。
5. 长期：D3D12/Vulkan、Nsight/PIX、UE 编辑器控制（L6）。

不建议现在做的：重复已经否定的 PBR/ORM 调查、用手调灯光“贴近参考帧”、合并 depth-only
植被 pass、把 billboard 硬烘焙。

## 5. 环境与外部工具

### Python（必须）

```bash
uv venv .venv
uv pip install -e .
.venv/Scripts/python.exe -m renderforge selftest
```

`pyproject.toml` 声明 `numpy`、`pillow`、`UnityPy`。如果 `uv sync` 不可用，
用 `uv pip install -e .` 等价。`UnityPy` 是 Unity 资源路线的必需依赖。

### 引擎（按目标装）

- **UE 5.8**：默认 `D:\UE\UE_5.8`，可用环境变量 `UE_DIR` 覆盖。
  工程：`ue_project/RenderForgeUE/RenderForgeUE.uproject`（已启用官方 MCP 插件）。
- **Unity 6000.0.83f1**：默认 `D:\Unity\60000.0.83f1\Editor\Unity.exe`，可用 `UNITY_EXE` 覆盖。
  工程：`unity_project/RenderForgeUnity/`（首次打开会重建 `Library/`）。
- **RenderDoc 1.45**：`C:\Program Files\RenderDoc\renderdoccmd.exe`；
  无界面脚本走 `qrenderdoc.exe --python`，脚本末尾必须 `os._exit(0)`。

### 可选外部工具（不在迁移包里，按需装）

| 工具 | 用途 | 获取 |
|---|---|---|
| RenderDoc | 截帧/导出（核心） | https://github.com/baldurk/renderdoc/releases |
| renderdoc-mcp v0.3.0 | 对话式分析（59 tools） | https://github.com/JiaboLi-GitHub/renderdoc-mcp/releases |
| AssetRipper 2.0.0 | Unity 包导出（GPL-3.0，只能独立进程调用） | https://github.com/AssetRipper/AssetRipper/releases |
| FModel | UE pak 浏览（GUI） | https://github.com/4sval/FModel |
| XXMI / ZZMI、AnimeStudio、SourceProbe | ZZZ/WuWa 专用，需用户自备 | 见 `toolbox/registry.json` 的 `local.detect` |
| Nsight Graphics / PIX | 备选捕获 | 厂商页面 |

`%RF_TOOLS%` 指向本机工具目录；`doctor` 用它检查 `%RF_TOOLS%\xxmi\...` 等路径。
项目内的 `tools/` 是外部二进制，不进 git、不进迁移包；新机器装好后 `doctor` 会重新识别。

### 仓库参考项目（可选）

`refs/` 是三个第三方参考项目的 clone，未提交、未迁移：

- `https://github.com/GuoRen0830/RDC2UE.git`
- `https://github.com/PowerSerg10/UE5_RenderDoc_To_OBJ.git`
- `https://github.com/JiaboLi-GitHub/renderdoc-mcp.git`

需要时重新 clone 到 `refs/`，不要复制其中代码进 `renderforge/`（许可证限制）。

## 6. 迁移包里 workspace 是裁剪过的

原始 `workspace/` 是 4.5 GB 级，绝大多数是 `.rdc` 导出的网格/贴图。迁移包只保留：

| 案例 | 保留内容 | 能否直接重跑 |
|---|---|---|
| `unity_synth` | 完整（169 KB） | ✅ 合成夹具，可重跑 |
| `zzz_frame01` | 完整（86 MB） | ⚠ 需要原 FrameAnalysis 目录才能重跑 ingest |
| `wuwa_f3765` | 完整（69 MB，最小真实 RenderDoc 案例） | ⚠ 需要原 `.rdc` 才能重跑 survey/export |
| `wuwa_bridge` | `build/` + `shots/` + 参考帧 + 身份 JSON | ❌ 没有 715 MB 的 `export/`，不能重跑 export/build |
| `wuwa_61250` | 元数据 + `shots/` + 身份 JSON | ❌ 没有 export/build |
| `wuwa_f3422` | 元数据 + `shots/` + 身份 JSON | ❌ 没有 export/build |
| `endfield_pilot` | 捕获失败证据（82 KB） | ❌ 反作弊拒绝注入，不再尝试 |

保留 `wuwa_bridge/build/` 是为了让 `selftest` 的 `wuwa-integrity` 和 `metrics` 两项
在没有 GPU/引擎的情况下仍然可跑。**不要**因为它有 `build/scene.json` 就以为
`export` 也在；`caseinfo` 会如实报告缺失阶段。

原始数据在本机仓库外，迁移时**没有复制**：

```
D:\HOU\cuibo_reverse\02_captures\WutheringWaves_dx11_original_*.rdc
D:\HOU\zzz_reverse\evidence\zzmi\Frame01\FrameAnalysis-2026-09-28-223512\
D:\HOU\RenderForge\workspace\endfield_pilot\...
```

如果要继续跑真实案例，把这几个目录按原路径放到新机器，或在 `workspace/<case>/case.json`
里改成新路径。`.rdc` 只读，绝不修改/复制进仓库。

## 7. Git 与远程

- 远程：`origin = https://github.com/hou36692-bot/RenderForge.git`
- 迁移基线：`main` 分支；本地 `main` 比 `origin/main` 多出：
  - `643e0be` Add launcher-auth watch-inject capture route and record Endfield ACE result
  - `0d41e5a` Merge feat/endfield-support into main
  - 以及本次迁移文档提交
- 迁移包同时提供 `RenderForge.bundle`（所有分支的完整历史）。
  新机器可以直接复制 `repo/`（已含 `.git`），也可以：
  ```bash
  git clone RenderForge.bundle RenderForge
  cd RenderForge
  git remote set-url origin https://github.com/hou36692-bot/RenderForge.git
  git branch feat/endfield-support origin/feat/endfield-support
  ```
- **本地提交尚未 push**（迁移时没有擅自改远程）。用户确认后才 `git push origin main`。
  远程的 `main` 仍停在 `28733b8`；push 后 GitHub 上的 PR #1 历史不受影响。

## 8. 验证清单（新机器按顺序）

```bash
uv venv .venv
uv pip install -e .
.venv/Scripts/python.exe -m renderforge doctor
.venv/Scripts/python.exe -m renderforge selftest          # 期望 6 passed
.venv/Scripts/python.exe -m renderforge status            # 6 个案例，遵守裁剪说明
.venv/Scripts/python.exe -m renderforge web               # http://127.0.0.1:8770/
```

可选（需要引擎/工具）：

```bash
.venv/Scripts/python.exe -m renderforge plan --game wuwa --target unreal
scripts/dev/mcp_acceptance.py      # MCP 协议级 14/14（会重建 wuwa_f3765 的 build）
scripts/dev/web_acceptance.py      # Web 工作台 68 项（需要 6 个案例数据）
```

注意：`mcp_acceptance.py` 跑完会让 `wuwa_f3765` 的 Unity 结果相对 build 变旧，
`status` 报 `emit-unity` 是正常的；重跑 `python -m renderforge unity-import wuwa_f3765` 即恢复。

## 9. 文件地图（先看这些）

```
CLAUDE.md                    项目约定 + 已验证事实（自动加载）
HANDOFF.md                   详细交接：Current Progress / What Worked / What Didn't Work
ROADMAP.md                   分层进度、游戏×阶段矩阵、下一步
docs/ARCHITECTURE.md         结构、数据契约、许可证政策
docs/TOOLBOX.md              工具表（自动生成，别手改）
docs/MATERIAL_SEMANTICS.md   PBR/贴图语义的否定结论
docs/ENDFIELD_RESEARCH.md    Endfield 捕获实测与反作弊结论
docs/RESUME_HERE.md          本文件
renderforge/cli.py           主入口
renderforge/pipeline.py      步骤图 / 可续跑
renderforge/caseinfo.py      从磁盘推断案例状态
renderforge/web/             本地工作台
toolbox/registry.json        工具注册表（状态是权威）
toolbox/games.json           游戏档案 + 已验证/缺口
toolbox/recipes.json         工作流
```

## 10. 交接时未完成 / 待确认

- [ ] 本地 `main` 的两个提交尚未 push 到 GitHub（需用户确认）。
- [ ] `workspace/` 的裁剪快照不是完整数据；真实 `.rdc`/FrameAnalysis 仍在原机器仓库外。
- [ ] 新机器未安装的外部工具（见第 5 节）由用户按需安装，`doctor` 会如实报 `MISSING`。
- [ ] 迁移包里的 `repo/` 已含 `CLAUDE.md`、`HANDOFF.md`、`.mcp.json`、`.claude/` 等
      被 `.gitignore` 忽略的本机文件；它们不会被 `git status` 显示，但必须随包复制。
