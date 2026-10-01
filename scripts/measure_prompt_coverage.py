# -*- coding: utf-8 -*-
r"""度量中文提示词适配覆盖率（离线、确定性、不需要 GPU / 不需要服务在跑）。

为什么单独成为一个脚本，而不是挂在端到端脚本里：
  覆盖率是 **(词表 × 分镜提示词)** 的属性，不是某一次出图运行的属性。
  挂在端到端脚本上有三个坏处：
    ① 要有 GPU、要 ComfyUI 在跑、要 aigc-comic 在跑，才拿得到一个纯文本指标；
    ② 一次运行失败（例如合成步被杀）就连指标都拿不到；
    ③ 指标值会被「这次跑了几条」污染，不可比。
  离线度量则是纯确定性计算：同一份词表 + 同一份分镜，永远得到同一个数。

输入来源必须是**被适配方的构造函数**：
  上一轮的教训 —— 用自己构造的提示词验证，拿到「零残留」的漂亮结果；
  换成 aigc-comic 真实分镜后覆盖率立刻塌掉。因此这里调用它自己的
  `lib/image.py::build_prompt` 与 `lib/script.py::DEMO_STORYBOARD`。

用法：
  python scripts/measure_prompt_coverage.py
  python scripts/measure_prompt_coverage.py --publish-metrics
  python scripts/measure_prompt_coverage.py --comic-root E:\aigc-comic --json out.json
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_real_prompts(comic_root: Path) -> list[tuple[str, str]]:
    """(分镜 id, 该分镜的真实提示词)，由 aigc-comic 自己的构造函数生成。

    ⚠️ 返回后**立刻**把新增的 sys.path 条目留在原地（不清理）：
       aigc-comic 的 `lib` 是包名极常见的目录名，反复插拔容易和本仓库的模块撞名。
       本进程只做一件事，留着更稳。
    """
    sys.path.insert(0, str(comic_root))
    lib_image = importlib.import_module("lib.image")
    lib_script = importlib.import_module("lib.script")
    sb = lib_script.DEMO_STORYBOARD
    return [(s.id, lib_image.build_prompt(sb, s)) for s in sb.shots]


def main() -> int:
    ap = argparse.ArgumentParser(description="度量中文提示词适配覆盖率")
    ap.add_argument("--comic-root", default=r"E:\aigc-comic", help="既有服务根目录")
    ap.add_argument("--json", default="", help="把逐条结果另存为 JSON")
    ap.add_argument("--publish-metrics", action="store_true", help="回填到指标口径表")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    from demo_c_manju.a1111_shim import ZH_TAG_LEXICON, adapt_prompt

    pairs = _load_real_prompts(Path(args.comic_root))
    rows = []
    for shot_id, prompt in pairs:
        adapted, st = adapt_prompt(prompt)
        rows.append(
            {
                "shot": shot_id,
                "source_prompt": prompt,
                "adapted_prompt": adapted,
                "cjk_coverage": st["cjk_coverage"],
                "mapped": st["mapped"],
                "total_cjk_chars": st["total_cjk_chars"],
                "residual_cjk_chars": st["residual_cjk_chars"],
                "dropped_terms": st["dropped_terms"],
            }
        )

    covs = [r["cjk_coverage"] for r in rows]
    mean_cov = sum(covs) / len(covs)
    summary = {
        "measured_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "lexicon_size": len(ZH_TAG_LEXICON),
        "n_shots": len(rows),
        "cjk_coverage_mean": round(mean_cov, 4),
        "cjk_coverage_min": round(min(covs), 4),
        "cjk_coverage_max": round(max(covs), 4),
        "mapped_mean": round(sum(r["mapped"] for r in rows) / len(rows), 2),
        "source": "aigc-comic lib/image.py::build_prompt + lib/script.py::DEMO_STORYBOARD",
        "rows": rows,
    }

    if not args.quiet:
        print(f"词表 {len(ZH_TAG_LEXICON)} 条，分镜 {len(rows)} 条，"
              f"覆盖率均 {mean_cov:.1%}（{min(covs):.1%} ~ {max(covs):.1%}）\n")
        for r in rows:
            print(f"  {r['shot']:4s} cov={r['cjk_coverage']:6.1%}  "
                  f"mapped={r['mapped']:2d}  残留={r['residual_cjk_chars']:2d}/{r['total_cjk_chars']:2d}  "
                  f"dropped={r['dropped_terms']}")
        print()
        for r in rows:
            print(f"  {r['shot']} → {r['adapted_prompt'][:150]}")

    if args.json:
        p = Path(args.json)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[*] 已写出 {p}")

    if args.publish_metrics:
        from common.metrics import registry

        value = (
            f"既有服务真实分镜提示词，{len(rows)} 条（词表 {len(ZH_TAG_LEXICON)} 条）："
            f"中文覆盖率均 {mean_cov:.1%}（最低 {min(covs):.1%}，最高 {max(covs):.1%}）；"
            f"平均命中 {summary['mapped_mean']} 个词条"
        )
        registry.set_measured("c_prompt_adaptation_coverage", value)
        # set_measured 只写持久层；条目上的 status/measured 是**导出时**才合并进去的，
        # 所以这里必须再导出一次，否则门户读到的仍是 pending。
        # （registry 模块顶部的注释即为此设计：实测值唯一持久化在 measurements.json，
        #   export_json() 每次从它合并回 metrics.json。）
        out = registry.export_json()
        print(f"[*] 已回填 c_prompt_adaptation_coverage：{value}")
        print(f"[*] 已导出门户数据源：{out}")

    # 覆盖率为 0 说明词表/分镜对不上，属于必须失败的情况
    return 0 if mean_cov > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
