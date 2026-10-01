r"""Demo C 指标层命令行入口。

    python -m demo_c_manju.metrics                       # 用默认人工假设出报告
    python -m demo_c_manju.metrics --per-frame-minutes 2.0
    python -m demo_c_manju.metrics --per-frame-minutes 4 --frames 8
    python -m demo_c_manju.metrics --publish-metrics     # 回填指标注册表

设计意图：`--per-frame-minutes` 存在的原因就是让面试官**当场改参数**。
一个能被现场质疑并立即重算的基线，比一个漂亮的固定倍数可信得多。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

from demo_c_manju.metrics import compare
from demo_c_manju.metrics.baseline import ManualBaseline, default_baseline


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m demo_c_manju.metrics",
        description="Demo C 指标层：实测 vs 申报假设，出效率对比表",
    )
    parser.add_argument("--outputs-dir", default=None, help="实测运行所在目录（默认 outputs/demo_c）")
    parser.add_argument("--out", default=None, help="报告输出目录")
    parser.add_argument("--per-frame-minutes", type=float, default=None, help="人工单张出图耗时假设")
    parser.add_argument("--frames", type=int, default=None, help="每条样片分镜张数假设")
    parser.add_argument("--stage-log", default=None, help="真实阶段埋点 JSON（可选）")
    parser.add_argument("--sensitivity", default="1,2,3,4,6,8", help="敏感性分析的自变量取值")
    parser.add_argument("--publish-metrics", action="store_true", help="回填指标注册表")
    args = parser.parse_args(argv)

    baseline: ManualBaseline = default_baseline()
    changes = {}
    if args.per_frame_minutes is not None:
        changes["per_frame_minutes"] = args.per_frame_minutes
    if args.frames is not None:
        changes["frames_per_sample"] = args.frames
    if changes:
        baseline = replace(baseline, **changes)

    problems = baseline.validate()
    if problems:
        print("人工基线假设不合法：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 2

    runs = compare.load_measured_runs(Path(args.outputs_dir) if args.outputs_dir else None)
    runs = compare.flag_cache_hits(runs)
    img = compare.image_stage_stats(runs)

    stage_log = None
    if args.stage_log:
        stage_log = json.loads(Path(args.stage_log).read_text(encoding="utf-8"))

    cmp_ = compare.build_comparison(baseline, img, stage_log)
    values = [float(v) for v in args.sensitivity.split(",") if v.strip()]
    sensitivity = baseline.sensitivity(
        values,
        measured_warm_s=img.get("per_frame_warm_s"),
        measured_cold_s=img.get("per_frame_cold_s"),
    )

    report = {
        "generated_by": "demo_c_manju/metrics/__main__.py",
        "baseline": baseline.as_dict(),
        "image_stage": img,
        "comparison": cmp_,
        "sensitivity": sensitivity,
    }
    paths = compare.write_report(report, Path(args.out) if args.out else None)

    print("=" * 70)
    print("Demo C 指标层（效率口径）")
    print("-" * 70)
    if img.get("available"):
        rep_cfg = img["representative_config"]
        print(f"代表配置：{img['size']} / {img['steps']} 步（{rep_cfg}）")
        print(f"  口径：{img['definition']}")
        for label, st in (("冷启动（含模型加载）", img.get("cold")), ("稳态（模型常驻）", img.get("warm"))):
            if st:
                print(f"  {label}：均值 {st['mean_s']}s  中位 {st['median_s']}s  "
                      f"区间 {st['min_s']}–{st['max_s']}s  n={st['n']}")
                if st.get("variance_note"):
                    print(f"    ⚠️ {st['variance_note']}")
            else:
                print(f"  {label}：无样本")
        im = cmp_.get("image_model", {})
        if im.get("total_s"):
            print(f"  整条样片出图耗时模型：{im['formula']} = {im['total_s']}s"
                  f" ≈ {round(im['total_s'] / 60, 2)} 分钟")
        print("  按「配置 × 冷热」分组：")
        for c in img["configs"]:
            mark = " ← 代表配置" if c["config"] == img.get("representative_config") else ""
            for state in ("cold", "warm"):
                st = c["load_states"].get(state)
                if not st:
                    continue
                print(f"    {c['size']:>10} / {c['steps']:>2} 步  {state:<4} n={st['n']}  "
                      f"均值 {st['mean_s']:>7}s{mark if state == 'warm' else ''}")
        print("  注：不输出跨配置/跨冷热的平均耗时 —— 不同工作负载求平均没有意义。")
    else:
        print(f"出图实测：不可用 —— {img.get('reason')}")
    if img.get("runs_excluded_as_cache"):
        print(f"  已剔除缓存命中 {img['runs_excluded_as_cache']} 条"
              "（不剔除会让平均耗时虚低）")

    print(f"人工基线（申报假设，非实测）：{cmp_['declared_total_minutes']} 分钟/条")
    for a in report["baseline"]["assumptions"]:
        print(f"    {a['label']:<12} {a['minutes']:>6} 分钟   可信度={a['confidence']}")

    print(f"实测覆盖阶段：{cmp_['measured_stage_count']}/{cmp_['stage_count']}")
    if cmp_["partial_speedup_image_only"] is not None:
        print(f"仅出图阶段部分倍数：{cmp_['partial_speedup_image_only']}x"
              "  ← 分母是假设，且只覆盖一个阶段，不等于整体效率提升")
    print(f"整体效率倍数：不给（实测覆盖不足，{cmp_['measured_stage_count']}/{cmp_['stage_count']}）")
    print("-" * 70)
    print("敏感性（改单张人工耗时假设）：")
    for row in report["sensitivity"]["rows"]:
        extra = ""
        if row.get("image_speedup"):
            extra = f"   出图倍数 {row['image_speedup']}x"
        print(f"    {row['per_frame_minutes']:>4} 分钟/张 → 人工合计 "
              f"{row['total_minutes']:>7} 分钟/条{extra}")
    if report["sensitivity"].get("image_speedup_is_assumption_driven"):
        print(f"    ⚠️ {report['sensitivity']['assumption_dominance_note']}")
    print("-" * 70)
    print(f"报告：{paths['markdown']}")
    print(f"      {paths['csv']}")
    print(f"      {paths['json']}")
    print("=" * 70)

    if args.publish_metrics:
        compare.publish_metrics(report)
        print("已回填指标注册表（portal/measurements.json）")

    return 0


if __name__ == "__main__":
    sys.exit(main())
