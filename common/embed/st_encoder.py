"""正式向量化后端：sentence-transformers + BAAI/bge-small-zh-v1.5。

选型理由：512 维、中文检索性价比高、CPU 也能跑（不占 8 GB 显存给视觉模型）。
模型权重默认缓存在 `models/` 下，随项目走，避免演示现场临时下载。

⚠️ 国内网络提示：HuggingFace 直连通常不可用，建议在 `.env` 里设
    HF_ENDPOINT=https://hf-mirror.com
本模块会在加载失败时把该建议写进异常信息。
"""

from __future__ import annotations

import os
from typing import List

import numpy as np

from common import config
from common.embed.base import Encoder, EncoderInfo, normalize_rows


class SentenceTransformerEncoder(Encoder):
    backend_name = "sentence-transformers"

    def __init__(self, model_name: str | None = None, cache_dir: str | None = None) -> None:
        # 延迟导入：没有 torch 的环境也要能 import 本模块（否则工厂无法优雅降级）
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self.model_name = model_name or config.EMBED_MODEL_NAME
        self.cache_dir = cache_dir or str(config.paths.models / "hf")

        try:
            self._model = SentenceTransformer(self.model_name, cache_folder=self.cache_dir)
        except Exception as exc:  # noqa: BLE001
            hint = ""
            if not os.getenv("HF_ENDPOINT"):
                hint = (
                    " 提示：国内网络建议在 .env 中设置 "
                    "HF_ENDPOINT=https://hf-mirror.com 后重试。"
                )
            raise RuntimeError(
                f"failed to load embedding model {self.model_name!r}: "
                f"{type(exc).__name__}: {exc}.{hint}"
            ) from exc

        dim = int(self._model.get_sentence_embedding_dimension())
        self.info = EncoderInfo(
            backend=self.backend_name,
            model=self.model_name,
            dim=dim,
            is_fallback=False,
        )

    def encode(self, texts: List[str]) -> np.ndarray:
        vecs = self._model.encode(
            texts,
            batch_size=32,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return normalize_rows(vecs)
