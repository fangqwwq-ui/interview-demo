# 简历项目 Demo 复现 —— 实施计划

> 版本：**v1.7**
> 建立日期：2026-10-01
> 项目根目录：**`E:\interview-demo`**
> 本文档为工程的唯一权威规格（single source of truth），实现与验收一律以本文件为准。

### 变更记录

| 版本 | 变更 |
|---|---|
| v1.0 | 初始计划，锁定四项决策 |
| v1.1 | ① 项目根目录迁至 `E:\interview-demo`；② 新增第 3 节资产盘点，发现 `humanize-chinese`；③ 下调 Demo B 工作量；④ 新增第三方开源引用合规风险 |
| v1.2 | **P0 资产盘点完成，结论重大**：发现 `E:\aigc-comic` 已是完成度 M1/M2 的漫剧全自动生产线（已产出 `final.mp4`、FastAPI 5 接口、pydantic 契约、音画同步），即 Demo C 的主体。① 项目定位由「从零构建」改为「已有资产收敛 + 补强」；② Demo C 方案全面重写，改为「增强既有服务 + 编排层分离」；③ 阶段工作量大面积下调，**Demo A 成为唯一需从零构建的模块** |
| v1.3 | **开源内核实测完成，Demo B 架构修正**：实测 `humanize-chinese` 后确认——其**检测能力可用**（95/100 打分 + 8 类明细 + 困惑度/突发度/段落熵CV），但**规则改写会破坏中文语法**（实测产出「之于于」「也的环节地位」等病句，且仅降 33 分仍属 HIGH）。① Demo B 改为「内核仅作检测器，改写走自研 LLM + RAG 链路」；② 自研质检层去掉困惑度/突发度（内核已提供，不得重复声称）；③ 新增 v1.3 的第 6 节修订 |
| v1.4 | **既有服务实测校正，Demo C 新增量收敛**：实起 `aigc-comic` 服务并读取 OpenAPI，确认其暴露 **8 个接口**（比 README 所述多出 `/run` `/tasks` `/tasks/{task_id}`），且 `/run` 的模型注释明确写「复用 pipeline 状态机 + 断点续跑」。① Demo C 的「状态机 / 断点续跑 / 重试」由原计划的**新增项**改判为**既有能力**，不得声称自研；② 第 8.2 节重写为「新增 vs 既有」边界表；③ Demo C 真实新增量收敛为三项：ComfyUI 出图适配器、审核层、指标层 |
| v1.5 | **网络带宽成为硬约束，torch 改分级方案**：实测官方源 ~170 KB/s、阿里云 ~340 KB/s，**换源无法解决**（瓶颈在本地出口）。① 新增第 2.4 节记录带宽约束；② torch 改分级：P1/Demo B 用 **CPU 版**（250 MB，快速解封），P2/Demo A 用 **CUDA 版**（2.6 GB，后台下载）；③ torch 版本必须锁定，否则会解析到 2.11.0 导致下载失控；④ 阶段排序据此调整为「先做不依赖 GPU 的 Demo B」 |
| v1.6 | **Demo B 链路跑通，第二个架构级发现：单一检测仪器不可用**。① 实起评测，发现第三方内核检测器在自建评测集上 **14/20 条得 0 分**，且**长度不是主因**（25→169 字分数稳定在 0→4，95 字后饱和）—— 真正原因是模式覆盖集中在「公文/学术腔」，对「评测腔/种草腔」基本失明；同时发现**上游 README 的示例分数不可复现**（标注 72 分，实测 0 分）。② 据此改为**双仪器并报**：第三方内核检测器 + 自研 AI 味词典（`demo_b_copywriting/tells.py`，实测 20/20 全部命中），评测输出**按语域分组**给出两把尺子的灵敏度，禁止合并成单一「总分」。③ 完成 Demo B 全链路：清洗层 → 索引层 → 检索层 → 改写双臂 → 质检层 → 评测框架 → 三路对照 Web 页；④ 语料改为**全自建**（35 条风格语料 + 20 条评测集，5 垂类 4 语域），不使用爬取数据 |
| v1.7 | **Demo D 门户与指标口径页完成**。① 新增 `common/metrics/claims.py`：**简历 11 条主张 → 证据映射**，带覆盖率自检（编号必须恰好覆盖 1–11、引用的指标 key 必须真实存在，否则拒绝导出）—— 这是「口径页无遗漏」的强制保障，而不是靠人工检查；② 新增 `portal/provenance.py`：三分类账本（第三方开源 / 既有自研 / 本次新增），逐项写明 License、**使用边界**与当场核实方式，其中把 `aigc-comic` 的「状态机 + 断点续跑」明确标注为**既有能力、不得声称自研**；③ 门户三页（概览入口 / 指标口径 / 来源声明）+ `/api/source` 源码查看（严格白名单，实测拦截 `.env`、目录穿越、绝对路径）+ `POST /api/refresh` 现场重算；④ 修正 README 一处**合规性事实错误**：原写「保留原 LICENSE」，实为上游**未附 LICENSE 文件**，MIT 仅由 README 徽章声明；⑤ README 补全 ComfyUI 的 **GPL-3.0** 使用边界（独立进程 + HTTP 边界 + 非再分发） |



---

## 0. 已锁定的决策

| 决策项 | 结论 | 影响 |
|---|---|---|
| LLM 推理后端 | **混合**：本地 Ollama 兜底 + 云端 API 提效 | 必须实现 Provider 抽象层，两套可热切换 |
| Demo 范围 | **A + B + C 全量**，汇聚为 Demo D 门户 | 需要统一底座，避免三套重复实现 |
| 交付形态 | 代码仓库 + 演示录屏 + 可访问 Web 门户 + 演示讲稿与话术 | 每个 Demo 除功能外，还需可录屏的稳定脚本 |
| 对外口径 | **定位为「方案的独立复现 / 技术验证版」** | README、演示话术、录屏字幕统一按此措辞 |
| 存放位置 | **全部置于 `E:\interview-demo`** | 路径使用 ASCII，规避中文路径在 `.ps1` 脚本中的编码问题 |
| 既有资产 | **原地不搬迁、不修改**，通过 HTTP 服务或只读引用复用 | 保护已验证可用的代码，避免破坏性改造 |

**口径原则（贯穿全部交付物）**：只声称「我按这套方案独立搭出了可复现的实现」，不声称「这就是当时服务 3 家工业园区 / 2 家 MCN 采购的生产系统」。所有性能数字必须能当场指出计算脚本与数据集。

---

## 1. 目标与范围

### 1.1 目标

为简历中的技术主张提供**可运行、可复现、可当场演示**的证据载体，把「写在纸上的能力」变成「打开就能跑的 Demo」。

### 1.2 范围

- 三个独立可演示的功能包（Demo A / B / C）
- 一个统一的门户与指标口径页（Demo D）
- 每个 Demo 的演示讲稿、追问应答清单、录屏清单

### 1.3 范围外

- 不修改、不搬迁 `E:\aigc-comic` 等既有工程（只通过接口或只读方式复用）
- 不在本项目内复现「运营结果型」数字（客户数量、采购合同、上线 SLA、可用性 99.8%）——这类只能靠原始材料佐证
- 不做真实生产部署、不做多租户、不做权限体系
- 不采购第三方数据（语料使用公开数据 + 自造样本）

---

## 2. 环境与硬件边界

| 项目 | 实测结果 | 对本方案的影响 |
|---|---|---|
| GPU | NVIDIA GeForce RTX 5070 Laptop，**8 GB 显存** | 本地 LLM 限 7–8B 量化；视觉/语音/LLM 需分时加载 |
| CUDA / torch | CUDA 12.8，torch 2.7.0+cu128（位于 `D:\ComfyUI\venv`） | ⚠️ RTX 5070 为 Blackwell（sm_120），**必须 torch ≥ 2.7 + cu128**，低版本报 no kernel image |
| 项目盘 E: | 1.6 TB 空闲 | 项目本体、模型缓存、向量索引、产物统一放这里 |
| 工具盘 D: | 578 GB 空闲 | ComfyUI 与既有资产所在 |
| 系统 Python | 3.13.12（托管） | 在 `E:\interview-demo\.venv` 新建独立环境 |
| 现有依赖 | 仅 fastapi / numpy / pandas / cv2 | 需补齐 torch、faiss、transformers 等 |
| LLM API Key | **全部未配置** | 需用户提供，否则云端通道不可用 |
| Ollama | **未安装** | 需安装 + 拉取量化模型 |
| FFmpeg | 既有 `aigc-comic` 依赖它合成视频 | 先验证可用，否则 Demo C 合成环节阻塞 |
| pip 源 | 清华源不可用，**统一走阿里云源** | `-i https://mirrors.aliyun.com/pypi/simple/` |

### 2.1 路径约定

| 用途 | 路径 |
|---|---|
| 项目根 | `E:\interview-demo` |
| Python 虚拟环境 | `E:\interview-demo\.venv` |
| 模型与权重缓存 | `E:\interview-demo\models` |
| 向量索引 | `E:\interview-demo\data\index` |
| 语料 | `E:\interview-demo\data\corpus` |
| 生成产物 | `E:\interview-demo\outputs` |
| ComfyUI（只读调用） | `D:\ComfyUI` → `http://127.0.0.1:8188` |
| 漫剧执行服务（复用） | `E:\aigc-comic` → `http://127.0.0.1:8000` |

### 2.2 环境隔离策略与 torch 分级方案

**Demo C 不导入 torch**——它通过 HTTP 调 ComfyUI 与 `aigc-comic` 服务，因此不需要为 C 在本项目 venv 内装 CUDA 版 torch。

**torch 采用分级方案**（因实测下载带宽受限，见 2.4）：

| 用途 | 版本 | 说明 |
|---|---|---|
| 阶段 P1 / Demo B | **CPU 版 torch** | 向量化（bge-small-zh）与评测在 CPU 上完全够用，约 250 MB，可快速解封 |
| 阶段 P2 / Demo A | **CUDA 版 torch（cu128）** | 视觉/语音实时推理需要 GPU；约 2.6 GB，安排后台下载 |
| ComfyUI 出图 | **无需本项目 torch** | 走 HTTP；ComfyUI 自带 `D:\ComfyUI\venv`（torch 2.7.0+cu128） |

- 新建独立 venv：`E:\interview-demo\.venv`（Python 3.13.14）
- torch 版本**必须锁定**，否则 `--index-url .../cu128` 会解析到最新版（实测拿到 2.11.0+cu128），下载量与耗时失控
- 可用 wheel 来源：
  - 官方 `https://download.pytorch.org/whl/cu128`（可用，实测约 170 KB/s）
  - **阿里云 `https://mirrors.aliyun.com/pytorch-wheels/cu128/`**（可用，实测约 340 KB/s；含 cp39–cp313 win 轮子）
  - 清华同类镜像 404，不可用
- 锁定版本的安装命令：
  ```powershell
  pip install --no-cache-dir "torch==2.7.0+cu128" "torchvision==0.22.1+cu128" `
      -f https://mirrors.aliyun.com/pytorch-wheels/cu128/ `
      -i https://mirrors.aliyun.com/pypi/simple/
  ```

> 降级预案（已具备条件）：若 GPU 版 torch 始终受限，Demo A 改为**跨进程调用 `D:\ComfyUI\venv\Scripts\python.exe`** 执行视觉推理，主进程只做编排。ComfyUI venv 内已有可用的 torch 2.7.0+cu128。

### 2.4 网络带宽约束（实测，影响排期）

| 来源 | 实测速率 | 结论 |
|---|---|---|
| 官方 pytorch cu128 | ~170 KB/s | 2.6 GB 需 ≈ 2 小时以上 |
| 阿里云 pytorch-wheels | ~340 KB/s | 2.6 GB 需 ≈ 2 小时 |
| 阿里云 PyPI 镜像（普通包） | 快 | base 依赖秒装 |

**换源无法解决**——瓶颈在本地出口带宽或限速，不在镜像。因此：

1. 大体积依赖（CUDA torch、onnxruntime-gpu）必须**后台下载**，不阻塞前台开发
2. 阶段排序据此调整：先做**不依赖 GPU 的 Demo B**，GPU 相关（Demo A）后置
3. 演示当天须确认 ComfyUI 与模型**已就位**，不依赖临时下载


### 2.3 P0 环境验证结果（2026-10-01 实测）

| 验证项 | 结果 | 备注 |
|---|---|---|
| FFmpeg / ffprobe | ✅ **可用** | `ffmpeg 8.1.2-full_build`（winget 安装），`ffprobe` 同在 PATH。Demo C 合成与音画同步可用 |
| ComfyUI 服务 | ✅ **可用** | `ComfyUI 0.28.0`，Python 3.11.9；`cuda:0 RTX 5070 Laptop GPU`，`vram_total 8.0 GB`；**可用节点 1045 个**，`CheckpointLoaderSimple` / `KSampler` / `CLIPTextEncode` / `VAEDecode` / `SaveImage` / `LoraLoader` 全部就绪 |
| ComfyUI 启动耗时 | ⚠️ **约 60–90 秒** | ComfyUI-Manager 会做依赖检查。演示与脚本必须等 `To see the GUI go to` 标志或轮询 `/system_stats`，不能假设立即就绪 |
| ComfyUI 次要告警 | ⚠️ 有 | 日志提示「You need pytorch with cu130 or higher to use optimized CUDA operations」，当前 cu128 下部分加速算子禁用（`comfy_kitchen cuda backend: disabled`），**不影响基本出图** |
| aigc-comic 服务 | ✅ **可用** | `/health` 返回 `{"status":"ok"}`；实测暴露 **8 个接口**（见 3.1）；启动耗时约 10 秒 |
| torch 镜像连通性 | ✅ 均可达 | 官方 `download.pytorch.org/whl/cu128`（200，72 条 torch-2.7）、**阿里云 `/pytorch-wheels/cu128`（200，同样 72 条，国内应更快）**；清华该镜像 404，不可用 |
| 项目 venv | ✅ 已建立 | `E:\interview-demo\.venv`，Python 3.13.14 |
| pip 缓存 | ⚠️ 需绕过 | 沙箱的批量删除保护会拦截 pip 的缓存清理导致安装失败，**必须加 `--no-cache-dir`**（已写入启动脚本） |

> ⚠️ **路径陷阱（已踩）**：Git Bash 的 `/e/...` `/tmp/...` 形式传给原生 Windows Python 时，会被按「当前盘符」解析成 `E:\e\...`、`E:\tmp\...`。调用本机 Python 时**一律使用 `E:/interview-demo/...` 这类 Windows 形式路径**。

---

## 3. 可复用资产盘点（P0 已完成）

### 3.1 盘点结论

| 资产 | 路径 | 实测状态 | 用途 | 复用方式 |
|---|---|---|---|---|
| **aigc-comic** ⭐ | `E:\aigc-comic` | **M1 完成**（`pipeline.py` 一条命令端到端出 MP4，`output\final.mp4` 已产出 151 KB）；**M2 完成**——实测 FastAPI 暴露 **8 个接口**：`/script` `/image` `/audio` `/compose` `/health` **`/run`（全流程编排，复用 pipeline 状态机 + 断点续跑）** **`/tasks`** **`/tasks/{task_id}`（任务状态查询）**；`lib/` 含 script/image/audio/compose/retry/state 六模块；3 个 workflow JSON | **Demo C 主体**（含编排与状态机） | HTTP 调用其 FastAPI 服务，**不改其代码**（实测工作区干净） |
| **humanize-chinese** ⭐ | `E:\ai-tools\humanize-chinese` | MIT 协议，工程完整（4646 行）；**实测：检测强、改写弱**（规则改写产病句，95→62 分仍属 HIGH） | **Demo B 的检测器**（仅打分，不用其改写） | 复制进 `third_party/`，保留上游 README 与溯源信息 |
| ComfyUI + `animagine-xl-4.0` | `D:\ComfyUI` | 可用，已跑通服务 | Demo C 真实出图底座 | HTTP `127.0.0.1:8188` |
| `ltx-video-2b-v0.9.5` | `D:\ComfyUI\models\checkpoints` | 可用 | 备选：文生视频 | 可选增强 |
| manju | `D:\manju` | `pipeline/` 主要是 Wan 系列模型下载脚本（download_v2/v3/wan 等）；`series/设定卡.json` 为角色设定卡 | Demo C 角色一致性参考 | 只读参考 |
| GPT-SoVITS | `D:\AI\GPT-SoVITS` | 存在 | Demo C 配音升级（替换 Edge-TTS） | 待确认可用性 |
| cnki-aigc---skill | `E:\ai-tools\cnki-aigc---skill` | 含 README / examples | Demo B 学术垂类改写 | 只读参考 |
| ai 小说工作区 | `E:\ai小说工作区` | 仅有 `output/` | 语料候选，价值低 | 暂不使用 |

### 3.2 对整体方案的三点影响

**① 项目定位改变。** 原计划是「从零构建三个 Demo」，实际是「**已有资产收敛补强**」：

| Demo | 原定位 | 修正后定位 | 工作量变化 |
|---|---|---|---|
| C 漫剧流水线 | 从零搭建 | 增强既有服务 + 编排层分离 | **大幅下调** |
| B 文案改写 | 从零搭建 | 开源内核 + 自研 RAG/指标层 | 下调 |
| A 多模态安防 | 从零搭建 | **维持从零构建（唯一无现成资产）** | 不变 |

**② 架构上出现一个漂亮的分离点。** `aigc-comic` 已把「分镜 / 出图 / 配音 / 合成」做成 FastAPI 服务，因此 Demo C 不再重复实现这些，而是做**编排层 + 真实出图适配 + 审核层 + 指标层**。这种「执行服务 ↔ 编排层」分离本身就是面试可讲的设计点。

**③ `aigc-comic` 的 M3 缺口正是简历的加分项。** 其 README 明确列出 M3 待做项：指数退避重试、断点续跑、任务状态机、角色一致性（LoRA + 固定 seed）。简历第 8 条主张的「批量生成与审核流水线」，正好落在这四项上。

### 3.3 引用合规

- `humanize-chinese` 保留原 `LICENSE`，在 `third_party/` 下原样存放并注明来源与版本
- `aigc-comic` 是你自己的项目，无第三方授权问题，但演示时须说明它与本 Demo 的调用关系
- 项目 README 与演示讲稿中**明确声明**哪些是既有/开源组件、哪些是本次自研
- ⚠️ 把既有组件讲成新写的，一旦被追问实现细节即暴露；如实说明「复用已有服务 + 本次新增编排与评测层」反而是加分项

---

## 4. 简历条目 → 交付映射

| # | 简历主张 | 可演示性 | 归属 | 证据形式 |
|---|---|---|---|---|
| 1 | 视觉识别 + 语音交互多模态系统 | 高 | A | 实时带框画面 + 语音问答闭环 |
| 2 | RAG 分层检索 + 上下文记忆，72%→94% | 高 | A | 评测集准确率对照表 |
| 3 | MCP 自动化：告警→工单→统计 | 高 | A | MCP 工具调用日志回放 |
| 4 | 接口响应 +40%、5 模块并发 | 中 | A / Bench | locust 压测报告（优化前/后） |
| 5 | 文案人类化 RAG：10 万+ 语料 / 6 垂类 / 91% | 高 | B | 语料统计 + 相似度指标 |
| 6 | AIGC 检测通过率 28%→89% | 中 | B | 第三方检测器打分对照 |
| 7 | MCP 工具链整合：改写 / 检测 | 高 | B | 工具链调用可视化 |
| 8 | 漫剧批量生成与审核，效率 +160% | 高 | C | 样片 MP4 + 流水线日志 + 耗时对比 |
| 9 | 电商 AI 三场景：文案 / 主图 / 客服 | 中 | C | 三个子功能页 |
| 10 | 小程序 AI 推荐，CTR +12% | 低 | D（网页版降级） | 离线推荐结果 + 指标口径页 |
| 11 | 可用性 99.8% / 成本 −45% / 安保量 −40% / 客户数 | **不可演示** | 口径页 | 仅口径定义，标注「需原始材料佐证」 |

---

## 5. 目录结构

```
E:\interview-demo\
├── PLAN.md                     # 本文件
├── README.md                   # 门户说明、快速开始、开源与既有组件声明
├── .env.example                # API Key 占位（不提交真实 Key）
├── .venv/                      # 独立虚拟环境
├── third_party/
│   └── humanize-chinese/       # 原样引入的开源内核（保留 LICENSE 与版本）
├── requirements/
│   ├── base.txt                # fastapi / pydantic / httpx / loguru
│   ├── vision.txt              # torch(cu128) / ultralytics / insightface / opencv
│   ├── nlp.txt                 # faiss-cpu / sentence-transformers / jieba
│   ├── audio.txt               # faster-whisper / soundfile
│   └── dev.txt                 # pytest / locust / rich
├── common/                     # 统一底座
│   ├── llm/{base,ollama_provider,cloud_provider,router}.py
│   ├── embed/                  # bge-small-zh-v1.5 封装
│   ├── vectorstore/            # FAISS 索引读写
│   ├── metrics/                # ★ 全部指标口径的唯一实现处
│   ├── config.py               # 统一配置（含显存分时加载开关）
│   ├── clients/                # 外部服务客户端：comfyui.py / aigc_comic.py
│   └── logging.py
├── demo_b_copywriting/         # 先做
│   ├── data/{raw,clean}/
│   ├── ingest.py               # 语料采集清洗去重
│   ├── build_index.py          # 向量化入库
│   ├── rewrite.py              # 检索增强改写（封装 humanize-chinese）
│   ├── quality.py              # 质检器（相似度 / 句式方差 / 困惑度 / 检测器分）
│   ├── evaluate.py             # 批量评测出报告
│   ├── app.py                  # Gradio 界面
│   └── reports/
├── demo_a_security/            # 唯一从零构建的模块
│   ├── vision/{detect.py,face.py}
│   ├── audio/asr.py
│   ├── rag/{indexer.py,retriever.py}
│   ├── agent/{planner.py,state_machine.py}
│   ├── mcp_server/server.py    # 轻量 MCP（stdio）暴露 3 个工具
│   ├── events/store.py         # 事件与工单存储（SQLite）
│   ├── bench/{locustfile.py,report.md}
│   └── app.py
├── demo_c_manju/               # 编排层（执行复用 E:\aigc-comic）
│   ├── orchestrator.py         # 分镜→出图→配音→合成 编排 + 状态机 + 断点续跑
│   ├── image_backend.py        # 出图后端：ComfyUI 适配器（替换 Pillow 占位）
│   ├── review/{rules.py,llm_review.py}
│   ├── workflows/              # ComfyUI workflow JSON
│   └── outputs/
├── portal/
│   ├── app.py                  # FastAPI 门户
│   ├── static/  templates/
│   └── metrics.json            # ★ 指标口径页数据源
├── data/{corpus,index}/
├── models/
├── outputs/
├── scripts/
│   ├── bootstrap_env.ps1
│   ├── start_comfyui.ps1
│   ├── start_aigc_comic.ps1
│   ├── start_ollama.ps1
│   └── run_all.ps1
└── docs/
    ├── demo_script_{A,B,C}.md  # 演示讲稿（2 分钟版）
    ├── objections.md           # 预设追问与应答
    └── recording_checklist.md  # 录屏清单
```

---

## 6. Demo B —— AIGC 文案人类化改写工作台（**优先级最高**）

### 6.1 为什么先做

依赖最少（纯文本）、单机可跑、5 分钟内能讲完、产出物直观，且**开源内核已现成**。其向量检索与改写模块可被 Demo A 直接复用。

### 6.2 处理链路（v1.3 修订：内核降级为检测器）

```
语料采集(垂类) → 清洗去重 → bge-small-zh-v1.5 向量化 → FAISS 入库
      ↓
用户输入 AIGC 文案
      ↓
① 检测器：humanize-chinese 打分（基线与终态，同一仪器）  ← 开源内核，仅此一处
      ↓
② RAG 检索：取同垂类原生文案 top-k（自研）
      ↓
③ LLM 改写：检索增强 + 语义保真约束（自研，核心增量）
      ↓
④ 质检：语义保真度(embedding 余弦) + 检测器分 + LLM 评审（自研）
      ↓
before/after 对照页 + 指标表 + 一键重跑
```

### 6.3 关键实现说明

- **为什么不用内核改写**（必答的面试追问）：实测 `humanize_cn.py --scene social` 把一段文本从 95 分只降到 62 分，且产出「时间管理之于于每个人来说都，有着至关重要的意义」「学会拒绝也的环节地位不可小觑」这类病句。纯规则替换 + 噪声注入无语义校验，不可交付。因此**内核只用作检测器**，改写走 LLM + RAG。详见 `third_party/README.md` 的实测评估。
- **语料合规**：不使用爬取的小红书真实数据（平台条款 + 个人数据风险）。改用「公开可商用中文文案数据集 + 按垂类自造样本」；**指标口径页必须标注语料来源与规模，不得声称 10 万+**。
- **自研增量（必须能讲清，且不得与内核重叠）**：
  1. **RAG 检索增强改写链路**——垂类语料向量库 + 检索注入 + LLM 改写
  2. **质检层**——语义保真度（embedding 余弦）、LLM 评审、检测器分三维
  3. **评测框架**——批量跑分、口径表、对照界面
  > ⚠️ 困惑度 / 突发度 / 段落熵 CV **由内核提供，不得声称自研**。
- **三路对照实验**（Demo B 最有说服力的展示）：同一输入跑三条路径并排对比——
  | 路径 | 说明 |
  |---|---|
  | 原始 AIGC 原文 | 基线，只测不改 |
  | 内核规则改写 | 证明「降分了但语法坏了」，反衬自研价值 |
  | **自研 LLM + RAG 改写** | 目标：降分更多且语义保真、语法通顺 |
  这一组对照既是技术验证，也是面试时最有说服力的一段叙事。

### 6.4 验收标准（Definition of Done）

- [x] 一条命令起界面 —— `python -m demo_b_copywriting.app`（或 `scripts\start_demo_b.ps1`），服务 `http://127.0.0.1:8100`
- [x] 20 条样本的三路对照页可直接展示（原文 / 内核改写 / 自研改写）—— 页面 + `reports/three_way.md`
- [x] 指标表自动生成：语义保真度、检测器分、语法异常检出数、AI 味残留数（LLM 评审分需 LLM 通道就绪，当前降级为「未产出」）
- [x] `evaluate.py` 可一键重跑并输出 `reports/metrics.csv`
- [x] README 标注：语料来源、开源组件与自研层边界、口径与简历数字的差异
- [ ] 自研改写臂需 LLM 通道就绪后才能产出效果数字（当前无 `DASHSCOPE_API_KEY`、未装 Ollama）

### 6.5 v1.6 已完成实现与实测结果

**代码结构**（全部位于 `demo_b_copywriting/`）：

| 文件 | 职责 | 自研 / 第三方 |
|---|---|---|
| `ingest.py` | 语料清洗 raw → clean，6 条可审计规则，产出清洗报告 | 自研 |
| `build_index.py` | 构建索引；**同时存 FAISS 加速副本与 numpy 权威副本** | 自研 |
| `retriever.py` | 加载索引 + 垂类感知重排；编码器不一致时**硬报错** | 自研 |
| `rewrite.py` | 两条改写臂：第三方规则改写 / 自研 RAG+LLM；提示词带版本号 | 双臂分列 |
| `detector.py` | 封装第三方内核，仅用于检测 | 第三方 wrapper |
| `tells.py` | AI 味词典（41 条字面 + 10 条模板），可逐条指认 | 自研 |
| `quality.py` | 质检层：5 道闸门 + 逐条判定依据 | 自研 |
| `evaluate.py` | 三路对照批量跑分 → CSV / JSON / Markdown / 回填口径表 | 自研 |
| `app.py` + `static/index.html` | 三路对照界面（零 CDN，纯静态，离线可用） | 自研 |

**实测结果（2026-10-01，对照臂 + 规则臂，20 条样本）**：

| 臂 | 检测分(前→后) | 检测分均降 | AI味残留(前→后) | 判定 pass/review/fail |
|---|---|---|---|---|
| 原文（对照） | 2.2 → 2.2 | 0 | 2.15 → 2.15 处 | 20 / 0 / 0 |
| 规则改写（第三方） | 2.2 → 1.0 | **1.2（中位数 0）** | 2.15 → 1.7 处（合计仅减 9/43） | 4 / 16 / 0 |

规则改写臂 **16/20 条判定为 review**，主要理由是 G4「AI 味残留超过原文的 50%」。
即：**该规则改写在本评测集上基本无效** —— 这是实测出来的，不是预设的结论。

**仪器灵敏度（关键发现，按语域）**：

| 语域 | 样本 | 内核得 0 分 | 内核均分 | 自研词典均命中 | 自研得 0 分 |
|---|---|---|---|---|---|
| 公文腔 | 11 | 8 | 2.55 | 1.91 | 0 |
| 评测腔 | 4 | 2 | 3.00 | 2.00 | 0 |
| 种草腔 | 4 | 3 | 1.00 | 2.75 | 0 |
| 科普腔 | 1 | 1 | 0.00 | 3.00 | 0 |
| **合计** | **20** | **14（70%）** | **2.2** | **2.15** | **0** |

**结论：任何一个单独的检测仪器都不足以支撑本 Demo 的指标。**
第三方内核检测器在 70% 的样本上得 0 分（对评测/种草腔基本失明），
自研词典则 20/20 全部命中。因此指标必须**双仪器并报并按语域分组**，
不得合并成单一「总分」—— 否则会得出「AI 味从 2.2 分降到 0 分、改善 100%」这种
看似漂亮、实际什么都没测出的结论。

**语料**：全自建，无爬取数据。35 条风格语料（5 垂类 × 5 范文 + 2 对照改写对）
+ 20 条评测样本（覆盖 5 垂类 4 语域）。清洗报告见 `demo_b_copywriting/data/clean/ingest_report.json`。

**当前唯一缺口**：自研 LLM 改写臂需要 LLM 通道。本地 Ollama 未安装、云端 Key 未配置，
因此该臂当前返回明确错误而非静默降级 —— 规则臂与对照臂不受影响，评测照常产出。


---

## 7. Demo A —— 多模态智能安防 Agent（唯一从零构建）

### 7.1 处理链路

```
视频流/摄像头
   ├→ YOLO11n 目标检测（人/车/异常物）
   └→ InsightFace 人脸识别（白名单/黑名单）
语音输入 → faster-whisper ASR
   ↓
Agent 编排（事件状态机）
   ├→ RAG 问答（分层检索 + 会话记忆）
   └→ MCP Server：query_events / create_ticket / stats
   ↓
工单系统（SQLite）+ 统计看板
```

### 7.2 显存分时加载策略（8 GB 的关键约束）

不能同时常驻 YOLO + InsightFace + Whisper + LLM。采用**阶段式加载**：

| 阶段 | 常驻 | 释放 |
|---|---|---|
| 检测阶段 | YOLO11n | 释放 ASR / LLM |
| 语音阶段 | faster-whisper small | 释放 YOLO |
| 问答阶段 | LLM（本地量化）| 释放视觉模型 |

由 `common/config.py` 的显存调度器统一管理。演示时按阶段推进，这本身就是可讲的技术点。

### 7.3 指标可复现要求

- **响应速度提升 40%**：给出「优化前」与「优化后」两版接口 + locust 压测脚本 + 报告。做法：v1 同步阻塞串行调用，v2 改异步并发 + 连接池，同机压测对比。
- **准确率 72%→94%**：构造 ≥ 100 条问答评测集，分别跑「无 RAG」与「分层检索 RAG」，输出混淆矩阵。**若实测达不到，按实测值写，并在口径页说明差异。**

### 7.4 验收标准

- [ ] 实时画面带检测框与识别标签
- [ ] 语音提问 → RAG 应答闭环可用
- [ ] MCP 三个工具可被外部客户端调用，调用日志可回放
- [ ] `bench/report.md` 含优化前后对照数据与原始压测输出
- [ ] 评测集准确率对照表（无 RAG vs 分层 RAG）

---

## 8. Demo C —— 漫剧智能生产流水线（编排层 + 复用既有服务）

### 8.1 架构定位

**不重写已有能力。** `E:\aigc-comic` 已提供 `分镜 / 出图 / 配音 / 合成` 四个执行服务与 FastAPI 接口，本项目只新增编排与质量层：

```
                         ┌─────────────────────────────────────┐
  剧本/选题 ──→ 编排层 ──→│  执行服务：E:\aigc-comic (FastAPI)   │
              (本项目)   │  /script  /image  /audio  /compose  │
                 │       └─────────────────────────────────────┘
                 │                      ↑
                 │        出图后端切换：Pillow 占位 → ComfyUI 适配器
                 ↓
              审核层（规则 + LLM 双通道）→ 指标层 → 成品 MP4
```

### 8.2 本次新增与既有能力的边界（实测校正）

⚠️ **实测发现既有服务已具备编排与状态机**：`/run` 的请求模型注释明确写着「复用 pipeline 状态机 + 断点续跑」，`/tasks` 与 `/tasks/{task_id}` 已提供任务状态查询。因此原先规划的「状态机 / 断点续跑」**不是新增项，而是既有能力**，不得声称自研。

| # | 内容 | 归属 | 说明 |
|---|---|---|---|
| 1 | **ComfyUI 出图适配器** | ✅ **本次新增** | 既有实现走 SD WebUI（`SD_API_URL=7860`）或 Pillow 占位图；需新增 ComfyUI（`8188`）后端，复用 `animagine-xl-4.0` |
| 2 | **审核层**（规则命中 + LLM 判定） | ✅ **本次新增** | 既有服务无此能力；对应简历「漫剧内容批量生成与审核流程」 |
| 3 | **指标层 / 效率口径** | ✅ **本次新增** | 各阶段耗时埋点、人工 vs 流水线对比表、口径页 |
| 4 | 任务状态机 | ♻️ **既有**（`/run` + `/tasks`） | 验收到「可演示」，不声称自研 |
| 5 | 断点续跑 + 重试 | ♻️ **既有**（"复用 pipeline 状态机"，`lib/retry.py`） | 同上 |
| 6 | 分镜 / 配音 / 合成执行 | ♻️ **既有**（`/script` `/audio` `/compose`） | 同上 |
| 7 | 音画同步（ffprobe 驱动时长） | ♻️ **既有**（`lib/compose.py`） | 同上 |

**结论**：Demo C 的定位收敛为 **「为既有流水线补齐真实出图后端与质量层，并纳入统一演示门户」**。这仍是实打实的工作，但对外表述必须严格区分「既有」与「新增」——这正好呼应简历第 8 条的「审核流程」与第 9 条的「电商工具」两项新能力。

### 8.3 效率指标的诚实口径

「效率提升 160%」需给出对比基线：**人工方式（手动分镜 + 手动出图 + 手动合成）耗时** vs **流水线自动耗时**。做法：录制一次人工全流程计时（可用脚本模拟确定性时长），与流水线实测耗时对照，输出对比表。

> 若无法获得可信基线，则改为表述「流水线单条样片产出耗时为 X 分钟」，不引用倍数。

### 8.4 验收标准

- [ ] 输入一段剧本，输出一条 30–60s 的 MP4 样片（**真实 ComfyUI 出图，非 Pillow 占位图**）
- [ ] ComfyUI 适配器可独立重跑（workflow JSON 独立可用）
- [ ] **审核模块（本次新增）**可演示：规则命中 + LLM 判定双通道，输出命中率
- [ ] **效率对比表（本次新增）**：人工基线 vs 流水线实测，含各阶段耗时
- [ ] 既有能力验证：`/run` 中断后重跑可跳过已完成步骤（**验证既有能力，不作为自研声明**）
- [ ] `E:\aigc-comic` 源码未被修改（工作区无新增改动）

---

## 9. Demo D —— 门户 + 指标口径页

### 9.1 门户

FastAPI 单页应用，提供：

- 三个 Demo 的入口（内嵌 Gradio / 独立路由）
- 接口文档（自动生成 OpenAPI）
- **指标口径页**：以 `portal/metrics.json` 为数据源，逐条列出每个数字的「指标定义 / 计算公式 / 数据集与规模 / 计算脚本路径 / 实测值 / 与简历值差异及原因」
- **组件来源声明区**：列出引用的第三方组件与既有自研项目及其 License

### 9.2 关于指标口径页的定位

这是整个交付里**面试价值最高的一页**。面试官追问「这个 89% 怎么算的」时，能当场翻出定义、公式和脚本，比任何 Demo 画面都有说服力。

`portal/metrics.json` 的 schema：

```json
{
  "metric": "AIGC 检测通过率",
  "resume_value": "89%",
  "definition": "改写后文案通过第三方 AIGC 检测器的比例",
  "formula": "通过条数 / 测试总条数",
  "dataset": "自建测试集，200 条",
  "script": "demo_b_copywriting/evaluate.py",
  "measured": "待实测",
  "gap_note": "简历值基于线上真实语料；本 Demo 基于公开数据集复现，数值不可直接对比"
}
```

### 9.3 验收标准

- [x] 一条命令起门户 —— `scripts\start_portal.ps1`（`http://127.0.0.1:8200`）
- [x] 三个 Demo 均可从门户进入 —— Demo B 已就绪可直达；A / C 未就绪，门户**如实显示状态与原因**（缺失依赖、前置阶段），不假装可用
- [x] 指标口径页覆盖简历全部 11 条主张，无遗漏 —— 由 `common/metrics/claims.py` 的**覆盖率自检**强制保证：编号必须恰好覆盖 1–11 且引用的指标 key 必须真实存在，否则**拒绝导出**
- [x] 每条指标都能点开看到对应计算脚本 —— `/api/source` 返回源码 + 行数 + sha256 摘要；白名单严格限定（实测 `.env`、`../` 穿越、绝对路径均被 403 拦截）
- [x] 组件来源声明完整 —— `portal/provenance.py` 三分类账本，含 License、使用边界、当场核实方式

### 9.4 v1.6 已完成实现

| 项 | 内容 |
|---|---|
| 服务 | `portal/app.py`（FastAPI，端口 8200，`/docs` 提供自动 OpenAPI） |
| 页面 | `/` 概览与入口、`/claims` 指标口径页、`/components` 来源声明页（纯静态，零 CDN） |
| 数据源 | `portal/metrics.json`（指标）、`portal/claims.json`（主张覆盖）、`portal/components.json`（来源）、`portal/measurements.json`（实测值持久化） |
| 现场刷新 | `POST /api/refresh` 重新生成三份口径数据 —— 演示时可证明「这些数字不是截图，是现算的」 |
| 主张覆盖 | 11 条：可演示（高置信）3 条、可演示（口径代理）7 条、降级演示 1 条、不可复现 1 条（第 11 条） |
| 指标现状 | 10 项：已实测 5 项、待实测 4 项、不可复现 1 项 |

**门户的一个关键设计**：口径页数据**每次请求都从磁盘现读**，而不是内嵌在 HTML 里。
这样「文档里的数字」与「评测脚本写入的数字」不可能不一致 —— 因为前者根本没有副本。

> ⚠️ 第 11 条（服务 3 家工业园区 / 可用性 99.8% 等）在门户上标注为**不可复现**，
> 并写明「需出示原始材料」。这是本页最重要的诚实性设计：**把无法证明的东西明确标出来，
> 而不是让它消失在页面里。**


---

## 10. 阶段划分与依赖

| 阶段 | 内容 | 前置依赖 | 工作量级 |
|---|---|---|---|
| **P0** | 环境底座：建 venv、装依赖、验证 FFmpeg、验证 `aigc-comic` 服务可起、装 Ollama、配 API Key、确定语料来源 | 用户提供 Key 与安装许可 | 小 |
| **P1** | Demo B 端到端：封装开源内核 + 补齐 RAG 与指标层 + 界面 | P0 | 中 |
| **P2** | Demo A 多模态链路 + MCP Server + 压测报告（**唯一从零构建**） | P1（复用 RAG 与 metrics） | **大** |
| **P3** | Demo C 编排层 + ComfyUI 适配 + 状态机 + 审核层 | P0 | **中（较 v1.0 大幅下调）** |
| **P4** | Demo D 门户 + 指标口径页 | P1 / P2 / P3 端口稳定 | 小 |
| **P5** | 演示物料：讲稿、追问应答、录屏 | P1–P4 | 中 |

**关键路径**：P0 → P1 → P2 → P4 → P5。
**可并行**：P3 与 P1/P2 无依赖冲突（全部走 HTTP），可在 P1 期间并行推进。

### 10.1 推进原则

每个阶段结束时必须产出**可运行的东西**。P1 完成后 Demo B 立刻可用于面试演示，后续 Demo 只是锦上添花——避免「全部做完才能演示」的高风险排期。

---

## 11. 统一技术选型

| 层 | 选型 | 理由 |
|---|---|---|
| LLM（本地） | Ollama + Qwen3-8B-Instruct（Q4_K_M） | 8 GB 显存下可跑；中文强 |
| LLM（云端） | 阿里云百炼（通义千问） | 与简历技术栈一致；中文效果好 |
| Embedding | `BAAI/bge-small-zh-v1.5` | CPU 可跑，512 维，中文检索性价比高 |
| 向量库 | FAISS | 零服务依赖，本地文件化，便于随项目分发 |
| 检测内核 | `third_party/humanize-chinese`（MIT） | **仅用其检测打分**；实测其规则改写会破坏中文语法，故不采用其改写 |
| 改写链路 | **自研：LLM + RAG 检索增强** | 内核改写不可用；这是 Demo B 的核心自研增量 |
| 漫剧执行 | `E:\aigc-comic`（自研既有） | 已有 FastAPI 服务与音画同步逻辑 |
| ASR | faster-whisper（small / medium） | CTranslate2 加速，显存占用低 |
| 视觉 | ultralytics YOLO11n + InsightFace buffalo_sc | 轻量、模型小、启动快 |
| 图像生成 | ComfyUI + animagine-xl-4.0 | 零额外下载 |
| TTS | 优先 `D:\AI\GPT-SoVITS`，回退 Edge-TTS（既有实现） | 升级画质/音质，回退路径已通 |
| Web | Gradio（单 Demo）+ FastAPI（门户） | 出界面快，门户统一入口 |
| 编排 | 自研轻量状态机 + 标准 MCP Server（stdio） | 可控、可讲、依赖少 |
| 存储 | SQLite | 零部署 |
| 压测 | locust | 报告可复现 |
| 测试 | pytest | 指标计算需单测保证口径正确 |

---

## 12. 风险登记册

| 等级 | 风险 | 影响 | 应对 |
|---|---|---|---|
| **高** | **口径风险**：简历含「3 家工业园区」「2 家 MCN 采购」「可用性 99.8%」等生产事实，Demo 无法证明 | 被追问时无法承接，信用受损 | 全部交付物统一「独立复现版」措辞；口径页单列「需原始材料佐证」条目；备好回应话术 |
| **高** | **数字复现风险**：94%、89%、+160%、+40% 等口径不明 | 一旦被问算法即失守 | 每个数字必须有定义 + 公式 + 脚本；实测达不到就按实测值写并说明差异 |
| **高** | **硬件限制**：8 GB 显存不支持多模型常驻 | Demo A 可能跑不动 | 阶段式分时加载；必要时视觉/ASR 走 `D:\ComfyUI\venv` 子进程 |
| **高** | **既有资产归属表述**：`aigc-comic` 与 `humanize-chinese` 都是既有物 | 若讲成「本次新写」被追问即暴露 | 明确三分：既有自研项目 / 第三方开源 / 本次新增；README 与讲稿均声明 |
| **中** | **跨工程耦合**：Demo C 依赖 `aigc-comic` 服务 | 对方代码变更可能影响 Demo | 只通过 HTTP 契约交互，不 import 其内部模块；锁定端口与接口签名 |
| **中** | **RTX 5070 架构兼容**：Blackwell sm_120 需 torch ≥ 2.7 + cu128 | 低版本直接报 kernel image 错误 | 锁定 cu128 轮子 |
| **中** | **API Key 缺失** | 云端通道不可用 | P0 需用户提供 `DASHSCOPE_API_KEY`；未提供则先只跑本地 |
| **中** | **语料合规**：小红书语料涉平台条款与个人数据 | 法律与平台风险 | 改用公开数据集 + 自造样本；口径页标注来源 |
| **中** | **表述合规**：「规避 AIGC 检测」触碰平台规则 | 演示内容存在合规争议 | 对外统一为「文案质量与人类化改写」 |
| **低** | **跨盘 IO**：项目在 E:，ComfyUI 在 D: | 稍慢 | 索引与产物放 E:，权重留 D: |
| **低** | **Ollama 未安装**，模型下载约 5 GB | P0 阻塞 | 需许可后安装；网络受限时以云端为主 |

---

## 13. 需要你提供 / 确认的事项

| # | 事项 | 说明 | 阻塞 | 状态 |
|---|---|---|---|---|
| 1 | **阿里云百炼 API Key** | 云端通道必需；不提供则只跑本地 | P0 | ⏳ 待提供 |
| 2 | **是否允许安装 Ollama 并下载 Qwen3-8B（约 5 GB）** | 本地兜底通道必需 | P0 | ⏳ 待确认 |
| 3 | `E:\aigc-comic` 确认作为 Demo C 执行服务（**不改其代码**） | 架构决策 | P3 | ⏳ 待确认 |
| 4 | `D:\AI\GPT-SoVITS` 是否可用于 TTS | 决定 Demo C 配音方案 | P3 | ⏳ 待确认 |
| 5 | **投递节奏**：近期是否有硬截止日期 | 决定是否先压缩兵力只交 Demo B | P0 | ⏳ 待确认 |
| 6 | 录屏是否需要出镜与配音讲解 | 影响 P5 产出形式 | P5 | ⏳ 待确认 |

---

## 14. 下一步

### P0 已完成

- [x] 资产盘点（第 3 节全部条目）
- [x] 项目根目录与 git 仓库建立：`E:\interview-demo`（分支 `master`）
- [x] 目录骨架（36 个目录）
- [x] `requirements/*.txt` 分模块依赖文件
- [x] `.env.example` 与 `scripts/*.ps1` 启动脚本（全 ASCII 内容）
- [x] `.venv` 建立（Python 3.13.14），base 依赖安装完成
- [x] 验证 FFmpeg 8.1.2 + ffprobe 可用
- [x] 验证 ComfyUI 服务可起（0.28.0，1045 节点，CUDA 正常）
- [x] 验证 `aigc-comic` 服务可起（`/health` ok，8 接口）
- [x] 引入 `third_party/humanize-chinese` 并完成两轮能力实测评估
- [x] 探测 torch 镜像连通性，确定阿里云备用源
- [x] **torch 2.7.0 CPU 版安装完成**（P1 解封；CUDA 版留给 P2/Demo A）

### P1 已完成（Demo B 全链路）

- [x] 语料层：全自建 35 条风格语料 + 20 条评测集 / 5 垂类 4 语域
- [x] 清洗层 `ingest.py`：6 条可审计规则 + 清洗报告落盘
- [x] 索引层 `build_index.py`：FAISS 加速副本 + numpy 权威副本双写
- [x] 检索层 `retriever.py`：垂类感知重排 + 编码器不一致硬报错 + 耗时测量
- [x] 改写层 `rewrite.py`：两条臂 + 提示词版本号 + 便携化输出清洗
- [x] 质检层 `quality.py`：5 道闸门 + 逐条判定依据 + 降级时排除不可靠项
- [x] 自研仪器 `tells.py`：41 条字面 + 10 条模板，实测 20/20 全部命中
- [x] 评测框架 `evaluate.py`：三路对照批量跑分，产出 CSV / JSON / Markdown，回填口径表
- [x] 演示服务 `app.py` + `static/index.html`：三路对照页，零 CDN 离线可用，6 个接口全部验证通过
- [x] 指标口径表 `portal/metrics.json`：Demo B 的 5 项指标已回填实测值

### P1 待执行（阻塞项，需要你）

- [ ] 安装 Ollama 并拉取 Qwen3-8B，验证本地推理连通 **（需你许可，约 5 GB）**
- [ ] 提供 `DASHSCOPE_API_KEY` 以跑通云端通道 **（需你提供）**
      → 两者任一就绪后，自研改写臂即可产出效果数字（重跑 `evaluate.py` 即可）
- [ ] 待 `sentence-transformers` + `faiss-cpu` 装完（后台下载中）后**重建索引**：
      编码器会从 `hashing-ngram` 切到 `bge-small-zh-v1.5`，
      **届时必须重建索引并重跑评测**，否则语义保真度仍不可用（`retriever.py` 会主动报错拦住混用）

### P4 已完成（Demo D 门户）

- [x] `common/metrics/claims.py`：11 条主张 → 证据映射 + 覆盖率自检（不通过则拒绝导出）
- [x] `portal/provenance.py`：三分类来源账本（第三方 / 既有 / 新增），含使用边界与核实方式
- [x] `portal/app.py` + 三个静态页：概览入口 / 指标口径 / 来源声明（零 CDN）
- [x] `/api/source` 源码查看（严格白名单，实测拦截 `.env`、目录穿越、绝对路径）
- [x] `POST /api/refresh` 现场重算口径数据
- [x] `scripts/start_portal.ps1` 一条命令起门户（端口 8200）

### P2 / P3 待执行

- [ ] 跑通 `aigc-comic` 的 `/run` 全流程一次，确认端到端可用（P3 前置）
- [ ] Demo A：YOLO11n + InsightFace + faster-whisper + RAG + MCP Server + locust 压测
      （需 CUDA 版 torch，2.6 GB，**已改为 `pip download` 方式后台预取**，
      避免与当前 venv 安装冲突；下载目录 `outputs/wheels`）
- [ ] Demo C：ComfyUI 出图适配器、审核层、指标层
- [ ] Demo 讲稿、答辩话术、录屏清单


> 本文档随实施迭代更新；任何口径调整必须同步更新第 4 节与第 12 节。

### 需要你确认的事项（仍未答复）

1. **近期有硬截止日期吗？** 有的话我把兵力压在已有闭环上打磨，而不是铺开做三个 Demo。
2. `E:\aigc-comic` 是否确认作为 Demo C 的执行服务（原地不改，只 HTTP 调用）？
3. `D:\AI\GPT-SoVITS` 是否可用作 TTS 备选？
4. 录屏偏好：屏幕上是否需要出现身份证件/真实姓名？（当前语料与示例均为自建占位内容）

