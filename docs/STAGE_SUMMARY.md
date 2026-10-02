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
| 实时画面带检测框与标签 | `outputs/demo_a/e2e/01_detected.png` | CUDA 真实生效，**7.63 ms**，检出 bus 0.939 + 4 person |
| 语音提问 → RAG 应答闭环 | `scripts/demo_a_e2e.py`（exit=0） | ASR 13.41 ms → RAG 5~8 ms → 工单落库 |
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

### 实测指标（4 项，全部脚本回填）

- **a_vision_latency** = **7.63 ms**（provider=CUDAExecutionProvider，未降级）
- **a_asr_latency** = **13.41 ms**（cpu/int8；⚠️ 纯音调输入，只证明链路与 VAD 正确，**不构成识别准确率证据**）
- **a_rag_accuracy** = **0.9118**（无 RAG 0.7647，n=102，**未达简历 94%**）
- **a_api_latency_gain** = RPS **+5.3%~+24.5%**（**未达简历 40%**；简历未说明衡量吞吐还是延迟，两者不可互相印证）

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

### C. 三个「假绿灯」（本项目最值得记的一类）

1. **`get_available_providers()` 含 CUDA ≠ CUDA 真能用**：ORT 缺依赖时
   **不抛异常只静默降级 CPU**，而那份清单仍列CUDA，连 `get_device()` 都返回 GPU。
2. **自检脚本自己犯了同一个错**：`--check` 的会话验证因 `sys.path[0]` 是
   scripts/ 而失败，走到 except 分支——而那分支回退到**不可信的清单**，
   于是显示「CUDA 可用：是」。一个专门防假绿灯的模块，自己造了假绿灯。
   → 三处修正：验证失败按不可用报 + 打印原因 + 补 sys.path 注入。
3. **版本结论错了三次**：查文档表 → 查 wheel 列表 → 真装真跑。
   每次依据都不同，只有第三次（真装 + 真实会话验证）是对的。

---

## 四、下一步

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | **真实中文录音** | 当前 ASR 只用合成音验证链路，**识别准确率无证据**；需一段真实录音 |
| P0 | `docs/demo_script_{A,B,C}.md` | 三个 Demo 的讲稿 |
| P0 | `docs/objections.md` | 面试追问应答底稿（覆盖本轮 5+3 个缺陷的追问路径） |
| P1 | `docs/recording_checklist.md` | 录屏清单 |
| P1 | InsightFace 人脸识别实测 | 已装未跑通（需 buffalo_l 权重，同样走 ModelScope） |
| P2 | Demo C 审核层 LLM 通道 | 需 `DASHSCOPE_API_KEY` 或 Ollama 许可 |

---

## 五、当前阻塞与风险

| 项 | 状态 | 影响 |
|---|---|---|
| **无 TTS** | 无可用 TTS 依赖 | ASR 只能用合成音验证链路，**识别准确率无法证明** |
| `DASHSCOPE_API_KEY` / Ollama | **未获得** | Demo B 改写臂与 Demo C 审核层 LLM 通道无法实测，相关指标明确标「未执行，不给估算」 |
| 沙箱删除守卫 | 已确认 | `pip uninstall` / 降级安装被拦并留下 `~onnxruntime` 残缺目录；**降级/修复需在普通终端跑** |
| 权重源 | 已解决 | `modelscope.cn` 实测 **9.5 MB/s**（比阿里云快 28 倍），统一从该源取 |

---

## 六、如何复现

```bash
cd E:/interview-demo
PY="E:/interview-demo/.venv/Scripts/python.exe"

# 0. 视觉/语音依赖（ONNX 全栈，无需 torch）
"$PY" scripts/setup_demo_a_vision.py            # 安装 + 下载权重
"$PY" scripts/setup_demo_a_vision.py --check    # 自检（真实推理会话验证 CUDA）

# 1. 状态机 + 工单自检（30 项断言）
"$PY" -m demo_a_security.selfcheck

# 2. 端到端（视觉 → 语音 → RAG → 工单）并回填指标
"$PY" scripts/demo_a_e2e.py

# 3. MCP 端到端（真实协议客户端，8 项）
"$PY" scripts/verify_mcp_client.py

# 4. 压测（需先启动服务，lifespan 预热约 67 秒）
"$PY" -m uvicorn demo_a_security.service:app --host 127.0.0.1 --port 8200 --log-level warning
"$PY" scripts/bench.py --concurrency 1,4,8,16 --rounds 150
"$PY" scripts/bench_report.py

# 5. 评测（回填 registry）
"$PY" -m demo_a_security.eval_set
"$PY" -m demo_a_security.evaluate --publish-metrics
```

> 压测前务必确认预热完成：打一次 `POST /v2/ask`，返回应< 2 秒。