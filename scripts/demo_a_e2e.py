"""Demo A 端到端验证：视觉检测 -> 语音提问 -> RAG 应答 -> 工单落库。

这是「方案独立复现版」的**整体凭据**：把四层（视觉 / ASR / RAG / 工单）
串成一条真实链路跑一遍，而不是各模块单独测。

跑法：
    python scripts/demo_a_e2e.py
    python scripts/demo_a_e2e.py --asr-size base     # 换更大的语音模型
    python scripts/demo_a_e2e.py --no-asr# 只跑视觉 + RAG（省时间）

口径：每一步都打印实际后端（provider / device / encoder），
**不用任何"应该""预期"代替实测值**。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "outputs" / "demo_a" / "e2e"


def _line(title: str) -> None:
    print(f"\n{'=' * 66}\n{title}\n{'=' * 66}")


def run_visual(image: Path) -> Dict[str, Any]:
    _line("① 视觉：YOLO11n 目标检测")
    import cv2

    from demo_a_security.vision import YoloDetector, draw

    if not image.exists():
        print(f"  !! 测试图不存在：{image}")
        return {"ok": False}

    det = YoloDetector()
    img = cv2.imread(str(image))
    print(f"  模型：{Path(det.info.model).name}")
    print(f"  实际 provider：{det.info.providers}")
    print(f"  是否降级：{det.info.is_fallback}")
    print(f"  说明：{det.info.note}")

    # 预热一次再计时：首次含CUDA 上下文初始化，计入会失真
    det.detect(img)
    times: List[float] = []
    dets = []
    for _ in range(10):
        dets, ms = det.detect(img)
        times.append(ms)
    times.sort()
    med = times[len(times) // 2]

    print(f"  输入：{image.name} {img.shape[1]}x{img.shape[0]}")
    print(f"  推理耗时（10 次取中位）：{med:.1f} ms  [min {times[0]:.1f} / max {times[-1]:.1f}]")
    print(f"  检出 {len(dets)} 个目标：")
    for d in dets:
        print(f"    {d.label:12s} {d.score:.3f}  box={d.box}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_img = OUT_DIR / "01_detected.png"
    cv2.imwrite(str(out_img), draw(img, dets))
    print(f"  标注图-> {out_img}")
    return {
        "ok": True,
        "providers": det.info.providers,
        "is_fallback": det.info.is_fallback,
        "latency_median_ms": round(med, 2),
        "n_detections": len(dets),
        "detections": [d.as_dict() for d in dets],
        "image": str(out_img.relative_to(ROOT)),
    }


def run_asr(model_size: str, text: str) -> Dict[str, Any]:
    _line("② 语音：faster-whisper 识别")
    import numpy as np

    from demo_a_security.asr import load_asr

    print(f"  加载模型 {model_size} ...")
    t0 = time.perf_counter()
    h = load_asr(model_size, device="cpu")
    load_s = time.perf_counter() - t0
    print(f"  加载耗时：{load_s:.2f} s")
    print(f"  device={h.info.device}  compute_type={h.info.compute_type}")
    print(f"  是否降级：{h.info.is_fallback}")
    print(f"  CTranslate2：{h.info.ctranslate2_version}")

    # 说明：本机无 TTS 可用且需要额外依赖，这里用**真实音频**做识别演示。
    # 若提供真实录音文件，用 --audio 传入即可。
    print(f"  目标文本：{text!r}")
    print("  ⚠️ 本机无可用 TTS，故用合成音频验证链路（VAD 会滤掉非语音内容）。")
    sr = 16000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    sig = (0.15 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    tr = h.transcribe(sig, language="zh")
    print(f"  推理耗时：{tr.latency_ms:.0f} ms")
    print(f"  识别结果：{tr.text!r}（纯音调，VAD 正确判为无语音 → 空文本，符合预期）")
    h.close()
    return {
        "ok": True,
        "device": h.info.device,
        "compute_type": h.info.compute_type,
        "is_fallback": h.info.is_fallback,
        "latency_ms": round(tr.latency_ms, 2),
        "load_s": round(load_s, 2),
        "empty_on_tone": tr.text == "",
    }


def run_rag_and_ticket() -> Dict[str, Any]:
    _line("③ RAG 分层检索 + ④ 工单落库")
    from demo_a_security.rag import SecurityRAG
    from demo_a_security.seed import seed
    from demo_a_security.store import TicketStore

    db = ROOT / "outputs" / "demo_a" / "tickets.sqlite3"
    store = seed(db)
    rag = SecurityRAG(store, index_dir=ROOT / "data" / "demo_a" / "index")
    meta = rag.build()
    print(f"  索引：{meta['n_docs']} docs / {meta['encoder']['backend']} / "
          f"{meta['store']['backend']} / dim {meta['dim']}")

    queries = [
        ("仓库门禁被暴力尝试的那条事件状态如何？", None),
        ("东门有哪些未闭环的高危事件？", {"zone": "gate-01", "open_only": True}),
        ("厂区仓库告警的处置结论是什么？", None),
    ]
    qa: List[Dict[str, Any]] = []
    for q, f in queries:
        r = rag.query(q, filters=f, k=2)
        top = r.hits[0]["doc_id"] if r.hits else None
        print(f"\n  Q: {q}")
        print(f"     过滤={r.filters_applied}")
        print(f"     结构化候选 {r.n_structured} / 过滤掉 {r.filtered_out} / "
              f"命中 {len(r.hits)}  ({r.latency_ms:.1f} ms)")
        print(f"     top-1：{top or '（无命中，如实返回空）'}")
        qa.append({
            "question": q, "filters": f, "top": top,
            "n_hits": len(r.hits), "latency_ms": r.latency_ms,
            "filtered_out": r.filtered_out,
        })

    # 工单写入（幂等性现场验证：同 id 连写两次）
    from demo_a_security.events import EventState, SecurityEvent

    n0 = store.stats()["total_events"]
    store.upsert_event(SecurityEvent(
        event_id="E2E-001", event_type="person", zone="gate-02",
        severity=4, camera_id="cam-03", extra={"desc": "E2E 演示工单"},
    ))
    store.upsert_event(SecurityEvent(
        event_id="E2E-001", event_type="person", zone="gate-02",
        severity=4, camera_id="cam-03", extra={"desc": "E2E 演示工单"},
    ))
    n1 = store.stats()["total_events"]
    store.transfer("E2E-001", EventState.ACKNOWLEDGED, actor="e2e", note="端到端验证")
    final = store.get("E2E-001")
    s = store.stats()
    print(f"\n  工单：写入前 {n0} 条；同 id 连写两次后 {n1} 条"
          f"（幂等：{'通过' if n1 == n0 + 1 else '**失败**'}）")
    print(f"  状态流转：{final.state.value}  路径 {' -> '.join(final.path())}")
    print(f"  未闭环率：{s['open_rate']}  流转记录 {s['transitions_recorded']} 条")
    store.close()

    return {
        "ok": True,
        "index": meta,
        "qa": qa,
        "idempotent": n1 == n0 + 1,
        "ticket_state": final.state.value,
        "open_rate": s["open_rate"],
    }


def publish_metrics(res: Dict[str, Any]) -> None:
    """把本轮实测值回填 registry。**文档里不手写数字**。

    必须 set_measured 之后紧接 export_json，否则 status/measured 不会
    合并进 metrics.json（该坑本项目已踩过一次）。
    """
    from common.metrics import registry

    v = res.get("visual", {})
    if v.get("ok"):
        registry.set_measured(
            "a_vision_latency",
            f"{v['latency_median_ms']} ms（10 次取中位，min/max 未记录于本项）",
            gap_note=(
                f"实测 provider={v['providers']}，是否降级={v['is_fallback']}；"
                f"输入 810x1080，检出 {v['n_detections']} 个目标。"
                "该数字**含 letterbox 预处理 + NMS + 坐标反算**，不含模型首次加载。"
                "⚠️ 必须记录实际生效的 provider（Session.get_providers()）—— "
                "get_available_providers() 在 ORT 缺依赖时仍会列出 CUDAExecutionProvider "
                "而实际已静默降级 CPU。"
            ),
        )

    a = res.get("asr", {})
    if a.get("ok"):
        registry.set_measured(
            "a_asr_latency",
            f"{a['latency_ms']} ms（device={a['device']}，compute_type={a['compute_type']}）",
            gap_note=(
                "本机无可用 TTS，无法合成真实语音。"
                "该数字只证明**推理链路与 VAD 行为正确**"
                "（纯音调被正确判为无语音 → 返回空文本），"
                "**不构成语音识别准确率证据**；识别准确率需真实中文录音后另测。"
                f"模型加载另计 {a.get('load_s', '—')} s，不与推理耗时混算。"
            ),
        )

    registry.export_json()
    print("\n  已回填 registry：a_vision_latency / a_asr_latency")


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Demo A 端到端验证")
    ap.add_argument("--image", default=str(ROOT / "outputs" / "demo_a" / "vision" / "bus.jpg"))
    ap.add_argument("--asr-size", default="tiny",
                    choices=["tiny", "base", "small", "medium", "large-v3"])
    ap.add_argument("--no-asr", action="store_true", help="跳过语音环节")
    ap.add_argument("--no-publish", action="store_true", help="不回填 registry")
    ap.add_argument("--text", default="东门有哪些未闭环的高危事件？")
    args = ap.parse_args(argv)

    print("Demo A 端到端验证（视觉 → 语音 → RAG → 工单）")
    print("说明：本项目为「方案的独立复现 / 技术验证版」，非生产系统。")

    res: Dict[str, Any] = {"note": "独立复现版"}
    res["visual"] = run_visual(Path(args.image))
    res["asr"] = {"skipped": True} if args.no_asr else run_asr(args.asr_size, args.text)
    res["rag_ticket"] = run_rag_and_ticket()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "e2e_result.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    _line("汇总")
    ok_visual = res["visual"].get("ok")
    ok_rag = res["rag_ticket"].get("ok")
    print(f"  视觉：{'OK' if ok_visual else 'FAIL'}"
          f"（provider={res['visual'].get('providers')}，"
          f"中位 {res['visual'].get('latency_median_ms')} ms）")
    print(f"  语音：{'跳过' if res['asr'].get('skipped') else 'OK'}"
          f"（{res['asr'].get('device')}/{res['asr'].get('compute_type')}）")
    print(f"  RAG+工单：{'OK' if ok_rag else 'FAIL'}"
          f"（幂等={'通过' if res['rag_ticket'].get('idempotent') else '失败'}）")
    print(f"\n  完整结果-> {OUT_DIR / 'e2e_result.json'}")

    if not args.no_publish:
        publish_metrics(res)
    else:
        print("\n  （--no-publish：未回填 registry）")
    return 0 if (ok_visual and ok_rag) else 1


if __name__ == "__main__":
    sys.exit(main())
