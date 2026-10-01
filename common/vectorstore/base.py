"""向量库抽象。

与本项目其他外部依赖一致采用「可插拔 + 降级」模式：
  - 首选 FAISS（IndexFlatIP，内积即余弦，因为向量已归一化）
  - 兜底 NumpyStore（暴力检索，零依赖）

语料规模在 Demo 级别（数千到数万条）时两者结果等价，差异只在速度。
因此口径页可以放心说明：切换后端不影响指标数值，只影响耗时。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class Hit:
    score: float
    meta: Dict[str, Any]

    def as_dict(self) -> Dict[str, Any]:
        return {"score": round(float(self.score), 4), **self.meta}


@dataclass
class StoreInfo:
    backend: str
    dim: int
    size: int = 0
    metric: str = "cosine"
    is_fallback: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        d = {
            "backend": self.backend,
            "dim": self.dim,
            "size": self.size,
            "metric": self.metric,
            "is_fallback": self.is_fallback,
        }
        d.update(self.extra)
        return d


class VectorStore(ABC):
    """最小契约：加、查、存、读。故意不做过滤/删除等复杂能力。"""

    info: StoreInfo

    @abstractmethod
    def add(self, vectors: np.ndarray, metas: List[Dict[str, Any]]) -> None:
        ...

    @abstractmethod
    def search(self, query: np.ndarray, k: int = 5) -> List[Hit]:
        ...

    @abstractmethod
    def save(self, path: str) -> None:
        ...

    @abstractmethod
    def load(self, path: str) -> None:
        ...

    def __len__(self) -> int:
        return self.info.size


def _as_2d(vec: np.ndarray) -> np.ndarray:
    arr = np.asarray(vec, dtype=np.float32)
    return arr.reshape(1, -1) if arr.ndim == 1 else arr
