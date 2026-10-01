"""Demo B 的演示服务 —— 三路对照界面。

设计取舍：**不引入任何前端框架，不引用任何 CDN**。
演示现场可能没有外网（而 `requirements` 的安装已经证明本机带宽很差），
所以页面必须是纯静态 HTML + 原生 JS，字体用系统字体栈。
只要 Python 进程起来了，界面就一定能打开。

## 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/` | 三路对照界面 |
| POST | `/api/rewrite` | 对一条文本跑三条臂并返回质检结果 |
| GET | `/api/health` | 编码器 / 向量库 / 检测器 / LLM 通道状态 |
| GET | `/api/metrics` | 指标口径表（`portal/metrics.json`） |
| GET | `/api/eval-summary` | 最近一次批量评测的聚合结果与仪器灵敏度 |
| GET | `/api/reports/{name}` | 读取 reports 下的报告（白名单） |

## 惰性初始化

索引加载与编码器构建要 1–3 秒，LLM 探活还要超时。
因此**不在启动时**做这些事，而是在第一次收到请求时初始化并缓存 ——
否则 `uvicorn` 会在演示者还没准备好时就开始计时。

用法：
    python -m demo_b_copywriting.app
    → http://127.0.0.1:8100
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger
from pydantic import BaseModel, Field

from common import config
from common.llm.router import LLMRouter
from demo_b_copywriting import tells
from demo_b_copywriting.detector import health as kernel_health
from demo_b_copywriting.quality import assess, instrument_provenance
from demo_b_copywriting.retriever import Retriever
from demo_b_copywriting.rewrite import (
    ARM_KERNEL,
    ARM_LLM,
    ARM_ORIGINAL,
    PROMPT_VERSION,
    SCENE_BY_VERTICAL,
    Redactor,
    kernel_rewrite,
)

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
PORTAL_DIR = HERE.parent / "portal"
REPORTS_DIR = HERE / "reports"

# 允许通过 HTTP 读取的报告文件白名单（不做任意路径拼接）
REPORT_ALLOWLIST = {
    "summary.json",
    "metrics.csv",
    "three_way.md",
    "ingest_report.json",
}

app = FastAPI(
    title="Demo B · AIGC 文案去 AI 味（方案验证版）",
    description=(
        "独立复现，非生产系统。三路对照：原文 / 第三方规则改写 / 自研 RAG+LLM 改写。"
    ),
    version="0.1.0",
)


# ============================================================ 惰性状态

@dataclass
class Runtime:
    retriever: Retriever
    router: LLMRouter
    redactor: Redactor


_state: Optional[Runtime] = None


def state() -> Runtime:
    global _state
    if _state is None:
        logger.info("首次请求，初始化检索器与改写器……")
        retriever = Retriever()
        router = LLMRouter()
        _state = Runtime(retriever=retriever, router=router, redactor=Redactor(retriever, router))
    return _state


# ============================================================ 请求模型


class RewriteRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="待改写文本")
    vertical: Optional[str] = Field(None, description="垂类；留空则不做垂类重排与示例注入")
    arms: list[str] = Field(
        default_factory=lambda: [ARM_ORIGINAL, ARM_KERNEL, ARM_LLM],
        description="要跑的臂",
    )


# ============================================================ 页面


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(status_code=500, detail=f"缺少界面文件 {page}")
    return FileResponse(page, media_type="text/html; charset=utf-8")


# ============================================================ 接口


@app.get("/api/health")
def health() -> Dict[str, Any]:
    rt = state()
    return {
        "ok": True,
        "index": rt.retriever.info,
        "detector": kernel_health(),
        "llm": rt.router.health(),
        "prompt_version": PROMPT_VERSION,
        "scene_map": SCENE_BY_VERTICAL,
        "tells": tells.stats(),
        "instrument_provenance": instrument_provenance(),
        "assets": {
            "comfyui": str(config.paths.comfyui),
            "aigc_comic": str(config.paths.aigc_comic),
            "note": "本 Demo 不依赖这两个服务；列出以便说明资产边界。",
        },
    }


@app.post("/api/rewrite")
def rewrite_endpoint(req: RewriteRequest) -> Dict[str, Any]:
    rt = state()
    text = req.text.strip()
    vertical = req.vertical or None
    encoder = rt.retriever.encoder

    result: Dict[str, Any] = {
        "input": {"text": text, "chars": len(text), "vertical": vertical},
        "prompt_version": PROMPT_VERSION,
        "arms": {},
        "ensemble": {},
    }

    if ARM_ORIGINAL in req.arms:
        a = assess(text, text, encoder, router=rt.router, control=True)
        result["arms"][ARM_ORIGINAL] = {
            "label": "原文（对照组）",
            "ok": True,
            "output_text": text,
            "assessment": a.as_dict(),
        }

    if ARM_KERNEL in req.arms:
        res = kernel_rewrite(text, vertical)
        a = assess(text, res.output_text, encoder, router=rt.router)
        result["arms"][ARM_KERNEL] = {
            "label": "规则改写（第三方 humanize-chinese）",
            **res.as_dict(),
            "assessment": a.as_dict(),
        }

    if ARM_LLM in req.arms:
        res = rt.redactor.rewrite(text, vertical)
        a = assess(text, res.output_text, encoder, router=rt.router)
        payload = {**res.as_dict(), "assessment": a.as_dict()}
        # 提示词体积较大，默认不回传全文，只给长度与版本；需要时用 ?debug 查看
        result["arms"][ARM_LLM] = {"label": "自研 RAG+LLM 改写", **payload}

    # 汇总对照：把三个臂的关键量并排放，避免前端自己算
    for arm, data in result["arms"].items():
        a = data.get("assessment") or {}
        result["ensemble"][arm] = {
            "detections": [
                (a.get("detection_before") or {}).get("score"),
                (a.get("detection_after") or {}).get("score"),
            ],
            "tells": [
                (a.get("tells_before") or {}).get("count"),
                (a.get("tells_after") or {}).get("count"),
            ],
            "verdict": a.get("verdict"),
            "fidelity": a.get("semantic_fidelity"),
            "fidelity_reliable": a.get("fidelity_reliable"),
            "length_ratio": a.get("length_ratio"),
            "elapsed_s": data.get("elapsed_s"),
        }

    return result


@app.get("/api/metrics")
def metrics() -> JSONResponse:
    path = PORTAL_DIR / "metrics.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="尚未生成 portal/metrics.json")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/eval-summary")
def eval_summary() -> JSONResponse:
    path = REPORTS_DIR / "summary.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail="尚未跑过批量评测。请先执行 `python -m demo_b_copywriting.evaluate`",
        )
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


@app.get("/api/reports/{name}")
def report(name: str):
    if name not in REPORT_ALLOWLIST:
        raise HTTPException(status_code=403, detail=f"不允许读取 {name}")
    path = REPORTS_DIR / name
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"{name} 不存在")
    if name.endswith(".csv"):
        return FileResponse(path, media_type="text/csv")
    if name.endswith(".md"):
        return FileResponse(path, media_type="text/markdown; charset=utf-8")
    return JSONResponse(json.loads(path.read_text(encoding="utf-8")))


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8100, log_level="info")


if __name__ == "__main__":
    main()
