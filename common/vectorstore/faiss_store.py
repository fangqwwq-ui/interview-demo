"""FAISS 向量库（IndexFlatIP）。

为什么用 IndexFlatIP 而非 IVF：
  内积（向量已 L2 归一化 → 等价余弦）+ 精确检索，结果与兜底的 NumpyStore 完全一致，
  这样「换后端不改指标」这句话才站得住。十万级以上再考虑 IVF/HNSW 并重新标定召回率。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np

from common.vectorstore.base import Hit, StoreInfo, VectorStore, _as_2d


class FaissStore(VectorStore):
    def __init__(self, dim: int = 512, metric: str = "cosine") -> None:
        import faiss  # noqa: PLC0415 - 延迟导入，缺失时由工厂降级

        self._faiss = faiss
        self.dim = dim
        self._index = faiss.IndexFlatIP(dim)
        self._metas: List[Dict[str, Any]] = []
        self.info = StoreInfo(
            backend="faiss-flat-ip",
            dim=dim,
            metric=metric,
            is_fallback=False,
            extra={"index": "IndexFlatIP", "exact": True},
        )

    def add(self, vectors: np.ndarray, metas: List[Dict[str, Any]]) -> None:
        arr = _as_2d(vectors).astype(np.float32)
        if arr.shape[1] != self.dim:
            raise ValueError(f"dim mismatch: store={self.dim}, incoming={arr.shape[1]}")
        if len(metas) != arr.shape[0]:
            raise ValueError(f"metas({len(metas)}) != vectors({arr.shape[0]})")
        self._index.add(arr)
        self._metas.extend(metas)
        self.info.size = self._index.ntotal

    def search(self, query: np.ndarray, k: int = 5) -> List[Hit]:
        if not self._metas:
            return []
        q = _as_2d(query).astype(np.float32)
        k = min(k, len(self._metas))
        scores, idx = self._index.search(q, k)
        return [
            Hit(score=float(s), meta=self._metas[int(i)])
            for s, i in zip(scores[0], idx[0])
            if i >= 0
        ]

    def save(self, path: str) -> None:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        self._faiss.write_index(self._index, str(p / "index.faiss"))
        with (p / "meta.jsonl").open("w", encoding="utf-8") as fh:
            for m in self._metas:
                fh.write(json.dumps(m, ensure_ascii=False) + "\n")
        (p / "info.json").write_text(
            json.dumps(self.info.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def load(self, path: str) -> None:
        p = Path(path)
        self._index = self._faiss.read_index(str(p / "index.faiss"))
        self._metas = [
            json.loads(line)
            for line in (p / "meta.jsonl").read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        self.dim = self._index.d
        self.info.dim = self.dim
        self.info.size = self._index.ntotal
