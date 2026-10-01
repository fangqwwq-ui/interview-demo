"""零依赖图片头解析。

## 为什么单独成一个模块

这个能力有两个使用方，且它们的依赖预算不同：

| 使用方 | 需要 | 依赖预算 |
|---|---|---|
| `comfy_adapter` | 知道本次出图的真实尺寸 | 已有 httpx |
| `review` 规则通道 | 判断资产是否损坏 / 是否疑似占位图 | **希望零依赖可用** |

如果把它留在适配器里，审核层就被迫依赖 httpx 才能跑规则 —— 这会让
「审核层可独立运行」这句话变成空话。因此抽出来，两边共用一个实现，
避免同一段解析逻辑出现两份（两份就会漂移，其中一份先坏掉）。

## 为什么不用 Pillow

尺寸写在文件头里，读 24 个字节就有。为一个尺寸引入图像库不划算，而且
Pillow 在本项目是**可选**依赖（`requirements/demo_c_runtime.txt`），
缺了不该让出图链路或审核链路断掉。

支持 PNG（ComfyUI `SaveImage` 默认输出）与 JPEG 的 SOF 段扫描。
"""

from __future__ import annotations

import struct
from typing import Optional, Tuple

# JPEG 里承载真实尺寸的 SOF（Start Of Frame）标记
_JPEG_SOF_MARKERS = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)


def is_png(blob: bytes) -> bool:
    return len(blob) >= 8 and blob[:8] == b"\x89PNG\r\n\x1a\n"


def is_jpeg(blob: bytes) -> bool:
    return len(blob) >= 3 and blob[:2] == b"\xff\xd8" and blob[2] == 0xFF


def image_size(blob: bytes) -> Optional[Tuple[int, int]]:
    """从字节头读出图片尺寸；无法识别返回 None（不抛异常）。

    返回 None 表示「这不是本模块认识的图片格式」或「头部被截断」，
    调用方应据此判定资产损坏，而不是当成异常崩溃。
    """
    if is_png(blob):
        if len(blob) < 24:
            return None
        w, h = struct.unpack(">II", blob[16:24])
        return int(w), int(h)

    if len(blob) >= 4 and blob[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(blob):
            if blob[i] != 0xFF:
                i += 1
                continue
            marker = blob[i + 1]
            if marker in _JPEG_SOF_MARKERS:
                h, w = struct.unpack(">HH", blob[i + 5 : i + 9])
                return int(w), int(h)
            # 无长度字段的标记，直接跳 2 字节
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg_len = struct.unpack(">H", blob[i + 2 : i + 4])[0]
            i += 2 + seg_len
    return None


def bytes_per_pixel(blob: bytes) -> Optional[float]:
    """字节数 / 像素数。

    这是判断「疑似占位图 / 纯色图」的代理量：真实 SDXL 出图约 1.3–1.5 B/px，
    而纯色补丁图能被 PNG 压到 0.05 以下。**它是启发式，不是判定性证据** ——
    一张构图极简的真实出图也可能偏低，所以规则通道只给警告、不给阻断。
    """
    size = image_size(blob)
    if not size:
        return None
    w, h = size
    if w <= 0 or h <= 0:
        return None
    return round(len(blob) / float(w * h), 5)
