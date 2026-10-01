"""Demo D —— 统一门户 + 指标口径页。

## 为什么门户值得单独做

面试官追问「这个 89% 怎么算的」的时候，**能当场翻出定义、公式、数据集与脚本路径**，
比任何 Demo 画面都有说服力。所以这个门户的重点不是好看，而是：

1. **指标口径页** —— 逐条列出每个数字的定义/公式/数据集/脚本/实测值/与简历值的差异
2. **主张覆盖页** —— 简历 11 条主张逐条对照，含可复现性判定与差异说明（不遗漏任何一条）
3. **来源声明页** —— 三分类账本：第三方开源 / 既有自研 / 本次新增，边界写死
4. **可点开的脚本** —— `/api/source/...` 直接返回计算脚本源码，白名单严格限定

## 一个刻意的设计：口径页永远从磁盘现读

`/api/claims`、`/api/metrics`、`/api/components` 每次都读磁盘文件，
并且提供 `POST /api/refresh` 现场重新生成。
演示时可以直接说「这些数字不是截图，是刚才现算的」，然后当场点一下刷新。

## 端口

8200。避开 8000（aigc-comic）、8100（Demo B）、8188（ComfyUI）。

用法：
    python -m portal.app
    → http://127.0.0.1:8200
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel

from common import config
from common.clients import http as httpx_client
from common.metrics import claims as claims_mod
from common.metrics import registry
from portal import provenance

ROOT = config.paths.root
STATIC = Path(__file__).resolve().parent / "static"
PORTAL_DIR = ROOT / "portal"
DEMO_B_REPORTS = ROOT / "demo_b_copywriting" / "reports"

app = FastAPI(
    title="简历项目 Demo 复现 · 门户",
    description=(
        "独立复现 / 技术验证版。非生产系统。"
        "本门户的数据源为代码生成的 JSON，不是手写文档。"
    ),
    version="0.1.0",
)

DEMO_B_URL = "http://127.0.0.1:8100"
AIGC_COMIC_URL = config.services.aigc_comic_base_url
COMFYUI_URL = config.services.comfyui_base_url


# ============================================================ 生成器（现算）

def _regen() -> Dict[str, Any]:
    """重新生成三份口径 JSON。任一步失败都会抛出，不静默。"""
    m = registry.export_json()
    c = claims_mod.export_json()
    p = provenance.export_json()
    return {"metrics": str(m), "claims": str(c), "components": str(p)}


def _read_json(path: Path, name: str) -> Dict[str, Any]:
    if not path.exists():
        _regen()
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"无法生成 {name}")
    return json.loads(path.read_text(encoding="utf-8"))


# ============================================================ 依赖探测

def _module_available(name: str) -> bool:
    import importlib.util  # noqa: PLC0415

    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _demo_status() -> List[Dict[str, Any]]:
    """三个 Demo 的就绪状态。**如实反映，未就绪就写未就绪并给原因。**"""
    b_probe = httpx_client.probe(f"{DEMO_B_URL}/api/health")
    comic = httpx_client.probe(f"{AIGC_COMIC_URL}/health")
    comfy = httpx_client.probe(f"{COMFYUI_URL}/system_stats")

    a_missing = [n for n in ("ultralytics", "insightface", "faster_whisper") if not _module_available(n)]
    c_new = [
        "ComfyUI 出图适配器",
        "审核层（规则 + LLM 双通道）",
        "指标层（阶段耗时 + 效率对比）",
    ]

    return [
        {
            "id": "B",
            "name": "AIGC 文案去 AI 味",
            "desc": "语料清洗 → 垂类检索 → 双路改写 → 质检 → 三路对照",
            "ready": bool(b_probe.get("ok")),
            "url": DEMO_B_URL,
            "launch": r"powershell -ExecutionPolicy Bypass -File E:\interview-demo\scripts\start_demo_b.ps1",
            "probe": b_probe,
            "increment": ["RAG 检索增强改写链路", "AI 味词典（自研仪器）", "质检层 5 道闸门", "评测框架与对照页"],
            "reuse": [
                "humanize-chinese（第三方，仅检测）",
                "规则改写臂（第三方基线，用于反衬）",
            ],
            "status_note": (
                "自研 LLM 改写臂需 LLM 通道（云端 Key 或本地 Ollama）；"
                "当前未配置时该臂返回明确错误，规则臂与对照臂不受影响。"
            ),
        },
        {
            "id": "A",
            "name": "多模态智能安防 Agent",
            "desc": "YOLO 检测 + InsightFace 人脸 + Whisper 语音 + RAG 问答 + MCP 工单",
            "ready": False,
            "url": None,
            "launch": None,
            "probe": {"missing_modules": a_missing},
            "increment": ["全链路从零构建（本项目唯一从零构建的模块）"],
            "reuse": ["RAG / 向量化 / 向量库复用 common 底座"],
            "status_note": (
                "未就绪：P2 阶段尚未开始。前置为 CUDA 版 torch（cu128），"
                f"后台下载中。缺失依赖：{', '.join(a_missing) or '（已装齐）'}"
            ),
        },
        {
            "id": "C",
            "name": "漫剧智能生产流水线",
            "desc": "剧本 → 出图 → 配音 → 合成 → 审核，输出样片 MP4",
            "ready": False,
            "url": None,
            "launch": None,
            "probe": {"aigc_comic": comic, "comfyui": comfy},
            "increment": c_new,
            "reuse": [
                "aigc-comic：**pipeline 状态机 + 断点续跑**（既有能力，不得声称自研）",
                "aigc-comic：/script /image /audio /compose 执行接口（既有）",
                "aigc-comic：音画同步（ffprobe 驱动，既有）",
                "ComfyUI：真实出图底座（第三方，GPL-3.0，仅 HTTP 调用）",
            ],
            "status_note": (
                "未就绪：P3 阶段尚未开始。执行服务与出图底座均已验证可用"
                f"（aigc-comic={_updown(comic)}, ComfyUI={_updown(comfy)}），"
                "但本次新增的三项能力尚未实现。"
            ),
        },
    ]


def _updown(probe: Dict[str, Any]) -> str:
    return "运行中" if probe.get("ok") else "未启动"


# ============================================================ 页面


def _page(name: str) -> FileResponse:
    path = STATIC / name
    if not path.exists():
        raise HTTPException(status_code=500, detail=f"缺少页面 {name}")
    return FileResponse(path, media_type="text/html; charset=utf-8")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return _page("index.html")


@app.get("/claims", include_in_schema=False)
def claims_page() -> FileResponse:
    return _page("claims.html")


@app.get("/components", include_in_schema=False)
def components_page() -> FileResponse:
    return _page("components.html")


@app.get("/static/{name}", include_in_schema=False)
def static_file(name: str) -> FileResponse:
    path = STATIC / name
    if not path.exists() or path.parent != STATIC:
        raise HTTPException(status_code=404, detail="not found")
    media = "text/css" if name.endswith(".css") else "application/octet-stream"
    return FileResponse(path, media_type=media)


# ============================================================ 接口


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"ok": True, "root": str(ROOT)}


@app.get("/api/overview")
def overview() -> Dict[str, Any]:
    metrics = _read_json(PORTAL_DIR / "metrics.json", "metrics.json")
    claims = _read_json(PORTAL_DIR / "claims.json", "claims.json")
    components = _read_json(PORTAL_DIR / "components.json", "components.json")

    ms = metrics["metrics"]
    by_status: Dict[str, int] = {}
    for m in ms:
        by_status[m["status"]] = by_status.get(m["status"], 0) + 1

    latest_eval = None
    summary = DEMO_B_REPORTS / "summary.json"
    if summary.exists():
        s = json.loads(summary.read_text(encoding="utf-8"))
        latest_eval = {
            "generated_at": s.get("generated_at"),
            "eval_set": s.get("eval_set"),
            "by_arm": {
                k: {
                    "label": v.get("label"),
                    "n_ok": v.get("n_ok"),
                    "detection_before_mean": v.get("detection_before_mean"),
                    "detection_after_mean": v.get("detection_after_mean"),
                    "score_drop_mean": v.get("score_drop_mean"),
                    "tells_before_mean": v.get("tells_before_mean"),
                    "tells_after_mean": v.get("tells_after_mean"),
                    "verdicts": v.get("verdicts"),
                }
                for k, v in (s.get("by_arm") or {}).items()
            },
            "by_register": s.get("by_register"),
        }

    return {
        "root": str(ROOT),
        "demos": _demo_status(),
        "metrics": {"total": len(ms), "by_status": by_status},
        "claims": {"total": claims["coverage"]["claim_count"], "coverage": claims["coverage"]},
        "components": components["counts"],
        "latest_eval": latest_eval,
    }


@app.get("/api/metrics")
def metrics() -> JSONResponse:
    return JSONResponse(_read_json(PORTAL_DIR / "metrics.json", "metrics.json"))


@app.get("/api/claims")
def claims() -> JSONResponse:
    return JSONResponse(_read_json(PORTAL_DIR / "claims.json", "claims.json"))


@app.get("/api/components")
def components() -> JSONResponse:
    return JSONResponse(_read_json(PORTAL_DIR / "components.json", "components.json"))


@app.post("/api/refresh")
def refresh() -> Dict[str, Any]:
    """现场重新生成口径页数据 —— 演示时用来证明「数字是现算的」。"""
    try:
        out = _regen()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc
    return {"ok": True, **out}


# ============================================================ 源码查看（白名单）

def _source_allowlist() -> set[str]:
    """白名单 = 注册表里出现过的脚本路径 + 若干说明文档。

    不采用「任意路径拼接」，因此不存在目录穿越面。
    """
    files: set[str] = set()
    for m in registry.METRICS:
        raw = (m.script or "").strip()
        if not raw or raw == "—" or raw == "-":
            continue
        path = raw.split("::")[0].strip()
        if path:
            files.add(path)
    files.update(
        {
            "README.md",
            "PLAN.md",
            "third_party/README.md",
            "demo_b_copywriting/data/SOURCES.md",
            "demo_b_copywriting/tells.py",
            "demo_b_copywriting/quality.py",
            "demo_b_copywriting/retriever.py",
            "demo_b_copywriting/rewrite.py",
            "demo_b_copywriting/ingest.py",
            "demo_b_copywriting/build_index.py",
            "demo_b_copywriting/app.py",
            "common/metrics/registry.py",
            "common/metrics/claims.py",
            "common/metrics/text_metrics.py",
            "portal/provenance.py",
        }
    )
    return files


@app.get("/api/source")
def source(path: str, max_lines: int = 400) -> Dict[str, Any]:
    """返回白名单内的源码内容。用于「点开就能看到计算脚本」。"""
    allow = _source_allowlist()
    if path not in allow:
        raise HTTPException(
            status_code=403,
            detail=f"不在白名单内：{path}（可查看的文件共 {len(allow)} 个）",
        )
    target = (ROOT / path).resolve()
    if not str(target).startswith(str(ROOT.resolve())) or not target.exists():
        raise HTTPException(status_code=404, detail=f"{path} 不存在或越界")

    if target.is_dir():
        listing = sorted(p.name for p in target.iterdir())
        return {"path": path, "kind": "directory", "entries": listing}

    text = target.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    return {
        "path": path,
        "kind": "file",
        "lines_total": len(lines),
        "truncated": len(lines) > max_lines,
        "sha256_12": hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        "content": "\n".join(lines[:max_lines]),
    }


def main() -> None:
    import uvicorn

    logger.info(f"门户根目录：{ROOT}")
    uvicorn.run(app, host="127.0.0.1", port=8200, log_level="info")


if __name__ == "__main__":
    main()
