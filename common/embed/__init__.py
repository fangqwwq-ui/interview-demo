"""向量化后端工厂。

用法：
    from common.embed import get_encoder
    enc = get_encoder()          # auto：优先语义模型，失败自动降级
    enc = get_encoder("st")      # 强制语义模型，失败即抛错
    enc = get_encoder("hashing") # 强制零依赖兜底

无论哪条路径，都通过 `enc.info` 暴露后端来源，供指标口径记录。
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from common.embed.base import Encoder, EncoderInfo, normalize_rows
from common.embed.hashing_encoder import HashingNgramEncoder

__all__ = [
    "Encoder",
    "EncoderInfo",
    "normalize_rows",
    "HashingNgramEncoder",
    "get_encoder",
]

_cached: Optional[Encoder] = None


def get_encoder(prefer: str = "auto", *, force_reload: bool = False) -> Encoder:
    """构建或复用向量化后端。

    prefer:
        auto    优先 sentence-transformers，失败降级 hashing（默认）
        st      强制 sentence-transformers
        hashing 强制零依赖兜底
    """
    global _cached
    if _cached is not None and not force_reload:
        return _cached

    prefer = (prefer or "auto").lower()

    if prefer == "hashing":
        _cached = HashingNgramEncoder()
        logger.warning(f"向量化后端：{_cached.info.backend}（强制兜底，无语义能力）")
        return _cached

    try:
        from common.embed.st_encoder import SentenceTransformerEncoder

        _cached = SentenceTransformerEncoder()
        logger.info(
            f"向量化后端：{_cached.info.backend} / {_cached.info.model} "
            f"(dim={_cached.info.dim})"
        )
        return _cached
    except Exception as exc:  # noqa: BLE001
        if prefer == "st":
            raise
        logger.warning(f"语义向量化不可用，降级为零依赖兜底：{type(exc).__name__}: {exc}")
        _cached = HashingNgramEncoder()
        logger.warning(
            f"向量化后端：{_cached.info.backend} —— 无语义能力，"
            "仅用于跑通链路；正式评测请装 torch + sentence-transformers"
        )
        return _cached
