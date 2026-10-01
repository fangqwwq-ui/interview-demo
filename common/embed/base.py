"""向量化后端抽象。

为什么要有降级路径：
演示现场不保证能临时装好 torch（本项目实测带宽仅 ~60–340 KB/s，装一次 torch 要数小时）。
因此把「语义向量化」做成可插拔能力：
  - 首选 SentenceTransformer（bge-small-zh-v1.5，质量好）
  - 兜底 HashingNgramEncoder（纯 numpy，零依赖，可离线跑通全流程）

无论走哪条，都必须把「本次用的是哪个后端」记进结果里 —— 否则指标不可复现。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List

import numpy as np


@dataclass
class EncoderInfo:
    backend: str
    model: str
    dim: int
    is_fallback: bool = False

    def as_dict(self) -> dict:
        return {
            "backend": self.backend,
            "model": self.model,
            "dim": self.dim,
            "is_fallback": self.is_fallback,
        }


class Encoder(ABC):
    """统一契约：encode(list[str]) -> (n, dim) 且已 L2 归一化，内积即余弦相似度。"""

    info: EncoderInfo

    @abstractmethod
    def encode(self, texts: List[str]) -> np.ndarray:
        ...

    def encode_one(self, text: str) -> np.ndarray:
        return self.encode([text])[0]


def normalize_rows(mat: np.ndarray) -> np.ndarray:
    """L2 归一化，使内积等价于余弦相似度。零向量保持为零向量。"""
    mat = np.asarray(mat, dtype=np.float32)
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms
