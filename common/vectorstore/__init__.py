"""向量库工厂。

    from common.vectorstore import get_store
    store = get_store(dim=512)          # auto：优先 FAISS，失败降级 numpy
    store = get_store(dim=512, prefer="faiss")
    store = get_store(dim=512, prefer="numpy")
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from common.vectorstore.base import Hit, StoreInfo, VectorStore
from common.vectorstore.numpy_store import NumpyStore

__all__ = ["Hit", "StoreInfo", "VectorStore", "NumpyStore", "get_store"]


def get_store(dim: int, prefer: str = "auto", metric: str = "cosine") -> VectorStore:
    prefer = (prefer or "auto").lower()

    if prefer == "numpy":
        return NumpyStore(dim=dim, metric=metric)

    try:
        from common.vectorstore.faiss_store import FaissStore

        store = FaissStore(dim=dim, metric=metric)
        logger.info(f"向量库后端：{store.info.backend} (dim={dim})")
        return store
    except Exception as exc:  # noqa: BLE001
        if prefer == "faiss":
            raise
        logger.warning(f"FAISS 不可用，降级 numpy 暴力检索：{type(exc).__name__}: {exc}")
        store = NumpyStore(dim=dim, metric=metric)
        logger.warning(f"向量库后端：{store.info.backend}（O(N)，仅适用 Demo 规模）")
        return store
