# RenderForge v0.1.0

**开发中**的首个公开版本。这是一个研究型工具箱，不是成品软件——请先读下面的能力边界再决定是否使用。

RenderForge 把游戏截帧（RenderDoc / 3DMigoto）和游戏自带资源包（Unity）转成可在 **Unreal Engine 5.8** 或 **Unity 6** 里打开、继续编辑的场景，并用数值指标验证还原得有多接近。

## 这个版本能做什么

| 能力 | 状态 |
|---|---|
| 鸣潮（UE4）RenderDoc 截帧 → UE 5.8 场景 | ✅ 真实数据验证：19,192 实例、535 网格，VP 拟合 97.3% |
| 源资产身份识别（与 CUE4Parse 源库 SHA-256 精确比对） | ✅ 27,727/28,823 实例（96.2%）带游戏自己的资产名 |
| 透明 pass 合并 + 逐材质混合模式判定 | ✅ 证据分级：proven（着色器反汇编）/ judgement（推断） |
| 绝区零（Unity）3DMigoto 帧 → Unity / UE 角色模型 | ✅ 上身/头发/鞋 + 贴图；头脸缺失（GPU 间接绘制，原因已查明） |
| Unity 资源包 → 场景包 | 🧪 仅自建测试包，无商业游戏样本 |
| 本地可视化工作台（总览 / 工作流 / 资源浏览器 / 场景结果） | ✅ 含画面点选定位、OBJ/GLB 导出、任务管理 |
| MCP 服务器（给 Claude 等 agent 用） | ✅ 协议级验收 16/16 |

## 这个版本不能做什么

- **材质是预览级**：底色 + 法线 + 遮罩 + 混合模式，不是游戏的着色器
- **光照不是游戏的**：用引擎默认的平行光 + SkyAtmosphere + SkyLight
- **Unity 侧有一个未修问题**：捕获的天空穹顶被渲染到相机前方，整帧被球面挡住（UE 侧正常）
- **蒙皮/动画未还原**：非刚体几何冻结在截获姿态
- **粒子/billboard 跳过**：无法可靠解出变换，宁可留缺口
- **只有整帧指标**：还不能回答"具体哪个物体不对"
- **逐材质常量块（真正的 PBR 路径）尚未解码**
- **未验证**：原神/星铁（无数据）、终末地（加密容器）、D3D12/Vulkan

## 安装

```bash
git clone https://github.com/hou36692-bot/RenderForge.git
cd RenderForge
uv venv .venv
uv pip install -e .
```

需要 Python 3.12。RenderDoc 路线需要 [RenderDoc](https://renderdoc.org/) 1.45+；发射到引擎需要 UE 5.8 和/或 Unity 6000.0。

## 30 秒自检（不需要 GPU 和引擎）

```bash
.venv\Scripts\python.exe -m renderforge selftest
```

6 项离线回归，约 4 秒。

## 验收状态

本版本发布前实际跑过：

- `selftest` — 6/6 通过
- `scripts/dev/web_acceptance.py` — 63/63 通过
- `scripts/dev/mcp_acceptance.py` — 16/16 通过（原始 JSON-RPC over stdio）

## 下载

源码 zip / tar.gz 由 GitHub 自动生成。**仓库不包含任何游戏资源、截帧数据或密钥**——你需要在本地用自己的工具和合法的游戏副本自行捕获。

## 许可证

MIT。第三方工具各有自己的许可证，详见 README 的「许可证与致谢」。

---

完整的能力说明、教程与限制见 [README](https://github.com/hou36692-bot/RenderForge#readme) 和 [ROADMAP](https://github.com/hou36692-bot/RenderForge/blob/main/ROADMAP.md)。
