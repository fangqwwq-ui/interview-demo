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

| 类别 | 组件 | 位置 | 说明 |
|---|---|---|---|
| **第三方开源** | humanize-chinese | `third_party/humanize-chinese` | MIT 协议。中文 AI 痕迹检测与改写内核。保留原 LICENSE，未做修改 |
| **既有自研项目** | aigc-comic | `E:\aigc-comic` | 本人既有的漫剧自动生产线（M1/M2 完成）。本项目**通过 HTTP 调用其 FastAPI 服务，未修改其代码** |
| **既有工具** | ComfyUI | `D:\ComfyUI` | 开源图像/视频生成框架，通过 HTTP API 调用 |
| **本次新增** | 本仓库其余全部代码 | `E:\interview-demo` | 统一底座、RAG 层、指标层、编排层、审核层、门户、Demo A 全部 |

---

## 三个 Demo

| Demo | 内容 | 目录 | 状态 |
|---|---|---|---|
| **B** | AIGC 文案人类化改写工作台：RAG 检索增强 + 四指标质检 | `demo_b_copywriting/` | 计划中（优先） |
| **A** | 多模态智能安防 Agent：视觉检测 + 人脸识别 + 语音问答 + MCP 工具编排 | `demo_a_security/` | 计划中（唯一从零构建） |
| **C** | 漫剧智能生产流水线：编排层 + ComfyUI 出图适配 + 状态机 + 审核层 | `demo_c_manju/` | 计划中（复用既有服务） |
| **D** | 统一门户 + 指标口径页 | `portal/` | 计划中 |

完整实施计划见 [`PLAN.md`](./PLAN.md)。

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

### 3. 启动外部服务

```powershell
powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1
```

| 端口 | 服务 | 用途 |
|---|---|---|
| 8188 | ComfyUI（`D:\ComfyUI`） | Demo C 图像生成 |
| 8000 | aigc-comic（`E:\aigc-comic`） | Demo C 执行服务 |
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
