# 阶段性小结（截至 2026-10-02）

> 口径：全部交付物统一为 **「方案的独立复现 / 技术验证版」**。
> 本文档中每个数字都能指出定义、公式、数据集与脚本路径；**数字由脚本回填，不可手写**。
> 实测达不到简历值的，**按实测写并说明差异**，不凑数。

---

## 一、总体进度

| Demo | 主题 | 状态 | 说明 |
|---|---|---|---|
| **A** | 多模态智能安防 Agent | 🟡 **非 GPU 部分 100%**，视觉/语音未接 | 从零构建，2661 行；5 项验收中 3 项已达标 |
| **B** | 文案「AI 味」检测与改写 | 🟢 功能完成，指标受限| 5 项指标已实测；检测器灵敏度不足已标注 |
| **C** | 漫剧智能生产流水线 | 🟡 编排层完成，**LLM 通道缺 Key** | 4 项指标已实测；审核层双通道只跑通规则通道 |
| **D** | 统一门户 + 指标口径页 | 🟢完成 | 11 项指标全部有实测凭据 |

代码总量与提交历史见 `git log`。最近三个提交：`4b0a345`、`788b1b7`、`aa1dea7`。

---

## 二、本轮（Demo A）完成内容

### 2.1 关键架构决策：绕开 CUDA torch

实测发现 Demo A 的三个模型**都不需要 torch CUDA**：

| 组件 | 推理运行时 | torch | 权重体积 |
|---|---|---|---|
| YOLO11n目标检测 | onnxruntime-gpu | **不需要** | 10.42 MiB |
| InsightFace 人脸识别 | onnxruntime-gpu | **不需要** | ~300 MiB |
| faster-whisper ASR | CTranslate2 | **不需要** | ~150 MiB |

**依据**：本机阿里云镜像带宽实测 **343 KB/s**，torch 2.7.0+cu128 + torchvision约 2.7 GB
→ **约 137 分钟**。绕开后总量约 500 MB。该决策已由用户确认。

顺带纠正一个容易误判的点：`faster-whisper` / `insightface` / `ultralytics` / `mcp` /
`locust` 全是 `py3-none-any` **纯 Python 轮子、零编译**。看到「没有 cp313-win 轮子」
**不等于**需要本地编译。

### 2.2 交付物

| 模块 | 文件 | 行数 | 内容 |
|---|---|---|---|
| 事件状态机 | `demo_a_security/events.py` | 188 | 5 状态闭环；转移表集中裁决；终态无出边；非法转移抛异常 |
| 工单存储 | `demo_a_security/store.py` | 386 | SQLite；幂等 upsert；transitions 可回放；`TicketStorePool` 每线程连接 |
| 分层 RAG | `demo_a_security/rag.py` | 458 | 结构化过滤 → 语义召回 → 会话记忆；`infer_filters` 问句意图抽取 |
| MCP Server | `demo_a_security/mcp_server.py` | 412 | mcp 2.x `MCPServer` 三工具；调用日志 jsonl 可回放 |
| 双版本服务 | `demo_a_security/service.py` | 384 | v1 同步 / v2 异步并发并存；`lifespan` 启动预热 |
| 评测 | `eval_set.py` / `evaluate.py` | 271 / 229 | 102 条自建评测集；两臂对照 |
| 自检 | `demo_a_security/selfcheck.py` | 196 | 30 项断言，**改动后必跑** |
| 压测 | `scripts/bench.py` / `bench_report.py` | — | httpx+asyncio；报告由原始 JSON 自动生成 |

### 2.3 实测结果（口径：脚本回填，文档不手写）

**① RAG 检索命中率** — `a_rag_accuracy`

```
无 RAG 基线 0.7647  →  分层 RAG 0.9118
绝对 +14.7pp / 相对 +19.2%，n=102
```

分组验证（这是设计命题的直接证据）：

| 分组 | 无 RAG | 分层 RAG | 提升 |
|---|---|---|---|
| 声明了结构化过滤（81 条） | 0.7778 | **0.9506** | **+17.3pp** |
| 未声明过滤（21 条） | 0.7143 | 0.7619 | +4.8pp |

> **未达简历所写的 94%，按实测值报告。**
> 剩余 9 条失败是**真实能力边界**（语义近似，如「门被撬了吗」命中「翻越围栏」的
> A-0001 而非「暴力尝试」的 A-0012），非判据问题 ——
> 严格口径与集合命中口径结果完全一致（均 0.9118）可证。

**② 接口吞吐提升** — `a_api_latency_gain`

| 并发 | v1 RPS | v2 RPS | RPS 提升 | P95 改善 | 波动 | 失败 |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 213.32 | 260.61 | +22.2% | 6.8% | — | 0 |
| 4 | 419.40 | 522.19 | **+24.5%** | 7.8% | — | 0 |
| 8 | 432.56 | 479.74 | +10.9% | 8.0% | — | 0 |
| 16 | 300.86 | 316.91 | +5.3% | 4.1% | — | 0 |

合计 **26,100 请求、失败 0**。三份完整报告见 `bench/report.md`（脚本生成）。

> **低于简历所写的 40%，按实测报告。** 且简历「响应速度提升 40%」未说明衡量的是
> 吞吐还是延迟分位，本项目以 **RPS 为主指标并同时给出 P95**，
> 因此**不可把此区间当作简历 40% 的印证**。
>
> 另注：提升最高的不是「并发越高越好」，而是并发 4（+24.5%）；并发 16 只有 +5.3%。
> 原因是异步收益来自「等待时间可重叠」，CPU 与线程池打满后没有可重叠的等待。

**③ 其他已达标项**

- **MCP三工具可被外部客户端调用 + 调用日志可回放**：真实 mcp 协议客户端验证 **8/8 通过**；
  回放能区分「预期失败且同样失败」与「结果不一致」。
- **状态机 + 工单存储自检**：**30 项断言全部通过**。

### 2.4 本轮定位并修复的 5 个真实缺陷

这五个都属于「不查日志只看最终数字，就会得出错误结论」的类型。已全部写入
`bench/report.md` 与代码注释。

1. **`sqlite3.InterfaceError` → HTTP 500**
   `check_same_thread=False` **只解除「跨线程抛异常」的检查，不提供并发安全**。
   单个 `sqlite3.Connection` 共享给 8 线程线程池会随机崩（并发 8 失败 91 次）。
   → 新增 `TicketStorePool`，每线程一条连接。
   ⚠️ 讽刺之处：`store.py` 早期 docstring 就写明了正确做法，v2 却违反了它 ——
   **注释里的结论要在写实现时逐条对照。**

2. **懒加载被误算成服务延迟**
   `rag.load()` 首次 **64,241 ms**、之后 **0.5 ms**。8 线程各自 load →
   并发争抢，8 路压测整体卡 **4 分 36 秒**，且第一个请求背上全部加载时间。
   → 用 FastAPI `lifespan` 启动预热。修复后并发 8 从 **4 分 36 秒 → 2.2 秒，失败 0**。

3. **连接建在错误线程**
   `/v2/stats` 在事件循环线程取好 store 再丢进线程池（连接创建线程 ≠ 使用线程）
   → `IndexError: tuple index out of range`。
   → 把取连接放进执行查询的函数内部。

4. **MCP 包装函数抹掉入参 schema**
   `_wrap` 写成 `def inner(*a, **kw)`，SDK 从签名反推 schema 只能看到两个形参，
   三个工具入参全退化成 `{"a":..., "kw":...}`，真实参数（zone/state/limit）消失。
   服务照跑，客户端按 schema 传参**必然失败**。→ `functools.wraps` 保留签名。

5. **压测方法本身不可信 → 结论可翻转**
   同一份代码两次跑出 **+28.2%** 与 **-9.2%** 两个相反结论。
   两个未控制变量：固定「先 v1 后 v2」（顺序偏差）+ 单轮采样（并发 16 时
   stdev 50~60ms、max 558ms）。
   → 每档 3 轮 + 轮内交替顺序 + 取中位数 + 输出 `rps_spread_pct` 可信度指标；
   **spread > 15% 的档位在报告里直接标为「结论不可信」**。
   修复后三轮结论一致，spread 稳定 3~6%。

另有两个自造 bug，同样是「静默型」：
- `rag.py` 用 `filters.pop()` 原地改字典 → `filters_applied` 审计凭据永远是 `{}`。
- 空集合falsy 导致过滤失效：`if allowed_ids and ...` 在候选被滤光时**静默退化成
  不过滤**，返回全库结果（表现为「查东门答西门」）。

---

## 三、Demo B / C 当前状态（简要）

**Demo B（文案 AI 味检测与改写）** — 5 项指标已实测，但有明确边界：
- 第三方 `humanize-chinese` 检测器灵敏度**分语域**：20 条评测集中 **14 条得 0 分**，
  对「评测腔/ 种草腔」基本失明；上游 README 的示例分数不可复现（标注 72，实测 0）。
  → 因此**禁止把内核分当唯一指标**，必须与自研词典并报、且按语域分组。
- 规则改写会破坏中文语法（95→62 仍 HIGH，产「之于于」等病句）→ 改写走自研 LLM + RAG。

**Demo C（漫剧流水线）** — 4 项指标已实测：
- A1111 shim 已打通 `aigc-comic` 端到端（`aigc-comic` **一行代码不改**）。
- 断点续跑已验证：task `4b916255dbb3` 从 `audio_done` 恢复耗时 **3.23s**，
  0 个阶段重做，MP4 完整 —— 但这是**`aigc-comic` 既有能力，不是自研**。
- 提示词适配覆盖率 **90.7%**（词表 100 条）；A/B 对照证明**真正起作用的是词表覆盖，
  剥离残留无肉眼差异** → 不得声称「剥离提升了画质」。
- **审核层 LLM 通道缺 Key未执行**，因此 `c_review_channel_complement`
  **明确不给估算值**。

---

## 四、下一步（按优先级）

| 优先级 | 事项 | 说明 |
|---|---|---|
| P0 | **ONNX 全栈一键安装脚本** | 已核实全部依赖真相，但**脚本尚未写**（勿当已完成） |
| P0 | Demo A 视觉/语音闭环 | YOLO11n ONNX + InsightFace + faster-whisper 实际推理，补齐「实时画面带检测框与标签」 |
| P1 | `docs/demo_script_{A,B,C}.md` | 三个 Demo 的讲稿 |
| P1 | `docs/objections.md` | 面试追问应答底稿（重点覆盖本轮 5 个缺陷的追问路径） |
| P1 | `docs/recording_checklist.md` | 录屏清单 |
| P2 | Demo C 审核层 LLM 通道 | 需 `DASHSCOPE_API_KEY` 或 Ollama 许可 |
| P2 | Demo B 改写臂接 LLM | 同上 |

---

## 五、当前阻塞与风险

| 项 | 状态 | 影响 |
|---|---|---|
| `DASHSCOPE_API_KEY` / Ollama 许可 | **未获得** | Demo B 改写臂与 Demo C 审核层 LLM 通道无法实测，相关指标明确标为「未执行，不给估算」 |
| `aigc-comic` 授权确认 | 未确认 | Demo C 的编排边界声明依赖于此 |
| 沙箱删除守卫 | 已确认存在 | `Path.unlink()` 会被拦截并抛 `SAFE_DELETE_FAIL_CLOSED`；已改为「删不掉就地重建」。**在普通终端跑 `aigc-comic`**，不要改其源码绕过 |
| 视觉/语音模型权重 | 未下载 | 约 500 MB（ONNX 路线），不需torch |

---

## 六、如何复现本轮结果

```bash
cd E:/interview-demo
PY="E:/interview-demo/.venv/Scripts/python.exe"

# 1. 自检（30 项断言，改代码后必跑）
"$PY" -m demo_a_security.selfcheck

# 2. 生成工单 + 索引 + 评测（--publish-metrics 回填 registry）
"$PY" -m demo_a_security.eval_set
"$PY" -m demo_a_security.evaluate --publish-metrics

# 3. MCP 端到端验证（真实协议客户端，8 项）
"$PY" scripts/verify_mcp_client.py

# 4. 压测（需先启动服务，lifespan 预热约 67 秒）
"$PY" -m uvicorn demo_a_security.service:app --host 127.0.0.1 --port 8200 --log-level warning
"$PY" scripts/bench.py --concurrency 1,4,8,16 --rounds 150
"$PY" scripts/bench_report.py
```

> 压测前务必确认预热完成：打一次 `POST /v2/ask`，返回应< 2 秒。
> 若不确认就压测，第一档会把64 秒模型加载算进延迟，读数失真。