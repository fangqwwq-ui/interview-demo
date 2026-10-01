"""审核层命令行入口。

    python -m demo_c_manju.review                    # 跑默认批次，写报告
    python -m demo_c_manju.review --no-llm           # 只跑规则通道（无需任何模型）
    python -m demo_c_manju.review --out D:/tmp/rev   # 指定输出目录
    python -m demo_c_manju.review --publish-metrics  # 同时回填指标注册表

设计意图：规则通道**不依赖任何模型**，所以在没有 API Key、
没有 Ollama 的机器上依然可跑并产出可解释结论 —— 这是本项目
「每一层都能在缺依赖时给出诚实结果」这条原则的延续。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loguru import logger

from demo_c_manju.review import reviewer


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo_c_manju.review",
        description="Demo C 审核层：规则通道 + LLM 通道，分层上报命中率",
    )
    parser.add_argument("--batch", default=None, help="批次 jsonl 路径")
    parser.add_argument("--out", default=None, help="报告输出目录")
    parser.add_argument("--no-llm", action="store_true", help="只跑规则通道")
    parser.add_argument(
        "--rules",
        default=None,
        help="只启用指定规则，逗号分隔（消融实验用）。例：C1_banned_term,C4_asset_broken",
    )
    parser.add_argument("--publish-metrics", action="store_true", help="回填指标注册表")
    parser.add_argument("--quiet", action="store_true", help="只打印摘要，不打印逐条明细")
    args = parser.parse_args(argv)

    batch = reviewer.load_batch(Path(args.batch) if args.batch else None)
    enabled = [r.strip() for r in args.rules.split(",") if r.strip()] if args.rules else None

    report = reviewer.review_batch(batch, with_llm=not args.no_llm, enabled_rules=enabled)
    paths = reviewer.write_report(report, Path(args.out) if args.out else None)

    ov = report["overall"]
    real = report["strata"]["real"]
    inj = report["strata"]["injected"]

    def pct(v):
        return "n/a" if v is None else f"{v * 100:.1f}%"

    print("=" * 66)
    print(f"批次：{report['batch_source']}   共 {report['batch_size']} 条")
    print(f"LLM 通道是否执行：{report['llm_channel_exercised']}")
    if report["llm_errors"]:
        print(f"  ⚠️ {report['llm_errors'][0]}")
        print("  → LLM 相关指标一律为 n/a，不做估算")
    print("-" * 66)
    print(f"{'分层':<10}{'送审':>5}{'可评估':>7}{'无法评估':>9}{'规则命中率':>11}{'LLM命中率':>11}{'并集':>9}")
    for name, s in (("真实产出", real), ("注入缺陷", inj), ("全体", ov)):
        print(
            f"{name:<10}{s['items']:>5}{s['assessable']:>7}{s['not_assessable']:>9}"
            f"{pct(s['rule_flag_rate']):>11}{pct(s['llm_flag_rate']):>11}"
            f"{pct(s['union_flag_rate']):>9}"
        )
    print("-" * 66)
    print(f"注入召回：{report['injected_recall']['caught']}"
          f"/{report['injected_recall']['expected_total']}"
          f"  ({pct(report['injected_recall']['recall'])})"
          "   ← 人工构造缺陷上的回归指标，不等于真实检出率")
    fpc = report["false_positive_control"]
    print(f"误报对照：{fpc['clean_flagged']}/{fpc['clean_total']}"
          f"  ({pct(fpc['false_positive_rate'])})")
    print(f"规则命中分布：{json.dumps(ov['findings_by_rule'], ensure_ascii=False)}")
    print(f"无法校验分布：{json.dumps(ov['unverifiable_by_rule'], ensure_ascii=False)}"
          "   ← 「没法校验」不是「通过」")
    print("-" * 66)
    print(f"报告：{paths['markdown']}")
    print(f"      {paths['json']}")
    print("=" * 66)

    if not args.quiet:
        for r in report["items"]:
            hits = ",".join(sorted((r["rule"] or {}).get("by_rule", {}))) or "-"
            print(f"  [{r['verdict']:<14}] {r['id']:<22} 规则={hits}")
            for reason in r["reasons"]:
                print(f"        - {reason}")

    if args.publish_metrics:
        reviewer.publish_metrics(report)
        print("已回填指标注册表（portal/measurements.json）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
