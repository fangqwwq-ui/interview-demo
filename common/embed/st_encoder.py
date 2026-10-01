"""正式向量化后端：sentence-transformers + BAAI/bge-small-zh-v1.5。

选型理由：512 维、中文检索性价比高、CPU 也能跑（不占 8 GB 显存给视觉模型）。
模型权重默认缓存在 `models/` 下，随项目走，避免演示现场临时下载。

⚠️ 国内网络提示：HuggingFace 直连通常不可用，建议在 `.env` 里设
    HF_ENDPOINT=https://hf-mirror.com
本模块会在加载失败时把该建议写进异常信息。

## 为什么优先加载「本地目录」而不是让库自己去 Hub 下

实测（2026-10-01）：`huggingface.co` 被代理拦截（502），镜像 `hf-mirror.com`
的**单文件下载正常**，但 `transformers 5.17` / `huggingface_hub 1.33` 加载模型时
还会请求若干 **API 元数据接口**，镜像对这些接口返回的不是 JSON，报：

    json.decoder.JSONDecodeError: Expecting value: line 1 column 1 (char 0)

即「库自动下载」这条路在本机断了。因此本模块**先找本地目录**
（`models/<模型名最后一段>/`，由 `scripts/fetch_embed_model.py` 预取），
找到就直接加载本地路径，完全不走网络。

这样还带来一个演示价值：**断网也能起向量检索**，不必依赖现场网络。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import numpy as np

from common import config
from common.embed.base import Encoder, EncoderInfo, normalize_rows


def resolve_local_model(model_name: str) -> Optional[Path]:
    """把 `BAAI/bge-small-zh-v1.5` 这类 hub id 映射到本地目录。

    只认「本地目录里确实有 config.json」的情况，避免把空目录当成有效模型，
    那样会在更靠后的地方报出更难懂的错。
    """
    candidate = config.paths.models / model_name.split("/")[-1]
    if (candidate / "config.json").exists():
        return candidate
    return None


class SentenceTransformerEncoder(Encoder):
    backend_name = "sentence-transformers"

    def __init__(self, model_name: str | None = None, cache_dir: str | None = None) -> None:
        # 延迟导入：没有 torch 的环境也要能 import 本模块（否则工厂无法优雅降级）
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        self.model_name = model_name or config.EMBED_MODEL_NAME
        self.cache_dir = cache_dir or str(config.paths.models / "hf")

        local = resolve_local_model(self.model_name)
        self.loaded_from = str(local) if local else self.model_name
        self.local_only = bool(local)

        try:
            if local:
                # 本地路径加载，不经网络
                self._model = SentenceTransformer(str(local))
            else:
                self._model = SentenceTransformer(self.model_name, cache_folder=self.cache_dir)
        except Exception as exc:  # noqa: BLE001
            hint = (
                f" 提示：本机未找到本地模型目录 "
                f"{config.paths.models / self.model_name.split('/')[-1]}。"
                "可运行 `python scripts/fetch_embed_model.py` 从镜像预取（约 96 MB），"
                "比让库直连 Hub 更可靠。"
            )
            raise RuntimeError(
                f"failed to load embedding model {self.model_name!r}: "
                f"{type(exc).__name__}: {exc}.{hint}"
            ) from exc

        # ⚠️ sentence-transformers ≥ 6 把该方法改名为 `get_embedding_dimension`，
        #    旧名仍可用但会抛 FutureWarning（污染演示时的日志输出）。
        #    这里优先用新名，旧版本回落到旧名 —— 两个版本都能跑且都不出声。
        getter = getattr(self._model, "get_embedding_dimension", None) or (
            self._model.get_sentence_embedding_dimension
        )
        dim = int(getter())
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
