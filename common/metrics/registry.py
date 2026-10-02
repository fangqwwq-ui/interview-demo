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
        instrument="sentence-transformers + BAAI/bge-small-zh-v1.5（本地目录加载，dim=512）",
        provenance="self-built",
        gap_note=(
            "本值只在 build_info.json 记录的编码器下成立。**换编码器必须重建索引并重跑评测**，"
            "否则新旧向量混用会让检索结果静默失真。"
            "无 torch 环境时 sentence-transformers 不可用，编码器会降级为 hashing-ngram 兜底 —— "
            "该后端**不具语义能力**，此时本指标无意义，口径页必须据 build_info 标出真正生效的后端。"
        ),
        tags=["demo-b", "quality"],
    ),
    MetricEntry(
        key="b_grammar_anomalies",
        name="语法异常检出数",
        resume_claim="（支撑「内容处理效率与质量」）",
        definition="改写结果中命中的中文语法/搭配异常模式数量",
        formula="Σ 命中的规则条数（规则集 R1/R2/R4，见脚本内 _RULES）",
        dataset="同上评测集",
        script="common/metrics/text_metrics.py::scan_grammar_anomalies",
        instrument="自研规则集（含 10 条合法中文 + 3 条损伤样本的精度自检）",
        provenance="self-built",
        gap_note=(
            "规则集只覆盖可机械判定的异常，不等于完整语法检查。"
            "用途是暴露「规则改写会破坏语法」这一实测事实。"
            "⚠️ 原规则集含 R3「语气词 + 标点」，实测在正常中文上误报"
            "（「我们走吧。」「太可了！」被判异常），已整条删除。"
            "规则集现为 R1/R2/R4，历史口径已按此重算。"
            "自检命令：python -m common.metrics.text_metrics"
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
        name="RAG 检索命中率（top-1 精确命中）",
        resume_claim="智能应答准确率 72% → 94%",
        definition=(
            "评测集上 top-1 检索结果落在标注答案集合内且不在 forbidden 集合内的比例；"
            "本Demo 不含 LLM 生成环节，衡量的是「检索是否取到正确工单」，"
            "不等同于端到端生成准确率"
        ),
        formula="correct / n（n=102；无标注答案的条目计入分母，考「该说没有时不说有」）",
        dataset="demo_a_security/eval_set.jsonl（102 条自建，人工撰写，未爬取）",
        script="demo_a_security/evaluate.py",
        instrument="bge-small-zh-v1.5(dim512) + FAISS IndexFlatIP + 分层结构化过滤",
        provenance="self-built",
        status="pending",
        gap_note=(
            "占位 —— 真实说明由 demo_a_security/evaluate.py 回填时按实测数据生成"
            "（含基线对比、未达简历值的声明、剩余失败样本的性质），此处不预写数字。"
        ),
        tags=["demo-a", "rag"],
    ),
    MetricEntry(
        key="a_api_latency_gain",
        name="接口吞吐提升幅度（v1 同步 vs v2 异步并发）",
        resume_claim="接口响应速度提升 40%",
        definition=(
            "同机、同库、同流量配比下，v2（异步并发+线程池每线程独立连接）"
            "相对 v1（同步阻塞+每请求新建连接）的 RPS 提升比例；"
            "分并发档位单独报告，不做跨档平均"
        ),
        formula="(RPS_v2 - RPS_v1) / RPS_v1，按 concurrency ∈ {1,4,8,16} 分档",
        dataset="outputs/demo_a/bench/bench_raw.json（httpx+asyncio 压测，非 locust）",
        script="scripts/bench.py",
        instrument=(
            "httpx 0.28.1 + asyncio（零新增依赖；locust 含 gevent，"
            "本机带宽约 340 KB/s 不值得为单个数字引入）"
        ),
        provenance="self-built",
        status="pending",
        gap_note=(
            "占位 —— 真实说明由 scripts/bench.py 回填时按实测数据生成"
            "（含各档对应关系与测量可靠性说明），此处不预写数字。"
        ),
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
        definition="审核模块判定为需人工复核的内容占比，**按样本分层上报，不混合**",
        formula="命中条数 / 该层可评估条数；分层 = real（真实产出）/ injected（人工构造缺陷）",
        dataset="demo_c_manju/data/review_batch.jsonl（12 条：3 真实 + 9 注入）",
        script="demo_c_manju/review/__main__.py",
        instrument="自研（规则通道 6 条规则；LLM 通道当前未接通）",
        provenance="self-built",
        status="pending",
        gap_note=(
            "本次新增能力；既有 aigc-comic 无审核模块。"
            "注入层的高命中率只证明「规则抓到了自己设计的缺陷」，属回归指标；"
            "真实层的命中率才有场景意义。两层的数字必须分开看，不给合并总数。"
        ),
        tags=["demo-c", "quality"],
    ),
    MetricEntry(
        key="c_review_channel_complement",
        name="审核双通道互补量",
        resume_claim="（支撑「审核流程」的可解释性）",
        definition="规则通道与 LLM 通道命中集合的差集规模，用来说明单通道会漏掉多少",
        formula="unique_to_rule（仅规则命中）+ unique_to_llm（仅 LLM 命中）",
        dataset="同上审核批次",
        script="demo_c_manju/review/reviewer.py::_stats",
        instrument="自研（双通道对账）",
        provenance="self-built",
        status="pending",
        gap_note=(
            "本指标存在的理由是：两通道量纲不同（规则是布尔命中、LLM 是概率判断），"
            "合并成一个「综合合规分」没有物理意义，且无法回归。"
            "另注：LLM 通道为**纯文本**判读，图像像素不送模型，"
            "因此它审的是「脚本与提示词是否自洽」，不是「图里的内容是否合规」。"
            "未接通时该指标只报规则通道值，不给 LLM 侧估算。"
        ),
        tags=["demo-c", "quality", "instrument"],
    ),
    MetricEntry(
        key="c_prompt_adaptation_coverage",
        name="中文提示词适配覆盖率",
        resume_claim="（支撑「漫剧流水线可用」的前置条件）",
        definition="中文提示词中**被成功转为英文标签的中文字符**占比（按字符数，不按词条数）",
        formula="1 − 残留中文字符数 / 原文中文字符总数",
        dataset="既有服务 aigc-comic 的真实分镜提示词（由其 lib/image.py::build_prompt 生成）",
        script="demo_c_manju/a1111_shim.py::adapt_prompt",
        instrument="自研词表（99 条）+ 数值年龄正则 + 残留中文剥离与计量",
        provenance="self-built",
        status="pending",
        gap_note=(
            "为什么按**字符数**而不按词条数：一个未覆盖的短词和一句未覆盖的长句权重不同，"
            "按词条计数会把长句的低覆盖掩盖掉。"
            "⚠️ 本指标的边界：它只度量「中文是否被转走」，**不度量转得对不对**；"
            "它也不度量出图质量 —— 本机无图文对齐模型，不构造语义评分。"
            "另注：残留中文默认被剥离（dropped_terms 逐条可查）。"
            "**剥离的收益未经证实**：scripts/ab_prompt_adaptation.py 的单变量对照（n=1，同 seed）显示，"
            "扩词表使出图从「偏离分镜」变为「贴合分镜」（差异显著），"
            "而在此基础上再剥离残留，肉眼**无可辨识差异**。"
            "即真正起作用的是词表覆盖；剥离只为覆盖率可量化、少送噪声，不声称提升画质。"
        ),
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
