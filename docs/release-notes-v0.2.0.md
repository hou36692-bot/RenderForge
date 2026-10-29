# RenderForge v0.2.0

**未发布**：本文对应 PR [#1](https://github.com/hou36692-bot/RenderForge/pull/1)（`feat/endfield-support`），合并后再发布。当前公开版本仍是 [v0.1.0](https://github.com/hou36692-bot/RenderForge/releases/tag/v0.1.0)。

这是一个研究型工具箱，不是成品软件。下面每一条都区分“本机实际跑过”和“只是接好了、还没验证”。

## 这个版本新增什么

### 1. 工作台新建任务支持三种来源

任务总览的「+ 新建任务」不再只认 `.rdc`：

| 来源 | 识别方式 | 新建后自动跑的第一步 |
|---|---|---|
| RenderDoc 截帧 | `RDOC` 文件头 | `survey` |
| 3DMigoto FrameAnalysis 目录 | `log.txt` + 逐 draw 转储（`NNNNNN-*`） | `ingest-3dmigoto` |
| Unity 资源包 | `UnityFS` / `UnityWeb` / `UnityRaw` / `UnityArchive` 头 | `ingest-unity` |

来源可以显式选择，也可以自动判断。检查只读文件头和目录清单，不复制大文件；页面不提供上传，填本机已有路径。
3DMigoto 摄入现在是该来源步骤图的第一步，页面能看到它在等什么。

### 2. 绝区零世界放置适配器接进 `build-model`

- 游戏档案 `zzz` 声明 `placement_adapter: zzz`，`build-model` 自动使用
- 每 draw 世界矩阵来自 `vs-cb1[0..3]`；相机姿态来自 `vs-cb0[123..126]`；场景相机用 `vs-cb3[5..8]` 的 3840×2160 世界帧（hfov 91.49 / vfov 60），角色预览相机（2880×1368 / vfov 24）保留在 `export/extract_placed.json`
- 213 个 draw 里 72 个有刚性世界矩阵；通过 `build-model` 筛选的 **58 个角色 draw 全部放置**；其余 141 个 UI / 非刚性 pass 跳过并计数，不在原点补假实例
- `--adapter none` 可回到原来的模型空间单位放置
- UE 与 Unity 都重新导入并截图：角色直立、全身、贴图完整

**不能说成“绝区零已完整还原”**：头/脸仍缺失（GPU 驱动间接绘制），蒙皮是 bind pose，参考帧是角色 UI 界面，所以 `metrics` 的 SSIM/mask 数字不能当放置质量——几何证据是 draw 106 对转储 RT 剪影的 IoU 0.9783。

### 3. 独立的 RenderDoc 现场捕获路线（Endfield 优先）

新增 `python -m renderforge capture` 与 MCP `step` 的 `capture` 动作：

- `targets`：列出本机 RenderDoc 目标
- `launch`：用 RenderDoc `ExecuteAndInject` 启动程序；每次请求必须显式 `--allow-launch` / `allow_launch=true`，权限不写入档案
- `trigger`：对已注入目标抓一帧，校验 `RDOC` 头，原始 `.rdc` 只读、不复制

这条路线**故意不进入 `run`**，不会在普通流水线里启动游戏；没有全局/子进程钩子，没有保护绕过。Endfield 档案已接上，但**本机还没有成功捕获的终末地 `.rdc`**，因此只能算候选路线，不是已兼容声明。调研与边界见 [ENDFIELD_RESEARCH.md](https://github.com/hou36692-bot/RenderForge/blob/main/docs/ENDFIELD_RESEARCH.md)。

## 这个版本修复什么

- `caseinfo._next_step` 的必需阶段分支被提前 `return` 挡住（不可达），空案例会误报“全部完成”；现在会正确建议 survey / ingest
- `shot` 使用构建记录的 fov 与分辨率（支持非 16:9），不再固定 1920×1080
- 适配器场景不再误报“VP 拟合 weak (0.00)”

## 验收状态

本版本发布前实际跑过：

- `python -m renderforge selftest` — 6/6
- `python scripts/dev/web_acceptance.py` — 68/68（含三种来源的检查与新建）
- ZZZ 端到端：`run zzz_frame01 --from build-model --force` 成功；UE 导入 15 meshes / 7 materials / 58 instances，Unity 导入同样 58 实例
- MCP 面上一轮的协议验收是 16/16；本轮改动后**没有重跑** MCP 验收

## 这个版本仍然不能做什么

- 绝区零头/脸缺失（GPU 间接绘制）；蒙皮/动画未还原，仍是 bind pose
- 材质仍是预览级；光照用引擎默认，不是游戏的
- 粒子 / billboard 跳过；逐物体差异指标、逐材质常量块（真正的 PBR 路径）尚未做
- 终末地现场捕获未验证；没有终末地 `.rdc`，也没有解包样本
- 原神 / 星铁无数据；D3D12 / Vulkan 未验证
- 工作台仍只能填本机路径，没有大文件上传，也没有删除任务入口

## 安装

```bash
git clone https://github.com/hou36692-bot/RenderForge.git
cd RenderForge
uv venv .venv
uv pip install -e .
```

需要 Python 3.12。RenderDoc 路线需要 [RenderDoc](https://renderdoc.org/) 1.45+；发射到引擎需要 UE 5.8 和/或 Unity 6000.0。

## 许可证

MIT。第三方工具各有自己的许可证，详见 README 的「许可证与致谢」。
