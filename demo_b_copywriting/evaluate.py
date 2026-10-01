"""Demo B 的评测框架 —— 三路对照批量跑分。

三路对照的两条改写臂来自 `rewrite.py`，第三条是原文本身（对照组）。
本模块负责：批量执行 → 逐条质检 → 汇总指标 → 落盘报告 → 回填指标口径注册表。

## 产物

| 文件 | 用途 |
|---|---|
| `reports/metrics.csv` | 长表，一行 = 一条样本 × 一个臂。所有数字的原始出处 |
| `reports/summary.json` | 按臂 / 按语域的聚合结果 + 仪器清单 |
| `reports/three_way.md` | 人工可读的三路对照页（演示与讲稿的主素材） |
| `portal/metrics.json` | 回填实测值后的指标口径表（Demo D 门户读它） |

## 一条硬纪律

**本模块是唯一允许把实测数字写进指标口径表的入口。**
`registry.set_measured()` 只在这里被调用，文档里不允许手写数字。
这样「文档数字 ↔ 哪次评测」永远对得上。

## 关于「对照组」的口径

`original` 臂的改写结果就是原文自身，因此它的分数差恒为 0、保真度恒为 1。
它存在的意义不是「表现好」，而是提供**同一次评测内部**的基准线 ——
跨评测比较分数，只有在数据集与仪器都一致时才成立。

用法：
    python demo_b_copywriting/evaluate.py
    python demo_b_copywriting/evaluate.py --limit 5 --no-llm-review
    python demo_b_copywriting/evaluate.py --arms kernel            # 只跑规则臂（无 LLM 环境）
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from common import config
from common.llm.router import LLMRouter
from common.metrics import registry
from demo_b_copywriting import tells
from demo_b_copywriting.quality import assess, instrument_provenance
from demo_b_copywriting.retriever import Retriever
from demo_b_copywriting.rewrite import (
    ARM_KERNEL,
    ARM_LLM,
    ARM_ORIGINAL,
    PROMPT_VERSION,
    Redactor,
    kernel_rewrite,
)

HERE = Path(__file__).resolve().parent
EVAL_FILE = HERE / "data" / "clean" / "ai_style_eval.jsonl"
REPORT_DIR = HERE / "reports"
PORTAL_METRICS = HERE.parent / "portal" / "metrics.json"

ARM_ORDER = [ARM_ORIGINAL, ARM_KERNEL, ARM_LLM]
ARM_LABEL = {
    ARM_ORIGINAL: "原文（对照组）",
    ARM_KERNEL: "规则改写（第三方 humanize-chinese）",
    ARM_LLM: "自研 RAG+LLM 改写",
}


# ============================================================ 载入


def load_eval_set(path: Path = EVAL_FILE) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"缺少评测集 {path} —— 请先运行 ingest.py")
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not rows:
        raise ValueError(f"{path} 为空")
    return rows


def _llm_available(router: LLMRouter) -> tuple[bool, Dict[str, Any]]:
    """探活一次。两条通道都不可用时，整条 LLM 臂跳过，而不是逐条失败 20 次。"""
    health = router.health()
    providers = health.get("providers") or {}
    ok = any((p or {}).get("ok") for p in providers.values())
    return ok, health


# ============================================================ 单条评测


@dataclass
class Row:
    id: str
    vertical: str
    register: str
    arm: str
    ok: bool
    error: Optional[str]
    chars_in: int
    chars_out: int
    length_ratio: float
    fidelity: Optional[float]
    fidelity_reliable: bool
    detection_before: Optional[int]
    detection_after: Optional[int]
    score_drop: Optional[int]
    grammar_before: int
    grammar_after: int
    grammar_delta: int
    tells_before: int
    tells_after: int
    tells_drop: int
    verdict: str
    reasons: str
    elapsed_s: float
    retrieval_ms: float
    provider: Optional[str]
    model: Optional[str]
    prompt_version: Optional[str]

    def as_dict(self) -> Dict[str, Any]:
        return self.__dict__.copy()


def _row_from(
    rec: Dict[str, Any], arm: str, rewritten: str, result: Any, assessment: Any
) -> Row:
    chat = (result.chat if getattr(result, "chat", None) else None) or {}
    return Row(
        id=rec["id"],
        vertical=rec.get("vertical", ""),
        register=rec.get("register", "未标注"),
        arm=arm,
        ok=bool(getattr(result, "ok", True)),
        error=getattr(result, "error", None),
        chars_in=assessment.original_chars,
        chars_out=assessment.rewritten_chars,
        length_ratio=assessment.length_ratio,
        fidelity=assessment.semantic_fidelity,
        fidelity_reliable=assessment.fidelity_reliable,
        detection_before=(assessment.detection_before or {}).get("score"),
        detection_after=(assessment.detection_after or {}).get("score"),
        score_drop=assessment.score_drop,
        grammar_before=int(assessment.grammar_before.get("count", 0)),
        grammar_after=int(assessment.grammar_after.get("count", 0)),
        grammar_delta=assessment.grammar_delta,
        tells_before=int(assessment.tells_before.get("count", 0)),
        tells_after=int(assessment.tells_after.get("count", 0)),
        tells_drop=int(assessment.tells_before.get("count", 0))
        - int(assessment.tells_after.get("count", 0)),
        verdict=assessment.verdict,
        reasons=" | ".join(assessment.reasons),
        elapsed_s=round(float(getattr(result, "elapsed_s", 0.0)), 3),
        retrieval_ms=round(float(getattr(result, "retrieval_ms", 0.0)), 3),
        provider=chat.get("provider"),
        model=chat.get("model"),
        prompt_version=(result.prompt or {}).get("version") if getattr(result, "prompt", None) else None,
    )


# ============================================================ 聚合


def _mean(values: List[Optional[float]]) -> Optional[float]:
    xs = [v for v in values if v is not None]
    return round(statistics.mean(xs), 4) if xs else None


def _median(values: List[Optional[float]]) -> Optional[float]:
    xs = [v for v in values if v is not None]
    return round(statistics.median(xs), 4) if xs else None


def aggregate(rows: List[Row]) -> Dict[str, Any]:
    """按臂聚合。只统计成功执行的条目，避免失败样本把均值拉低。"""
    out: Dict[str, Any] = {}
    for arm in ARM_ORDER:
        sel = [r for r in rows if r.arm == arm]
        if not sel:
            continue
        ok_sel = [r for r in sel if r.ok]
        verdicts: Dict[str, int] = {}
        for r in sel:
            verdicts[r.verdict] = verdicts.get(r.verdict, 0) + 1
        out[arm] = {
            "label": ARM_LABEL.get(arm, arm),
            "n_total": len(sel),
            "n_ok": len(ok_sel),
            "n_error": len(sel) - len(ok_sel),
            "detection_before_mean": _mean([r.detection_before for r in sel]),
            "detection_after_mean": _mean([r.detection_after for r in ok_sel]),
            "score_drop_mean": _mean([r.score_drop for r in ok_sel]),
            "score_drop_median": _median([r.score_drop for r in ok_sel]),
            "tells_before_mean": _mean([r.tells_before for r in sel]),
            "tells_after_mean": _mean([r.tells_after for r in ok_sel]),
            "tells_drop_mean": _mean([r.tells_drop for r in ok_sel]),
            "tells_drop_total": sum(r.tells_drop for r in ok_sel),
            "grammar_before_mean": _mean([r.grammar_before for r in sel]),
            "grammar_after_mean": _mean([r.grammar_after for r in ok_sel]),
            "grammar_new_anomaly_records": sum(1 for r in ok_sel if r.grammar_delta > 0),
            "fidelity_mean": _mean([r.fidelity for r in ok_sel if r.fidelity_reliable]),
            "fidelity_reliable": bool(sel and sel[0].fidelity_reliable),
            "length_ratio_mean": _mean([r.length_ratio for r in ok_sel]),
            "verdicts": verdicts,
            "elapsed_s_mean": _mean([r.elapsed_s for r in ok_sel]),
            "retrieval_ms_mean": _mean([r.retrieval_ms for r in ok_sel if r.retrieval_ms]) or 0.0,
        }
    return out


def by_register(rows: List[Row]) -> Dict[str, Any]:
    """按语域统计「内核检测器」的灵敏度 —— 用来证明单一仪器不可用。"""
    out: Dict[str, Any] = {}
    regs = sorted({r.register for r in rows})
    for reg in regs:
        sel = [r for r in rows if r.register == reg and r.arm == ARM_ORIGINAL]
        if not sel:
            continue
        scores = [r.detection_before for r in sel if r.detection_before is not None]
        tell_counts = [r.tells_before for r in sel]
        out[reg] = {
            "n": len(sel),
            "kernel_score_zero": sum(1 for s in scores if s == 0),
            "kernel_score_mean": _mean(scores),
            "kernel_score_max": max(scores) if scores else None,
            "tells_mean": _mean(tell_counts),
            "tells_zero": sum(1 for c in tell_counts if c == 0),
            "note": "kernel_score_* 来自第三方检测器；tells_* 来自自研词典",
        }
    return out


# ============================================================ 落盘


CSV_FIELDS = [
    "id", "vertical", "register", "arm", "ok", "error",
    "chars_in", "chars_out", "length_ratio",
    "fidelity", "fidelity_reliable",
    "detection_before", "detection_after", "score_drop",
    "grammar_before", "grammar_after", "grammar_delta",
    "tells_before", "tells_after", "tells_drop",
    "verdict", "reasons", "elapsed_s", "retrieval_ms",
    "provider", "model", "prompt_version",
]


def write_csv(path: Path, rows: List[Row]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r.as_dict())


def write_markdown(path: Path, rows: List[Row], summary: Dict[str, Any]) -> None:
    lines: List[str] = []
    lines.append("# Demo B 三路对照报告")
    lines.append("")
    lines.append(f"生成时间：{summary['generated_at']}　样本数：{summary['eval_set']['n']}")
    lines.append("")

    lines.append("## 聚合结果")
    lines.append("")
    lines.append("| 臂 | 样本 | 检测分(前→后) | 检测分降幅 | AI味残留(前→后) | 新增语法异常条数 | 判定 pass/review/fail |")
    lines.append("|---|---|---|---|---|---|---|")
    for arm in ARM_ORDER:
        s = summary["by_arm"].get(arm)
        if not s:
            continue
        v = s["verdicts"]
        lines.append(
            f"| {s['label']} | {s['n_ok']}/{s['n_total']} "
            f"| {s['detection_before_mean']} → {s['detection_after_mean']} "
            f"| {s['score_drop_mean']} "
            f"| {s['tells_before_mean']} → {s['tells_after_mean']} "
            f"| {s['grammar_new_anomaly_records']} "
            f"| {v.get('pass', 0)} / {v.get('review', 0)} / {v.get('fail', 0)} |"
        )
    lines.append("")

    lines.append("## 仪器灵敏度（按语域）")
    lines.append("")
    lines.append("内核检测器得 0 分 = 仪器未检出任何问题，**不等于文本没有 AI 味**。")
    lines.append("")
    lines.append("| 语域 | 样本 | 内核得0分 | 内核均分 | 内核最高 | 自研词典均命中 | 自研得0分 |")
    lines.append("|---|---|---|---|---|---|---|")
    for reg, s in summary["by_register"].items():
        lines.append(
            f"| {reg} | {s['n']} | {s['kernel_score_zero']} | {s['kernel_score_mean']} "
            f"| {s['kernel_score_max']} | {s['tells_mean']} | {s['tells_zero']} |"
        )
    lines.append("")

    lines.append("## 逐条对照")
    lines.append("")
    grouped: Dict[str, List[Row]] = {}
    for r in rows:
        grouped.setdefault(r.id, []).append(r)
    for rid in sorted(grouped):
        sel = {r.arm: r for r in grouped[rid]}
        base = sel.get(ARM_ORIGINAL) or next(iter(sel.values()))
        lines.append(f"### {rid}　（垂类：{base.vertical}　语域：{base.register}）")
        lines.append("")
        for arm in ARM_ORDER:
            r = sel.get(arm)
            if not r:
                continue
            lines.append(
                f"- **{ARM_LABEL.get(arm, arm)}**：检测分 {r.detection_before} → {r.detection_after}"
                f"；AI 味残留 {r.tells_before} → {r.tells_after}"
                f"；长度比 {r.length_ratio}；判定 `{r.verdict}`"
                + (f"；耗时 {r.elapsed_s}s" if arm != ARM_ORIGINAL else "")
            )
            if r.error:
                lines.append(f"  - ⚠️ 错误：{r.error}")
            if r.reasons:
                lines.append(f"  - 判定依据：{r.reasons}")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


# ============================================================ 回填口径表


def _fmt(v: Any) -> str:
    return "—" if v is None else str(v)


def apply_measurements(summary: Dict[str, Any], *, retrieval_bench: Dict[str, Any]) -> List[str]:
    """把实测值回填到指标注册表。返回被更新的 key 列表。

    这是**唯一**允许写入实测数字的位置。
    """
    by_arm = summary["by_arm"]
    kern = by_arm.get(ARM_KERNEL, {})
    llm = by_arm.get(ARM_LLM, {})
    updated: List[str] = []

    reg = summary["by_register"]
    zero = sum(s["kernel_score_zero"] for s in reg.values())
    total = sum(s["n"] for s in reg.values())
    coverage = (
        f"{total} 条中 {zero} 条内核得 0 分"
        if total and zero
        else "内核在全部样本上均有检出"
    )

    # --- 检测分下降 ---
    arm_used = llm if llm.get("n_ok") else kern
    arm_name = ARM_LABEL.get(ARM_LLM if llm.get("n_ok") else ARM_KERNEL)
    if arm_used.get("n_ok"):
        registry.set_measured(
            "b_detection_score_drop",
            f"{arm_name}：检测分均值 {_fmt(arm_used['detection_before_mean'])} → "
            f"{_fmt(arm_used['detection_after_mean'])}，"
            f"均降 {_fmt(arm_used['score_drop_mean'])} 分（中位数 {_fmt(arm_used['score_drop_median'])}），"
            f"n={arm_used['n_ok']}",
            gap_note=(
                "简历值是「平台检测通过率」，本 Demo 用开源检测器的分数代理，两者不是同一把尺子。"
                f"**更重要的限制**：该检测器的灵敏度分语域 —— {coverage}，"
                "对「评测腔 / 种草腔」覆盖不足。因此本项必须与 b_ai_tell_residual 并报，"
                "不得单独引用。详见 third_party/README.md。"
            ),
        )
        updated.append("b_detection_score_drop")

    # --- 自研 AI 味残留 ---
    if arm_used.get("n_ok"):
        registry.set_measured(
            "b_ai_tell_residual",
            f"{arm_name}：均命中 {_fmt(arm_used['tells_before_mean'])} → "
            f"{_fmt(arm_used['tells_after_mean'])} 处，"
            f"均减少 {_fmt(arm_used['tells_drop_mean'])} 处，"
            f"合计减少 {arm_used['tells_drop_total']} 处（n={arm_used['n_ok']}）",
        )
        updated.append("b_ai_tell_residual")

    # --- 语义保真度 ---
    if arm_used.get("fidelity_reliable") and arm_used.get("fidelity_mean") is not None:
        registry.set_measured(
            "b_semantic_fidelity",
            f"{arm_name}：均 {arm_used['fidelity_mean']}（n={arm_used['n_ok']}）",
        )
    else:
        registry.set_measured(
            "b_semantic_fidelity",
            "未测出可用值 —— 当前环境缺 sentence-transformers，向量化走零依赖兜底，"
            "余弦不具语义意义，已排除在判定之外",
        )
    updated.append("b_semantic_fidelity")

    # --- 语法异常 ---
    if arm_used.get("n_ok"):
        registry.set_measured(
            "b_grammar_anomalies",
            f"{arm_name}：{arm_used['n_ok']} 条中有 "
            f"{arm_used['grammar_new_anomaly_records']} 条出现新增语法异常；"
            f"均命中 {_fmt(arm_used['grammar_before_mean'])} → "
            f"{_fmt(arm_used['grammar_after_mean'])}",
        )
        updated.append("b_grammar_anomalies")

    # --- 检索耗时 ---
    if retrieval_bench:
        registry.set_measured(
            "b_retrieval_latency",
            f"中位数 {retrieval_bench.get('median_ms')} ms，"
            f"P95 {retrieval_bench.get('p95_ms')} ms"
            f"（{retrieval_bench.get('samples')} 次采样，"
            f"后端 {retrieval_bench.get('store_backend')}，"
            f"语料 {summary['eval_set']['n']} 条查询 / {summary['index']['doc_count']} 篇文档）",
            gap_note=(
                "仅在 Demo 规模（数十~数千条）下测量，且**不含向量化耗时**（只测向量检索本身）。"
                "十万级以上需换 IVF/HNSW 并重新标定召回率。"
            ),
        )
        updated.append("b_retrieval_latency")

    registry.export_json(PORTAL_METRICS)
    return updated


# ============================================================ 主流程


def run(
    *,
    arms: Optional[List[str]] = None,
    limit: Optional[int] = None,
    vertical: Optional[str] = None,
    register: Optional[str] = None,
    with_llm_review: bool = False,
    report_dir: Path = REPORT_DIR,
    retrieval_repeat: int = 3,
) -> Dict[str, Any]:
    arms = arms or ARM_ORDER
    records = load_eval_set()
    if vertical:
        records = [r for r in records if r.get("vertical") == vertical]
    if register:
        records = [r for r in records if r.get("register") == register]
    if limit:
        records = records[:limit]
    if not records:
        raise ValueError("筛选后评测集为空")

    # 场地准备：索引 + 编码器只加载一次（否则每条都重建，耗时口径会被加载开销污染）
    retriever = Retriever()
    encoder = retriever.encoder
    router = LLMRouter()

    llm_ok, llm_health = _llm_available(router)
    if not llm_ok and ARM_LLM in arms:
        logger.warning(
            "LLM 两条通道均不可用，跳过自研改写臂（规则臂与对照臂不受影响）。"
            f"通道状态：{json.dumps(llm_health.get('providers', {}), ensure_ascii=False)[:200]}"
        )
        arms = [a for a in arms if a != ARM_LLM]

    redactor = Redactor(retriever, router) if ARM_LLM in arms else None

    rows: List[Row] = []
    for idx, rec in enumerate(records, start=1):
        text = rec["text"]
        vert = rec.get("vertical")
        logger.info(f"[{idx}/{len(records)}] {rec['id']}（{vert} / {rec.get('register')}）")

        if ARM_ORIGINAL in arms:
            a = assess(text, text, encoder, router=router, with_llm_review=False, control=True)
            dummy = type("R", (), {"ok": True, "error": None, "elapsed_s": 0.0,
                                   "retrieval_ms": 0.0, "chat": None, "prompt": None})()
            rows.append(_row_from(rec, ARM_ORIGINAL, text, dummy, a))

        if ARM_KERNEL in arms:
            res = kernel_rewrite(text, vert)
            a = assess(text, res.output_text, encoder, router=router, with_llm_review=with_llm_review)
            rows.append(_row_from(rec, ARM_KERNEL, res.output_text, res, a))

        if ARM_LLM in arms and redactor is not None:
            res = redactor.rewrite(text, vert)
            a = assess(text, res.output_text, encoder, router=router, with_llm_review=with_llm_review)
            rows.append(_row_from(rec, ARM_LLM, res.output_text, res, a))

    logger.info(f"检索耗时基准测试（{len(records)} 条查询 × {retrieval_repeat} 次）……")
    retrieval_bench = retriever.benchmark([r["text"] for r in records], k=3, repeat=retrieval_repeat)

    summary: Dict[str, Any] = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "generated_by": "demo_b_copywriting/evaluate.py",
        "eval_set": {
            "file": str(EVAL_FILE.relative_to(HERE.parent)),
            "n": len(records),
            "verticals": sorted({r.get("vertical", "") for r in records}),
            "registers": sorted({r.get("register", "") for r in records}),
        },
        "arms": arms,
        "index": retriever.info,
        "prompt_version": PROMPT_VERSION if ARM_LLM in arms else None,
        "llm": {"available": llm_ok, "health": llm_health},
        "by_arm": aggregate(rows),
        "by_register": by_register(rows),
        "retrieval_bench": retrieval_bench,
        "instruments": instrument_provenance(),
        "tells_dictionary": tells.stats(),
        "caveats": [
            "对照组（原文）的分数差恒为 0，作用是提供同批次基准线，不代表「表现好」。",
            "内核检测器灵敏度分语域，得 0 分不等于没有 AI 味 —— 必须与自研词典并报。",
            "语义保真度在兜底编码器下不具语义意义，报告已标出 fidelity_reliable。",
            "所有效果数字都绑定具体通道（provider/model）与提示词版本，跨版本不可直接比。",
        ],
    }

    report_dir.mkdir(parents=True, exist_ok=True)
    write_csv(report_dir / "metrics.csv", rows)
    write_markdown(report_dir / "three_way.md", rows, summary)
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    summary["updated_metrics"] = apply_measurements(summary, retrieval_bench=retrieval_bench)
    (report_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(f"报告已写入 {report_dir}（{len(rows)} 行）")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo B 三路对照评测")
    parser.add_argument("--arms", nargs="*", default=ARM_ORDER, choices=ARM_ORDER)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--vertical", default=None)
    parser.add_argument("--register", default=None)
    parser.add_argument("--no-llm-review", action="store_true", help="不做 LLM 评审（省调用）")
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--retrieval-repeat", type=int, default=3)
    args = parser.parse_args()

    summary = run(
        arms=args.arms,
        limit=args.limit,
        vertical=args.vertical,
        register=args.register,
        with_llm_review=not args.no_llm_review,
        report_dir=args.report_dir,
        retrieval_repeat=args.retrieval_repeat,
    )

    print(json.dumps(summary["by_arm"], ensure_ascii=False, indent=2))
    print("\n--- 仪器灵敏度（按语域）---")
    print(json.dumps(summary["by_register"], ensure_ascii=False, indent=2))
    print(f"\n已更新指标口径表：{', '.join(summary['updated_metrics'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
