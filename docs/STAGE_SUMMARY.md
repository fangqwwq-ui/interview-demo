# 阶段性小结（截至 2026-10-02，Demo A 已全部完成）

> 口径：全部交付物统一为 **「方案的独立复现 / 技术验证版」**。
> 本文档中每个数字都能指出定义、公式、数据集与脚本路径；**数字由脚本回填，不可手写**。
> 实测达不到简历值的，**按实测写并说明差异**，不凑数。

---

## 一、总体进度

| Demo | 主题 | 状态 | 说明 |
|---|---|---|---|
| **A** | 多模态智能安防 Agent | 🟢 **5/5 验收项全部达成** | 从零构建；视觉走 ONNX+CUDA，语音走 CTranslate2，**全程不依赖 torch** |
| **B** | 文案「AI 味」检测与改写 | 🟢 功能完成，指标受限 | 5 项指标已实测；检测器灵敏度不足已标注 |
| **C** | 漫剧智能生产流水线 | 🟡 编排层完成，**LLM 通道缺 Key** | 4 项指标已实测；审核层双通道只跑通规则通道 |
| **D** | 统一门户 + 指标口径页 | 🟢 完成 | **13 项**指标全部有实测凭据 |

---

## 二、Demo A 验收（PLAN §7.4 五项全部达成）

| 验收项 | 凭据 | 实测 |
|---|---|---|
| 实时画面带检测框与标签 | `outputs/demo_a/e2e/01_detected.png` | CUDA 真实生效，**6.5 ms**，检出 bus 0.939 + 4 person |
| 语音提问 → RAG 应答闭环 | `scripts/demo_a_e2e.py`（exit=0） | **真实中文语音** 1380 ms → RAG 5~8 ms → 工单落库 |
| MCP 三工具 + 日志回放 | `scripts/verify_mcp_client.py` | **8/8** 通过（真实 mcp 协议客户端） |
| `bench/report.md` 对照数据 | 脚本从原始 JSON 生成 | 4 档并发，26100 请求，失败 0 |
| 评测集准确率对照表 | `outputs/demo_a/eval_result.json` | 102 条，0.7647 → **0.9118** |

### 关键架构决策：全栈绕开 torch

| 组件 | 推理运行时 | torch | 实测 |
|---|---|---|---|
| YOLO11n | onnxruntime-gpu（官方 onnx 10.42 MiB） | 不需要 | CUDA 生效，7.63 ms |
| InsightFace | onnxruntime-gpu | 不需要 | 已装 |
| faster-whisper | CTranslate2 4.8.2 | 不需要 | cpu-int8，13.41 ms |

**依据**：本机镜像 343 KB/s，torch cu128 约 2.7 GB → **约 137 分钟**；
换 ONNX + CTranslate2 后约 500 MB。CUDA 运行时**只读复用** `D:\ComfyUI`
的 torch 2.7.0+cu128 自带 CUDA 12.8（`os.add_dll_directory` + `ort.preload_dlls()`）。

### 实测指标（5 项 Demo A，全部脚本回填）

- **a_vision_latency** = **6.5 ms**（provider=CUDAExecutionProvider，未降级，含 letterbox+NMS+坐标反算）
- **a_asr_accuracy** = SRA **6/12 = 0.50**，平均 CER **0.1649**（faster-whisper small / cpu-int8）
  - 分组：语义描述 CER 0.043（最好）｜口语短问 CER 0.352（最差）
  - ⚠️ 语音为 **TTS 合成**（真人音色），信噪比高、无方言口音，**识别率系统性偏高**；
    n=12 置信区间宽 → 只作回归护栏，非真人场景性能依据
- **a_asr_latency** = 真实语音 **1380 ms**（4.8 s 音频）；模型加载另计 1.08 s，不混算
- **a_rag_accuracy** = **0.9118**（无 RAG 0.7647，n=102，**未达简历 94%**）
- **a_api_latency_gain** = RPS **+5.3%~+24.5%**（**未达简历 40%**；口径不同不可互相印证）

> 语音数据来源：`edge-tts` 合成**带标准答案**的 12 条中文语音（两种音色交替），
> 覆盖结构化过滤 / 语义描述 / 口语 / 数字混读四类问法。
> 这是本项目唯一能拿到「有标注的真实语音」的途径 —— 否则 ASR 只能靠合成音调
> 证明链路跑通，**没有准确率证据**。

---

## 三、本轮定位并修复的真实缺陷

均属「不查日志只看最终数字，就会得出错误结论」的类型。

### A. 三个性能/功能倒退（代码错误，非调参）

1. **`sqlite3.InterfaceError` → HTTP 500**：`check_same_thread=False` **不提供并发安全**
   （只解除「跨线程抛异常」的检查）。→ `TicketStorePool` 每线程连接。
   讽刺之处：`store.py` 早期 docstring 就写明了正确做法，v2 却违反了它。
2. **懒加载被误算成服务延迟**：`rag.load()` 首次 **64 秒**、之后 0.5 ms。
   8 线程各自 load → 压测卡 **4 分 36 秒**。→ `lifespan` 预热后降到 **2.2 秒**。
3. **连接建在错误线程**：`/v2/stats` 在事件循环线程取 store 再丢进线程池
   → `IndexError`。→ 取连接放进执行查询的函数内部。

### B. 压测方法本身不可信

同一份代码两次跑出 **+28.2%** 与 **-9.2%** 两个相反结论。
根因：固定「先 v1 后 v2」（顺序偏差）+ 单轮采样（并发 16 时 max 达 558 ms）。
→ 交替顺序 + 3 轮中位数 + `rps_spread_pct` 可信度指标；spread >15% 的档位
在报告里直接标为「结论不可信」。

### C. 三个「假绿灯」与一个「假低分」（本项目最值得记的一类）

1. **`get_available_providers()` 含 CUDA ≠ CUDA 真能用**：ORT 缺依赖时
   **不抛异常只静默降级 CPU**，而那份清单仍列CUDA，连 `get_device()` 都返回 GPU。
2. **自检脚本自己犯了同一个错**：`--check` 的会话验证因 `sys.path[0]` 是
   scripts/ 而失败，走到 except 分支——而那分支回退到**不可信的清单**，
   于是显示「CUDA 可用：是」。一个专门防假绿灯的模块，自己造了假绿灯。
   → 三处修正：验证失败按不可用报 + 打印原因 + 补 sys.path 注入。
3. **版本结论错了三次**：查文档表 → 查 wheel 列表 → 真装真跑。
   每次依据都不同，只有第三次（真装 + 真实会话验证）是对的。
4. **ASR「识别很差」其实是判定工具坏了**：首轮 SRA 0.25 / CER 0.313，
   看着像识别能力差。实查发现 whisper 输出**繁体**（「廠區倉庫」）而标注简体，
   **内容全对却判 DIFF**。加繁简归一后 → SRA 0.50 / CER 0.165。
   **分数异常时先怀疑度量，再怀疑被测对象** —— 打印几条原始输出往往一眼看出。
   ⚠️ 归一写了口径理由（下游是 bge 向量检索，繁简 embedding 几乎一致）；
   若下游是逐字比对则不能这样判。「快递员→快递援」这类同音错字仍判错，
   **不是放宽标准**。

---

## 四、下一步

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | `docs/demo_script_{A,B,C}.md` | 三个 Demo 的讲稿 |
| P0 | `docs/objections.md` | 面试追问应答底稿（覆盖本轮全部缺陷的追问路径） |
| P1 | **真人录音验证** | 当前 ASR 用 TTS 合成语音，**识别率偏高**。有真人录音后应重测，这也是面试时最可能被追问的边界 |
| P1 | `docs/recording_checklist.md` | 录屏清单 |
| P1 | InsightFace 人脸识别实测 | 已装未跑通（需 buffalo_l 权重，同样走 ModelScope）|
| P2 | ASR 模型升级到 medium | small 在口语短问上 CER 0.352，medium 应显著改善（但 CPU 上更慢）|
| P2 | Demo C 审核层 LLM 通道 | 需 `DASHSCOPE_API_KEY` 或 Ollama 许可 |

---

## 五、当前阻塞与风险

| 项 | 状态 | 影响 |
|---|---|---|
| **无真人录音** | 仅有 TTS 合成语音 | ASR 识别率**偏高**，不可作为真人场景依据；已写入 gap_note |
| `DASHSCOPE_API_KEY` / Ollama | **未获得** | Demo B 改写臂与 Demo C 审核层 LLM 通道无法实测，相关指标明确标「未执行，不给估算」 |
| 沙箱删除守卫 | 已确认 | `pip uninstall` / 降级安装被拦并留下 `~onnxruntime` 残缺目录；**降级/修复需在普通终端跑** |
| 权重源 | 已解决 | `modelscope.cn` 实测 **9.5 MB/s**（比阿里云快 28 倍），统一从该源取 |

---

## 六、如何复现

```bash
cd E:/interview-demo
PY="E:/interview-demo/.venv/Scripts/python.exe"
DEF="C:/Users/18751/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

# 0. 视觉/语音依赖（ONNX 全栈，无需 torch）
"$PY" scripts/setup_demo_a_vision.py            # 安装 + 下载权重
"$PY" scripts/setup_demo_a_vision.py --check    # 自检（真实推理会话验证 CUDA）

# 1. 状态机 + 工单自检（30 项断言）
"$PY" -m demo_a_security.selfcheck

# 2. 端到端（视觉 → 真实语音 → RAG → 工单）并回填指标
"$PY" scripts/demo_a_e2e.py

# 3. 语音识别准确率（需先生成语音数据，用默认解释器跑）
"$DEF" scripts/gen_speech_eval.py
"$PY"  scripts/eval_asr.py --size small --publish-metrics

# 4. MCP 端到端（真实协议客户端，8 项）
"$PY" scripts/verify_mcp_client.py

# 5. 压测（需先启动服务，lifespan 预热约 67 秒）
"$PY" -m uvicorn demo_a_security.service:app --host 127.0.0.1 --port 8200 --log-level warning
"$PY" scripts/bench.py --concurrency 1,4,8,16 --rounds 150
"$PY" scripts/bench_report.py

# 6. RAG 评测（回填 registry）
"$PY" -m demo_a_security.eval_set
"$PY" -m demo_a_security.evaluate --publish-metrics
```

> 压测前务必确认预热完成：打一次 `POST /v2/ask`，返回应< 2 秒。