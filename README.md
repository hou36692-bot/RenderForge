# RenderForge

RenderDoc 截帧 → Unreal Engine 5.8 可编辑场景 的对话式工作台。

## 目录

| 路径 | 作用 |
|---|---|
| `renderforge/` | Python 包：RenderDoc 任务运行器、构建器、UE 导入脚本 |
| `renderforge/rd_scripts/` | 在 RenderDoc 内部运行的脚本（Python 3.6，仅标准库） |
| `scripts/` | 分析/实验脚本 |
| `docs/` | 笔记、发现 |
| `tools/` | 下载的第三方二进制（git 忽略） |
| `refs/` | 参考项目 clone（git 忽略） |
| `workspace/<案例>/` | 每个截帧案例的生成物（git 忽略）。**原始 .rdc 不复制、不修改** |

## 工具链（本机已验证）

- RenderDoc 1.45：`C:\Program Files\RenderDoc`，`qrenderdoc.exe --python` 无界面跑脚本
- renderdoc-mcp v0.3.0：`tools/renderdoc-mcp/bin/renderdoc-mcp.exe`，对话式分析（59 个工具）
- UE 5.8.2：`D:\UE\UE_5.8`
- Python：`.venv`（uv，3.12，numpy/pillow）

## 用法（当前）

```bash
# 1. 勘察：列出所有 draw 并按渲染目标分组
.venv/Scripts/python.exe renderforge/rd_run.py survey <capture.rdc> <out_dir>
.venv/Scripts/python.exe scripts/survey_summary.py <out_dir>/survey.json
# 2. 导出某个 EventId 范围
.venv/Scripts/python.exe renderforge/rd_run.py export <capture.rdc> <out_dir> 2037-19821
```

进度见 [ROADMAP.md](ROADMAP.md)。
