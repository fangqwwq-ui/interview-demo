"""零依赖向量库：numpy 暴力内积检索。

Demo 语料规模（数千~数万条）下与 FAISS 结果一致，只是复杂度 O(N)。
真实场景应在十万级以上换 FAISS/IVF，这一点在口径页明确写出，
避免把「能跑通」包装成「高性能」。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from common.vectorstore.base import Hit, StoreInfo, VectorStore, _as_2d


class NumpyStore(VectorStore):
    def __init__(self, dim: int = 512, metric: str = "cosine") -> None:
        self.dim = dim
        self._vectors: np.ndarray = np.zeros((0, dim), dtype=np.float32)
        self._metas: List[Dict[str, Any]] = []
        self.info = StoreInfo(
            backend="numpy-bruteforce",
            dim=dim,
            metric=metric,
            is_fallback=True,
            extra={"note": "O(N) 暴力检索，仅适用于 Demo 规模"},
        )

    def add(self, vectors: np.ndarray, metas: List[Dict[str, Any]]) -> None:
        arr = _as_2d(vectors)
        if arr.shape[1] != self.dim:
            raise ValueError(f"dim mismatch: store={self.dim}, incoming={arr.shape[1]}")
        if len(metas) != arr.shape[0]:
            raise ValueError(f"metas({len(metas)}) != vectors({arr.shape[0]})")
        self._vectors = np.vstack([self._vectors, arr]) if len(self._vectors) else arr
        self._metas.extend(metas)
        self.info.size = len(self._metas)

    def search(self, query: np.ndarray, k: int = 5) -> List[Hit]:
        if not self._metas:
            return []
        q = _as_2d(query)[0]
        scores = self._vectors @ q  # 向量已 L2 归一化，内积 == 余弦
        k = min(k, len(self._metas))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [Hit(score=float(scores[i]), meta=self._metas[i]) for i in top]

    def save(self, path: str) -> None:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        np.save(p / "vectors.npy", self._vectors)
        with (p / "meta.jsonl").open("w", encoding="utf-8") as fh:
            for m in self._metas:
                fh.write(json.dumps(m, ensure_ascii=False) + "\n")
        (p / "info.json").write_text(
            json.dumps(self.info.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load(self, path: str) -> None:
        p = Path(path)
        self._vectors = np.load(p / "vectors.npy")
        self._metas = [
            json.loads(line) for line in (p / "meta.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        self.dim = self._vectors.shape[1]
        self.info.dim = self.dim
        self.info.size = len(self._metas)
