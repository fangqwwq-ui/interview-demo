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

用法：
  python scripts/run_aigc_comic_e2e.py \
      --base http://127.0.0.1:8000 \
      --topic 学霸的反转 \
      --out outputs/demo_c_pipeline
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
    try:
        _, shim = _get("http://127.0.0.1:7860/internal/health")
        record["shim"] = {
            "ok": shim.get("ok"),
            "comfyui_version": (shim.get("comfyui") or {}).get("comfyui_version"),
            "lexicon_size": shim.get("lexicon_size"),
        }
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
    out_file = out_dir / "run_record.json"
    out_file.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[*] 过程记录：{out_file}")

    return 0 if status == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
