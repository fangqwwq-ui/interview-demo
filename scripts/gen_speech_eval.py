"""生成真实中文语音评测集（用 edge-tts 合成）。

为什么需要它
------------
`demo_a_security/asr.py` 此前只用**合成音调**验证链路，缺口径里写明
「不构成语音识别准确率证据」。要补上这个证据，就必须有**带标注的真实语音**。

真实录音本机没有，而 edge-tts（微软 Edge 的在线 TTS）实测**能联网合成**，
产出的就是真人音色中文语音。把它作为评测输入是诚实的：
  - 语音是真实合成的**语音信号**（非音调），ASR 面对的是真实的声学问题；
  - 文本是**已知的标准答案**，所以「识别对不对」可判定；
  - 但要说清边界：这是 **TTS 合成语音**，不是真人现场录音，
    信噪比、韵律、口音都比真人更"干净"，**识别率会偏高**，
    不可当作真人嘈杂环境下的性能依据。

用法（**必须用 workbuddy 默认解释器**，项目 venv 里没有 edge_tts）：
    C:/Users/18751/.workbuddy/binaries/python/envs/default/Scripts/python.exe \
        scripts/gen_speech_eval.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "outputs" / "demo_a" / "audio"
MANIFEST = OUT_DIR / "speech_eval.jsonl"

# 覆盖 Demo A 的三类真实问法：结构化过滤类、语义描述类、口语类。
# 文本刻意包含数字与专有名词（cam-01、A-0012、gate-01），
# 因为这类词最能暴露 ASR 的实际问题（字母数字混读）。
TEXTS: List[Dict[str, Any]] = [
    {"id": "S01", "text": "东门有哪些未闭环的高危事件？", "kind": "结构化过滤"},
    {"id": "S02", "text": "仓库门禁被暴力尝试的那条事件状态如何？", "kind": "语义描述"},
    {"id": "S03", "text": "厂区仓库的烟雾告警后来怎么处理的？", "kind": "语义描述"},
    {"id": "S04", "text": "西门夜间货车滞留的处理结果是什么？", "kind": "语义描述"},
    {"id": "S05", "text": "办公楼非白名单人员进入，现在是什么状态？", "kind": "语义描述"},
    {"id": "S06", "text": "哪些事件是误报？", "kind": "口语短问"},
    {"id": "S07", "text": "还没有闭环的事情有哪些？", "kind": "口语短问"},
    {"id": "S08", "text": "最高危险等级的事件是什么？", "kind": "结构化过滤"},
    {"id": "S09", "text": "摄像头零一记录了什么事件？", "kind": "数字混读"},
    {"id": "S10", "text": "快递员那条为什么被标记为误报？", "kind": "语义描述"},
    {"id": "S11", "text": "门被撬了吗？", "kind": "口语短问"},
    {"id": "S12", "text": "全场未闭环的高危事件请列出来。", "kind": "口语长句"},
]

# 两种音色，便于观察音色对识别的影响（不是必须，但成本极低）
VOICES = ["zh-CN-XiaoxiaoNeural", "zh-CN-YunxiNeural"]


async def synth_one(text: str, voice: str, out: Path, retries: int = 3) -> bool:
    """合成一条语音，失败重试。

    ⚠️ 必须重试：edge-tts 走网络，实测会偶发
    ``NoAudioReceived: No audio was received``（12 条里偶发 1 条）。
    一次失败就跳过会让评测集出现**随机缺项**，而缺项会让
    后续准确率的分母在不同机器上不一致 —— 结果不可比。
    """
    import edge_tts

    for attempt in range(retries):
        try:
            await edge_tts.Communicate(text, voice).save(str(out))
            if out.exists() and out.stat().st_size > 1000:
                return True
            print(f"  [retry{attempt + 1}] {out.name} 内容异常（{out.stat().st_size if out.exists() else 0} B）")
        except Exception as exc:  # noqa: BLE001
            print(f"  [retry{attempt + 1}] {type(exc).__name__}: {exc}")
        if attempt < retries - 1:
            await asyncio.sleep(3)  # 退避，避免立刻重试打同一接口
    return False


async def main() -> int:
    try:
        import edge_tts  # noqa: F401
    except ModuleNotFoundError:
        print(
            "本脚本需要 edge_tts，而它**不在项目 venv 里**。\n"
            "请用 workbuddy 默认解释器运行：\n"
            "  C:/Users/18751/.workbuddy/binaries/python/envs/default/Scripts/python.exe "
            "scripts/gen_speech_eval.py",
            file=sys.stderr,
        )
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    ok = 0
    # 每条文本用一个音色（交替），避免只测单一音色
    for i, item in enumerate(TEXTS):
        voice = VOICES[i % len(VOICES)]
        name = f"{item['id']}.mp3"
        p = OUT_DIR / name
        print(f"  合成 {item['id']} [{voice}] {item['text'][:24]}")
        if await synth_one(item["text"], voice, p):
            rows.append({
                "id": item["id"],
                "text": item["text"],
                "kind": item["kind"],
                "voice": voice,
                "audio": str(p.relative_to(ROOT)),
                "bytes": p.stat().st_size,
            })
            ok += 1
        else:
            print(f"  [SKIP] {item['id']}")

    MANIFEST.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    print(f"\n成功 {ok}/{len(TEXTS)}；清单 -> {MANIFEST}")
    print("下一步：python scripts/eval_asr.py")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
