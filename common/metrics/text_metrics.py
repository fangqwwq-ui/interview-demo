"""可复用的文本质量度量。

两个指标：
  1. `semantic_fidelity` —— 语义保真度（衡量改写有没有改跑偏）
  2. `scan_grammar_anomalies` —— 语法/搭配异常扫描

关于规则集的诚实边界（面试会被问）：
`scan_grammar_anomalies` 只覆盖**可机械判定**的异常模式，不是完整的中文语法检查器。
它存在的目的是量化一个实测事实：纯规则改写会为了降低 AI 痕迹而破坏中文通顺度。
所以它测的不是「文本好不好」，而是「有没有被改坏」。

## 规则的准入标准（血泪教训，见下）

**一条规则在正常中文语料上必须零误报，否则不许进规则集。**
宁可规则少而准，也不要为了凑数量保留一条会在正常中文上乱报的规则 ——
指标里的噪声比指标缺失更危险。

为此本模块内置两组回归对照（`_GRAMMATICAL_CONTROLS` / `_DAMAGED_CONTROLS`），
可用 `self_test()` 或 `python -m common.metrics.text_metrics` 跑。

### 已删除规则记录：R3_dangling_particle

早期版本有一条 R3，定义为「助词/语气词后直接跟句读」，参与字符集是
`的了着过吗呢吧啊呀哦嘛`。

**实测证明这条规则的定义是错的**：这些词在中文里本就大量位于句末，
后跟标点完全合法。它在 Demo C 的 12 条审核样本上误报 10 次，
在 Demo B 的 20 条评测集上误报 4 次，命中的全部是正常句子：

| 被误报的文本 | 实际是否合法 |
|---|---|
| 我们走吧。 | 合法 |
| 我放你桌上了，别忘了吃。 | 合法 |
| 这款单品真的太可了！ | 合法 |
| 用过一次就再也回不去了，姐妹们千万不要错过。 | 合法 |

一句「太可了！」被判为语法异常，说明规则本身没有可辩护的定义，而不是阈值没调好。
因此**整条删除**，不做收窄 —— 收窄后的版本（只查「的地得」+标点）依然会在
「这是我昨天买的，你看看吧。」这类合法句子上误报。

删除后规则集为 R1/R2/R4。日志与历史评测口径需按 R1/R2/R4 重新表述。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

import numpy as np

# 永远不会合法叠用的功能词
_FUNCTION_WORDS = "的的了了吗吗呢呢也也于于和和与与就就都都地地得得着着过过"

# ⚠️ 这里曾有一个 `_PARTICLES` 常量，用于已删除的 R3。
#    删除原因见模块文档「已删除规则记录：R3_dangling_particle」。
#    不要重新引入基于「语气词 + 标点」的规则。

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
        "id": "R4_punct_anomaly",
        "reason": "连续标点或中英标点混用",
    },
]

# ------------------------------------------------------------ 精度回归对照
# 准入标准：合法组必须 0 命中，损伤组必须 ≥1 命中。
# 这两组是「防止同类错误再次引入」的护栏 —— R3 事件就是这么发现的。

# 正常中文：任何一条被命中都说明规则集在误报
_GRAMMATICAL_CONTROLS: List[str] = [
    "我们走吧。",
    "我放你桌上了，别忘了吃。",
    "你先把资料发我，我明天再看看。",
    "这款产品真的好用，性价比很高。",
    "他昨天来过，说改天还要再来一次。",
    "风停了，整座城市安静下来。",
    "这是我昨天买的，你看看吧。",
    "说出口就回不去了。",
    "是啊，谁都想少花点钱。",
    "你先睡吧，我把这点活干完。",
]

# 被规则改写改坏的中文：应当至少被一条规则命中
_DAMAGED_CONTROLS: List[str] = [
    "我想想想了很久也没想通。",          # R2 同字三连
    "这个方案也也还需要再讨论一下。",      # R1 功能词叠用
    "他居然说：：这件事不用再提了。",      # R4 连续标点
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

    # R3 已删除。原实现为「[语气词](?=[标点])」，经实测在正常中文上大量误报
    # （「我们走吧。」「太可了！」均被命中）。详见模块文档的删除记录。

    # R4 连续标点 / 中英标点混用
    # 合法叠用白名单：中文里「？！/！？」是可接受的语气叠用。
    # （旧版本这里写的是 {"……","——","」","）"}，但那些字符根本不在 _PUNCT 字符集里，
    #   所以那是一段永不触发的死代码 —— 一并清理，避免读代码的人被误导。）
    _LEGIT_DOUBLE = {"？！", "！？"}
    for m in re.finditer(f"[{_PUNCT_CN}{_PUNCT_EN}]{{2,}}", src):
        if m.group(0) in _LEGIT_DOUBLE:
            continue
        start, end = max(0, m.start() - 6), min(len(src), m.end() + 6)
        findings.append(
            {
                "rule": "R4_punct_anomaly",
                "matched": m.group(0),
                "context": src[start:end],
            }
        )

    # 中英标点混用：只在「中文字符 + ASCII 标点」时才判，精度优先。
    # 反例（不应命中）："1girl, school uniform" —— 标点前是 ASCII 字符，属正常英文标签。
    for m in re.finditer(r"[\u4e00-\u9fff]([,.;:!?])", src):
        start, end = max(0, m.start() - 6), min(len(src), m.end() + 6)
        findings.append(
            {
                "rule": "R4_punct_anomaly",
                "matched": m.group(0),
                "context": src[start:end],
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


# ------------------------------------------------------------ 仪器自检


def self_test() -> Dict[str, Any]:
    """规则集精度自检。

    为什么要做成函数而不是写成测试注释：
    这个规则集会被 Demo B（改写质检）和 Demo C（内容审核）共用。它一旦误报，
    两个 Demo 的结论同时被污染（R3 事件就是这样一次污染两个 Demo）。
    把护栏放进代码里，任何改动后都能一行命令验证。

    返回 `ok=False` 表示规则集在合法中文上误报，或在损伤样本上漏报。
    """
    false_positives: List[Dict[str, str]] = []
    for text in _GRAMMATICAL_CONTROLS:
        res = scan_grammar_anomalies(text)
        if res.count:
            false_positives.append(
                {
                    "text": text,
                    "hits": f"{res.count}",
                    "rules": ",".join(sorted(res.by_rule)),
                }
            )

    missed: List[Dict[str, str]] = []
    for text in _DAMAGED_CONTROLS:
        res = scan_grammar_anomalies(text)
        if not res.count:
            missed.append({"text": text})

    return {
        "rule_set": [r["id"] for r in _RULES],
        "grammatical_controls": len(_GRAMMATICAL_CONTROLS),
        "damaged_controls": len(_DAMAGED_CONTROLS),
        "false_positives": false_positives,
        "false_positive_rate": round(len(false_positives) / len(_GRAMMATICAL_CONTROLS), 4),
        "missed": missed,
        "recall_on_controls": round(
            (len(_DAMAGED_CONTROLS) - len(missed)) / len(_DAMAGED_CONTROLS), 4
        ),
        "ok": not false_positives and not missed,
    }


def _main() -> int:
    import json
    import sys

    result = self_test()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["ok"]:
        print("规则集自检未通过：合法中文出现误报，或损伤样本漏报。", file=sys.stderr)
        return 1
    print("规则集自检通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
