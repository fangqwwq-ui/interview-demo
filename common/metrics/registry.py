"""指标口径注册表。

这个文件解决的是面试里最容易被问穿的问题：「你这个数字怎么算的？」

每一项都必须能回答五件事：
  1. 指标的中文名与口径定义
  2. 计算公式
  3. 数据来源（用哪个数据集、什么规模）
  4. 计算脚本路径（可现场打开）
  5. 与简历数值的差异及原因

`export_json()` 的输出直接喂给门户的指标口径页（Demo D）。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from common import config


@dataclass
class MetricEntry:
    key: str
    name: str
    resume_claim: str
    definition: str
    formula: str
    dataset: str
    script: str
    instrument: str
    status: str = "pending"  # pending | measured | not_reproducible
    measured: Optional[str] = None
    gap_note: str = ""
    provenance: str = ""  # self-built | third-party | existing
    tags: List[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# ============================================================ 注册表

METRICS: List[MetricEntry] = [
    # ---------------- Demo B ----------------
    MetricEntry(
        key="b_detection_score_drop",
        name="AI 痕迹检测分下降幅度",
        resume_claim="AIGC 检测通过率 28% → 89%",
        definition="同一文本改写前后，用同一检测器打分的分数差",
        formula="score_before - score_after（分数越低越像人类）",
        dataset="demo_b_copywriting/data/clean 下的评测集",
        script="demo_b_copywriting/evaluate.py",
        instrument="humanize-chinese 检测器（第三方开源，commit efdefaa）",
        provenance="third-party",
        gap_note=(
            "简历值是「平台检测通过率」，本 Demo 用开源检测器的分数代理。"
            "两者不是同一把尺子，数值不可直接对比 —— 只在同一次评测内做前后对照。"
        ),
        tags=["demo-b", "detection"],
    ),
    MetricEntry(
        key="b_ai_tell_residual",
        name="AI 味表达残留数",
        resume_claim="（支撑「语义保留改写」的可解释度量）",
        definition="改写后文本中命中的 AI 高频表达处数（含短语与模板句式两类）",
        formula="Σ 命中处数；词典见 demo_b_copywriting/tells.py（硬禁用 41 条 + 模板 10 条）",
        dataset="demo_b_copywriting/data/clean/ai_style_eval.jsonl",
        script="demo_b_copywriting/tells.py::count",
        instrument="自研 AI 味词典（本项目自建，非第三方）",
        provenance="self-built",
        gap_note=(
            "本指标存在的理由是：第三方内核检测器在「评测腔 / 种草腔」上灵敏度不足"
            "（20 条评测集有 14 条得 0 分），单一仪器会漏测。"
            "词典只覆盖可逐条指认的表达，不等于完整的「AI 味」定义。"
        ),
        tags=["demo-b", "quality", "instrument"],
    ),
    MetricEntry(
        key="b_semantic_fidelity",
        name="语义保真度",
        resume_claim="语义保留改写",
        definition="改写后文本与原文的语义相似度，衡量「改得像人但没改跑偏」",
        formula="cos(encode(原文), encode(改写后))，向量 L2 归一化后取内积",
        dataset="同上评测集",
        script="common/metrics/text_metrics.py::semantic_fidelity",
        instrument="bge-small-zh-v1.5（无 torch 环境下降级为 hashing-ngram 兜底）",
        provenance="self-built",
        gap_note="兜底向量化后端不具语义能力，指标会失真 —— 口径页必须标出实际使用的后端。",
        tags=["demo-b", "quality"],
    ),
    MetricEntry(
        key="b_grammar_anomalies",
        name="语法异常检出数",
        resume_claim="（支撑「内容处理效率与质量」）",
        definition="改写结果中命中的中文语法/搭配异常模式数量",
        formula="Σ 命中的规则条数（规则见脚本内 RULES）",
        dataset="同上评测集",
        script="common/metrics/text_metrics.py::scan_grammar_anomalies",
        instrument="自研规则集",
        provenance="self-built",
        gap_note=(
            "规则集只覆盖可机械判定的异常（叠字、悬空助词、标点异常等），"
            "不等于完整语法检查。用途是暴露「规则改写会破坏语法」这一实测事实。"
        ),
        tags=["demo-b", "quality"],
    ),
    MetricEntry(
        key="b_retrieval_latency",
        name="检索耗时",
        resume_claim="—",
        definition="单次 top-k 向量检索的墙钟耗时",
        formula="perf_counter 差值，取多次中位数",
        dataset="构建好的语料索引",
        script="demo_b_copywriting/evaluate.py",
        instrument="FAISS IndexFlatIP（不可用时降级 numpy 暴力检索）",
        provenance="self-built",
        gap_note="仅在 Demo 规模（数千条）下测量；十万级以上需换 IVF/HNSW 并重新标定召回。",
        tags=["demo-b", "performance"],
    ),
    # ---------------- Demo A ----------------
    MetricEntry(
        key="a_rag_accuracy",
        name="RAG 问答准确率",
        resume_claim="智能应答准确率 72% → 94%",
        definition="评测集上答案与标准答案一致的条目占比",
        formula="正确条数 / 评测集总条数",
        dataset="≥100 条自建问答评测集（demo_a_security/rag/eval_set.jsonl）",
        script="demo_a_security/rag/evaluate.py",
        instrument="自研评测脚本",
        provenance="self-built",
        status="pending",
        gap_note="以实测值为准；若达不到 94%，按实测值写并在本页说明差异原因。",
        tags=["demo-a", "rag"],
    ),
    MetricEntry(
        key="a_api_latency_gain",
        name="接口响应提升幅度",
        resume_claim="接口响应速度提升 40%",
        definition="同一接口优化前后在相同压测条件下的 P95 延迟改善比例",
        formula="(P95_before - P95_after) / P95_before",
        dataset="locust 压测结果（demo_a_security/bench/report.md）",
        script="demo_a_security/bench/locustfile.py",
        instrument="locust",
        provenance="self-built",
        status="pending",
        gap_note="优化前版本为「同步串行调用」，优化后为「异步并发 + 连接池」，同机压测。",
        tags=["demo-a", "performance"],
    ),
    # ---------------- Demo C ----------------
    MetricEntry(
        key="c_pipeline_throughput",
        name="流水线单条样片耗时",
        resume_claim="内容处理效率提升 160%",
        definition="从输入剧本到产出成片的总墙钟耗时（含出图、配音、合成）",
        formula="Σ 各阶段 elapsed_s（由 common/logging.py::stage 埋点汇总）",
        dataset="demo_c_manju 运行日志",
        script="demo_c_manju/orchestrator.py",
        instrument="自研计时埋点",
        provenance="self-built",
        status="pending",
        gap_note=(
            "简历的 +160% 需要「人工基线」才成立。本 Demo 只报绝对耗时；"
            "若无法取得可信人工基线，不引用倍数。"
        ),
        tags=["demo-c", "performance"],
    ),
    MetricEntry(
        key="c_review_hit_rate",
        name="审核命中率",
        resume_claim="漫剧内容批量生成与审核流程",
        definition="审核模块判定为需人工复核的内容占比",
        formula="命中条数 / 送审条数",
        dataset="流水线产出的分镜与文案",
        script="demo_c_manju/review/",
        instrument="自研（规则 + LLM 双通道）",
        provenance="self-built",
        status="pending",
        gap_note="本次新增能力；既有 aigc-comic 无审核模块。",
        tags=["demo-c", "quality"],
    ),
    # ---------------- 不可复现项 ----------------
    MetricEntry(
        key="x_production_facts",
        name="生产运营事实（不可演示）",
        resume_claim="服务 3 家工业园区 / 2 家 MCN 采购 / 月度可用性 99.8% / 人工成本 -45% / 安保工作量 -40%",
        definition="属于运营与商业事实，需由原始业务材料佐证",
        formula="—",
        dataset="不适用",
        script="—",
        instrument="—",
        provenance="not-reproducible",
        status="not_reproducible",
        gap_note=(
            "本仓库是技术方案的独立复现，无法证明此类生产事实。"
            "面试如被追问，应如实说明并出示原始材料（上线记录 / 合同 / 监控截图）。"
        ),
        tags=["global", "口径红线"],
    ),
]


# ============================================================ 导出

# ⚠️ 实测值必须落在**独立文件**里，不能只存在 METRICS 的内存对象中。
# 原因：metrics.json 会被多个进程导出（evaluate.py 写入实测值，selfcheck.py 也会重新导出）。
# 若实测值只存在于内存，后一个进程导出时会把 status 重新写成 pending，
# 把刚测出来的数字**静默覆盖掉**。这在演示现场就是「数字刚才还在，一刷新没了」。
# 因此：测量结果持久化到 measurements.json，export_json() 每次都从它合并回来。
MEASUREMENTS_FILE = config.paths.root / "portal" / "measurements.json"


def load_measurements(path: Optional[Path] = None) -> dict:
    target = path or MEASUREMENTS_FILE
    if not target.exists():
        return {}
    try:
        return json.loads(target.read_text(encoding="utf-8")).get("measurements", {})
    except json.JSONDecodeError:
        return {}


def save_measurements(payload: dict, path: Optional[Path] = None) -> Path:
    target = path or MEASUREMENTS_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {
                "generated_by": "common/metrics/registry.py::set_measured",
                "note": "实测值的唯一持久化位置。export_json() 会把它合并进 metrics.json。",
                "measurements": payload,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return target


def export_json(path: Optional[Path] = None) -> Path:
    target = path or (config.paths.root / "portal" / "metrics.json")
    target.parent.mkdir(parents=True, exist_ok=True)

    # 合并已持久化的实测值，避免被重新导出冲掉
    for key, rec in load_measurements().items():
        try:
            entry = by_key(key)
        except KeyError:
            continue
        if rec.get("measured"):
            entry.status = "measured"
            entry.measured = rec["measured"]
        if rec.get("gap_note"):
            entry.gap_note = rec["gap_note"]

    payload = {
        "generated_by": "common/metrics/registry.py",
        "note": (
            "本文件由代码生成，是门户指标口径页的数据源。"
            "任何口径调整必须同时更新 PLAN.md 第 4 节与第 12 节。"
            "实测值持久化在 portal/measurements.json。"
        ),
        "metrics": [m.as_dict() for m in METRICS],
    }
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def by_key(key: str) -> MetricEntry:
    for m in METRICS:
        if m.key == key:
            return m
    raise KeyError(f"unknown metric key: {key}")


def set_measured(key: str, value: str, *, gap_note: str | None = None) -> MetricEntry:
    """回填实测值，并**持久化**到 portal/measurements.json。

    设计意图：禁止在文档里手写数字，只允许由评测脚本调用本函数回填。
    这样「文档里的数字」永远能追溯到「哪次评测写进去的」，且不会被
    其他进程的重新导出覆盖。
    """
    entry = by_key(key)
    entry.status = "measured"
    entry.measured = value
    if gap_note is not None:
        entry.gap_note = gap_note

    from datetime import datetime, timezone  # noqa: PLC0415

    payload = load_measurements()
    payload[key] = {
        "measured": value,
        "gap_note": entry.gap_note,
        "measured_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }
    save_measurements(payload)
    return entry


def gaps() -> List[MetricEntry]:
    """尚未实测的指标 —— 用于防止「数字被悄悄写进文档」。"""
    return [m for m in METRICS if m.status == "pending"]
