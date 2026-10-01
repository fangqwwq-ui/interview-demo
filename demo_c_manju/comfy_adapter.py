r"""Demo C · ComfyUI 出图适配器（本次新增能力之一）。

## 这个模块解决什么问题

既有 `E:\aigc-comic` 的出图后端走的是 SD WebUI（`SD_API_URL=7860`）或 Pillow 占位图。
本机没有独立的 A1111，但有 `D:\ComfyUI`（v0.28.0，1045 节点，含 `animagine-xl-4.0`）。
所以需要一个**把 ComfyUI 接成出图后端**的适配器，让流水线拿到真实图像而不是占位图。

> ⚠️ 本文件顶部使用原始字符串（raw string）是必须的：文档字符串里含 `D:\ComfyUI`
> 这类 Windows 路径，非原始字符串会把 `\a` 解析成响铃符（静默损坏文档文本）、
> 把 `\C` 报成 SyntaxWarning。同类问题在本项目别处也出现过，改动时注意。

## 与既有能力的分界（口径红线）

| 环节 | 归属 |
|---|---|
| 本适配器、workflow 构造、错误处理、显存卸载 | ✅ 本次新增 |
| 流水线状态机、断点续跑、分镜/配音/合成、音画同步 | ♻️ `aigc-comic` 既有，**不得声称自研** |
| ComfyUI 本体 | ⚠️ 第三方，**GPL-3.0**，仅通过 HTTP 调用 |

## 三个工程决策（面试会被问）

**1. workflow 是代码构造的，但每次运行都把实际使用的 JSON 落盘。**
   这样「能独立重跑」有据可依：拿落盘的 `workflow.json` 直接喂 ComfyUI 就能复现同一张图。
   光有构造函数不够 —— 你没法证明当时跑的是哪一版节点参数。

**2. seed 一律显式化，不用 -1。**
   ComfyUI 的 API 不像 Web UI 那样有 `control_after_generate`，
   传 `-1` 得到的是一张无法复现的图。因此由本项目生成随机 seed 并记录，
   复现只需带上同一个 seed。

**3. 提供 `free_vram()`。**
   8 GB 显存不允许同时驻留 SDXL 与其它模型（Demo A 的 YOLO/InsightFace/Whisper）。
   分时加载不是可选项，是实现约束。因此卸载能力必须做成接口，而不是靠重启进程。
"""

from __future__ import annotations

import argparse
import json
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from common import config
from common.clients import http as httpx_client
from demo_c_manju.imgsize import image_size

HERE = Path(__file__).resolve().parent
WORKFLOW_DIR = HERE / "workflows"
OUTPUT_DIR = config.paths.outputs / "demo_c"

# ⚠️ ComfyUI 是 GPL-3.0。本项目只把它当独立服务通过 HTTP 调用，
#    不链接源码、不修改文件、不再分发，因此本仓库代码不因 GPL 传染而需 GPL 发布。
COMFYUI_LICENSE = "GPL-3.0"
COMFYUI_BOUNDARY = "独立进程 + HTTP 边界 + 非再分发"

DEFAULT_CHECKPOINT = "animagine-xl-4.0.safetensors"

# animagine-xl-4.0 的推荐质量标签（该模型是 Danbooru 标签体系）
ANIMAGINE_QUALITY = "masterpiece, best quality, very aesthetic, absurdres"

# 通用负向提示词：质量下限 + 常见崩坏
DEFAULT_NEGATIVE = (
    "lowres, bad anatomy, bad hands, text, error, missing finger, extra digits, "
    "fewer digits, cropped, worst quality, low quality, low score, bad score, "
    "average score, signature, watermark, username, blurry"
)

# ComfyUI 原生 SDXL 分辨率（1024 系），竖构图
DEFAULT_WIDTH = 832
DEFAULT_HEIGHT = 1216
DEFAULT_STEPS = 28
DEFAULT_CFG = 6.0
DEFAULT_SAMPLER = "euler_ancestral"
DEFAULT_SCHEDULER = "normal"


# ============================================================ 结果结构


@dataclass
class GeneratedImage:
    filename: str
    subfolder: str
    type: str
    local_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    bytes_size: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "filename": self.filename,
            "subfolder": self.subfolder,
            "type": self.type,
            "local_path": self.local_path,
            "width": self.width,
            "height": self.height,
            "bytes_size": self.bytes_size,
        }


@dataclass
class GenerationResult:
    ok: bool
    prompt: str
    negative: str
    checkpoint: str
    seed: int
    width: int
    height: int
    steps: int
    cfg: float
    elapsed_s: float
    prompt_id: Optional[str] = None
    images: List[GeneratedImage] = field(default_factory=list)
    workflow_path: Optional[str] = None
    error: Optional[str] = None
    node_errors: Dict[str, Any] = field(default_factory=dict)
    backend: Dict[str, Any] = field(default_factory=dict)
    # ---- 冷/热状态（效率口径必需）----
    # 模型是否常驻对单张耗时的影响超过一倍，因此必须记录**提交前的显存状态**。
    # 这里记显存而不是只记一个 boolean：显存是可观测的物理量，
    # 而「冷/热」是由它推导出来的判断，推导规则写在 metrics/compare.py 里。
    vram_free_before_gb: Optional[float] = None
    vram_total_gb: Optional[float] = None
    free_before: bool = False

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "checkpoint": self.checkpoint,
            "seed": self.seed,
            "size": f"{self.width}x{self.height}",
            "steps": self.steps,
            "cfg": self.cfg,
            "elapsed_s": round(self.elapsed_s, 2),
            "prompt_id": self.prompt_id,
            "images": [i.as_dict() for i in self.images],
            "workflow_path": self.workflow_path,
            "error": self.error,
            "node_errors": self.node_errors,
            "backend": self.backend,
            "prompt": self.prompt,
            "negative": self.negative,
            "vram_free_before_gb": self.vram_free_before_gb,
            "vram_total_gb": self.vram_total_gb,
            "free_before": self.free_before,
        }


# ============================================================ 客户端


class ComfyUIError(RuntimeError):
    def __init__(self, message: str, *, node_errors: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.node_errors = node_errors or {}


class ComfyUIClient:
    """ComfyUI HTTP 客户端。只依赖 httpx，不导入 ComfyUI 的任何代码。"""

    def __init__(self, base_url: Optional[str] = None, *, timeout: float = 60.0) -> None:
        self.base_url = (base_url or config.services.comfyui_base_url).rstrip("/")
        self.timeout = timeout
        self._object_info: Optional[Dict[str, Any]] = None

    # ---------------------------------------------------------- 基础

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{self.base_url}{path}"
        with httpx_client.client(url, timeout=self.timeout) as c:
            resp = c.get(url, params=params or None)
        if resp.status_code >= 400:
            raise ComfyUIError(f"GET {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        ct = resp.headers.get("content-type", "")
        return resp.content if "image" in ct or "octet" in ct else resp.json()

    def _post(self, path: str, payload: Dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        with httpx_client.client(url, timeout=self.timeout) as c:
            resp = c.post(url, json=payload)
        if resp.status_code >= 400:
            detail: Any = resp.text[:400]
            node_errors: Dict[str, Any] = {}
            try:
                body = resp.json()
                detail = body.get("error") or detail
                node_errors = body.get("node_errors") or {}
            except Exception:  # noqa: BLE001
                pass
            raise ComfyUIError(f"POST {path} -> HTTP {resp.status_code}: {detail}", node_errors=node_errors)
        return resp.json() if resp.content else {}

    def health(self) -> Dict[str, Any]:
        try:
            stats = self._get("/system_stats")
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "base_url": self.base_url, "error": f"{type(exc).__name__}: {exc}"}

        devices = stats.get("devices") or [{}]
        dev = devices[0]
        return {
            "ok": True,
            "base_url": self.base_url,
            "comfyui_version": (stats.get("system") or {}).get("comfyui_version"),
            "python": (stats.get("system") or {}).get("python_version", "").split()[0] or None,
            "device": dev.get("name"),
            "vram_total_gb": round((dev.get("vram_total") or 0) / 1024**3, 2),
            "vram_free_gb": round((dev.get("vram_free") or 0) / 1024**3, 2),
            "torch": dev.get("torch_version"),
            "license": COMFYUI_LICENSE,
            "boundary": COMFYUI_BOUNDARY,
        }

    def object_info(self, node: Optional[str] = None) -> Dict[str, Any]:
        if self._object_info is None:
            self._object_info = self._get("/object_info")
        if node:
            return self._object_info.get(node) or {}
        return self._object_info

    # ---------------------------------------------------------- 资源发现

    def checkpoints(self) -> List[str]:
        """从节点定义里读出可用 checkpoint 列表 —— 不猜、不硬编码。"""
        info = self.object_info("CheckpointLoaderSimple")
        try:
            return list(info["input"]["required"]["ckpt_name"][0])
        except Exception:  # noqa: BLE001
            return []

    def samplers(self) -> List[str]:
        info = self.object_info("KSampler")
        try:
            return list(info["input"]["required"]["sampler_name"][0])
        except Exception:  # noqa: BLE001
            return []

    def schedulers(self) -> List[str]:
        info = self.object_info("KSampler")
        try:
            return list(info["input"]["required"]["scheduler"][0])
        except Exception:  # noqa: BLE001
            return []

    def required_nodes(self) -> Dict[str, bool]:
        """自检用：确认本适配器依赖的节点都存在。"""
        wanted = [
            "CheckpointLoaderSimple",
            "EmptyLatentImage",
            "CLIPTextEncode",
            "KSampler",
            "VAEDecode",
            "SaveImage",
        ]
        info = self._object_info if self._object_info is not None else self.object_info()
        return {n: (n in info) for n in wanted}

    # ---------------------------------------------------------- 执行

    def submit(self, workflow: Dict[str, Any], client_id: Optional[str] = None) -> str:
        payload = {"prompt": workflow, "client_id": client_id or str(uuid.uuid4())}
        body = self._post("/prompt", payload)
        prompt_id = body.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"/prompt 未返回 prompt_id：{body}")
        return str(prompt_id)

    def history(self, prompt_id: str) -> Dict[str, Any]:
        return self._get(f"/history/{prompt_id}")

    def wait(self, prompt_id: str, *, timeout_s: float = 600.0, poll_s: float = 1.0) -> Dict[str, Any]:
        """轮询直到完成。返回该 prompt 的 outputs。

        为什么用轮询而不是 websocket：WebSocket 会引入额外的连接生命周期与重连逻辑，
        而本适配器的调用方（流水线）本来就需要一个带超时的阻塞语义。
        轮询的代价是延迟，但在 Demo 规模下可以忽略；换来的是实现简单且无隐藏状态。
        """
        started = time.perf_counter()
        while True:
            hist = self.history(prompt_id)
            entry = hist.get(prompt_id)
            if entry:
                status = (entry.get("status") or {}).get("status_str")
                if status == "error":
                    msgs = (entry.get("status") or {}).get("messages") or []
                    raise ComfyUIError(
                        f"ComfyUI 执行报错（prompt_id={prompt_id}）：{json.dumps(msgs, ensure_ascii=False)[:400]}"
                    )
                if entry.get("outputs") is not None:
                    return entry.get("outputs") or {}
            if time.perf_counter() - started > timeout_s:
                raise ComfyUIError(f"等待 ComfyUI 完成超时（>{timeout_s}s，prompt_id={prompt_id}）")
            time.sleep(poll_s)

    def fetch_image(self, item: Dict[str, Any]) -> bytes:
        return self._get(
            "/view",
            filename=item.get("filename"),
            subfolder=item.get("subfolder", ""),
            type=item.get("type", "output"),
        )

    def interrupt(self) -> None:
        try:
            self._post("/interrupt", {})
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"interrupt 失败：{exc}")

    def free_vram(self, *, unload_models: bool = True, free_memory: bool = True) -> Dict[str, Any]:
        """卸载模型、释放显存。

        8 GB 显存下这是硬需求：Demo A 的 YOLO / InsightFace / Whisper 与 SDXL 不能共存。
        """
        try:
            return self._post("/free", {"unload_models": unload_models, "free_memory": free_memory})
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"free_vram 失败（可能已被占用）：{exc}")
            return {"ok": False, "error": str(exc)}

    def queue(self) -> Dict[str, Any]:
        return self._get("/queue")


# ============================================================ workflow 构造


def build_sdxl_txt2img(
    *,
    positive: str,
    negative: str = DEFAULT_NEGATIVE,
    checkpoint: str = DEFAULT_CHECKPOINT,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    steps: int = DEFAULT_STEPS,
    cfg: float = DEFAULT_CFG,
    sampler: str = DEFAULT_SAMPLER,
    scheduler: str = DEFAULT_SCHEDULER,
    seed: Optional[int] = None,
    batch_size: int = 1,
    filename_prefix: str = "demo_c",
) -> Dict[str, Any]:
    """构造 SDXL 文生图 workflow（ComfyUI API 格式）。

    节点编号沿用 ComfyUI 默认工作流的编号，便于和 Web UI 里导出的 JSON 对照。
    """
    if seed is None:
        seed = random.randint(1, 2**31 - 1)

    return {
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": checkpoint},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": int(width), "height": int(height), "batch_size": int(batch_size)},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": positive, "clip": ["4", 1]},
            "_meta": {"title": "正向提示词"},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["4", 1]},
            "_meta": {"title": "负向提示词"},
        },
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": int(seed),
                "steps": int(steps),
                "cfg": float(cfg),
                "sampler_name": sampler,
                "scheduler": scheduler,
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "8": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "9": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": filename_prefix, "images": ["8", 0]},
        },
    }


def extract_seed(workflow: Dict[str, Any]) -> int:
    return int(workflow["3"]["inputs"]["seed"])


# ============================================================ 高级入口


def generate(
    positive: str,
    *,
    negative: str = DEFAULT_NEGATIVE,
    checkpoint: str = DEFAULT_CHECKPOINT,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    steps: int = DEFAULT_STEPS,
    cfg: float = DEFAULT_CFG,
    sampler: str = DEFAULT_SAMPLER,
    scheduler: str = DEFAULT_SCHEDULER,
    seed: Optional[int] = None,
    quality_tags: bool = True,
    save_dir: Optional[Path] = None,
    timeout_s: float = 600.0,
    client: Optional[ComfyUIClient] = None,
    free_after: bool = False,
    free_before: bool = False,
) -> GenerationResult:
    """生成一张图并把图片与 workflow 落盘。

    `quality_tags=True` 会在正向提示词前追加 animagine 的推荐质量标签。

    `free_before=True` 表示本次按**冷启动**协议测量：提交前先卸载模型、
    让显存回到基线。配套记录 `vram_free_before_gb`，使「冷/热」可由
    可观测的显存值推导，而不是仅凭调用方声明。
    """
    client = client or ComfyUIClient()
    started = time.perf_counter()
    full_positive = f"{ANIMAGINE_QUALITY}, {positive}" if quality_tags else positive

    # 记录提交前的显存状态（可观测），供效率口径区分冷启动与模型常驻
    vram_free_before: Optional[float] = None
    vram_total: Optional[float] = None
    if free_before:
        client.free_vram()
        time.sleep(1.5)
    try:
        stats = client.health()
        vram_free_before = stats.get("vram_free_gb")
        vram_total = stats.get("vram_total_gb")
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取提交前显存状态失败（不影响出图）：{}", exc)

    workflow = build_sdxl_txt2img(
        positive=full_positive,
        negative=negative,
        checkpoint=checkpoint,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        sampler=sampler,
        scheduler=scheduler,
        seed=seed,
    )
    actual_seed = extract_seed(workflow)

    run_dir = (save_dir or OUTPUT_DIR) / f"{datetime.now():%Y%m%d-%H%M%S}_seed{actual_seed}"
    run_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = run_dir / "workflow.json"
    workflow_path.write_text(
        json.dumps(workflow, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    result = GenerationResult(
        ok=False,
        prompt=full_positive,
        negative=negative,
        checkpoint=checkpoint,
        seed=actual_seed,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        elapsed_s=0.0,
        workflow_path=str(workflow_path),
        backend={"license": COMFYUI_LICENSE, "boundary": COMFYUI_BOUNDARY,
                 "base_url": client.base_url},
        vram_free_before_gb=vram_free_before,
        vram_total_gb=vram_total,
        free_before=free_before,
    )

    try:
        prompt_id = client.submit(workflow)
        result.prompt_id = prompt_id
        outputs = client.wait(prompt_id, timeout_s=timeout_s)

        for _node_id, out in (outputs or {}).items():
            for item in (out.get("images") or []):
                blob = client.fetch_image(item)
                target = run_dir / item["filename"]
                target.write_bytes(blob)
                wh = image_size(blob)
                result.images.append(
                    GeneratedImage(
                        filename=item.get("filename", ""),
                        subfolder=item.get("subfolder", ""),
                        type=item.get("type", "output"),
                        local_path=str(target),
                        width=wh[0] if wh else None,
                        height=wh[1] if wh else None,
                        bytes_size=len(blob),
                    )
                )
        result.ok = bool(result.images)
        if not result.ok:
            result.error = "ComfyUI 返回成功但没有产出图片"
    except ComfyUIError as exc:
        result.error = str(exc)
        result.node_errors = exc.node_errors
    except Exception as exc:  # noqa: BLE001
        result.error = f"{type(exc).__name__}: {exc}"
    finally:
        result.elapsed_s = time.perf_counter() - started
        if free_after:
            client.free_vram()
        # 把本次结果一并落盘，便于复盘（含失败信息）
        (run_dir / "result.json").write_text(
            json.dumps(result.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return result


def preflight(client: Optional[ComfyUIClient] = None) -> Dict[str, Any]:
    """出图前置自检：服务、依赖节点、可用 checkpoint、显存。"""
    client = client or ComfyUIClient()
    health = client.health()
    if not health.get("ok"):
        return {"ok": False, "health": health, "hint": "ComfyUI 未启动或端口不通（127.0.0.1:8188）"}

    nodes = client.required_nodes()
    missing = [n for n, ok in nodes.items() if not ok]
    ckpts = client.checkpoints()
    default_ok = DEFAULT_CHECKPOINT in ckpts

    notes: List[str] = []
    if missing:
        notes.append(f"缺少必需节点：{missing}（可能被自定义节点覆盖或未加载）")
    if not default_ok:
        notes.append(
            f"默认 checkpoint「{DEFAULT_CHECKPOINT}」不在可用列表；"
            f"可用项：{ckpts}。请用 --checkpoint 指定。"
        )

    return {
        "ok": not missing and bool(ckpts),
        "health": health,
        "nodes": nodes,
        "missing_nodes": missing,
        "checkpoints": ckpts,
        "default_checkpoint": DEFAULT_CHECKPOINT,
        "default_checkpoint_available": default_ok,
        "samplers_sample": client.samplers()[:8],
        "schedulers": client.schedulers(),
        "license": COMFYUI_LICENSE,
        "boundary": COMFYUI_BOUNDARY,
        "notes": notes,
    }


# ============================================================ CLI


def main() -> int:
    parser = argparse.ArgumentParser(description="Demo C · ComfyUI 出图适配器")
    parser.add_argument("--prompt", default=None, help="正向提示词（不含质量标签）")
    parser.add_argument("--negative", default=DEFAULT_NEGATIVE)
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--width", type=int, default=DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=DEFAULT_HEIGHT)
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--cfg", type=float, default=DEFAULT_CFG)
    parser.add_argument("--sampler", default=DEFAULT_SAMPLER)
    parser.add_argument("--scheduler", default=DEFAULT_SCHEDULER)
    parser.add_argument("--seed", type=int, default=None, help="不传则随机；复现请显式传入")
    parser.add_argument("--timeout", type=float, default=600.0)
    parser.add_argument("--no-quality-tags", action="store_true")
    parser.add_argument("--free-after", action="store_true", help="生成后卸载模型释放显存")
    parser.add_argument(
        "--free-before",
        action="store_true",
        help="生成**前**先卸载模型，按冷启动协议测量（结果里记录提交前显存）",
    )
    parser.add_argument("--preflight", action="store_true", help="只做前置自检，不出图")
    parser.add_argument("--dump-workflow", action="store_true", help="只导出 workflow JSON，不执行")
    args = parser.parse_args()

    if args.preflight:
        print(json.dumps(preflight(), ensure_ascii=False, indent=2))
        return 0

    if args.dump_workflow:
        wf = build_sdxl_txt2img(
            positive=f"{ANIMAGINE_QUALITY}, {args.prompt or 'a quiet street at dusk'}",
            negative=args.negative,
            checkpoint=args.checkpoint,
            width=args.width,
            height=args.height,
            steps=args.steps,
            cfg=args.cfg,
            sampler=args.sampler,
            scheduler=args.scheduler,
            seed=args.seed if args.seed is not None else 12345,
        )
        WORKFLOW_DIR.mkdir(parents=True, exist_ok=True)
        target = WORKFLOW_DIR / "sdxl_txt2img.json"
        target.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"written": str(target), "nodes": list(wf)}, ensure_ascii=False, indent=2))
        return 0

    if not args.prompt:
        parser.error("除 --preflight / --dump-workflow 外必须提供 --prompt")

    res = generate(
        args.prompt,
        negative=args.negative,
        checkpoint=args.checkpoint,
        width=args.width,
        height=args.height,
        steps=args.steps,
        cfg=args.cfg,
        sampler=args.sampler,
        scheduler=args.scheduler,
        seed=args.seed,
        quality_tags=not args.no_quality_tags,
        timeout_s=args.timeout,
        free_after=args.free_after,
        free_before=args.free_before,
    )
    print(json.dumps(res.as_dict(), ensure_ascii=False, indent=2))
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
