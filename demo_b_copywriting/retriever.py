"""检索层：加载索引 + 垂类感知检索。

两个设计要点，都是为了让检索指标可复现、可追问：

**1. 向量化后端必须与建库时一致，否则直接报错。**
   不同编码器产出的向量不在同一空间。用 A 编码器查 B 编码器建的库，
   相似度数值毫无意义，但程序不会报错 —— 会静默产出垃圾结果。
   因此这里把「后端不一致」当成硬错误，而不是 warning。

**2. 垂类过滤做成「重排」而不是「过滤」。**
   向量库契约（common/vectorstore/base.py）刻意只有 add/search，没有过滤器。
   Demo 语料每个垂类仅 7 条，一旦做硬过滤，top-3 可能凑不满。
   所以采用：先超额召回（overfetch），命中垂类的优先，不足再用全局结果补齐。
   好处是永不返回空结果，且交付时能如实说明「垂类字段是重排信号，不是硬约束」。

用法：
    from demo_b_copywriting.retriever import Retriever
    r = Retriever()
    hits, ms = r.search_with_latency("在当今快节奏的职场环境中", k=3, vertical="职场效率")
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from loguru import logger

from common.embed import get_encoder
from common.vectorstore import get_store

INDEX_DIR = Path(__file__).resolve().parent.parent / "data" / "index" / "demo_b"

# 编码器 backend 名 → get_encoder 的 prefer 取值
_ENCODER_PREFER = {
    "sentence-transformers": "st",
    "hashing-ngram": "hashing",
}

# overfetch 倍数：垂类重排需要比 k 更大的候选池
_OVERFETCH = 6


class EncoderMismatchError(RuntimeError):
    """索引记录的编码器与当前可用编码器不一致。宁可报错，不产出无意义的相似度。"""


class Retriever:
    def __init__(
        self,
        index_dir: Path = INDEX_DIR,
        *,
        prefer_encoder: str = "auto",
        prefer_store: str = "auto",
        allow_encoder_mismatch: bool = False,
    ) -> None:
        self.index_dir = Path(index_dir)
        self.build_info = self._read_json(self.index_dir / "build_info.json", "build_info.json")
        self.encoder = get_encoder(prefer_encoder)
        self._check_encoder(allow_encoder_mismatch)
        self.store = self._load_store(prefer_store)
        self.last_latency_ms: float = 0.0

    # ---------------------------------------------------------- 加载

    @staticmethod
    def _read_json(path: Path, name: str) -> Dict[str, Any]:
        if not path.exists():
            raise FileNotFoundError(
                f"缺少 {path} —— 请先运行 `python demo_b_copywriting/build_index.py`"
            )
        return json.loads(path.read_text(encoding="utf-8"))

    def _check_encoder(self, allow_mismatch: bool) -> None:
        recorded = (self.build_info.get("encoder") or {}).get("backend")
        current = self.encoder.info.backend
        if recorded and recorded != current:
            msg = (
                f"编码器不一致：索引由 {recorded!r} 构建，当前可用的是 {current!r}。"
                "两者向量不在同一空间，相似度数值无意义。"
                "请重建索引，或安装对应依赖。"
            )
            if allow_mismatch:
                logger.warning(msg + "（已按要求忽略，结果不可用于指标）")
            else:
                raise EncoderMismatchError(msg)

    def _load_store(self, prefer_store: str):
        """加载向量库。

        优先用建库时的后端；不可用则退回 numpy 权威副本（vectors.npy）。
        """
        native = self._read_json(self.index_dir / "info.json", "info.json")
        recorded_backend = str(native.get("backend", ""))
        dim = int(self.build_info.get("encoder", {}).get("dim") or native.get("dim") or 0)

        wants_faiss = recorded_backend.startswith("faiss") and prefer_store != "numpy"
        if wants_faiss and (self.index_dir / "index.faiss").exists():
            try:
                store = get_store(dim=dim, prefer="faiss")
                store.load(str(self.index_dir))
                logger.info(f"向量库已加载：{store.info.backend}（建库后端 {recorded_backend}）")
                return store
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    f"FAISS 索引加载失败（{type(exc).__name__}: {exc}），"
                    "回退到 numpy 权威副本 vectors.npy"
                )

        if not (self.index_dir / "vectors.npy").exists():
            raise FileNotFoundError(
                f"{self.index_dir} 下既无可用 index.faiss，也无 vectors.npy。请重建索引。"
            )
        store = get_store(dim=dim, prefer="numpy")
        store.load(str(self.index_dir))
        logger.info(f"向量库已加载：{store.info.backend}（源自 numpy 权威副本）")
        return store

    # ---------------------------------------------------------- 检索

    @property
    def info(self) -> Dict[str, Any]:
        return {
            "index_dir": str(self.index_dir),
            "doc_count": self.build_info.get("doc_count"),
            "corpus_digest": self.build_info.get("corpus_digest"),
            "built_at": self.build_info.get("built_at"),
            "encoder": self.encoder.info.as_dict(),
            "store": self.store.info.as_dict(),
            "built_with_encoder": (self.build_info.get("encoder") or {}).get("backend"),
        }

    def search(self, text: str, k: int = 3, vertical: Optional[str] = None) -> List[Dict[str, Any]]:
        hits, _ = self.search_with_latency(text, k=k, vertical=vertical)
        return hits

    def search_with_latency(
        self, text: str, k: int = 3, vertical: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], float]:
        """检索 top-k。返回 (结果列表, 耗时毫秒)。

        耗时是 b_retrieval_latency 指标的原始数据，在这里就地测量，
        避免调用方各测一套导致口径不一。
        """
        if not text or not text.strip():
            return [], 0.0

        size = int(self.store.info.size or 0)
        fetch = min(size, max(k, k * _OVERFETCH))

        q = self.encoder.encode_one(text)
        started = time.perf_counter()
        raw = self.store.search(np.asarray(q, dtype=np.float32), k=fetch)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.last_latency_ms = elapsed_ms

        items = [{**h.meta, "score": round(float(h.score), 4)} for h in raw]

        if vertical and items:
            matched = [it for it in items if it.get("vertical") == vertical]
            others = [it for it in items if it.get("vertical") != vertical]
            items = matched + others
        return items[:k], round(elapsed_ms, 3)

    def benchmark(
        self, queries: List[str], k: int = 3, *, repeat: int = 3
    ) -> Dict[str, Any]:
        """测量检索耗时（取多次中位数）。

        为什么报中位数而不是均值：单次检索耗时会受 Python GC、CPU 频率影响，
        均值容易被个别抖动拉偏。中位数更能代表「典型一次检索」。
        """
        per_query: List[float] = []
        for q in queries:
            for _ in range(repeat):
                # 只测向量检索本身：编码耗时由 encoder 决定，不属于本指标口径
                qv = self.encoder.encode_one(q)
                started = time.perf_counter()
                self.store.search(np.asarray(qv, dtype=np.float32), k=k)
                per_query.append((time.perf_counter() - started) * 1000.0)

        if not per_query:
            return {}
        s = sorted(per_query)
        n = len(s)
        median = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
        return {
            "queries": len(queries),
            "repeat": repeat,
            "samples": n,
            "median_ms": round(median, 3),
            "mean_ms": round(sum(s) / n, 3),
            "p95_ms": round(s[min(n - 1, int(n * 0.95))], 3),
            "min_ms": round(s[0], 3),
            "max_ms": round(s[-1], 3),
            "store_backend": self.store.info.backend,
        }


def load_or_build(index_dir: Path = INDEX_DIR, **kwargs: Any) -> Retriever:
    """索引不存在时自动构建一次，省掉演示时的两条命令。"""
    if not (Path(index_dir) / "build_info.json").exists():
        logger.info("索引不存在，先构建一次……")
        from demo_b_copywriting.build_index import build  # noqa: PLC0415

        build(out_dir=Path(index_dir))
    return Retriever(index_dir, **kwargs)
