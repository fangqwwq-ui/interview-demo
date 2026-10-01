"""审核层编排：把规则通道与 LLM 通道的结果汇总成可交付的报告。

## 核心设计：不合并成「一个总分」

本模块刻意**不**输出类似「综合合规分 87」这样的数字。原因：

- 两个通道量纲不同（规则是布尔命中，LLM 是概率性判断），加权求和没有物理意义；
- 合并后无法回答「这个结论是规则给的还是模型给的」，也无法做回归；
- 合并会掩盖真正有价值的信号 —— **两通道命中集合不重合的部分**。

因此报告输出的是：

| 指标 | 回答的问题 |
|---|---|
| `rule_flag_rate` | 规则通道认为多少内容需要处理 |
| `llm_flag_rate` | LLM 通道认为多少内容需要处理（不可用时为 null） |
| `channel_agreement_rate` | 两通道一致的比例 |
| `unique_to_rule` | 只有规则抓到、LLM 没抓到的条数 |
| `unique_to_llm` | 只有 LLM 抓到、规则没抓到的条数 |
| `unverifiable_by_rule` | 规则**根本没法校验**的条数（不是通过） |

`unique_to_rule + unique_to_llm` 就是「为什么必须双通道」的直接证据。

## 分层上报

批次里 `kind` 分两类，**分开统计、不混合出总命中率**：

- `real`：流水线真实产出（本机是 ComfyUI 实际出图）
- `injected`：人工构造的缺陷样本，逐条标注了预期命中哪条规则

理由和 Demo B 完全一致：在人工构造的缺陷上测出高命中率毫无意义 ——
那是「规则能抓到自己设计的缺陷」，属于自证。真正有信息量的是
**真实层上的命中率**，以及 `injected` 层里那条**干净对照**有没有被误报。

## 前置条件缺失时的诚实处理

`real` 层的样本引用 `outputs/demo_c/**` 下的真实出图产物，这些是构建产物、
不入库。若在未跑过 ComfyUI 的机器上直接审核，这些资产不存在。
此时**不判为不合格**（那会冤枉内容本身），而是标记 `not_assessable`
并给出补跑命令。宁可报告里出现「无法评估」，也不让通过率虚高。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger

from common import config
from common.llm.router import LLMRouter
from common.metrics import registry
from demo_c_manju.review import llm_judge

# ⚠️ 这里显式 `import demo_c_manju.review.rules as rules`（而不是
#    `from demo_c_manju.review import rules`）是为了避开包内循环导入：
#    `review/__init__.py` 会导入本模块，两类写法在部分初始化状态下行为不同。
import demo_c_manju.review.rules as rules

DEFAULT_BATCH = config.paths.root / "demo_c_manju" / "data" / "review_batch.jsonl"
REPORT_DIR = config.paths.outputs / "demo_c_review"

VERDICT_BLOCK = "block"
VERDICT_REVIEW = "review"
VERDICT_PASS = "pass"
VERDICT_NOT_ASSESSABLE = "not_assessable"

PREREQ_HINT = (
    "该样本引用真实出图产物（outputs/demo_c/**，构建产物不入库）。"
    "请先运行 `python -m demo_c_manju.comfy_adapter --seed 12345` 生成后再审核。"
)


# ------------------------------------------------------------ 数据加载


@dataclass
class ReviewItem:
    id: str
    kind: str
    note: str = ""
    script: str = ""
    prompt: str = ""
    asset: Optional[str] = None
    character_tags: Optional[Dict[str, str]] = None
    expected_issue: Optional[str] = None
    expected_rule: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "note": self.note,
            "asset": self.asset,
            "character_tags": self.character_tags,
            "expected_issue": self.expected_issue,
            "expected_rule": self.expected_rule,
        }


@dataclass
class ReviewBatch:
    items: List[ReviewItem] = field(default_factory=list)
    source: str = str(DEFAULT_BATCH)

    @property
    def real(self) -> List[ReviewItem]:
        return [i for i in self.items if i.kind == "real"]

    @property
    def injected(self) -> List[ReviewItem]:
        return [i for i in self.items if i.kind == "injected"]


def load_batch(path: Optional[Path] = None) -> ReviewBatch:
    target = Path(path) if path else DEFAULT_BATCH
    if not target.exists():
        raise FileNotFoundError(f"审核批次不存在：{target}")

    items: List[ReviewItem] = []
    seen: Dict[str, int] = {}
    for lineno, raw in enumerate(target.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target.name} 第 {lineno} 行不是合法 JSON：{exc}") from exc

        item_id = str(rec.get("id") or "").strip()
        if not item_id:
            raise ValueError(f"{target.name} 第 {lineno} 行缺少 id")
        if item_id in seen:
            raise ValueError(
                f"{target.name} 出现重复 id {item_id!r}（第 {seen[item_id]} 行与第 {lineno} 行）；"
                "重复 id 会让两份不同内容的结论互相覆盖。"
            )
        seen[item_id] = lineno

        kind = str(rec.get("kind") or "").strip()
        if kind not in {"real", "injected"}:
            raise ValueError(
                f"{target.name} 第 {lineno} 行 kind={kind!r} 非法（只允许 real|injected）；"
                "分层统计依赖这个字段，不能留空。"
            )

        items.append(
            ReviewItem(
                id=item_id,
                kind=kind,
                note=rec.get("note") or "",
                script=rec.get("script") or "",
                prompt=rec.get("prompt") or "",
                asset=rec.get("asset"),
                character_tags=rec.get("character_tags"),
                expected_issue=rec.get("expected_issue"),
                expected_rule=rec.get("expected_rule"),
            )
        )
    return ReviewBatch(items=items, source=str(target))


# ------------------------------------------------------------ 单条审核


def _prerequisite_missing(item: ReviewItem) -> bool:
    """real 层样本依赖构建产物。产物不在 → 无法评估，而不是不合格。"""
    if item.kind != "real" or not item.asset:
        return False
    path = rules.resolve_asset_path(item.asset)
    return path is None or not path.exists()


def review_item(
    item: ReviewItem,
    *,
    router: Optional[LLMRouter] = None,
    with_llm: bool = True,
    enabled_rules: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    if _prerequisite_missing(item):
        return {
            "id": item.id,
            "kind": item.kind,
            "note": item.note,
            "asset_declared": item.asset is not None,
            "verdict": VERDICT_NOT_ASSESSABLE,
            "reasons": [f"前置产物缺失：{item.asset}", PREREQ_HINT],
            "rule": None,
            "llm": None,
            "channels": {"rule_flagged": None, "llm_flagged": None},
            "expected_rule": item.expected_rule,
            "expected_caught": None,
        }

    outcome = rules.scan_item_rules(
        item.id,
        script=item.script,
        prompt=item.prompt,
        asset=item.asset,
        character_tags=item.character_tags,
        rules=enabled_rules,
    )

    llm_result: Optional[Dict[str, Any]] = None
    if with_llm:
        llm_result = llm_judge.judge(item.script, item.prompt, router=router, item_id=item.id)

    # ---- 判定 ----
    reasons: List[str] = []
    if outcome.blocked:
        verdict = VERDICT_BLOCK
        reasons.append("规则通道命中阻断级问题：" + ", ".join(sorted(outcome.by_rule)))
    elif outcome.flagged:
        verdict = VERDICT_REVIEW
        reasons.append("规则通道命中警告级问题：" + ", ".join(sorted(outcome.by_rule)))
    else:
        verdict = VERDICT_PASS
        reasons.append("规则通道无命中")

    llm_flagged: Optional[bool] = None
    if llm_result is not None:
        if llm_result.get("ok"):
            llm_flagged = bool(llm_result.get("low_match")) or not bool(
                llm_result.get("compliant", True)
            ) or bool(llm_result.get("issues"))
            if llm_flagged:
                if verdict == VERDICT_PASS:
                    verdict = VERDICT_REVIEW
                bits = []
                if not llm_result.get("compliant", True):
                    bits.append("判定不合规")
                if llm_result.get("low_match"):
                    bits.append(f"分镜一致性 {llm_result.get('script_asset_match')} 分低于阈值")
                if llm_result.get("issues"):
                    bits.append(f"{len(llm_result['issues'])} 条待确认问题")
                reasons.append("LLM 通道命中：" + "；".join(bits))
            else:
                reasons.append("LLM 通道无命中")
        else:
            reasons.append(f"LLM 通道不可用：{llm_result.get('error')}")

    if outcome.unverifiable:
        reasons.append(
            f"规则通道有 {len(outcome.unverifiable)} 项**无法校验**（不是通过）："
            + "; ".join(u["rule"] for u in outcome.unverifiable)
        )

    expected_caught: Optional[bool] = None
    if item.expected_rule:
        expected_caught = item.expected_rule in outcome.by_rule

    return {
        "id": item.id,
        "kind": item.kind,
        "note": item.note,
        "asset_declared": item.asset is not None,
        "verdict": verdict,
        "reasons": reasons,
        "rule": outcome.as_dict(),
        "llm": llm_result,
        "channels": {"rule_flagged": outcome.flagged, "llm_flagged": llm_flagged},
        "expected_rule": item.expected_rule,
        "expected_issue": item.expected_issue,
        "expected_caught": expected_caught,
    }


# ------------------------------------------------------------ 统计


def _rate(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


def _stats(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """对一个分层（或全体）算指标。只统计可评估条目。"""
    assessable = [r for r in results if r["verdict"] != VERDICT_NOT_ASSESSABLE]
    n = len(assessable)

    rule_flagged = [r for r in assessable if r["channels"]["rule_flagged"]]
    rule_blocked = [r for r in assessable if r["verdict"] == VERDICT_BLOCK]

    llm_ok = [r for r in assessable if r["llm"] and r["llm"].get("ok")]
    llm_flagged = [r for r in llm_ok if r["channels"]["llm_flagged"]]

    union = [r for r in assessable if r["channels"]["rule_flagged"] or r["channels"]["llm_flagged"]]

    both = [r for r in llm_ok if r["channels"]["rule_flagged"] and r["channels"]["llm_flagged"]]
    neither = [r for r in llm_ok if not r["channels"]["rule_flagged"] and not r["channels"]["llm_flagged"]]
    unique_to_rule = [r for r in llm_ok if r["channels"]["rule_flagged"] and not r["channels"]["llm_flagged"]]
    unique_to_llm = [r for r in llm_ok if r["channels"]["llm_flagged"] and not r["channels"]["rule_flagged"]]

    findings_by_rule: Dict[str, int] = {}
    unverifiable_by_rule: Dict[str, int] = {}
    for r in assessable:
        for rid, cnt in (r["rule"] or {}).get("by_rule", {}).items():
            findings_by_rule[rid] = findings_by_rule.get(rid, 0) + cnt
        for u in (r["rule"] or {}).get("unverifiable", []):
            unverifiable_by_rule[u["rule"]] = unverifiable_by_rule.get(u["rule"], 0) + 1

    verdicts: Dict[str, int] = {}
    for r in results:
        verdicts[r["verdict"]] = verdicts.get(r["verdict"], 0) + 1

    asset_declared = [r for r in results if r.get("asset_declared")]
    return {
        "items": len(results),
        "assessable": n,
        "not_assessable": len(results) - n,
        "asset_declared": len(asset_declared),
        "asset_declared_rate": _rate(len(asset_declared), len(results)),
        "verdicts": verdicts,
        "rule_flagged": len(rule_flagged),
        "rule_flag_rate": _rate(len(rule_flagged), n),
        "rule_blocked": len(rule_blocked),
        "rule_block_rate": _rate(len(rule_blocked), n),
        "llm_available": bool(llm_ok),
        "llm_assessed": len(llm_ok),
        "llm_flagged": len(llm_flagged) if llm_ok else None,
        "llm_flag_rate": _rate(len(llm_flagged), len(llm_ok)) if llm_ok else None,
        "union_flagged": len(union),
        "union_flag_rate": _rate(len(union), n),
        "channel_agreement": len(both) + len(neither),
        "channel_agreement_rate": _rate(len(both) + len(neither), len(llm_ok)) if llm_ok else None,
        "unique_to_rule": len(unique_to_rule) if llm_ok else None,
        "unique_to_llm": len(unique_to_llm) if llm_ok else None,
        "unique_to_rule_ids": [r["id"] for r in unique_to_rule],
        "unique_to_llm_ids": [r["id"] for r in unique_to_llm],
        "findings_by_rule": findings_by_rule,
        "unverifiable_by_rule": unverifiable_by_rule,
        "unverifiable_total": sum(unverifiable_by_rule.values()),
    }


def _recall(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """只在标注了 expected_rule 的注入样本上算召回。"""
    expected = [r for r in results if r.get("expected_rule")]
    caught = [r for r in expected if r.get("expected_caught") is True]
    # 用 `is False` 而非 `not`：无法评估（None）不该被算成漏抓
    missed = [r for r in expected if r.get("expected_caught") is False]
    return {
        "definition": "标注了预期规则的注入样本中，该规则确实命中的比例",
        "expected_total": len(expected),
        "caught": len(caught),
        "recall": _rate(len(caught), len(expected)),
        "misses": [
            {"id": r["id"], "expected_rule": r["expected_rule"], "actual": (r["rule"] or {}).get("by_rule", {})}
            for r in missed
        ],
    }


def _false_positive_control(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """干净对照样本（injected 且未标注预期规则）上有没有误报。"""
    clean = [r for r in results if r["kind"] == "injected" and not r.get("expected_rule")]
    flagged = [r for r in clean if r["channels"]["rule_flagged"]]
    return {
        "definition": "未标注任何预期缺陷的注入对照样本中，被规则误报的比例",
        "clean_total": len(clean),
        "clean_flagged": len(flagged),
        "false_positive_rate": _rate(len(flagged), len(clean)),
        "flagged_ids": [r["id"] for r in flagged],
    }


# ------------------------------------------------------------ 主流程


def review_batch(
    batch: Optional[ReviewBatch] = None,
    *,
    router: Optional[LLMRouter] = None,
    with_llm: bool = True,
    enabled_rules: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    batch = batch or load_batch()
    router = router or (LLMRouter() if with_llm else None)

    results = [
        review_item(i, router=router, with_llm=with_llm, enabled_rules=enabled_rules)
        for i in batch.items
    ]

    llm_errors = sorted(
        {r["llm"]["error"] for r in results if r["llm"] and not r["llm"].get("ok")}
    )

    report = {
        "generated_by": "demo_c_manju/review/reviewer.py",
        "batch_source": batch.source,
        "batch_size": len(batch.items),
        "channel": {
            "rule": rules.rule_provenance(),
            "llm": llm_judge.channel_provenance(),
        },
        "llm_channel_exercised": bool(router and with_llm),
        "llm_errors": llm_errors,
        "overall": _stats(results),
        "strata": {
            "real": _stats([r for r in results if r["kind"] == "real"]),
            "injected": _stats([r for r in results if r["kind"] == "injected"]),
        },
        "injected_recall": _recall(results),
        "false_positive_control": _false_positive_control(results),
        "prerequisite_missing": [
            {"id": r["id"], "asset_hint": r["reasons"][0]} for r in results
            if r["verdict"] == VERDICT_NOT_ASSESSABLE
        ],
        "items": results,
    }
    return report


# ------------------------------------------------------------ 输出


def write_report(report: Dict[str, Any], out_dir: Optional[Path] = None) -> Dict[str, str]:
    out = Path(out_dir) if out_dir else REPORT_DIR
    out.mkdir(parents=True, exist_ok=True)

    json_path = out / "report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = out / "REPORT.md"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def _pct(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v * 100:.1f}%"


def render_markdown(report: Dict[str, Any]) -> str:
    ov = report["overall"]
    real = report["strata"]["real"]
    inj = report["strata"]["injected"]
    rec = report["injected_recall"]
    fpc = report["false_positive_control"]

    lines: List[str] = []
    lines.append("# Demo C 审核层报告")
    lines.append("")
    lines.append(f"- 批次来源：`{report['batch_source']}`")
    lines.append(f"- 批次规模：{report['batch_size']} 条")
    lines.append(f"- 审核通道：规则通道 + LLM 通道（LLM 本次是否实际执行：{report['llm_channel_exercised']}）")
    lines.append("")
    lines.append("## 1. 结论先行")
    lines.append("")
    lines.append("| 分层 | 送审 | 可评估 | 无法评估 | 规则命中率 | LLM 命中率 | 并集命中率 |")
    lines.append("|---|---|---|---|---|---|---|")
    for name, s in (("真实产出", real), ("注入缺陷", inj), ("全体", ov)):
        lines.append(
            f"| {name} | {s['items']} | {s['assessable']} | {s['not_assessable']} | "
            f"{_pct(s['rule_flag_rate'])} | {_pct(s['llm_flag_rate'])} | {_pct(s['union_flag_rate'])} |"
        )
    lines.append("")
    if report["llm_errors"]:
        lines.append(f"> ⚠️ LLM 通道未生效：{report['llm_errors'][0]}")
        lines.append("> 因此本次报告只有规则通道的数字是实测的；LLM 相关列一律为 n/a，不做估算。")
        lines.append("")

    lines.append("## 2. 双通道为何必要")
    lines.append("")
    if ov["llm_available"]:
        lines.append(f"- 两通道判定一致（都命中或都不命中）：{ov['channel_agreement']}/{ov['llm_assessed']} = {_pct(ov['channel_agreement_rate'])}")
        lines.append(f"- 只有规则抓到：**{ov['unique_to_rule']}** 条 → {ov['unique_to_rule_ids']}")
        lines.append(f"- 只有 LLM 抓到：**{ov['unique_to_llm']}** 条 → {ov['unique_to_llm_ids']}")
        lines.append("")
        lines.append("两个 `unique_*` 数字之和就是「单通道会漏掉多少」的直接证据。")
    else:
        lines.append("LLM 通道本次未执行，`unique_to_llm` 无法测量 —— **此处不给估算值**。")
        lines.append("")
        lines.append("可先看规则通道内部的互补性：各规则的命中分布见下表。")
    lines.append("")

    lines.append("## 3. 规则命中分布")
    lines.append("")
    lines.append("| 规则 | 命中次数 | 无法校验次数 |")
    lines.append("|---|---|---|")
    all_rules = sorted(set(ov["findings_by_rule"]) | set(ov["unverifiable_by_rule"]))
    for rid in all_rules:
        lines.append(
            f"| {rid} | {ov['findings_by_rule'].get(rid, 0)} | {ov['unverifiable_by_rule'].get(rid, 0)} |"
        )
    lines.append("")
    lines.append("> 「无法校验」与「通过」是两回事，因此单列一列。")
    lines.append("")

    lines.append("## 4. 注入样本召回")
    lines.append("")
    lines.append(f"口径：{rec['definition']}")
    lines.append("")
    lines.append(f"- 标注了预期规则的样本：{rec['expected_total']} 条")
    lines.append(f"- 规则成功抓到：{rec['caught']} 条 → 召回率 **{_pct(rec['recall'])}**")
    if rec["misses"]:
        lines.append("- 漏抓：")
        for m in rec["misses"]:
            lines.append(f"  - `{m['id']}` 预期 `{m['expected_rule']}`，实际命中 {m['actual']}")
    lines.append("")
    lines.append("> ⚠️ 这个召回率是在**人工构造的缺陷**上测的，等于「规则抓到了自己设计的缺陷」。")
    lines.append("> 它有回归价值（改规则后能看出有没有变差），但**不能**当作真实场景的检出率。")
    lines.append("")

    lines.append("## 5. 误报对照")
    lines.append("")
    lines.append(f"口径：{fpc['definition']}")
    lines.append("")
    lines.append(f"- 干净对照样本：{fpc['clean_total']} 条，误报 {fpc['clean_flagged']} 条 → 误报率 {_pct(fpc['false_positive_rate'])}")
    if fpc["flagged_ids"]:
        lines.append(f"- 被误报的 id：{fpc['flagged_ids']}")
    lines.append("")

    lines.append("## 6. 逐条明细")
    lines.append("")
    lines.append("| id | 分层 | 判定 | 规则命中 | LLM | 说明 |")
    lines.append("|---|---|---|---|---|---|")
    for r in report["items"]:
        rule_hits = ", ".join(sorted((r["rule"] or {}).get("by_rule", {}))) or "-"
        llm_cell = "-"
        if r["llm"]:
            llm_cell = "ok" if r["llm"].get("ok") else "unavailable"
        lines.append(
            f"| `{r['id']}` | {r['kind']} | {r['verdict']} | {rule_hits} | {llm_cell} | "
            f"{'; '.join(r['reasons'])} |"
        )
    lines.append("")

    if report["prerequisite_missing"]:
        lines.append("## 7. 无法评估的样本")
        lines.append("")
        for p in report["prerequisite_missing"]:
            lines.append(f"- `{p['id']}`：{p['asset_hint']}")
        lines.append("")

    lines.append("## 8. 口径与边界（会被追问，先写在前面）")
    lines.append("")
    lines.append(f"- 规则通道：{len(report['channel']['rule']['rules'])} 条自研规则；" 
                 f"词表 {report['channel']['rule']['lexicon']['total_terms']} 条，"
                 f"级别「{report['channel']['rule']['lexicon']['tier']}」")
    for gap in report["channel"]["rule"]["lexicon"]["gaps"]:
        lines.append(f"  - ⚠️ 词表缺口：{gap}")
    lines.append(f"- 占位图阈值：{report['channel']['rule']['placeholder_threshold_bpp']} B/px（启发式，非判定性）")
    lines.append(f"- 无法校验语义：{report['channel']['rule']['unverifiable_semantics']}")
    llm_p = report["channel"]["llm"]
    lines.append(f"- LLM 通道：{llm_p['mode']}，vision_available={llm_p['vision_available']}")
    lines.append(f"  - {llm_p['vision_note']}")
    lines.append(f"- 复现命令：`python -m demo_c_manju.review --out outputs/demo_c_review`")
    lines.append("")
    return "\n".join(lines)


def publish_metrics(report: Dict[str, Any]) -> None:
    """把实测值回填到指标注册表（禁止手写数字）。"""
    ov = report["overall"]
    real = report["strata"]["real"]
    inj = report["strata"]["injected"]

    value = (
        f"规则通道 · 注入层 {inj['rule_flagged']}/{inj['assessable']} "
        f"({_pct(inj['rule_flag_rate'])})；"
        f"真实层 {real['rule_flagged']}/{real['assessable']} "
        f"({_pct(real['rule_flag_rate'])})；"
        f"误报对照 {report['false_positive_control']['clean_flagged']}/"
        f"{report['false_positive_control']['clean_total']}"
    )
    if not ov["llm_available"]:
        value += "；LLM 通道未执行（无 Key / 无本地模型），未估算"

    registry.set_measured("c_review_hit_rate", value)
    logger.info("已回填 c_review_hit_rate: {}", value)

    if ov["llm_available"]:
        registry.set_measured(
            "c_review_channel_complement",
            f"一致 {ov['channel_agreement']}/{ov['llm_assessed']}；"
            f"仅规则 {ov['unique_to_rule']}；仅 LLM {ov['unique_to_llm']}",
        )
    else:
        registry.set_measured(
            "c_review_channel_complement",
            "本次为单通道（规则）实测：规则命中 %d 条。"
            "双通道互补量需要 LLM 通道可用后重跑才能测出，当前不给估算值。"
            % ov["rule_flagged"],
        )
