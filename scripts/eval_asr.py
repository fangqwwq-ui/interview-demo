"""Demo A 语音识别准确率评测（真实中文语音）。

数据来源
--------
`synth_speech.py`（或 `gen_speech_eval.py`）用 edge-tts 合成的中文语音，
**带标准答案标注**。这是本项目目前唯一有标注的真实语音来源。

判定口径（严格，不含糊）
------------------------
用**字符错误率 CER** 与**整句正确率 SRA** 两个指标，不用「意思差不多就算对」：

  CER = (替换 + 删除 + 插入) / 标准答案字符数
  整句正确 = 识别结果规范化后与标准答案**完全一致**（严格相等）

规范化做三件事：去标点空白、**繁体转简体**、不做同义改写。
繁简必须归一的理由：whisper 输出常是繁体而标注是简体，
不归一会把「内容全对」的样本判成错（实测 S03 内容 100% 正确却判 DIFF）。
**这不是放宽标准** —— 「快递员」→「快递援」这类同音错字仍会被判错。

为什么不用语义相似度打分：
  1. 没有可复现的自动评分模型，用模型给自己打分不可信；
  2. 严格相等虽然保守，但**可人工核对、可争论**；
  3. 同时给 CER 与整句正确率，既能看出「差多少」也能看出「能不能直接用」。

⚠️ 口径边界（必须一并汇报，不能只报好看的数字）：
  - 语音是 **TTS 合成**的真人音色，不是现场真人录音。
    合成语音信噪比高、韵律规整、**没有方言口音与背景噪声**，
    因此这里的识别率**系统性偏高**，不能当作真人嘈杂场景的性能依据。
  - 只用 tiny/small 等小模型；未评测大模型。
  - 12 条评测集规模小，**置信区间宽**，适合做回归护栏而非对外宣称指标。

运行：
    python scripts/eval_asr.py                       # 默认 tiny
    python scripts/eval_asr.py --size small
    python scripts/eval_asr.py --publish-metrics
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

MANIFEST = ROOT / "outputs" / "demo_a" / "audio" / "speech_eval.jsonl"
OUT_DIR = ROOT / "outputs" / "demo_a"

# 中文语音里的标点与语气词，规范化时统一去掉，避免
# 「，」的有无被判成错误（那是标点差异，不是识别错误）。
_PUNCT = re.compile(r"[\s，。？！、；：""''（）《》\-—…·,.?!;:\"'()\[\]]+")

# 繁简归一：whisper 的输出常是繁体（「廠區倉庫」），而标注是简体（「厂区仓库」）。
# 不归一会把「内容全对」的样本判成错 —— 实测S03 内容 100% 正确却判 DIFF，
# 只因繁简差异。这是**判定工具的缺陷**，不是 ASR 的问题。
# ⚠️ 口径必须明确记录：这里把「繁简不同但语义相同」视为**识别正确**。
#    依据：Demo A 的下游是 RAG 检索（基于 bge 向量），而 bge 对
#    「廠區倉庫」与「厂区仓库」的embedding 几乎一致，因此繁简差异
#    在本项目的链路里**不影响可用性**。若下游是逐字比对则不能这样判。
_T2S = None
try:
    from opencc import OpenCC

    _T2S = OpenCC("t2s")
except Exception:  # noqa: BLE001
    print("[warn] opencc 不可用，繁简差异将计入 CER（会低估准确率）。"
          "安装：pip install opencc-python-reimplemented --no-cache-dir "
          "-i https://mirrors.aliyun.com/pypi/simple/")


def normalize(s: str) -> str:
    """去标点与空白 + 繁转简，只留实义字符。"""
    s = s or ""
    if _T2S is not None:
        s = _T2S.convert(s)
    return _PUNCT.sub("", s)


def levenshtein(a: str, b: str) -> int:
    """标准编辑距离（滚动数组，O(min(m,n)) 空间）。"""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def cer(ref: str, hyp: str) -> float:
    """字符错误率。ref 为空时返回 0（避免除零）。"""
    ref_n, hyp_n = normalize(ref), normalize(hyp)
    if not ref_n:
        return 0.0
    return levenshtein(ref_n, hyp_n) / len(ref_n)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Demo A 语音识别准确率评测")
    ap.add_argument("--size", default="tiny",
                    choices=["tiny", "base", "small", "medium", "large-v3"])
    ap.add_argument("--manifest", default=str(MANIFEST))
    ap.add_argument("--publish-metrics", action="store_true", help="回填 registry")
    args = ap.parse_args(argv)

    manifest = Path(args.manifest)
    if not manifest.exists():
        print(f"未找到语音清单：{manifest}\n"
              f"请先运行：\n"
              f"  C:/Users/18751/.workbuddy/binaries/python/envs/default/Scripts/python.exe "
              f"scripts/gen_speech_eval.py", file=sys.stderr)
        return 2

    rows: List[Dict[str, Any]] = [
        json.loads(l) for l in manifest.read_text(encoding="utf-8").splitlines() if l.strip()
    ]
    print("=" * 70)
    print(f"Demo A 语音识别准确率评测（模型 {args.size}，{len(rows)} 条真实中文语音）")
    print("=" * 70)
    print("⚠️ 语音为 edge-tts 合成，识别率会系统性偏高，不代表真人嘈杂场景")
    print()

    from demo_a_security.asr import load_asr

    t0 = time.perf_counter()
    h = load_asr(args.size, device="cpu")
    load_s = time.perf_counter() - t0
    print(f"模型：{args.size} / device={h.info.device} / compute_type={h.info.compute_type}")
    print(f"加载耗时：{load_s:.2f} s\n")

    results: List[Dict[str, Any]] = []
    for r in rows:
        audio = ROOT / r["audio"]
        tr = h.transcribe(str(audio), language="zh")
        c = cer(r["text"], tr.text)
        exact = normalize(tr.text) == normalize(r["text"])
        results.append({
            "id": r["id"], "kind": r["kind"], "ref": r["text"], "hyp": tr.text,
            "cer": round(c, 4), "exact": exact, "latency_ms": round(tr.latency_ms, 1),
            "audio_duration_s": tr.audio_duration_s,
        })
        mark = "OK  " if exact else ("~   " if c <= 0.25 else "DIFF")
        print(f"  [{mark}] {r['id']} CER={c:.3f} {tr.latency_ms:6.0f}ms")
        if not exact:
            print(f"         标准: {r['text']}")
            print(f"         识别: {tr.text}")

    h.close()

    n = len(results)
    exact_n = sum(1 for r in results if r["exact"])
    mean_cer = sum(r["cer"] for r in results) / n if n else 0.0
    print(f"\n{'-' * 70}")
    print(f"整句正确率（SRA，严格相等）：{exact_n}/{n} = {exact_n / n:.4f}")
    print(f"平均字符错误率（CER）：      {mean_cer:.4f}")
    print(f"平均推理耗时：              {sum(r['latency_ms'] for r in results) / n:.0f} ms")
    print(f"总音频时长：                {sum(r['audio_duration_s'] for r in results):.1f} s")

    by_kind: Dict[str, List[float]] = {}
    for r in results:
        by_kind.setdefault(r["kind"], []).append(r["cer"])
    print("\n分组（看是哪类问法最容易错）：")
    for k, v in sorted(by_kind.items(), key=lambda x: sum(x[1]) / len(x[1])):
        ex = sum(1 for r in results if r["kind"] == k and r["exact"])
        tot = sum(1 for r in results if r["kind"] == k)
        print(f"  {k:12s} CER={sum(v) / len(v):.4f}  整句正确 {ex}/{tot}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "asr_eval_result.json"
    out.write_text(json.dumps({
        "model": args.size, "device": h.info.device, "n": n,
        "sra": round(exact_n / n, 4), "mean_cer": round(mean_cer, 4),
        "load_s": round(load_s, 2),
        "results": results,
        "by_kind": {k: {"cer": round(sum(v) / len(v), 4),
                        "n": sum(1 for r in results if r["kind"] == k)}
                    for k, v in by_kind.items()},
        "caveat": (
            "语音为 edge-tts 合成（真人音色），信噪比高、无方言与背景噪声，"
            "识别率系统性偏高；12 条样本量小，置信区间宽。"
            "适合做回归护栏，不作为真人场景性能依据。"
            "判定口径：去标点 + 繁简归一后严格相等；同音错字（如 快递员->快递援）"
            "仍判错，未使用语义相似度打分。"
        ),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果 -> {out}")

    if args.publish_metrics:
        from common.metrics import registry
        registry.set_measured(
            "a_asr_accuracy",
            f"整句正确率 {exact_n}/{n} = {exact_n / n:.4f}；平均 CER {mean_cer:.4f}"
            f"（模型 {args.size}，{h.info.device}/{h.info.compute_type}）",
            gap_note=(
                f"12 条 edge-tts 合成中文语音（真人音色，两种音色交替），"
                f"平均推理 {sum(r['latency_ms'] for r in results) / n:.0f} ms，"
                f"模型加载另计 {load_s:.2f} s不混算。"
                "⚠️ 口径边界：合成语音信噪比高、无方言口音与背景噪声，"
                "**识别率系统性偏高**，不可作为真人嘈杂场景的性能依据；"
                "样本量小（n=12）置信区间宽，适合做回归护栏。"
                "判定用严格相等（SRA）+ 字符错误率（CER），未使用语义相似度打分。"
            ),
        )
        registry.export_json()
        print(f"已回填 registry：a_asr_accuracy")

    return 0


if __name__ == "__main__":
    sys.exit(main())
