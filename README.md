# 简历项目 Demo —— 可复现技术验证集

> 本项目把简历中的技术主张转化为**可运行、可复现、可当场演示**的实现。
> 定位为**方案的独立复现 / 技术验证版**，不等同于任何生产系统。

---

## ⚠️ 口径声明（请先读这一段）

1. **本仓库是技术方案的独立复现，不是生产系统。** 简历中「服务 3 家工业园区」「2 家 MCN 机构采购」「月度可用性 99.8%」等属于运营与商业事实，本仓库无法证明，也不做此类声明。
2. **所有性能数字都必须可追溯。** 每个数字在 `portal/metrics.json` 中有对应的指标定义、计算公式、数据集、计算脚本。实测值与简历数值不一致时，以实测值为准并注明差异原因，**不做数字凑合**。
3. **组件来源三分**（见下文「组件来源声明」）。把既有组件或开源项目讲成本次新写的，一旦被追问实现细节即会暴露。

---

## 组件来源声明

> 完整的边界说明与当场核实方式见 **[门户的来源声明页](http://127.0.0.1:8200/components)**（`portal/components.json`）。

| 类别 | 组件 | 位置 | 说明 |
|---|---|---|---|
| **第三方开源** | humanize-chinese | `third_party/humanize-chinese` | ⚠️ 上游**未附独立 `LICENSE` 文件**，MIT 仅由 README 徽章声明。本项目**原样引入未做任何修改**，仅用作**检测打分**；其规则改写会破坏中文语法，故不用于改写。详见 `third_party/README.md` |
| **第三方开源** | ComfyUI | `D:\ComfyUI` | **GPL-3.0**。本项目**仅作为独立服务通过 HTTP 调用**（`127.0.0.1:8188`）：不链接源码、不修改文件、不再分发，因此本仓库不因 GPL 传染而需以 GPL 发布 |
| **第三方开源** | FFmpeg / FastAPI / FAISS / sentence-transformers 等 | 系统 PATH / PyPI | 运行时依赖，许可证见门户来源声明页 |
| **既有自研项目** | aigc-comic | `E:\aigc-comic` | 本人既有的漫剧自动生产线。本项目**通过 HTTP 调用其 FastAPI 服务，未修改其代码**（演示时可当场 `git status` 验证）。⚠️ **其 `/run` 的「pipeline 状态机 + 断点续跑」为既有能力，不得声称本次自研** |
| **本次新增** | `common/`、`demo_b_copywriting/`、`portal/` 及其余本仓库代码 | `E:\interview-demo` | 统一底座、RAG 链路、AI 味词典、质检层、评测框架、门户与口径页、Demo A 全部 |

---

## 四个模块

| 模块 | 内容 | 目录 | 状态 |
|---|---|---|---|
| **B** | AIGC 文案去 AI 味：清洗 → 垂类检索 → 双路改写 → 质检 → 三路对照 | `demo_b_copywriting/` | ✅ **可运行**（自研 LLM 臂待 LLM 通道） |
| **C** | 漫剧智能生产流水线：ComfyUI 出图适配 + 审核层 + 指标层 | `demo_c_manju/` | ✅ **三项新增能力可运行**（MP4 成片待 `aigc-comic` 起服务） |
| **D** | 统一门户 + 指标口径页 + 来源声明 | `portal/` | ✅ **可运行** |
| **A** | 多模态智能安防 Agent：视觉检测 + 人脸识别 + 语音问答 + MCP 工具编排 | `demo_a_security/` | ⏳ 待做（唯一从零构建；前置 CUDA torch） |

完整实施计划见 [`PLAN.md`](./PLAN.md)。

> **Demo C 的三项新增能力**（既有 `aigc-comic` 无这些能力；状态机/断点续跑等属**既有**，不声称自研）：
> ① `comfy_adapter.py` ComfyUI 出图适配器 —— 实测同 seed 逐字节可复现、`/free` 显存卸载有效；
> ② `review/` 审核层 —— 规则 6 条 + LLM 通道，分层上报且「无法校验」与「通过」分列；
> ③ `metrics/` 指标层 —— 人工基线参数化、缓存命中剔除、按配置与冷热分组。

---

## 快速开始

### 1. 建立环境

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_env.ps1
```

网络受限时可先跳过 torch（Demo B 不需要）：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\bootstrap_env.ps1 -SkipTorch
```

> torch 必须走 cu128 源。本机 GPU 为 RTX 5070 Laptop（Blackwell，sm_120），旧版轮子会直接报 `no kernel image is available`。

### 2. 配置

```powershell
Copy-Item .env.example .env
# 编辑 .env，至少填 DASHSCOPE_API_KEY（云端通道）
```

### 3. 启动 Demo B（三路对照界面）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_demo_b.ps1
# → http://127.0.0.1:8100
```

一条命令即可：索引不存在时脚本会先自动完成语料清洗与建库。

### 4. 启动门户（含指标口径页）

```powershell
powershell -ExecutionPolicy Bypass -File scripts\start_portal.ps1
# → http://127.0.0.1:8200
```

### 5. 重跑评测（产出指标）

```powershell
.venv\Scripts\python.exe -m demo_b_copywriting.evaluate
# → demo_b_copywriting/reports/{metrics.csv, summary.json, three_way.md}
# → 并回填 portal/measurements.json（实测值的唯一持久化位置）
```

### 6. 环境自检（面试前跑一遍）

```powershell
.venv\Scripts\python.exe scripts\selfcheck.py

# 自研语法规则集的精度护栏：10 条合法中文必须 0 命中 + 3 条损伤样本必须命中。
# 规则集被 Demo B 与 Demo C 共用，一旦误报会同时污染两个 Demo 的结论。
.venv\Scripts\python.exe -m common.metrics.text_metrics
```

> 这条护栏的来历：v1.8 发现原 `R3_dangling_particle`（「语气词 + 标点」）会把
> 「我们走吧。」「太可了！」这类**正常中文**判为语法异常（Demo C 误报 10 次、Demo B 误报 4 次），
> 已整条删除。规则集现为 **R1/R2/R4**，Demo B 语法维度数字同步由 0.2 更正为 **0.0**。
> 完整记录见 `PLAN.md` §8.6。

### 7. Demo C —— 出图 / 审核 / 指标（三项新增能力）

```powershell
# 7.1 出图前置自检（服务、依赖节点、可用 checkpoint、显存）
.venv\Scripts\python.exe -m demo_c_manju.comfy_adapter --preflight

# 7.2 真实出图（seed 显式化才可复现；生成前先卸载模型则为冷启动口径）
.venv\Scripts\python.exe -m demo_c_manju.comfy_adapter `
    --prompt "1girl, solo, standing on a rooftop, sunset sky, city skyline" `
    --seed 20261001 --free-before
# → outputs/demo_c/<时间戳>_seed<N>/{*.png, workflow.json, result.json}
#   （workflow.json 可独立喂给 ComfyUI 复现同一张图）

# 7.3 内容审核（规则通道零依赖，无 LLM 也能跑）
.venv\Scripts\python.exe -m demo_c_manju.review --no-llm
# → outputs/demo_c_review/{REPORT.md, report.json}

# 7.4 效率口径（实测 vs 申报假设；可当场改假设重算）
.venv\Scripts\python.exe -m demo_c_manju.metrics --per-frame-minutes 4
# → outputs/demo_c_metrics/{REPORT.md, comparison.csv, report.json}
```

**Demo C 的三条效率口径硬规则**（由代码强制，不靠人自觉）：

1. **不跨配置求平均** —— 832×1216/28 步 与 512×512/4 步是不同工作负载，平均出来的数字没有意义；
2. **剔除 ComfyUI 缓存命中** —— 同 prompt + 同 seed 重复提交会命中节点缓存（实测仅 0.49 s，不含推理），计入会让吞吐凭空翻倍；
3. **实测与申报假设绝不混算** —— 对比表每格标注来源，未实测的阶段留空，不填估算值。

> 结论：**不引用任何整体效率倍数**。只报绝对耗时 —— 稳态单张 14.46 s（n=3，极差 0.07 s）、
> 冷启动 21.11 s、6 张样片出图 93.4 s。人工基线为申报假设（54 分钟/条，依据逐条列出，可改参重算）。

### 端口分配

| 端口 | 服务 | 用途 |
|---|---|---|
| 8200 | **本门户**（`portal/`） | 概览、指标口径页、来源声明 |
| 8100 | **Demo B**（`demo_b_copywriting/`） | 三路对照界面 |
| 8188 | ComfyUI（`D:\ComfyUI`） | Demo C 图像生成（GPL-3.0，仅 HTTP 调用） |
| 8000 | aigc-comic（`E:\aigc-comic`） | Demo C 执行服务（既有资产） |
| 11434 | Ollama | 本地 LLM 兜底通道 |


---

## 目录结构

```
E:\interview-demo\
├── PLAN.md                 实施计划（唯一权威规格）
├── common/                 统一底座：LLM 路由 / 向量 / 指标 / 外部服务客户端
├── demo_a_security/        Demo A
├── demo_b_copywriting/     Demo B
├── demo_c_manju/           Demo C（编排层，执行复用 aigc-comic）
├── portal/                 Demo D 门户与指标口径页
├── third_party/            引入的第三方开源组件
├── requirements/           分模块依赖
├── scripts/                环境与启动脚本
└── docs/                   演示讲稿、追问应答、录屏清单
```

---

## 硬件与约束

| 项 | 值 |
|---|---|
| GPU | RTX 5070 Laptop，8 GB 显存 |
| CUDA / torch | 12.8 / 2.7.0+cu128 |
| 关键约束 | 8 GB 显存不足以同时驻留视觉 + 语音 + LLM，须阶段式加载（`VRAM_STAGED_LOADING`） |
| pip 源 | 阿里云（清华源在本机不可用） |

---

## 进度

见 `PLAN.md` 第 14 节。
