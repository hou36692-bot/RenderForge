# 终末地接入调研

日期：2026-10-29。本文区分公开工具能力、本机检查和尚未验证的推断；**不是已兼容声明**。

## 首轮目标（用户修正后的当前范围）

用户明确要求：**先拓展终末地现场截帧模块，没有样本就现场截；解包模块仅验证少量网格/贴图可以导出。** 不做地图全局准备，不把没有捕获文件当作改变路线的理由。

当前路线：正常调试工具现场截帧 → `survey/export/build` 限定实际 draw 与资源范围 → Scene Package → 既有引擎发射与验证。源资产读取是独立的小规模能力验证，后续才研究帧资源到源资产的关联。

以下地图工具调研保留作参考，不再是首轮前置条件。现场捕获已由用户提出；仍不修改反作弊、不使用保护绕过、不自动更新游戏或登录账号。

## 路线核实

| 工具 | 公开证据支持的用途 | 限制与定位 |
|---|---|---|
| [Endfield-map-extractor](https://github.com/An1X3R/Endfield-map-extractor) | 地图区域选择、实例布局、Blender 场景构建 | 主候选，尚未本机运行。首次准备可能需要全局索引，不等于按区域即可局部准备 |
| [EndfieldStudio](https://github.com/EIHRTeam/EndfieldStudio) | CLI `endfield-dump` 的 PNG、OBJ、Mesh/Material JSON、源路径索引导出 | 资源补充；没有完整实例布局/场景重建的明确承诺。不同作者的同名项目不能混用 |
| [XXMI Launcher](https://github.com/SpectrumQT/XXMI-Launcher)、[EFMI-Package](https://github.com/SpectrumQT/EFMI-Package)、[EFMI-Tools](https://github.com/SpectrumQT/EFMI-Tools) | D3D11 运行时工具及角色/武器、LOD、Blender mod 工作流 | EFMI-Package 把静态对象支持列为计划项；不是本轮离线场景链路，不安装或启动 |
| [RenderDoc](https://github.com/baldurk/renderdoc) | 通用 GPU 帧、pass、纹理、Shader 与 post-VS 分析 | 未核实终末地本机捕获成功；存在反作弊边界，不能把 API 支持当作可直接捕获 |

### `data_package` 不等于完整场景包

公开 `src/webui_export_worker.py` 的数据包路径写出选择、请求、layer JSONL、manifest 和 audit，不调度 Blender；静态实例记录没有完整网格和材质贴图文件。完整几何/近似材质走 `.blend` 构建路径。

可核实的实例字段包括 `instanceId`、`assetId`、`positionUnity`、`matrixUnityColumnMajor`、`bindingStatus` 与 `source`。布局和资源解析记录可含 CAB/PathID，但两工具之间的 CAB/PathID 关联尚未实际验证，不作为默认前提。64 位 PathID 应以字符串保存。

矩阵类型 18/27、世界/局部含义、单位与 Blender 换轴，仍须在真实小样本上比较。工具输出写着 `worldResolution: world` 不是独立验证。

## 本机检查

检查命令使用项目 `.venv/Scripts/python.exe`，第三方数据读取使用 `-I -B`。游戏文件保持只读。具体值在 [本地检查记录](../workspace/endfield_pilot/preflight/local_inventory.json)。

- 已执行 `doctor`、`plan --game endfield --target unreal` / `unity` 和 `status`。RenderDoc 1.45、UnityPy 1.25.4、UE 5.8、Unity 6000.0.83f1、AssetRipper 可检测到。
- Blender 注册信息：4.5.13 和 5.2.1；尚未运行终末地场景桥接。
- 普通 `globalgamemanagers` 可读出 Unity 2021.3.34f5，游戏补丁/内容版本仍未知。
- `Endfield.exe` 修改时间独立核实为 2026-08-21 00:34:29 +08:00。初步子任务报告的 2025 年日期有误；文件时间本身也不是游戏版本。
- `index_initial.json` 和 `index_main.json` 都不是明文 JSON，是有效 base64 编码的二进制。最初按明文 JSON 读取失败，随后仅核实编码/长度/哈希，没有解密或输出内容。
- StreamingAssets 的 VFS：17 个 `.blc`，1041 个 `.chk`，约 60.9 GB；Persistent 的 VFS 另有 21 个 `.blc`、99 个 `.chk`，约 2.69 GB。1058 是前一组索引与 chunk 的合计，不是 `.chk` 数量。
- 没有终末地 `.rdc`、Frame Analysis、完整场景导出或成功案例。其他工程已有的角色模型不能代替场景验收。

## 接入边界

1. 原始目录与文件只读；输出在 `workspace/endfield_pilot/`，不复制大型游戏容器。
2. 候选提取器独立运行，不把其代码作为 RenderForge 的依赖导入。MIT 主仓库附带组件仍可能是 GPL/AGPL；不默认再分发，不保存或输出密钥。
3. 下载与解压物各自放入新的空目录；自有脚本另置。Blender 打开外部 `.blend` 禁用自动执行脚本。
4. Scene Package 显式声明 `space: unity`，行向量矩阵、米制、D3D UV；由现有 `space.convert_scene` 转为 UE，不重复换轴/乘 100。
5. 保留实例与共享几何，多材质按 primitive 接入现有契约；缺失或代理必须报告，不在原点补假实例。
6. 离线源没有捕获机位时，只使用标记清楚的诊断相机；没有同机位参考则图像指标为 N/A。
7. 既有工具的全局 `verified-here` 不等于终末地路线通过。实际提取、导入、保存重载和截图跑过之前保持候选/未验证说明。

## 可行性闸门结果

主候选固定为 `v3.8` / commit `5b9b847df1e93a2164289a42e35deba95e615c28`。公开源代码审查没有发现所查启动器/准备流程启动游戏或运行时注入；这不是整个依赖树的安全审计。

### 格式预检：有初步一致性，但不是解包通过

- 实际只读检查两组 VFS 的 **38 个 `.blc`**：第一项明文 little-endian uint32 全部为 **3**，处于提取器代码允许的 1–100 范围。
- 提取器代码引用的 `C3442D43`、`7064D8E2` 两个组在本机存在。
- [容器头检查记录](../workspace/endfield_pilot/preflight/blc_headers.json) 只记录版本与文件元数据，没有记录密钥、nonce 或加密内容。
- 这个 header version 不是 EndfieldStudio 所称的 index codeVersion；**不能据此断言游戏 1.6.x 或解码兼容**。完整读取、资源关联和场景布局仍未跑过。

### 首次准备：必须扩大扫描范围，等待单独授权

固定版本的 `src/endfield_first_run.py` 准备计划硬编码 Map01 和 Map02；包括全量 CAB 清单、资源索引、依赖闭包、实例数据库、Map01 地形、Map02 四象限地形和 Stage119 数据集/audit。没有小区域 bootstrap。

在首次准备通过前，区域任务接口返回 `409 first_run_required`。即使只输出布局数据包也需要 Stage119。小区域选择只发生在**准备后的导出阶段**。

上游没有公布完整首跑总时长或缓存体积。公开的约几十秒/128×128 场景构建数字是在数据准备之后测量的，不能拿来估算首跑。当前工作盘约 1.52 TB 可用，但空间充足不代表允许默认大规模扫描。

所以目前停止在预检：**没有下载、安装或运行主候选提取器，没有解包，没有终末地导入器或 UE 截图。** 下一步需要用户明确允许一次全局准备，并设定试跑时间/缓存预算；否则保留证据等待已有完整区域导出，不写假定格式的场景桥接。

准备通过后再验证约 20–100 个静态实例的小区域：资源绑定、三处以上布局矩阵/距离对照、坐标/朝向/法线/UV、多材质、UE 编辑保存重载与真实可见截图。失败则保留报告和阻塞，不声称兼容完成。

源代码依据：[首次准备文档](https://github.com/An1X3R/Endfield-map-extractor/blob/5b9b847df1e93a2164289a42e35deba95e615c28/docs/FIRST_RUN_BOOTSTRAP.md)、[准备计划](https://github.com/An1X3R/Endfield-map-extractor/blob/5b9b847df1e93a2164289a42e35deba95e615c28/src/endfield_first_run.py)、[VFS 读取器](https://github.com/An1X3R/Endfield-map-extractor/blob/5b9b847df1e93a2164289a42e35deba95e615c28/src/extractor_core/vfs.py)。

## 主要公开证据

- Endfield-map-extractor：`src/webui_export_worker.py`、`src/endfield_scene_export.py`、`src/build_endfield_map_layers.py`、`src/extractor_core/instances.py`、`schemas/webui_export_job_v2.schema.json`、第三方许可证说明。
- EndfieldStudio：README、`original` 导出说明、VFS codeVersion 4/5 限制。
- EFMI-Package README 和 EFMI-Tools modder guide；XXMI Launcher 的存在不代表获得游戏注入授权。

所有公开能力均需结合具体版本和真实样本验证，本文未把第三方文档宣传当作本机成功证据。
