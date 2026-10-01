"""简历 11 条主张 → 证据映射。

## 为什么需要这个文件

`registry.py` 管的是**指标**（可量化的数字）。但简历里有 11 条主张，
其中若干条不是数字而是功能（「MCP 自动化：告警→工单→统计」），
还有一条（生产运营事实）根本无法复现。

Demo D 门户的验收标准写着「指标口径页覆盖简历全部 11 条主张，无遗漏」。
只靠指标表会漏掉功能类与不可复现类，因此需要这一层做**逐条对照**：

    简历主张 → 归属 Demo → 可复现性判定 → 关联指标 → 证据形式 → 差异说明

## 三条纪律

1. **每条主张都必须出现**，不允许因为「不好讲」而略去 —— 略去本身就是不诚实。
2. `reproducibility` 必须是四档之一，且**只有 `high` 才允许在讲稿里当作强证据**。
3. `gap_note` 必须写清「简历值」与「本仓库能给出什么」的差距。
   例如第 5 条声称「10 万+ 语料」，本仓库只有自建语料 —— 这一类差距必须写明。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from common import config

# 可复现性四档
REPRO_HIGH = "high"                  # 可当场演示且能量化
REPRO_MEDIUM = "medium"              # 可演示，但量化口径与简历不同
REPRO_DOWNGRADED = "downgraded"      # 需降级形式演示（如小程序 → 网页）
REPRO_NOT = "not_reproducible"       # 无法复现，仅能说明

REPRO_LABEL = {
    REPRO_HIGH: "可演示（高置信）",
    REPRO_MEDIUM: "可演示（口径代理）",
    REPRO_DOWNGRADED: "降级演示",
    REPRO_NOT: "不可复现",
}

# 实现状态
STATUS_DONE = "done"
STATUS_PARTIAL = "partial"
STATUS_PENDING = "pending"
STATUS_NA = "not_applicable"


@dataclass
class Claim:
    id: str
    no: int
    resume_claim: str
    demo: str
    reproducibility: str
    status: str
    evidence: str
    metric_keys: List[str] = field(default_factory=list)
    gap_note: str = ""
    route: Optional[str] = None      # 门户内的入口链接（若有）

    def as_dict(self) -> dict:
        d = asdict(self)
        d["reproducibility_label"] = REPRO_LABEL.get(self.reproducibility, self.reproducibility)
        return d


CLAIMS: List[Claim] = [
    Claim(
        id="c01_multimodal",
        no=1,
        resume_claim="视觉识别 + 语音交互的多模态智能系统（YOLO 目标检测 + InsightFace 人脸识别 + Whisper 语音）",
        demo="A",
        reproducibility=REPRO_HIGH,
        status=STATUS_PENDING,
        evidence="实时带检测框的画面 + 语音提问→检索→作答闭环",
        metric_keys=[],
        gap_note=(
            "简历未给出该系统处理的视频路数或识别准确率，因此本 Demo 只验证「链路可跑通」与"
            "「单帧耗时」，不声称任何准确率。准确率需带标注的测试集，本仓库不造这个数。"
        ),
    ),
    Claim(
        id="c02_rag",
        no=2,
        resume_claim="RAG 分层检索 + 上下文记忆，智能应答准确率 72% → 94%",
        demo="A",
        reproducibility=REPRO_MEDIUM,
        status=STATUS_PENDING,
        evidence="自建问答评测集上的准确率对照表（原始 RAG vs 分层检索 + 上下文记忆）",
        metric_keys=["a_rag_accuracy"],
        gap_note=(
            "简历的 72%/94% 来自真实业务问答；本 Demo 用自建评测集（≥100 条）实测。"
            "评测集规模与领域分布不同，**数值不可直接对比**，只能在同一次评测内做前后对照。"
        ),
    ),
    Claim(
        id="c03_mcp_workflow",
        no=3,
        resume_claim="MCP 自动化：告警 → 工单 → 统计的闭环",
        demo="A",
        reproducibility=REPRO_HIGH,
        status=STATUS_PENDING,
        evidence="MCP 工具调用日志回放（query_events / create_ticket / stats 三个工具）",
        metric_keys=[],
        gap_note="链路可完整演示；不涉及生产环境的告警源，改用脚本注入的事件流。",
    ),
    Claim(
        id="c04_api_latency",
        no=4,
        resume_claim="接口响应速度提升 40%，5 模块并发调用",
        demo="A",
        reproducibility=REPRO_MEDIUM,
        status=STATUS_PENDING,
        evidence="locust 压测报告：优化前（同步串行）vs 优化后（异步并发 + 连接池）的 P95 延迟",
        metric_keys=["a_api_latency_gain"],
        gap_note=(
            "简历的 +40% 基于线上流量特征；本 Demo 在同机同负载下自测。"
            "**同机压测无法复现线上并发特征**，数值只用于证明「优化方向有效」。"
        ),
    ),
    Claim(
        id="c05_humanize_rag",
        no=5,
        resume_claim="文案人类化 RAG 系统：10 万+ 语料、6 个垂类、相似度 91%",
        demo="B",
        reproducibility=REPRO_MEDIUM,
        status=STATUS_PARTIAL,
        evidence="语料统计 + 语义相似度指标 + 三路对照页",
        metric_keys=["b_semantic_fidelity", "b_ai_tell_residual"],
        gap_note=(
            "⚠️ **规模差距必须写明**：简历声称 10 万+ 语料 / 6 垂类，本仓库语料为"
            "**全自建 35 条 / 5 垂类**（因不使用爬取数据，平台条款与个人数据风险）。"
            "因此本 Demo 能证明的是**链路可行性**，不是「规模下的检索质量」。"
            "「相似度 91%」也未采用 —— 简历未说明该 91% 是哪种相似度，本 Demo 改用可复算的余弦值。"
        ),
    ),
    Claim(
        id="c06_detection_pass",
        no=6,
        resume_claim="AIGC 检测通过率 28% → 89%",
        demo="B",
        reproducibility=REPRO_MEDIUM,
        status=STATUS_PARTIAL,
        evidence="第三方检测器打分对照 + 自研 AI 味词典命中数，按语域分组",
        metric_keys=["b_detection_score_drop", "b_ai_tell_residual"],
        gap_note=(
            "简历值是**平台检测通过率**（通过/不通过），本 Demo 用开源检测器的**分数**代理，"
            "两者不是同一把尺子。更关键的是：实测发现该检测器灵敏度分语域，"
            "在 20 条评测集上 14 条得 0 分，因此**必须与自研词典并报**，不得单独引用。"
        ),
    ),
    Claim(
        id="c07_mcp_toolchain",
        no=7,
        resume_claim="MCP 工具链整合：把改写与检测能力封装为可调用工具",
        demo="B",
        reproducibility=REPRO_HIGH,
        status=STATUS_PENDING,
        evidence="工具链调用可视化（改写工具 / 检测工具的调用入参与返回）",
        metric_keys=[],
        gap_note="链路可完整演示；复用 Demo A 的 MCP Server 框架。",
    ),
    Claim(
        id="c08_manju_pipeline",
        no=8,
        resume_claim="漫剧内容批量生成与审核流程，内容处理效率提升 160%",
        demo="C",
        reproducibility=REPRO_MEDIUM,
        status=STATUS_PENDING,
        evidence="30–60s 样片 MP4 + 流水线各阶段耗时日志 + 审核命中率",
        metric_keys=["c_pipeline_throughput", "c_review_hit_rate"],
        gap_note=(
            "⚠️ **归属必须讲清**：执行服务 `E:\\aigc-comic` 是既有资产，"
            "其中「pipeline 状态机 + 断点续跑」为**既有能力**，不得声称本次自研。"
            "本次新增仅为：ComfyUI 出图适配器、审核层、指标层。"
            "「+160%」需可信人工基线才成立，否则只报绝对耗时、不引倍数。"
        ),
    ),
    Claim(
        id="c09_ecommerce",
        no=9,
        resume_claim="电商 AI 三场景：文案生成 / 主图生成 / 智能客服",
        demo="C",
        reproducibility=REPRO_MEDIUM,
        status=STATUS_PENDING,
        evidence="三个子功能页：文案（复用 Demo B 链路）、主图（复用 ComfyUI 适配器）、客服（复用 Demo A 的 RAG）",
        metric_keys=[],
        gap_note="简历未给出效果指标，本 Demo 只演示功能可达，不声称转化率类数字。",
    ),
    Claim(
        id="c10_miniprogram",
        no=10,
        resume_claim="小程序 AI 推荐，点击率提升 12%",
        demo="D",
        reproducibility=REPRO_DOWNGRADED,
        status=STATUS_PENDING,
        evidence="降级为网页版：离线推荐结果 + 推荐理由 + 口径页说明",
        metric_keys=[],
        gap_note=(
            "「点击率 +12%」需要真实流量才能测，**本仓库无法复现**。"
            "降级为「推荐结果可生成且理由可解释」，并在口径页明确标注该主张不可复现。"
        ),
    ),
    Claim(
        id="c11_production_facts",
        no=11,
        resume_claim="服务 3 家工业园区 / 2 家 MCN 采购 / 月度可用性 99.8% / 人工成本 −45% / 安保工作量 −40%",
        demo="D",
        reproducibility=REPRO_NOT,
        status=STATUS_NA,
        evidence="仅口径定义，标注「需原始材料佐证」",
        metric_keys=["x_production_facts"],
        gap_note=(
            "这类属于**运营与商业事实**，需由上线记录 / 合同 / 监控截图佐证。"
            "本仓库是技术方案的独立复现，无法证明此类事实。"
            "面试如被追问，应如实说明并出示原始材料，不得用 Demo 画面替代。"
        ),
    ),
]


# ============================================================ 导出


def by_id(cid: str) -> Claim:
    for c in CLAIMS:
        if c.id == cid:
            return c
    raise KeyError(f"unknown claim id: {cid}")


def coverage() -> dict:
    """覆盖率自检：确保 11 条主张无遗漏，且关联的指标 key 都真实存在。"""
    from common.metrics import registry  # 延迟导入，避免循环

    known = {m.key for m in registry.METRICS}
    missing_metrics = []
    for c in CLAIMS:
        for k in c.metric_keys:
            if k not in known:
                missing_metrics.append((c.id, k))

    nums = sorted(c.no for c in CLAIMS)
    expected = list(range(1, 12))
    return {
        "claim_count": len(CLAIMS),
        "missing_no": [n for n in expected if n not in nums],
        "duplicate_no": [n for n in nums if nums.count(n) > 1],
        "dangling_metric_keys": missing_metrics,
        "ok": not missing_metrics and nums == expected,
    }


def export_json(path: Optional[Path] = None) -> Path:
    target = path or (config.paths.root / "portal" / "claims.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    check = coverage()
    if not check["ok"]:
        raise ValueError(f"主张覆盖自检未通过，拒绝导出：{json.dumps(check, ensure_ascii=False)}")
    payload = {
        "generated_by": "common/metrics/claims.py",
        "note": "简历 11 条主张的逐条对照。coverage 自检保证无遗漏、无悬空指标引用。",
        "coverage": check,
        "reproducibility_labels": REPRO_LABEL,
        "claims": [c.as_dict() for c in CLAIMS],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
