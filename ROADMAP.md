# RenderForge 开发路线图

状态图例：✅ 已验证　🟡 初步可用/部分验证　⬜ 未开始

```
[M0 工作区] ██████████ 100%   环境调查、仓库、参考项目
[M1 提取  ] ███████░░░  70%   RenderDoc 截帧 → 网格/贴图/常量缓冲
[M2 重建  ] ██░░░░░░░░  20%   变换求解（世界矩阵 / 相机）
[M3 导入UE] ░░░░░░░░░░   0%   UE 5.8 无头导入：网格、贴图、材质、关卡
[M4 对话化] ░░░░░░░░░░   0%   skill / MCP 编排，一句话跑完整流程
[M5 资源匹配] ░░░░░░░░░░ 0%   用游戏原始资源（FModel/UnityPy…）替换 GPU 提取物
[M6 材质  ] ░░░░░░░░░░   0%   从 shader / 贴图推断材质
```

## M0 工作区与调研 ✅
- ✅ 本机环境：RenderDoc 1.45、UE 5.8.2、uv/Python 3.12、git
- ✅ renderdoc-mcp v0.3.0（MIT，内置 RenderDoc 1.43 运行时）可用，用于对话式分析
- ✅ 关键发现：`qrenderdoc.exe --python` 可无界面运行 RenderDoc 完整 Python API（Python 3.6），无需手动点 GUI
- ✅ 参考项目已 clone 至 `refs/`（RDC2UE 无许可证，仅作思路参考，不复制代码）

## M1 提取（RenderDoc → 磁盘） 🟡
- ✅ `survey`：列出所有 draw 及渲染目标分组，用来定位场景 pass（1.9GB 截帧 77 秒）
- ✅ `export`：按 EventId 范围导出 顶点属性、索引、post-VS 位置、常量缓冲、PS 贴图(PNG)、参考画面
- 🟡 已在《鸣潮》D3D11 截帧上验证（角色 UI 帧、世界场景帧）
- ⬜ D3D12 / Vulkan / GL 截帧验证
- ⬜ 实例化绘制（numInstances>1）

## M2 重建 🟡
- ✅ 通用变换求解：最小二乘 local→clip 矩阵（静态网格精确，误差 ~1e-5）
- ⬜ 无需 VP 的相对世界空间（以参考 draw 为基准）
- ⬜ 相机 VP 识别（cbuffer 候选 + 验证）
- ⬜ 蒙皮网格：回退为 post-VS 烘焙

## M3 导入 UE 5.8 ⬜
- ⬜ 无头 `UnrealEditor-Cmd -ExecutePythonScript` 导入 StaticMesh / Texture / Material / 关卡 Actor
- ⬜ 截图对比 RenderDoc 参考画面

## M4 对话化 ⬜
- ⬜ 项目 skill + `.mcp.json`（renderdoc-mcp、UE MCP）
- ⬜ 单入口命令：`survey → export → build → import`

## M5 / M6 后续 ⬜
- 游戏资源匹配（FModel/CUE4Parse、UnityPy/AssetRipper），按游戏做 profile，互不影响
- 材质：贴图语义（BaseColor/Normal/ORM）推断，shader 参数还原

> 说明：每个条目都会标注“已验证 / 初步实现”。没有跑过的功能不会标 ✅。
