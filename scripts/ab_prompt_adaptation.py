# -*- coding: utf-8 -*-
r"""提示词适配层的作用 —— 受控对照实验（单变量拆分）。

背景（为什么要做这个实验）：
  端到端跑通 aigc-comic 后，出图与分镜语义不符（分镜要求「大学教室·抬头看黑板」，
  实际出来是抽象彩色背景）。定位后发现适配层有两处问题：
    ① 词表缺「时间/教室道具/神态/身份」等类别，关键限定词以中文原样进了提示词；
    ② 残留中文的处置方式不当。
  修完之后出图明显贴合分镜 —— 但**同一轮改了多个变量**，
  单看「改前 / 改后」两张图不能归因到具体某一项。
  本脚本把变量拆开，逐条对照，避免用一张好看的图讲一个无法证伪的故事。

三组对照（其余全部固定：seed / 尺寸 / 步数 / cfg / 采样器 / checkpoint / 负向词）：
  V0_raw       原文直接用，不做任何适配   —— 作为「不适配」的基线
  V1_keep      新词表 + **保留**残留中文   —— 隔离「扩词表」的作用
  V2_strip     新词表 + **剥离**残留中文   —— 隔离「剥残留」的作用（= shim 当前行为）

⚠️ 本实验**不产出自动化评分**：
   本机没有图文对齐模型（CLIP/BLIP 之类），用文本模型硬凑一个「语义贴合度」
   分数只会制造一个看起来客观、实际无意义的数字。
   因此这里只产出**可复现的产图**与**可量化的提示词统计**（覆盖率 / 中文残留字符数），
   图像层面的结论留给评审自行比对，不由脚本代为宣称。

用法：
  python scripts/ab_prompt_adaptation.py --shot s1 \
      --out outputs/demo_c_pipeline/ab_prompt
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


def _load_service_prompt(shot_id: str) -> str:
    """用 aigc-comic 自己的 build_prompt 生成提示词，避免我手搓一个「好看」的样本。

    ⚠️ 上一轮的教训：我最初用**自己构造**的提示词验证适配层，得到
       `unmapped_terms: []` 的漂亮结果；换成服务真实分镜后覆盖率立刻塌到 4~5 个词。
       所以这里必须调用对方的构造函数，而不是自己写测试输入。
    """
    comic = Path(r"E:\aigc-comic")
    sys.path.insert(0, str(comic))
    lib_image = importlib.import_module("lib.image")
    lib_script = importlib.import_module("lib.script")
    sb = lib_script.DEMO_STORYBOARD
    for shot in sb.shots:
        if shot.id == shot_id:
            return lib_image.build_prompt(sb, shot)
    raise SystemExit(f"未找到分镜 {shot_id}，可用：{[s.id for s in sb.shots]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="提示词适配层单变量对照实验")
    ap.add_argument("--shot", default="s1")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--width", type=int, default=768)
    ap.add_argument("--height", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--out", default="outputs/demo_c_pipeline/ab_prompt")
    args = ap.parse_args()

    from demo_c_manju.a1111_shim import adapt_prompt
    from demo_c_manju.comfy_adapter import generate

    raw = _load_service_prompt(args.shot)

    v1_text, v1_stats = adapt_prompt(raw, strip_residual=False)
    v2_text, v2_stats = adapt_prompt(raw, strip_residual=True)

    variants = [
        ("V0_raw", raw, {"mode": "不适配（原文直用）"}),
        ("V1_keep", v1_text, {"mode": "新词表 + 保留残留", **_pick(v1_stats)}),
        ("V2_strip", v2_text, {"mode": "新词表 + 剥离残留（shim 当前行为）", **_pick(v2_stats)}),
    ]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []

    print(f"源提示词：{raw}\n")
    for name, prompt, meta in variants:
        print(f"[*] {name} …")
        res = generate(
            prompt,
            checkpoint="animagine-xl-4.0.safetensors",
            width=args.width,
            height=args.height,
            steps=args.steps,
            cfg=7.0,
            seed=args.seed,
            quality_tags=False,   # 服务自带风格词，不叠加
            save_dir=out_dir / name,
        )
        rec = {
            "variant": name,
            **meta,
            "prompt_sent": prompt,
            "ok": res.ok,
            "error": res.error,
            "elapsed_s": res.elapsed_s,
            "seed": res.seed,
            "size": f"{res.width}x{res.height}",
            "steps": res.steps,
        }
        if res.ok and res.images:
            from demo_c_manju.imgsize import bytes_per_pixel

            blob = Path(res.images[0].local_path).read_bytes()
            rec["image"] = res.images[0].local_path
            rec["bytes"] = len(blob)
            rec["bytes_per_px"] = round(bytes_per_pixel(blob) or 0, 4)
            rec["sha256_12"] = __import__("hashlib").sha256(blob).hexdigest()[:12]
        records.append(rec)
        print(f"    ok={res.ok} {res.elapsed_s:.2f}s  {rec.get('bytes_per_px')} B/px  sha={rec.get('sha256_12')}")

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "shot": args.shot,
        "source_prompt": raw,
        "fixed_params": {
            "checkpoint": "animagine-xl-4.0.safetensors",
            "seed": args.seed,
            "size": f"{args.width}x{args.height}",
            "steps": args.steps,
            "cfg": 7.0,
        },
        "note": (
            "本实验只固定参数、产出对照图与提示词统计，**不给出自动化语义评分**："
            "本机无图文对齐模型，硬凑分数会制造一个看似客观实则无意义的数字。"
        ),
        "variants": records,
    }
    (out_dir / "ab_result.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\n[*] 结果：{out_dir / 'ab_result.json'}")
    return 0


def _pick(stats: dict) -> dict:
    """只保留可比的量化字段，避免把整份统计塞进结果。"""
    return {
        "cjk_coverage": stats["cjk_coverage"],
        "mapped": stats["mapped"],
        "residual_cjk_chars": stats["residual_cjk_chars"],
        "total_cjk_chars": stats["total_cjk_chars"],
        "dropped_terms": stats["dropped_terms"],
    }


if __name__ == "__main__":
    raise SystemExit(main())
