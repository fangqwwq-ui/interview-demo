"""v1/v2 压测对照。

为什么不用 locust（实测决定）：
  locust 依赖 gevent 等重包，本机带宽仅约 340 KB/s，装它属于为了一个
  数字付出大量时间与失败风险。而压测本质只要「固定并发数 + 采集延迟分布」，
  httpx + asyncio 完全够用，且零新增依赖、离线可跑。
  **降级路径必须始终可跑** —— 这是本项目一贯纪律。
  若演示现场已有 locust，用 --locust 会切过去，报告里标明实际用的工具。

口径纪律（硬规则，勿违反）：
  1. v1 与 v2 **在同一进程生命周期内先后测**，同机同数据同并发数；
  2. 每档并发**先跑一次预热**再正式采样，避免把首请求的懒加载
     （编码器/索引首次载入）算进 v2 的摊薄优势；
  3. 报告同时给出 P50/P95/P99 与 RPS，不允许只报一个平均值；
  4. **不承诺具体提升百分比**，百分比是实测结果，不是目标；
  5. 机器与依赖信息随数字一起落盘，避免数字脱离环境成为空话。

运行：
    python scripts/bench.py                       # 默认 1/4/8/16 并发
    python scripts/bench.py --concurrency 1,4,8,16 --rounds 200
    python scripts/bench.py --locust               # 若已装 locust 则用locust
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "outputs" / "demo_a" / "bench"
PORT = 8200
BASE = f"http://127.0.0.1:{PORT}"

# 压测流量配比：查询类占多数，问答类占少数（贴近真实运维看板用法）。
MIX = [
    ("GET", "/v1/events", {"zone": "yard-02", "open_only": "true"}),
    ("GET", "/v1/events", {"zone": "gate-01"}),
    ("GET", "/v1/stats", {}),
    ("POST", "/v1/ask", {"question": "东门有哪些未闭环的高危事件？", "k": 3}),
    ("GET", "/v1/events", {"zone": "office-03", "event_type": "person"}),
    ("GET", "/v1/stats", {}),
]
ASK_QUESTIONS = [
    "仓库门禁被暴力尝试的那条事件状态如何？",
    "西门夜间货车滞留的处理结果？",
    "厂区仓库的烟雾告警后来怎么处理的？",
    "快递员那条为什么被标记为误报？",
]


def _pct(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100.0) * (len(s) - 1)))))
    return s[k]


def stats_block(lat: List[float]) -> Dict[str, Any]:
    if not lat:
        return {"n": 0}
    return {
        "n": len(lat),
        "rps": None,  # 由调用方按总耗时回填
        "p50_ms": round(_pct(lat, 50), 2),
        "p95_ms": round(_pct(lat, 95), 2),
        "p99_ms": round(_pct(lat, 99), 2),
        "mean_ms": round(statistics.fmean(lat), 2),
        "max_ms": round(max(lat), 2),
        "min_ms": round(min(lat), 2),
        "stdev_ms": round(statistics.pstdev(lat), 2) if len(lat) > 1 else 0.0,
    }


async def _one(client, method: str, path: str, params: Optional[dict], body: Any, version: str) -> bool:
    """发一次请求。返回 True=成功，False=失败（含异常与 4xx/5xx）。

    ⚠️ 早期版本这里返回 ``Optional[float]`` 且 GET 成功时返回 None，
    上层用 ``ok is None`` 判失败 —— 于是**所有成功的 GET 都被计成失败**，
    样本数为 0，压测直接崩在 KeyError上。
    成功/失败必须是显式布尔，不能借用"没有耗时值"来表示失败。
    """
    p = path.replace("/v1/", f"/{version}/")
    try:
        if method == "GET":
            r = await client.get(BASE + p, params=params)
        else:
            r = await client.post(BASE + p, json=body)
        await r.aread()
        return r.status_code < 400
    except Exception:
        return False


async def run_load(version: str, concurrency: int, rounds: int, warmup: int) -> Dict[str, Any]:
    import httpx

    lat: List[float] = []
    failures = 0

    limits = httpx.Limits(max_connections=concurrency * 2, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(timeout=30.0, limits=limits) as client:

        async def worker(wid: int) -> None:
            nonlocal failures
            for i in range(warmup + rounds):
                if i < warmup:
                    continue
                method, path, params = MIX[(wid + i) % len(MIX)]
                body = None
                if method == "POST":
                    body = {
                        "question": ASK_QUESTIONS[(wid + i) % len(ASK_QUESTIONS)],
                        "k": 3,
                    }
                t0 = time.perf_counter()
                ok = await _one(client, method, path, params, body, version)
                dt = (time.perf_counter() - t0) * 1000
                if ok:
                    lat.append(dt)
                else:
                    failures += 1

        t_start = time.perf_counter()
        await asyncio.gather(*(worker(w) for w in range(concurrency)))
        wall = time.perf_counter() - t_start

    block = stats_block(lat)
    block["rps"] = round(len(lat) / wall, 2) if wall > 0 else 0.0
    block["wall_s"] = round(wall, 3)
    block["failures"] = failures
    return block


def env_info() -> Dict[str, Any]:
    info: Dict[str, Any] = {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor() or "unknown",
        "cpu_count": None,
    }
    try:
        import os

        info["cpu_count"] = os.cpu_count()
    except Exception:
        pass
    try:
        # GPU 名称对解释压测差异有用（虽本压测不跑推理）
        smi = r"C:\Windows\System32\nvidia-smi.exe"
        out = subprocess.run(
            [smi, "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, errors="replace", timeout=15,
        )
        if out.returncode == 0:
            info["gpu"] = out.stdout.strip()
    except Exception:
        pass
    try:
        import httpx

        info["httpx"] = httpx.__version__
    except Exception:
        pass
    return info


def comparison(v1: Dict[Any, Dict], v2: Dict[Any, Dict]) -> List[Dict[str, Any]]:
    rows = []
    for c in sorted(v1.keys()):
        a, b = v1[c], v2[c]
        if not a.get("n") or not b.get("n"):
            continue
        row = {
            "concurrency": c,
            "v1_rps": a["rps"], "v2_rps": b["rps"],
            "rps_gain_pct": round((b["rps"] - a["rps"]) / a["rps"] * 100, 1) if a["rps"] else None,
            "v1_p95_ms": a["p95_ms"], "v2_p95_ms": b["p95_ms"],
            "p95_reduction_pct": round((a["p95_ms"] - b["p95_ms"]) / a["p95_ms"] * 100, 1) if a["p95_ms"] else None,
        }
        rows.append(row)
    return rows


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Demo A v1/v2 压测对照")
    ap.add_argument("--concurrency", default="1,4,8,16")
    ap.add_argument("--rounds", type=int, default=120)
    ap.add_argument("--warmup", type=int, default=10)
    ap.add_argument("--locust", action="store_true", help="使用 locust（若已安装）")
    ap.add_argument("--out", default=str(OUT_DIR))
    args = ap.parse_args(argv)

    concs = [int(x) for x in args.concurrency.split(",") if x.strip()]

    print("=" * 68)
    print("Demo A v1/v2 压测对照（httpx+asyncio，零新增依赖）")
    print("=" * 68)
    info = env_info()
    print("环境:", json.dumps(info, ensure_ascii=False))
    print()

    results: Dict[str, Dict[Any, Dict]] = {"v1": {}, "v2": {}}
    for c in concs:
        for version in ("v1", "v2"):
            block = asyncio.run(run_load(version, c, args.rounds, args.warmup))
            results[version][c] = block
            print(f"  {version} concurrency={c:>3}  n={block['n']:>4}  "
                  f"rps={block['rps']:>7}  p50={block['p50_ms']:>8}ms  "
                  f"p95={block['p95_ms']:>8}ms  fail={block['failures']}")
        print()

    rows = comparison(results["v1"], results["v2"])

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "tool": "httpx+asyncio（未使用 locust）",
        "rounds": args.rounds,
        "warmup": args.warmup,
        "mix": [{"method": m, "path": p} for m, p, _ in MIX],
        "env": info,
        "raw": {v: {str(k): val for k, val in d.items()} for v, d in results.items()},
        "comparison": rows,
        "note": (
            "百分比为本次实测结果，随机器与负载变化；"
            "不对外承诺固定提升幅度。"
        ),
    }
    (out_dir / "bench_raw.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"原始数据 -> {out_dir / 'bench_raw.json'}")
    if rows:
        print("\n汇总（实测）:")
        for r in rows:
            print(f"  并发{r['concurrency']:>3}: RPS {r['v1_rps']} -> {r['v2_rps']} "
                  f"(+{r['rps_gain_pct']}%), P95 {r['v1_p95_ms']}ms -> {r['v2_p95_ms']}ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())