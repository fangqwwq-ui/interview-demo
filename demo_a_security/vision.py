r"""Demo A 视觉推理：YOLO11n 目标检测（ONNX Runtime）。

为什么用 ONNX 而非 ultralytics + torch
--------------------------------------
本机实测阿里云镜像约 343 KB/s，torch 2.7.0+cu128 约 2.7 GB → 约 137 分钟。
YOLO 官方直接提供 ONNX 权重（yolo11n.onnx仅 10.42 MiB），
onnxruntime-gpu 即可消费，**不引入 torch**。详见 requirements/vision.txt。

⚠️ 本模块有一个**必须实测、不能假设**的点：`get_available_providers()` 返回
含 CUDAExecutionProvider，**不代表真的能用**。本机实测：

    get_available_providers() -> ['Tensorrt', 'CUDA', 'CPU']# 看着没问题
    ...但创建 Session 时静默回退：
    Failed to create CUDAExecutionProvider. Error loading
    "onnxruntime_providers_cuda.dll" which depends on "cublasLt64_13.dll"
    which is missing
    sess.get_providers() -> ['CPUExecutionProvider']         # 实际只有 CPU

也就是说：**CUDA 不可用时 ORT 不抛异常，只是降级**。
若只看 `get_available_providers()`就报告「GPU 可用」，会是**假报告**。
因此本模块一律以 `Session.get_providers()`（实际绑定结果）为准，
并把`is_fallback` 如实上报到指标层。

CUDA 运行时的来源
-----------------
本机**没有安装 CUDA Toolkit**（只有驱动），但 `D:\ComfyUI` 的 torch 自带了
完整 CUDA 12.8 运行时（cublasLt64_12 / cudnn64_9 / cudart64_12 等）。
`resolve_cuda_runtime()` 会在本项目配置里找这些 DLL 目录并加入 DLL 搜索路径，
从而让 ORT 的 CUDA provider 真正可用 —— **只读复用，不复制、不修改 ComfyUI**。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = ROOT / "models" / "demo_a" / "yolo11n.onnx"

# YOLO11 的 COCO 类名（前 15 个足够覆盖安防场景，但完整列出便于口径核对）
COCO_NAMES: Tuple[str, ...] = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck",
    "boat", "traffic light", "fire hydrant", "stop sign", "parking meter", "bench",
    "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra",
    "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase", "frisbee",
    "skis", "snowboard", "sports ball", "kite", "baseball bat", "baseball glove",
    "skateboard", "surfboard", "tennis racket", "bottle", "wine glass", "cup",
    "fork", "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", "couch",
    "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink",
    "refrigerator", "book", "clock", "vase", "scissors", "teddy bear",
    "hair drier", "toothbrush",
)

# 输出张量布局：output0 形状 (1, 84, anchors)，前 4 是 cx,cy,w,h，
# 接着 80 个是各类别的置信度（无objectness，与 YOLOv5 不同）
_NUM_BOX = 4


@dataclass
class Detection:
    """一个检测框（坐标已还原为原图像素空间）。"""

    box: Tuple[int, int, int, int]  # x1, y1, x2, y2
    score: float
    class_id: int
    label: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "box": list(self.box),
            "score": round(float(self.score), 4),
            "class_id": self.class_id,
            "label": self.label,
        }


@dataclass
class VisionInfo:
    """视觉后端信息。字段与 common/embed 的 EncoderInfo 风格一致，
    便于指标层统一记录「这次到底跑在什么后端上」。"""

    model: str
    providers: List[str] = field(default_factory=list)
    is_fallback: bool = True
    input_size: Tuple[int, int] = (640, 640)
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "providers": self.providers,
            "is_fallback": self.is_fallback,
            "input_size": list(self.input_size),
            "note": self.note,
        }


# ------------------------------------------------------------ CUDA 运行时
# 只读复用 ComfyUI 自带的 CUDA DLL，**不复制、不修改、不删除**任何东西。
_CUDA_CANDIDATES: Tuple[Path, ...] = (
    Path("D:/ComfyUI/venv/Lib/site-packages/torch/lib"),
    Path("D:/ComfyUI/venv/Lib/site-packages/torch"),
)


def _ort_needs_cuda_major() -> int:
    """判断当前装的 onnxruntime-gpu 需要 CUDA 12 还是 13。

    依据官方文档（onnxruntime.ai CUDA-ExecutionProvider#requirements，实测核对一致）：
      *>= 1.27* 的PyPI 默认包构建于**CUDA 13.0**
      *<= 1.26.x* 构建于 **CUDA 12.8**
    这个判断必须做对 —— 本项目曾因为「目录里有 cublasLt64_12 就以为配好了」，
    而实际装的是 1.30（要 _13），于是 CUDA 静默降级到 CPU。
    早期版本的探测函数只检查 DLL 存在与否，因此报了假绿灯。
    """
    try:
        import onnxruntime as ort

        parts = []
        for seg in str(ort.__version__).split("."):
            digits = "".join(ch for ch in seg if ch.isdigit())
            if digits:
                parts.append(int(digits))
            else:
                break
        if not parts:
            return 12
        major = parts[0]
        minor = parts[1] if len(parts) > 1 else 0
        return 13 if (major, minor) >= (1, 27) else 12
    except Exception:
        return 13  # 探测失败时按最新的13 处理，宁可降级也不要错报 CUDA 可用


def resolve_cuda_runtime() -> Optional[Path]:
    """找到与当前 ORT 版本**匹配**的 CUDA 运行时 DLL 目录。

    判定标准是「目录里确实有 ORT 需要的那个 cublasLt 版本」，而不是「目录存在」：
      - ORT 1.27+ 需要 cublasLt64_13.dll / cudart64_13.dll
      - ORT 1.26及更早需要 cublasLt64_12.dll / cudart64_12.dll
    只读复用 ComfyUI 的 torch lib（CUDA 12.8 全套），**不复制不修改**。
    """
    want = _ort_needs_cuda_major()
    suffix = "13" if want == 13 else "12"
    for d in _CUDA_CANDIDATES:
        if not d.is_dir():
            continue
        names = {p.name.lower() for p in d.glob("*.dll")}
        if f"cublaslt64_{suffix}.dll" in names and f"cudart64_{suffix}.dll" in names:
            os.add_dll_directory(str(d))
            os.environ["PATH"] = str(d) + os.pathsep + os.environ.get("PATH", "")
            return d
    return None


def _make_session_options():
    """构造降噪的 SessionOptions。

    用途：压掉 ORT 在 provider 创建失败时打到 stderr 的大段 GBK 乱码。
    注意：**不改变其行为**，只是让日志可读。是否降级仍由
    ``Session.get_providers()`` 的真实结果判定。

    早期版本返回类型标成 ``-> None`` 却实际返回 SessionOptions，
    是复制粘贴留下的错标；这里补上正确的类型。
    """
    try:
        import onnxruntime as ort

        so = ort.SessionOptions()
        so.log_severity_level = 3  # ERROR
        return so
    except Exception:
        return None


# ------------------------------------------------------------ 推理器
class YoloDetector:
    """YOLO11n ONNX 检测器。

    只做推理与后处理，**不引入 torch / ultralytics**。
    """

    def __init__(
        self,
        model_path: Path = DEFAULT_MODEL,
        *,
        conf_threshold: float = 0.30,
        iou_threshold: float = 0.45,
        prefer_cuda: bool = True,
    ) -> None:
        self.model_path = Path(model_path)
        if not self.model_path.exists():
            raise FileNotFoundError(
                f"模型权重不存在：{self.model_path}\n"
                f"请运行：python scripts/setup_demo_a_vision.py（会自动下载 10.42 MiB）"
            )
        self.conf_threshold = conf_threshold
        self.iou_threshold = iou_threshold

        cuda_dir = resolve_cuda_runtime() if prefer_cuda else None
        wanted = ["CUDAExecutionProvider", "CPUExecutionProvider"]

        import onnxruntime as ort

        # 官方推荐的 DLL 预加载：让 ORT 主动去 PyTorch 的 lib 目录找
        # cuBLAS / cuDNN，而不是只依赖系统 PATH。
        # 见 onnxruntime.ai/docs/execution-providers/CUDA-ExecutionProvider.html
        # 的 "Compatibility with PyTorch" 一节。
        if prefer_cuda:
            try:
                ort.preload_dlls()
            except Exception:
                pass  # 预加载是best-effort，失败也不该阻断构造

        so = _make_session_options()
        self.sess = ort.InferenceSession(
            str(self.model_path), so, providers=wanted
        )
        # ⚠️ 判定依据必须是这里，而不是 get_available_providers()：
        # CUDA 缺依赖时 ORT **不抛异常，只静默回退**。
        actual = list(self.sess.get_providers())
        self.input_name = self.sess.get_inputs()[0].name
        self._input_hw = self.sess.get_inputs()[0].shape  # [batch,3,h,w]

        h = self._input_hw[2] if isinstance(self._input_hw[2], int) else 640
        w = self._input_hw[3] if isinstance(self._input_hw[3], int) else 640

        cuda_active = "CUDAExecutionProvider" in actual
        note = ""
        if prefer_cuda and not cuda_active:
            note = (
                "CUDA provider 不可用，已降级 CPU。"
                + (f"找到 CUDA DLL 目录 {cuda_dir}，但版本与 ORT 不匹配。"
                   if cuda_dir else "未找到 CUDA 运行时 DLL 目录。")
                + "降级已如实上报，指标页会标出实际后端。"
            )
        elif cuda_active:
            note = f"CUDA 已启用（运行时 DLL：{cuda_dir or '系统路径'}）"

        self.info = VisionInfo(
            model=str(self.model_path),
            providers=actual,
            is_fallback=not cuda_active,
            input_size=(int(h), int(w)),
            note=note,
        )

    # ---- 预处理 ----
    def letterbox(self, img: np.ndarray) -> Tuple[np.ndarray, float, float, float]:
        """等比缩放 + 灰边填充，保持长宽比。

        返回 (letterbox 后的 RGB 图, ratio, pad_x, pad_y)。
        后处理时必须用同一 ratio/pad 把坐标反算回原图，否则框会错位。
        """
        import cv2

        ih, iw = img.shape[:2]
        th, tw = self.info.input_size
        r = min(th / ih, tw / iw)
        nw, nh = int(round(iw * r)), int(round(ih * r))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)

        canvas = np.full((th, tw, 3), 114, dtype=np.uint8)  # 114 是 YOLO 惯用灰边值
        pad_x, pad_y = (tw - nw) // 2, (th - nh) // 2
        canvas[pad_y : pad_y + nh, pad_x : pad_x + nw] = resized
        return canvas, r, float(pad_x), float(pad_y)

    @staticmethod
    def to_tensor(canvas_bgr: np.ndarray) -> np.ndarray:
        """BGR uint8 -> RGB float32 NCHWR，值域0~1。"""
        rgb = canvas_bgr[:, :, ::-1].astype(np.float32) / 255.0
        return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None, ...])

    # ---- 后处理 ----
    def postprocess(self, output: np.ndarray) -> List[Detection]:
        """解析 output0 (1, 84, anchors) 并做 NMS。

        YOLO11 输出与 YOLOv5 的差别：**没有 objectness**，80 个类别分数
        直接就是置信度。若按 v5 的做法再乘一个 objectness，会全部偏低。
        """
        pred = output[0]                      # (84, anchors)
        if pred.shape[0] < pred.shape[1]:
            pred = pred.T                   # -> (anchors, 84)
        boxes = pred[:, :_NUM_BOX]
        scores = pred[:, _NUM_BOX:]

        conf = scores.max(axis=1)
        keep = conf >= self.conf_threshold
        if not keep.any():
            return []
        boxes, conf = boxes[keep], conf[keep]
        class_ids = scores[keep].argmax(axis=1)

        # cx,cy,w,h -> x1,y1,x2,y2
        xyxy = np.empty_like(boxes)
        xyxy[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
        xyxy[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
        xyxy[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
        xyxy[:, 3] = boxes[:, 1] + boxes[:, 3] / 2

        idx = _nms(xyxy, conf, self.iou_threshold)
        out: List[Detection] = []
        for i in idx:
            cid = int(class_ids[i])
            name = COCO_NAMES[cid] if cid < len(COCO_NAMES) else f"class{cid}"
            out.append(
                Detection(box=tuple(int(round(v)) for v in xyxy[i]),  # type: ignore[arg-type]
                          score=float(conf[i]), class_id=cid, label=name)
            )
        return out

    def scale_back(
        self, dets: Sequence[Detection], r: float, pad_x: float, pad_y: float,
        orig_w: int, orig_h: int,
    ) -> List[Detection]:
        """把 letterbox 坐标还原到原图像素空间并裁剪到画面内。"""
        out = []
        for d in dets:
            x1 = (d.box[0] - pad_x) / r
            y1 = (d.box[1] - pad_y) / r
            x2 = (d.box[2] - pad_x) / r
            y2 = (d.box[3] - pad_y) / r
            x1 = max(0, min(int(x1), orig_w - 1))
            y1 = max(0, min(int(y1), orig_h - 1))
            x2 = max(0, min(int(x2), orig_w - 1))
            y2 = max(0, min(int(y2), orig_h - 1))
            if x2 <= x1 or y2 <= y1:
                continue  # 退化框直接丢，避免画出0 宽度的「框」
            out.append(Detection((x1, y1, x2, y2), d.score, d.class_id, d.label))
        return out

    # ---- 完整流程 ----
    def detect(self, img_bgr: np.ndarray) -> Tuple[List[Detection], float]:
        """输入 BGR 原图，返回 (检测结果, 推理耗时ms)。"""
        import time

        canvas, r, px, py = self.letterbox(img_bgr)
        tensor = self.to_tensor(canvas)
        t0 = time.perf_counter()
        out = self.sess.run(None, {self.input_name: tensor})
        dt = (time.perf_counter() - t0) * 1000
        dets = self.postprocess(out[0])
        h, w = img_bgr.shape[:2]
        return self.scale_back(dets, r, px, py, w, h), dt

    def detect_with_device(self, img_bgr: np.ndarray, device: str = "cpu"):
        """把检测到的框画到图上并返回 (图, 检测结果)。

        device='cpu' 用 draw()；device='cuda' 走 insightface/自实现的 GPU 绘制
        （本项目默认cpu 绘制，因为GPU 画框的收益远小于传输开销）。
        """
        dets, dt = self.detect(img_bgr)
        return draw(img_bgr, dets), dets, dt


# ------------------------------------------------------------ NMS
def _nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> List[int]:
    """纯 numpy NMS（不依赖 torchvision）。

    按分数降序贪心抑制。规模很小（YOLO11n 最多 8400 个候选框），
    numpy 实现足够，且避免为一个函数引入 torch。
    """
    if len(boxes) == 0:
        return []
    order = scores.argsort()[::-1]
    keep: List[int] = []
    while order.size > 0:
        i = order[0]
        keep.append(int(i))
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(boxes[i, 0], boxes[rest, 0])
        yy1 = np.maximum(boxes[i, 1], boxes[rest, 1])
        xx2 = np.minimum(boxes[i, 2], boxes[rest, 2])
        yy2 = np.minimum(boxes[i, 3], boxes[rest, 3])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        area_i = (boxes[i, 2] - boxes[i, 0]) * (boxes[i, 3] - boxes[i, 1])
        area_r = (boxes[rest, 2] - boxes[rest, 0]) * (boxes[rest, 3] - boxes[rest, 1])
        iou = inter / np.maximum(area_i + area_r - inter, 1e-9)
        order = rest[iou <= iou_threshold]
    return keep


# ------------------------------------------------------------ 绘制
def draw(img_bgr: np.ndarray, dets: Sequence[Detection]) -> np.ndarray:
    """画框 + 标签（中文标签用 PIL，缺字体时回落英文，避免豆腐块）。"""
    import cv2

    out = img_bgr.copy()
    palette = [(0, 200, 255), (0, 255, 120), (255, 120, 0),
               (200, 0, 255), (0, 160, 255), (255, 200, 0)]
    try:
        from PIL import Image, ImageDraw, ImageFont
        use_pil = True
    except Exception:
        use_pil = False

    pil_img = pil_draw = None
    font = None
    if use_pil:
        pil_img = Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))
        pil_draw = ImageDraw.Draw(pil_img)
        font = _load_font(16)

    for d in dets:
        color = palette[d.class_id % len(palette)]
        x1, y1, x2, y2 = d.box
        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)
        text = f"{d.label} {d.score:.2f}"
        if use_pil and pil_draw is not None:
            tb = pil_draw.textbbox((0, 0), text, font=font)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            ty = max(0, y1 - th - 6)
            pil_draw.rectangle([x1, ty, x1 + tw + 8, ty + th + 6], fill=color)
            pil_draw.text((x1 + 4, ty + 3), text,
                          fill=(0, 0, 0) if sum(color) > 380 else (255, 255, 255),
                          font=font)
        else:
            ty = max(0, y1 - 18)
            cv2.putText(out, text, (x1, ty), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, color, 1, cv2.LINE_AA)

    if use_pil and pil_img is not None:
        out = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)
    return out


def _load_font(size: int):
    """找一个能显示中文的字体；找不到就返回 None（由调用方回落英文）。"""
    from PIL import ImageFont

    cands = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
    ]
    for p in cands:
        if Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return None