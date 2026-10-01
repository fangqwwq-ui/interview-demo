"""质检层：对改写结果做三维校验，并给出可解释的放行判定。

为什么需要一个「闸门」而不是只报几个数字：
改写是**有损**操作。单纯报「分数降了 30 分」会让一个把语义改跑偏的结果看着很成功。
质检层的作用是：在报告任何效果数字之前，先判断这份结果**配不配被计入指标**。

三维校验：

| 维度 | 仪器 | 能回答的问题 |
|---|---|---|
| AI 痕迹 | 第三方内核检测器（0–100 分） | 改写后整体还像不像 AI |
| 语义保真 | 向量余弦（原文 vs 改写后） | 有没有改跑偏 / 丢掉信息 |
| 语法异常 | 本项目自研规则集 | 有没有被改坏（规则改写的典型症状） |

外加**自研 AI 味残留计数**（`tells.py`），它可以逐条指到具体短语，弥补总分不可追问的缺陷。

## 降级时的诚实处理

若无 torch 环境，向量化走 hashing-ngram 兜底，此时余弦相似度**不具语义意义**。
本模块不隐瞒也不假装：`Assessment.fidelity_reliable = False`，闸门跳过 G2，
并在报告里写明「该项未参与判定」。让指标缺失，好过让指标说谎。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from loguru import logger

from common.embed.base import Encoder
from common.llm.base import LLMError
from common.llm.router import LLMRouter
from common.metrics import text_metrics
from demo_b_copywriting import tells
from demo_b_copywriting.detector import KERNEL_COMMIT, detect

# ------------------------------------------------------------ 闸门阈值
# 所有阈值集中在这里，便于面试时逐条解释为什么是这个数。

GATE_LENGTH_MIN = 0.5       # 低于此值认为丢信息严重
GATE_LENGTH_MAX = 1.5       # 高于此值认为注水
GATE_FIDELITY_PASS = 0.75   # 余弦 ≥ 此值 → 通过
GATE_FIDELITY_FAIL = 0.60   # 余弦 < 此值 → 失败
GATE_TELL_RESIDUAL_RATIO = 0.5  # 残留超过原文的一半 → 改写无效

VERDICT_PASS = "pass"
VERDICT_REVIEW = "review"
VERDICT_FAIL = "fail"


@dataclass
class Assessment:
    original_chars: int
    rewritten_chars: int
    length_ratio: float
    semantic_fidelity: Optional[float]
    fidelity_reliable: bool
    detection_before: Dict[str, Any]
    detection_after: Dict[str, Any]
    score_drop: Optional[int]
    grammar_before: Dict[str, Any]
    grammar_after: Dict[str, Any]
    grammar_delta: int
    tells_before: Dict[str, Any]
    tells_after: Dict[str, Any]
    verdict: str
    reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    llm_review: Optional[Dict[str, Any]] = None
    errors: List[str] = field(default_factory=list)
    encoder: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "original_chars": self.original_chars,
            "rewritten_chars": self.rewritten_chars,
            "length_ratio": self.length_ratio,
            "semantic_fidelity": self.semantic_fidelity,
            "fidelity_reliable": self.fidelity_reliable,
            "detection_before": self.detection_before,
            "detection_after": self.detection_after,
            "score_drop": self.score_drop,
            "grammar_before": self.grammar_before,
            "grammar_after": self.grammar_after,
            "grammar_delta": self.grammar_delta,
            "tells_before": self.tells_before,
            "tells_after": self.tells_after,
            "verdict": self.verdict,
            "reasons": self.reasons,
            "warnings": self.warnings,
            "llm_review": self.llm_review,
            "errors": self.errors,
            "encoder": self.encoder,
            "gates": {
                "length": [GATE_LENGTH_MIN, GATE_LENGTH_MAX],
                "fidelity_pass": GATE_FIDELITY_PASS,
                "fidelity_fail": GATE_FIDELITY_FAIL,
                "tell_residual_ratio": GATE_TELL_RESIDUAL_RATIO,
            },
        }


def _detection_summary(res) -> Dict[str, Any]:
    return {
        "score": res.score,
        "level": res.level,
        "issue_count": res.issue_count,
        "categories": res.categories,
        "stats": res.stats,
    }


def _try_detect(text: str, errors: List[str], label: str) -> Optional[Dict[str, Any]]:
    try:
        return _detection_summary(detect(text))
    except Exception as exc:  # noqa: BLE001
        msg = f"检测器不可用（{label}）：{type(exc).__name__}: {exc}"
        logger.warning(msg)
        errors.append(msg)
        return None


# ------------------------------------------------------------ LLM 评审

_REVIEW_SYSTEM = (
    "你是一名严格的中文编辑。判断改写后的文本是否在「更自然」的同时保住了原意。\n"
    "只输出 JSON，不要任何解释或 Markdown 围栏，格式：\n"
    '{"naturalness": 1-10, "meaning_preserved": true/false, '
    '"added_facts": ["改写中新增而原文没有的事实"], "notes": "一句话说明"}'
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def llm_review(
    original: str, rewritten: str, router: Optional[LLMRouter] = None
) -> Dict[str, Any]:
    """LLM 评审。失败时返回 {'ok': False, 'error': ...}，不抛异常打断评测。"""
    router = router or LLMRouter()
    prompt = (
        f"【原文】\n{original}\n\n"
        f"【改写后】\n{rewritten}\n\n"
        "请评估改写后的文本：自然度、是否保住原意、有没有新增原文没有的事实。"
    )
    try:
        result = router.chat(
            [{"role": "system", "content": _REVIEW_SYSTEM},
             {"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=512,
        )
    except LLMError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    raw = result.text or ""
    match = _JSON_RE.search(raw)
    if not match:
        return {"ok": False, "error": "评审输出无法解析为 JSON", "raw": raw[:200],
                "provider": result.provider, "model": result.model}

    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return {"ok": False, "error": f"JSON 解析失败：{exc}", "raw": raw[:200],
                "provider": result.provider, "model": result.model}

    return {
        "ok": True,
        "naturalness": data.get("naturalness"),
        "meaning_preserved": data.get("meaning_preserved"),
        "added_facts": data.get("added_facts") or [],
        "notes": data.get("notes") or "",
        "provider": result.provider,
        "model": result.model,
        "elapsed_s": round(result.elapsed_s, 3),
    }


# ------------------------------------------------------------ 主流程


def assess(
    original: str,
    rewritten: str,
    encoder: Encoder,
    *,
    router: Optional[LLMRouter] = None,
    with_llm_review: bool = False,
    control: bool = False,
) -> Assessment:
    """对一条改写结果做三维校验并给出判定。

    `control=True` 表示这是对照组（改写文本就是原文本身）。
    此时**跳过全部效果闸门**，只做测量、不判定 —— 因为「和原文一样」不该被
    报成「改写效果不足」，那会让对照组在报告里显得比真实改写还差。

    ⚠️ 不能用 `original == rewritten` 自动推断对照组：内核规则改写有可能原样返回
    原文，那属于「改写无效」，必须照常判定。因此这里用显式参数，不猜。
    """
    errors: List[str] = []
    warnings: List[str] = []
    reasons: List[str] = []

    original = original or ""
    rewritten = rewritten or ""

    # ---- 维度 1：AI 痕迹（第三方内核）
    det_before = _try_detect(original, errors, "原文") or {}
    det_after = _try_detect(rewritten, errors, "改写后") if rewritten else {}
    score_drop: Optional[int] = None
    if det_before and det_after:
        score_drop = det_before["score"] - det_after["score"]

    # ---- 维度 2：语义保真（向量余弦）
    fidelity: Optional[float] = None
    reliable = not encoder.info.is_fallback
    if rewritten:
        try:
            fidelity = round(text_metrics.semantic_fidelity(original, rewritten, encoder), 4)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"语义保真度计算失败：{type(exc).__name__}: {exc}")
    if not reliable:
        warnings.append(
            f"向量化后端为 {encoder.info.backend}（兜底），"
            "语义保真度不具语义意义，已排除在判定之外"
        )

    # ---- 维度 3：语法异常（自研规则）
    g_before = text_metrics.scan_grammar_anomalies(original).as_dict()
    g_after = text_metrics.scan_grammar_anomalies(rewritten).as_dict() if rewritten else {"count": 0, "by_rule": {}, "samples": []}
    grammar_delta = int(g_after["count"]) - int(g_before["count"])

    # ---- 自研：AI 味残留
    t_before = tells.count(original).as_dict()
    t_after = tells.count(rewritten).as_dict() if rewritten else {"count": 0, "by_category": {}, "hits": []}

    ratio = text_metrics.length_ratio(original, rewritten) if rewritten else 0.0

    # ---- 对照组：只测量，不判定
    if control:
        return Assessment(
            original_chars=len(original),
            rewritten_chars=len(rewritten),
            length_ratio=ratio,
            semantic_fidelity=fidelity,
            fidelity_reliable=reliable,
            detection_before=det_before,
            detection_after=det_after,
            score_drop=score_drop,
            grammar_before=g_before,
            grammar_after=g_after,
            grammar_delta=grammar_delta,
            tells_before=t_before,
            tells_after=t_after,
            verdict=VERDICT_PASS,
            reasons=["对照组：文本未改写，不参与效果判定，仅提供同批次基准线"],
            warnings=warnings,
            errors=errors,
            encoder=encoder.info.as_dict(),
        )

    # ---- 闸门
    if not rewritten.strip():
        return Assessment(
            original_chars=len(original), rewritten_chars=0, length_ratio=0.0,
            semantic_fidelity=None, fidelity_reliable=reliable,
            detection_before=det_before, detection_after={}, score_drop=None,
            grammar_before=g_before, grammar_after=g_after, grammar_delta=grammar_delta,
            tells_before=t_before, tells_after=t_after,
            verdict=VERDICT_FAIL, reasons=["改写输出为空"], warnings=warnings,
            errors=errors, encoder=encoder.info.as_dict(),
            llm_review=llm_review(original, rewritten, router) if with_llm_review else None,
        )

    verdict = VERDICT_PASS
    if ratio < GATE_LENGTH_MIN:
        verdict = VERDICT_REVIEW
        reasons.append(f"G1 长度比 {ratio} < {GATE_LENGTH_MIN}，疑似丢信息")
    elif ratio > GATE_LENGTH_MAX:
        verdict = VERDICT_REVIEW
        reasons.append(f"G1 长度比 {ratio} > {GATE_LENGTH_MAX}，疑似注水")

    if reliable and fidelity is not None:
        if fidelity < GATE_FIDELITY_FAIL:
            verdict = VERDICT_FAIL
            reasons.append(f"G2 语义保真度 {fidelity} < {GATE_FIDELITY_FAIL}，已改跑偏")
        elif fidelity < GATE_FIDELITY_PASS:
            if verdict != VERDICT_FAIL:
                verdict = VERDICT_REVIEW
            reasons.append(f"G2 语义保真度 {fidelity} < {GATE_FIDELITY_PASS}，需人工确认")
    else:
        reasons.append("G2 跳过（编码器不可靠）")

    if grammar_delta > 0:
        if verdict != VERDICT_FAIL:
            verdict = VERDICT_REVIEW
        reasons.append(
            f"G3 改写引入了 {grammar_delta} 处新的语法异常"
            f"（{g_before['count']} → {g_after['count']}）"
        )

    before_n = int(t_before["count"])
    after_n = int(t_after["count"])
    if before_n and after_n > before_n * GATE_TELL_RESIDUAL_RATIO:
        if verdict != VERDICT_FAIL:
            verdict = VERDICT_REVIEW
        reasons.append(
            f"G4 AI 味残留 {after_n} 处，超过原文 {before_n} 处的 "
            f"{int(GATE_TELL_RESIDUAL_RATIO * 100)}%，改写效果不足"
        )

    if not reasons:
        reasons.append("全部闸门通过")

    review = llm_review(original, rewritten, router) if with_llm_review else None
    if review and review.get("ok"):
        if review.get("added_facts"):
            if verdict != VERDICT_FAIL:
                verdict = VERDICT_REVIEW
            reasons.append(f"G5 LLM 评审指出新增事实：{review['added_facts']}")
        if review.get("meaning_preserved") is False:
            verdict = VERDICT_FAIL
            reasons.append("G5 LLM 评审认为原意未保住")
    elif review and not review.get("ok"):
        warnings.append(f"LLM 评审不可用：{review.get('error')}")

    return Assessment(
        original_chars=len(original),
        rewritten_chars=len(rewritten),
        length_ratio=ratio,
        semantic_fidelity=fidelity,
        fidelity_reliable=reliable,
        detection_before=det_before,
        detection_after=det_after,
        score_drop=score_drop,
        grammar_before=g_before,
        grammar_after=g_after,
        grammar_delta=grammar_delta,
        tells_before=t_before,
        tells_after=t_after,
        verdict=verdict,
        reasons=reasons,
        warnings=warnings,
        llm_review=review,
        errors=errors,
        encoder=encoder.info.as_dict(),
    )


def instrument_provenance() -> Dict[str, Any]:
    """质检所用仪器的来源声明，供口径页引用。"""
    return {
        "detector": {
            "self_built": False,
            "component": "humanize-chinese",
            "commit": KERNEL_COMMIT,
            "used_for": "detection-only",
        },
        "fidelity": {
            "self_built": True,
            "method": "cosine similarity of L2-normalized embeddings",
        },
        "grammar": {
            "self_built": True,
            "rules": [r["id"] for r in text_metrics._RULES],
        },
        "tells": {"self_built": True, **tells.stats()},
    }
