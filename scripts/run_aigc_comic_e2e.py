# -*- coding: utf-8 -*-
r"""端到端跑通既有服务 aigc-comic 的 /run，并把全过程耗时按阶段打点。

为什么需要这个脚本（而不是直接 curl）：
  /run 是**同步**接口，整个过程（分镜→出图→配音→合成）要 1~2 分钟。
  本脚本用一个足够长的超时把响应等回来，同时记录墙钟耗时，
  以便和 Demo C 出图适配器自己测得的单帧耗时做交叉验证。

定位说明：
  这条链路的「pipeline 状态机 + 断点续跑」是 aigc-comic 的**既有能力**，
  本次 Demo 只做「接入与复现」，不声称自研。
  本次新增的是它下游的 a1111_shim（把 ComfyUI 伪装成 A1111 协议），
  使既有代码零改动即可拿到真实出图。

⚠️ 本机已知陷阱（首次失败必看）：
  `aigc-comic` 的 `lib/compose.py` 在合成结束后会 `Path(p).unlink()` 清理自己的
  临时分片，这是正当代码。但在**受沙箱管控的解释器**下，`unlink` 会被
  `sitecustomize.py` 改道到回收站并走 `_check_bulk_delete_guard`；
  一旦该滚动窗口内的删除配额用尽，守卫会 `raise SystemExit(1)` ——
  而 compose.py 只兜了 `except OSError`，**SystemExit 不是 OSError**，
  于是整个 `/run` 以 HTTP 500 结束。
  症状具有误导性：状态文件里 `status=audio_done`（脚本/出图/配音都成功），
  只有合成步消失。
  → 解决办法：在**普通终端**里跑 aigc-comic，或减少同窗口内的删除操作。
  → **不要去改 `aigc-comic` 的源码**（口径约束：既有项目代码不改动）。

用法：
  python scripts/run_aigc_comic_e2e.py \
      --base http://127.0.0.1:8000 \
      --topic 学霸的反转 \
      --out outputs/demo_c_pipeline

注：提示词适配覆盖率指标**不由本脚本发布**，归口
    `scripts/measure_prompt_coverage.py`（离线确定性度量）。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def _post(url: str, payload: dict, timeout: float) -> tuple[int, dict]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # 服务端 500 也把 body 读回来，否则看不到失败原因
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, {"raw": raw}


def _get(url: str, timeout: float = 10.0) -> tuple[int, dict]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))


def _read_shim_calls(health: dict) -> list[dict]:
    """读 shim 的调用流水（`/internal/health` 会告知落盘路径，不硬编码）。"""
    log = health.get("call_log")
    if not log:
        return []
    p = Path(log)
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _summarize_adaptation(calls: list[dict]) -> dict:
    """本轮出图实际用到的提示词适配情况（只记录，不发布指标）。

    ⚠️ 指标 `c_prompt_adaptation_coverage` 的**归口发布者**是
       `scripts/measure_prompt_coverage.py`（离线、确定性、不需要 GPU 与服务在线）。
       这里只把「本次运行实际发生的情况」记进 run_record.json 当作旁证，
       避免同一指标有两个发布者互相覆盖。

    为什么按「运行窗口」切片而不是读全量流水：
      calls.jsonl 是**追加**的，含历史调试调用。混进来会让记录反映的
      不是本次样片，而是「这台机器上跑过的所有出图」。
    """
    stats = [c.get("prompt_stats") or {} for c in calls]
    stats = [s for s in stats if "cjk_coverage" in s]
    if not stats:
        return {"n_calls": 0, "reason": "本轮无带覆盖率的调用记录"}

    covs = [float(s["cjk_coverage"]) for s in stats]
    mapped = [int(s.get("mapped") or 0) for s in stats]
    n_lora = sum(1 for s in stats if s.get("lora_tags"))
    return {
        "n_calls": len(stats),
        "cjk_coverage_mean": round(sum(covs) / len(covs), 4),
        "cjk_coverage_min": round(min(covs), 4),
        "cjk_coverage_max": round(max(covs), 4),
        "mapped_mean": round(sum(mapped) / len(mapped), 2),
        "calls_with_lora_tags": n_lora,
        "lora_applied": False,
        "note": "指标口径以 scripts/measure_prompt_coverage.py 的离线度量为准",
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="端到端跑通 aigc-comic /run")
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--topic", default="学霸的反转")
    ap.add_argument("--task-id", default="", help="传入则断点续跑")
    ap.add_argument("--timeout", type=float, default=600.0)
    ap.add_argument("--out", default="outputs/demo_c_pipeline", help="过程记录输出目录")
    args = ap.parse_args()

    base = args.base.rstrip("/")
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    record: dict = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "base": base,
        "topic": args.topic,
        "resume_of": args.task_id or None,
    }

    # 前置：确认 shim 在跑。若不在，出图会静默走占位图 —— 那样跑出来的
    # 样片在口径上属于「占位图链路」，与本次要证明的事情无关。
    shim_health: dict = {}
    calls_before: list[dict] = []
    try:
        _, shim_health = _get("http://127.0.0.1:7860/internal/health")
        record["shim"] = {
            "ok": shim_health.get("ok"),
            "comfyui_version": (shim_health.get("comfyui") or {}).get("comfyui_version"),
            "prompt_lexicon_size": shim_health.get("prompt_lexicon_size"),
        }
        calls_before = _read_shim_calls(shim_health)
    except Exception as exc:  # noqa: BLE001
        record["shim"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        print("[!] shim 不可用，出图将退化为占位图：", record["shim"]["error"])

    print(f"[*] POST /run  topic={args.topic!r}  timeout={args.timeout}s")
    t0 = time.perf_counter()
    status, body = _post(f"{base}/run", {"topic": args.topic, "task_id": args.task_id}, args.timeout)
    elapsed = time.perf_counter() - t0
    record["http_status"] = status
    record["run_elapsed_s"] = round(elapsed, 3)
    record["run_response"] = body

    print(f"[*] HTTP {status}  墙钟 {elapsed:.2f}s")
    if status != 200:
        print("[!] 失败响应：", json.dumps(body, ensure_ascii=False)[:800])

    task_id = body.get("task_id") if isinstance(body, dict) else None
    if task_id:
        try:
            _, st = _get(f"{base}/tasks/{task_id}")
            record["task_state"] = st
            print(f"[*] 任务 {task_id} 状态：{st.get('status')}")
        except Exception as exc:  # noqa: BLE001
            record["task_state_error"] = f"{type(exc).__name__}: {exc}"

    video = (body or {}).get("video_path")
    if video:
        p = Path(video)
        record["video"] = {
            "path": video,
            "exists": p.exists(),
            "bytes": p.stat().st_size if p.exists() else None,
        }
        print(f"[*] 成片：{video}  {record['video']['bytes']} bytes")

    record["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")

    # 本次运行实际发生的提示词适配情况（旁证，不发布指标）
    if shim_health:
        calls_now = _read_shim_calls(shim_health)
        record["prompt_adaptation_observed"] = _summarize_adaptation(calls_now[len(calls_before):])
        obs = record["prompt_adaptation_observed"]
        if obs.get("n_calls"):
            print(
                f"[*] 本轮适配观测：{obs['n_calls']} 条，覆盖率均 {obs['cjk_coverage_mean']:.1%}"
                f"（{obs['cjk_coverage_min']:.1%}~{obs['cjk_coverage_max']:.1%}）"
            )

    out_file = out_dir / "run_record.json"
    out_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[*] 过程记录：{out_file}")

    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
