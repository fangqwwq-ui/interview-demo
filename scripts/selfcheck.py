"""P0 自检：一键验证底座各层是否可用。

用法（在项目根目录执行）：
    .venv\\Scripts\\python.exe scripts\\selfcheck.py

设计意图：这份自检本身就是交付物的一部分 —— 面试现场可以跑一次，
证明「这套东西在我机器上是活的」，而不是只有 PPT。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PASS, FAIL, WARN = "[PASS]", "[FAIL]", "[WARN]"
results: list[tuple[str, str, str]] = []


def check(name: str, fn) -> None:
    try:
        status, detail = fn()
        results.append((name, status, detail))
    except Exception as exc:  # noqa: BLE001
        results.append((name, FAIL, f"{type(exc).__name__}: {exc}"))


# ------------------------------------------------------------------ 各项检查

def c_config():
    from common import config

    config.paths.ensure()
    return PASS, f"root={config.paths.root} | llm={config.llm.describe()}"


def c_logging():
    from common import logging as plog

    with plog.stage("自检阶段") as st:
        sum(range(1000))
        st["items"] = 1
    t = plog.timings()[-1]
    return PASS, f"{t['stage']} elapsed={t['elapsed_s']}s"


def c_kernel():
    from demo_b_copywriting import detector

    h = detector.health()
    if not h.get("ok"):
        return FAIL, h.get("error", "unknown")
    return PASS, f"mode={h['mode']} probe_score={h['probe_score']} level={h['probe_level']}"


def c_encoder():
    from common.embed import get_encoder

    enc = get_encoder("auto")
    vecs = enc.encode(["今天天气不错，适合出门散步。", "今天天气很好，适合散步。"])
    sim = float(vecs[0] @ vecs[1])
    tag = WARN if enc.info.is_fallback else PASS
    detail = f"backend={enc.info.backend} dim={enc.info.dim} sim={sim:.3f}"
    if enc.info.is_fallback:
        detail += "  <- 无语义能力，仅跑通链路"
    return tag, detail


def c_store():
    import numpy as np

    from common.vectorstore import get_store

    store = get_store(dim=8)
    vecs = np.zeros((3, 8), dtype=np.float32)
    vecs[0, 0] = 1.0
    vecs[1, 1] = 1.0
    vecs[2, 0] = 0.8
    vecs[2, 1] = 0.6
    store.add(vecs, [{"id": i} for i in range(3)])
    hits = store.search(np.array([1.0] + [0.0] * 7, dtype=np.float32), k=2)
    tag = WARN if store.info.is_fallback else PASS
    return tag, f"backend={store.info.backend} size={len(store)} top={[h.meta['id'] for h in hits]}"


def c_text_metrics():
    from common.metrics.text_metrics import scan_grammar_anomalies

    broken = "时间管理之于于每个人来说都，有着至关重要的意义。肯定的是，我们会一直一直坚持。"
    good = "时间管理对每个人都很重要，关键在于能不能坚持。"
    fb, fg = scan_grammar_anomalies(broken), scan_grammar_anomalies(good)
    if fb.count <= fg.count:
        return FAIL, f"规则集未能区分受损文本（broken={fb.count}, good={fg.count}）"
    return PASS, f"broken={fb.count} 处 / good={fg.count} 处，规则={list(fb.by_rule)}"


def c_registry():
    from common.metrics import registry

    p = registry.export_json()
    return PASS, f"{len(registry.METRICS)} 项指标 -> {p.name}；待实测 {len(registry.gaps())} 项"


def c_services():
    from common import config
    from common.clients import http as http_client

    out = []
    for label, url in (
        ("comfyui", config.services.comfyui_base_url + "/system_stats"),
        ("aigc-comic", config.services.aigc_comic_base_url + "/health"),
    ):
        r = http_client.probe(url)
        if r.get("ok"):
            out.append(f"{label}=up({r.get('status')})")
        else:
            out.append(f"{label}=down({r.get('status') or r.get('error', '')[:40]})")
    all_up = all("=up" in o for o in out)
    return (PASS if all_up else WARN), " ".join(out)


def c_llm_router():
    from common.llm.router import default_router

    r = default_router()
    h = r.health()
    cloud_ok = h["providers"]["cloud"].get("ok")
    local_ok = h["providers"]["local"].get("ok")
    if not cloud_ok and not local_ok:
        return WARN, f"两条通道均不可用（cloud={'no-key' if not cloud_ok else 'fail'}, local=down）"
    return PASS, f"mode={h['mode']} order={h['order']} cloud={cloud_ok} local={local_ok}"


CHECKS = [
    ("配置加载", c_config),
    ("阶段计时", c_logging),
    ("检测器（开源内核）", c_kernel),
    ("向量化后端", c_encoder),
    ("向量库后端", c_store),
    ("文本指标规则", c_text_metrics),
    ("指标口径注册表", c_registry),
    ("外部服务连通", c_services),
    ("LLM 混合路由", c_llm_router),
]


def main() -> int:
    print("=" * 74)
    print("P0 自检 —— 项目底座可用性")
    print("=" * 74)
    for name, fn in CHECKS:
        check(name, fn)

    width = max(len(n) for n, _, _ in results)
    for name, status, detail in results:
        print(f"{status} {name.ljust(width)}  {detail}")

    n_fail = sum(1 for _, s, _ in results if s == FAIL)
    n_warn = sum(1 for _, s, _ in results if s == WARN)
    print("-" * 74)
    print(f"合计 {len(results)} 项：通过 {len(results) - n_fail - n_warn}，警告 {n_warn}，失败 {n_fail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
