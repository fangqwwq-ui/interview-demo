# 面试 Demo 全链路演示 Runbook（P5 录屏预演）

> 定位：**「方案的独立复现 / 技术验证版」**。所有对外措辞、录屏字幕、讲稿统一按此口径。
> 本文件是现场录屏的**操作脚本 + 讲稿串词 + 风险回退**合一文档。逐镜分镜见同目录 `recording_shotlist.xlsx`。

---

## 0. 端口与服务地图（先记牢，录屏时少切窗口）

| 端口 | 服务 | 启动命令（见 §3） | 本会话实测 |
|---|---|---|---|
| 8000 | aigc-comic（漫剧执行，既有） | 普通终端跑 `app.py` | /health → 200 `{"status":"ok"}` |
| 8100 | Demo B 文案三路对照 | `python -m demo_b_copywriting.app` | / → 200 |
| 8188 | ComfyUI（只读复用，本次未起） | `start_comfyui.ps1` | 本会话网络被拦，未起 |
| 8200 | Demo D 统一门户（含指标口径页） | `python -m portal.app` | /claims → 200 |

---

## 1. 演示总流程（建议 12–15 分钟）

```
开场(1m) → Demo A 安防(3m) → Demo B 文案(3m) → Demo C 漫剧(4m) → Demo D 门户口径(3m) → 收尾+追问(2m)
```

总原则：**先结论后展开**；每个数字都能指出「定义 + 公式 + 脚本路径」；遇到 n/a 主动声明，不掩饰。

---

## 2. 环境与前置（录屏前 10 分钟自检）

- 解释器分流（重要）：
  - **项目 venv** `E:\interview-demo\.venv\Scripts\python.exe` 跑 A / B / D。
  - **aigc-comic 必须用 workbuddy 默认解释器** `C:\Users\18751\.workbuddy\binaries\python\envs\default\Scripts\python.exe`（含 `edge_tts 7.2.8`）；项目 venv 无 edge_tts。
- 模型就绪度：
  - Demo A：`models/demo_a/yolo11n.onnx`、`models/bge-small-zh-v1.5/`、`data/index/demo_b/` 均就位。⚠️ `faster-whisper-tiny` 权重**未下完**（`.incomplete`），ASR 步离线会失败 → 用 `--no-asr` 跑"视觉→RAG→工单"离线子链路（已验证绿）。
  - Demo C：出图走**占位图链路**（不设 `SD_API_URL`），不依赖 ComfyUI；真实出图历史证据 `e7046afba61a` / `78c80dba8b74`。
- LLM 通道（**n/a，必须声明**）：Demo B 自研改写臂、Demo C LLM 审核通道未接生产 LLM（无 Key / 未装 Ollama），按现状交付。

---

## 3. 各 Demo 启动命令与实测

### Demo A — 多模态安防（视觉→ASR→RAG→工单）
```bash
cd E:\interview-demo
.venv\Scripts\python.exe scripts\demo_a_e2e.py --no-asr
```
- 预期输出（**本会话实测 2026-10-02 23:17**）：
  - 视觉：YOLO11n，`provider=['CUDAExecutionProvider','CPUExecutionProvider']`，**未降级**，中位 **14.0 ms**，检出 bus + 4 person（810×1080）。
  - RAG：bge-small-zh-v1.5 + faiss-flat-ip，14 条索引，分层检索命中正常。
  - 工单：幂等写入通过（连写两次 14→15 条），状态流转 `detected→acknowledged`。
  - 落盘：`outputs/demo_a/e2e/e2e_result.json`、标注图 `01_detected.png`。
- 讲稿要点：四层串联是"方案独立复现版整体凭据"，每步打印**真实后端**（provider/device/encoder），不用"预期"代替实测。
- ⚠️ 若评委要看语音问答：需先补全 `faster-whisper-tiny`（联网 `modelscope` ~9.5MB/s），或临时 `python scripts\demo_a_e2e.py --asr-size base`（联网拉 small）。

### Demo B — 文案人类化（三路对照）
```bash
cd E:\interview-demo
.venv\Scripts\python.exe -m demo_b_copywriting.app      # 端口 8100
```
- 预期：`http://127.0.0.1:8100` → 200，三路对照页（原文 / 内核改写 `humanize-chinese` / 自研改写）。
- 讲稿要点：明确三分来源——`humanize-chinese` 是**第三方开源**（保留 LICENSE、声明来源，非自研）；自研改写臂为本次新增，但**当前 n/a（未接 LLM）**，页面上该列可能为空或回退。
- ⚠️ **口径红线**：绝不能把 `humanize-chinese` 讲成自研。

### Demo C — 漫剧流水线（aigc-comic 既有能力 + 本次新增 a1111_shim）
> ⚠️ **录制方式（关键）**：本环境 HTTP `/run` 偶发 500（uvicorn 线程内编排脆弱、服务端不落详细日志）。**录屏走 `pipeline.py` CLI 更可控、可逐步复述日志**，且对缺失音频静音补段、稳健出片。

推荐录屏命令（断点续跑演示）：
```bash
# 终端①：普通终端启动执行服务（默认解释器，勿用项目 venv）
cd E:\aigc-comic
C:\Users\18751\.workbuddy\binaries\python\envs\default\Scripts\python.exe app.py

# 终端②：跑全链路
cd E:\aigc-comic
python pipeline.py --topic "学霸的反转"          # 新建任务，约 20–25s 出片
# 记录打印出的 task_id（如 17300d64b46f）
python pipeline.py --task <上一步task_id>        # 断点续跑：脚本/图/音频全跳过，仅重合成 ~5s
```
- 实测证据（本会话）：
  - `pipeline.py --task a5258ee3f412` 续跑 **5.0s** 出 `final.mp4`（154KB）；其中 `audios.s4` 因 `edge_tts` 偶发 `NoAudioReceived` 缺一段，合成步自动补"静音段"，**不中断**。
  - 历史首跑 `17300d64b46f`：24.09s → done；同 id 再跑 13.06s（三步跳过）。
- 讲稿要点：**"pipeline 状态机 + 断点续跑是 aigc-comic 既有能力，本次只做接入与复现，不声称自研"**；本次新增的是下游 `a1111_shim`（把 ComfyUI 伪装成 A1111，使既有代码零改动拿真实出图）。
- ⚠️ 若坚持用 `/run`：成功即录；一旦 500，**立即切 `pipeline.py --task <id>` 兜底**，不要卡镜头。
- ⚠️ 沙箱删除守卫：合成步 `unlink()` 清理分片在**受管控解释器**下会被拦并触发 `SystemExit(1)`（compose 只兜 `OSError`），导致 `/run` 500、状态停在 `audio_done`。**对策：aigc-comic 在普通终端跑**（录屏本就该如此），不改其源码。

### Demo D — 统一门户 + 指标口径页
```bash
cd E:\interview-demo
.venv\Scripts\python.exe -m portal.app        # 端口 8200
```
- 预期：`/`（概览入口）、`/claims`（指标口径页）、`/components`（来源声明页）均 200；`/docs` 提供 OpenAPI。
- 讲稿要点：逐条点开 `/claims`，每个数字展示「指标定义 / 计算公式 / 数据集与规模 / 计算脚本路径 / 实测值 / 与简历值差异及原因」。例：Demo B 语法维度实测 0.0（规则噪声），不粉饰。
- 来源账本：`portal/provenance.py` 三分类（第三方 / 既有 / 新增），含 License 与使用边界。

---

## 4. 风险与回退清单（录屏前必读）

| # | 风险 | 触发 | 回退 / 话术 |
|---|---|---|---|
| R1 | Demo C HTTP `/run` 500 | uvicorn 线程编排脆弱 | 切 `pipeline.py --task <id>`；或声明"既有能力已验证，录屏用 CLI 路径" |
| R2 | Demo C 合成步 `unlink` 被沙箱守卫拦 | 在沙箱/管控解释器跑 | aigc-comic 必须在**普通终端**跑；不改源码 |
| R3 | Demo A ASR 失败 | `faster-whisper` 未下完 | 用 `--no-asr` 跑离线子链路；声明 ASR 为联网补齐项 |
| R4 | Demo B 自研改写列为空 | 未接 LLM | 主动声明 n/a，强调 `humanize-chinese` 为开源非自研 |
| R5 | ComfyUI 起不来 | 本机会话网络被拦 | 用占位图链路演示；真实出图走历史证据 `e7046afba61a`/`78c80dba8b74` |
| R6 | 数据小瑕疵：`audios.s4` done=true 但 path=null | edge_tts 偶发 | 合成步已静音兜底，不影响成片；讲稿不主动提，被追问如实说 |

---

## 5. 录屏操作纪律

- 屏幕录制即可（无需出镜）；当前语料/示例均为自建占位内容，不出现真实证件/姓名。
- 每个 Demo 开录前先 `cd` 到正确目录、确认解释器正确（见 §2）。
- 耗时长的步骤（C 首跑 ~25s）提前说明"现在开始跑全链路，约 20 秒"，避免静默等待。
- 任何一步翻车：按 §4 回退，**当场说"这是既有能力的验证路径，我们切到 CLI 兜底"**，体现工程素养而非掩饰。
