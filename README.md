# RenderForge

以 Claude Code 对话为入口的**多游戏、多引擎图形资源重建工具箱**：把游戏截帧（RenderDoc / 3DMigoto）和游戏自带资源（Unity / Unreal 包体）转成可在 **Unreal Engine 5.8** 或 **Unity 6** 里查看和继续编辑的场景。

它是一组可组合的工具，不是一条固定流水线；输入不必经过 RenderDoc。

## 先看这几份文档

| 文档 | 内容 |
|---|---|
| [ROADMAP.md](ROADMAP.md) | 进度、游戏×阶段覆盖矩阵、下一步顺序、所有未完成项 |
| [docs/TOOLBOX.md](docs/TOOLBOX.md) | 工具能力地图（自动生成）：每个工具的类型/状态/许可证/最近更新/接入方式 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 分层结构、两种中立数据格式、三类工具的区别、许可证与密钥政策 |
| [CLAUDE.md](CLAUDE.md) | 给 Claude 的工作约定和已验证的技术事实 |

## 目录

| 路径 | 作用 |
|---|---|
| `toolbox/` | 数据：`registry.json`（36 个工具）、`games.json`（7 个游戏档案）、`recipes.json`（5 条工作流）、`github_meta.json`（许可证/更新时间快照） |
| `renderforge/` | Python 包：CLI、摄入、求解、对账、空间转换、发射器、MCP 客户端 |
| `renderforge/rd_scripts/` | 在 RenderDoc 内部运行的脚本（Python 3.6） |
| `renderforge/ue_scripts/` | 在 UE 内部运行的脚本（Python 3.11） |
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

# RenderDoc 截帧路线
survey <case> --rdc <file.rdc>          列 draw、找场景 pass
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

## 当前能力（如实）

- ✅ 鸣潮（UE4）RenderDoc 世界场景 → UE 5.8 与 Unity，机位一致；53 个 GPU 网格与 CUE4Parse 源网格精确匹配
- ✅ 绝区零 3DMigoto 帧 → 角色模型（上身/头发/鞋，贴图）进入 Unity 与 UE；头脸缺失（GPU 间接绘制，原因已查明）
- 🧪 Unity 资源包 → 场景包 → UE/Unity：仅合成包，层级矩阵与 Unity 真值误差 1.6e-7
- ✅ UE 5.8 官方 MCP 可对话式读取重建关卡
- ⚠ 终末地：资源容器加密，通用工具打不开；原神/星铁：本机无数据，未验证

详见 [ROADMAP.md](ROADMAP.md)。
