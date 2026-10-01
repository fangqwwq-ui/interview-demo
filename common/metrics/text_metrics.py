"""可复用的文本质量度量。

两个指标：
  1. `semantic_fidelity` —— 语义保真度（衡量改写有没有改跑偏）
  2. `scan_grammar_anomalies` —— 语法/搭配异常扫描

关于规则集的诚实边界（面试会被问）：
`scan_grammar_anomalies` 只覆盖**可机械判定**的异常模式，不是完整的中文语法检查器。
它存在的目的是量化一个实测事实：纯规则改写会为了降低 AI 痕迹而破坏中文通顺度。
所以它测的不是「文本好不好」，而是「有没有被改坏」。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

import numpy as np

# 永远不会合法叠用的功能词
_FUNCTION_WORDS = "的的了了吗吗呢呢也也于于和和与与就就都都地地得得着着过过"

# 助词/语气词，后面正常不应直接跟句读
_PARTICLES = "的了着过吗呢吧啊呀哦嘛"

_PUNCT_CN = "，。！？；、："
_PUNCT_EN = ",.!?;:"

_RULES: List[Dict[str, str]] = [
    {
        "id": "R1_function_word_doubling",
        "reason": "功能词叠用，中文中不存在这种搭配（典型规则改写损伤）",
    },
    {
        "id": "R2_char_triple_repeat",
        "reason": "同一字连续出现 3 次以上，几乎必为改写或拼接错误",
    },
    {
        "id": "R3_dangling_particle",
        "reason": "助词/语气词后直接跟句读，句子成分残缺",
    },
    {
        "id": "R4_punct_anomaly",
        "reason": "连续标点或中英标点混用",
    },
]


@dataclass
class GrammarFindings:
    count: int
    by_rule: Dict[str, int] = field(default_factory=dict)
    samples: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {"count": self.count, "by_rule": self.by_rule, "samples": self.samples}


def scan_grammar_anomalies(text: str, max_samples: int = 8) -> GrammarFindings:
    """扫描语法/搭配异常。返回命中数与可展示的样本。"""
    findings: List[Dict[str, Any]] = []
    src = text or ""

    # R1 功能词叠用
    for m in re.finditer(f"[{_FUNCTION_WORDS}]", src):
        i = m.start()
        if i + 1 < len(src) and src[i] == src[i + 1]:
            start, end = max(0, i - 6), min(len(src), i + 8)
            findings.append(
                {
                    "rule": "R1_function_word_doubling",
                    "matched": src[i : i + 2],
                    "context": src[start:end],
                }
            )

    # R2 单字连续 3 次以上
    for m in re.finditer(r"(.)\1{2,}", src):
        ch = m.group(1)
        if ch.isspace():
            continue
        start, end = max(0, m.start() - 6), min(len(src), m.end() + 8)
        findings.append(
            {
                "rule": "R2_char_triple_repeat",
                "matched": m.group(0),
                "context": src[start:end],
            }
        )

    # R3 助词后紧跟句读
    for m in re.finditer(f"[{_PARTICLES}](?=[{_PUNCT_CN}{_PUNCT_EN}])", src):
        i = m.start()
        start, end = max(0, i - 8), min(len(src), i + 6)
        findings.append(
            {
                "rule": "R3_dangling_particle",
                "matched": src[i : i + 2],
                "context": src[start:end],
            }
        )

    # R4 连续标点 / 中英混用
    for m in re.finditer(f"[{_PUNCT_CN}{_PUNCT_EN}]{{2,}}", src):
        if m.group(0) in {"……", "——", "」", "）"}:
            continue
        start, end = max(0, m.start() - 6), min(len(src), m.end() + 6)
        findings.append(
            {
                "rule": "R4_punct_anomaly",
                "matched": m.group(0),
                "context": src[start:end],
            }
        )
    for m in re.finditer(f"[{_PUNCT_EN}]\\s*[\\u4e00-\\u9fff]", src):
        findings.append(
            {
                "rule": "R4_punct_anomaly",
                "matched": m.group(0),
                "context": src[max(0, m.start() - 6) : m.end() + 6],
            }
        )

    by_rule: Dict[str, int] = {}
    for f in findings:
        by_rule[f["rule"]] = by_rule.get(f["rule"], 0) + 1

    # 样本按规则均衡取，避免 R4 淹没展示
    samples: List[Dict[str, Any]] = []
    seen: Dict[str, int] = {}
    for f in findings:
        if seen.get(f["rule"], 0) >= 2:
            continue
        seen[f["rule"]] = seen.get(f["rule"], 0) + 1
        samples.append(f)
        if len(samples) >= max_samples:
            break

    return GrammarFindings(count=len(findings), by_rule=by_rule, samples=samples)


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """余弦相似度。向量若已归一化，等价于内积。"""
    a = np.asarray(a, dtype=np.float32).ravel()
    b = np.asarray(b, dtype=np.float32).ravel()
    na, nb = np.linalg.norm(a), np.linalg.norm(b)
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def semantic_fidelity(original: str, rewritten: str, encoder) -> float:
    """语义保真度 = 原文与改写文的向量余弦相似度。

    encoder 需实现 `encode(List[str]) -> np.ndarray`。
    ⚠️ 若 encoder 是 hashing-ngram 兜底后端，本指标**不具语义意义**，
    仅反映字面重合度。调用方必须把 encoder.info 一并记入结果。
    """
    vecs = encoder.encode([original, rewritten])
    return cosine_similarity(vecs[0], vecs[1])


def length_ratio(original: str, rewritten: str) -> float:
    """改写后 / 原文长度比。用于发现「改写等于重写」或「改写过短丢信息」。"""
    lo = len(original.strip())
    return round(len(rewritten.strip()) / lo, 4) if lo else 0.0


def summarize(lengths: Sequence[float]) -> Dict[str, float]:
    """纯 Python 的均值/中位数/极值，避免为几个数字引入 numpy 之外的重依赖。"""
    xs = [float(x) for x in lengths if x is not None]
    if not xs:
        return {}
    xs_sorted = sorted(xs)
    n = len(xs_sorted)
    mid = n // 2
    median = xs_sorted[mid] if n % 2 else (xs_sorted[mid - 1] + xs_sorted[mid]) / 2
    return {
        "count": n,
        "mean": round(sum(xs) / n, 4),
        "median": round(median, 4),
        "min": round(xs_sorted[0], 4),
        "max": round(xs_sorted[-1], 4),
    }
