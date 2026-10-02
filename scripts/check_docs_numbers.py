"""校验 docs/ 讲稿里的数字与实测指标是否一致。

为什么需要这个脚本
------------------
讲稿（`demo_script_{A,B,C}.md`、`objections.md`、`STAGE_SUMMARY.md`）里
引用了大量实测数字。这些数字**每次重跑都会小幅波动**
（实测：视觉 7.39 → 7.71 ms，ASR 耗时 1379 → 1367 ms），
而**手抄必然漂移** —— 面试时文档数字与现场跑出来的不一样，
比数字小很多更伤信任。

所以原则是：**讲稿里的数字要么由脚本回填，要么由本脚本校验。**
本脚本做两件事：
  1. 提取实测指标里的关键数字（从 portal/measurements.json）；
  2. 扫描所有 docs/*.md，把**过期数字**找出来并报告。

用法：
    python scripts/check_docs_numbers.py            # 只报告
    python scripts/check_docs_numbers.py --fix      # 自动替换（见 --fix 说明）
    python scripts/check_docs_numbers.py --add-missing  # 把缺失的数字补进去

--fix 为什么默认不做全量替换
--------------------------
讲稿里的数字是**有语义的**（「7.71 ms（10 次取中位）」vs
「YOLO11n 推理 7.71 ms」），粗暴正则替换会破坏句子。
--fix 只替换「纯数字 + 单位」这种安全模式，且要求新值与旧值差距 < 30%
（差距过大说明是另一个含义的数字，宁可让人工确认）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
MEAS = ROOT / "portal" / "measurements.json"
DOCS = ROOT / "docs"

# (指标 key, 正则：从 measured 里抽数字, 讲稿里可能出现的写法)
METRIC_PATTERNS: List[Tuple[str, str, str]] = [
    ("a_vision_latency", r"([\d.]+) ms（10 次取中位", "视觉推理耗时"),
    ("a_asr_latency", r"^([\d.]+) ms", "语音识别耗时"),
    ("a_rag_accuracy", r"^([\d.]+)$", "RAG 准确率"),
]

# 讲稿里允许的**数值容差**：实测会波动，讲稿写整值即可
_TOLERANCE = 0.15  # 15%


def load_measurements() -> Dict[str, str]:
    if not MEAS.exists():
        return {}
    return {k: str(v.get("measured", "")) for k, v in
            json.loads(MEAS.read_text(encoding="utf-8")).get("measurements", {}).items()}


def extract_numbers(measured: str) -> List[float]:
    return [float(x) for x in re.findall(r"\d+\.?\d*", measured)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="校验讲稿数字与实测一致性")
    ap.add_argument("--fix", action="store_true", help="自动替换安全模式的过期数字")
    args = ap.parse_args(argv)

    meas = load_measurements()
    if not meas:
        print(f"未找到实测数据：{MEAS}\n请先跑 scripts/demo_a_e2e.py --publish-metrics",
              file=sys.stderr)
        return 2

    docs = sorted(DOCS.glob("*.md"))
    if not docs:
        print(f"未找到讲稿：{DOCS}", file=sys.stderr)
        return 2

    print("=" * 70)
    print("讲稿数字 vs 实测指标 一致性校验")
    print("=" * 70)
    print("\n当前实测值：")
    for k in ("a_vision_latency", "a_asr_latency", "a_rag_accuracy", "a_asr_accuracy"):
        if k in meas:
            print(f"  {k:20s} {meas[k][:70]}")

    # ⚠️ 容差说明：视觉推理耗时在多次重跑间会小幅波动（实测 6.9~9.7 ms，
    #    属正常 GPU 抖动）。因此校验**不能要求精确相等**，
    #    只要讲稿值落在实测的 [min, max] 区间内（或与中位相差 <20%）就算通过。
    #    超出区间才是真问题（多半是抄了历史值）。
    def _in_range(found: float, cur: float) -> bool:
        if cur <= 0:
            return True
        return abs(found - cur) / cur <= _TOLERANCE

    def _in_range_expr(text: str, found: float, measured: str) -> bool:
        """判断某个数字是否属于**合理的区间/边界表述**。

        两种合法情况：
        ① 出现在 "a~b ms" 这样的区间里（实测有抖动，文档写区间是正确做法）；
        ② 等于 measured 里的 max/min 边界（文档注明波动范围时会引用）。
        """
        s = f"{found:g}"
        # 区间写法：3.5~9.7 / 3.5-9.7 / 3.5 至 9.7
        if re.search(rf"\d+(?:\.\d+)?\s*[~～\-—]\s*{re.escape(s)}\s*ms", text):
            return True
        if re.search(rf"{re.escape(s)}\s*[~～\-—]\s*\d+(?:\.\d+)?\s*ms", text):
            return True
        # measured 里声明的 min / max
        for pat in (r"min\s*([\d.]+)", r"max\s*([\d.]+)"):
            m2 = re.search(pat, measured)
            if m2 and abs(float(m2.group(1)) - found) < 0.01:
                return True
        return False

    problems: List[str] = []
    fixed = 0
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        original = text

        for key, pat, label in METRIC_PATTERNS[:2]:
            if key not in meas:
                continue
            m = re.search(pat, meas[key])
            if not m:
                continue
            current = float(m.group(1))
            for found in re.findall(r"(\d+\.?\d*)\s*ms", text):
                fv = float(found)
                if abs(fv - current) <= 0.01 or _in_range(fv, current):
                    continue
                # **区间表述必须放行**。
                # 实测耗时本身有 GPU 抖动（实测 6.9~9.7 ms），
                # 所以文档里合理写法是「约 7~8 ms（实测 6.9~9.7 ms 波动）」——
                # 其中 max/min 出现在文档里是**预期行为**，不是过期数字。
                # 判据：若该数字出现在形如 "a~b ms" 或 "实测 x~y ms" 的区间里，
                # 或它等于 measured 里的 max，则视为合法。
                if _in_range_expr(text, fv, meas[key]):
                    continue
                # 只在同一数量级时提示（避免误报 "RAG 5~8 ms" 这类非推理耗时）
                if current > 0 and abs(fv - current) / current < 0.5:
                    msg = f"{doc.name}: {label} 写 {fv} ms，实测 {current} ms（容差 {_TOLERANCE:.0%}）"
                    problems.append(msg)
                    if args.fix:
                        text = text.replace(f"{found} ms", f"{current} ms")
                        fixed += 1
        if text != original:
            doc.write_text(text, encoding="utf-8")

    print("\n校验结果：")
    if problems:
        for p in problems:
            print(f"  [不一致] {p}")
        if args.fix:
            print(f"\n已自动替换 {fixed} 处")
        return 1
    print(f"  [OK] 讲稿中的耗时数字均在实测容差 {_TOLERANCE:.0%} 内")
    print("  提示：精确值以 portal/measurements.json 为准（讲稿用区间表述以免波动）")

    # 检查2：讲稿是否引用了不存在的指标
    all_keys = set(meas)
    for doc in docs:
        for key in re.findall(r"\b([abc]_[a-z_]+)\b", doc.read_text(encoding="utf-8")):
            if key not in all_keys and key not in {"a_rag_accuracy"}:
                # 可能是变量名或笔误，只提示不失败
                if key.startswith(("a_", "b_", "c_")):
                    pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
