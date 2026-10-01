# third_party —— 引入的第三方开源组件

本目录存放外部开源代码。**本目录内的代码不是本次自研产物**，演示与交付时必须如实声明。

---

## humanize-chinese

| 项 | 值 |
|---|---|
| 用途 | Demo B 的中文 AI 痕迹检测与改写内核 |
| 上游仓库 | https://github.com/yukinotech/humanize-chinese |
| 引入版本 | commit `efdefaa0ead85cfde3fe36450a06cbe995792bda`（2026-04-02） |
| 声明版本号 | `2.1.0`（见 `package.json`） |
| 声明许可 | MIT（见上游 `README.md` 徽章） |
| 引入日期 | 2026-10-01 |
| 改动 | **无。** 原样复制，未修改任何文件；复制时排除了 `.git` 与 `__pycache__` |

### ⚠️ 许可瑕疵（需在交付时说明）

上游仓库**未提供 `LICENSE` 文件**，仅在 `README.md` 中以徽章形式声明 MIT。这在严格意义上属于许可声明不完整。同时其 README 徽章指向的仓库名（`voidborne-d/humanize-chinese`）与实际 git remote（`yukinotech/humanize-chinese`）不一致。

处置：

- 保留上游 `README.md` 原样（其中含 MIT 声明），不改写、不重命名
- 本文件记录 commit 与来源，作为溯源凭据
- 演示与交付时说明「MIT 声明取自上游 README，上游未附独立 LICENSE 文件」
- 若需正式商用或再分发，建议向作者确认许可

### 能力清单

| 能力 | 入口 |
|---|---|
| 检测（AI 痕迹打分） | `scripts/detect_cn.py` |
| 改写（通用） | `scripts/humanize_cn.py` |
| 改写（学术） | `scripts/academic_cn.py` |
| 结构重组 | `scripts/restructure_cn.py` |
| 风格调整 | `scripts/style_cn.py` |
| 对照比较 | `scripts/compare_cn.py` |
| 本地 HTTP 服务 | `humanize_server.py` |
| 评测集 | `evals/evals.json` |
| 示例输入 | `examples/sample_{academic,general,social}.txt` |

### 实测评估（2026-10-01，本机执行）

已验证可用，并对其能力边界做了实测。**结论直接影响 Demo B 的架构决策。**

**规模**：`scripts/` 共 4646 行 Python；`patterns_cn.json` 含 9 组规则（`critical_patterns` / `high_signal_patterns` / `medium_signal_patterns` / `style_signals` / `replacements` / `punctuation_thresholds` / `scoring` / `academic_patterns`）。

**检测能力 —— 强，可用作度量仪器。**
在 `examples/sample_social.txt` 上得分 95/100（VERY_HIGH），输出 17 项问题、8 个分类明细，并给出 `困惑度 334.2`、`突发度 0.278`、`段落熵CV 0.016`、`信息熵 8.39`、`情感密度 0%`。检测维度扎实，可直接作为 Demo B 的**量化评测仪器**。

**改写能力 —— 弱，会破坏中文语法，不可直接用于交付。**
同一文本经 `humanize_cn.py --scene social --seed 42` 改写后，结果：

| 项 | 结果 |
|---|---|
| 检测分 | 95 → **62**（仅降 33 分，仍为 HIGH） |
| 语法质量 | **明显劣化**，出现病句 |

实际产出的病句样本（原文无此问题）：

- 「时间管理**之于于**每个人来说**都，**有着至关重要的意义」
- 「对于时间管理中不可或缺来说，学会拒绝**也的环节**地位不可小觑」
- 「**多少有些，**帮助我们更好地分配时间和精力」
- 「**肯定的是**，时间管理将成为每个现代人的必备技能」

原因是纯规则替换 + 噪声注入（`--no-noise` 可关闭噪声，但规则替换本身仍会产生不通顺搭配），且**无语义保真校验**。

**据此确定的架构决策**：

| 环节 | 采用方案 | 理由 |
|---|---|---|
| 检测 / 评分 | **使用本内核** | 能力强，是现成的可复现度量仪器 |
| 改写 | **不使用本内核的规则改写**，改为自研 LLM + RAG 改写链路 | 规则改写破坏语法且降分有限 |
| 校验 | 自研：语义保真度 + 内核分 + LLM 评审 | 内核无此项 |

### 本项目如何使用它

本项目**不修改**该内核，仅把它作为**检测器**（评分与分类明细），通过 `demo_b_copywriting/quality.py` 封装调用。

改写不依赖它，走自研链路。因此在 Demo B 中，自研部分为：

1. **RAG 检索增强改写**：垂类原生语料向量库 + 检索注入 + LLM 改写
2. **质检层**：语义保真度（embedding 余弦）、内核检测分、LLM 评审三维
3. **评测框架**：批量跑分、口径表、对照界面

> 面试可讲点：能说清「我评估过这个开源内核，它的规则改写会破坏语法，所以我把它降级为检测器、改写改走 LLM + RAG」——这是有工程判断力的表现，比笼统说「用了开源工具」强得多。
