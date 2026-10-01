"""零依赖兜底向量化：中文字符 n-gram + 特征哈希 + 子线性 TF 加权。

定位说明（重要，避免被误解为「宣称自研语义模型」）：
这不是语义模型，**不捕捉语义相似性**，只是把文本映射成稀疏词面特征并稠密化。
它的唯一用途是：在没有 torch 的环境下让检索链路的**代码路径与前缀流程可完整跑通**，
以及在演示现场作为保底，保证 Demo 不因依赖缺失而挂掉。

生产/正式评测一律用 `SentenceTransformerEncoder`。
结果里通过 EncoderInfo.is_fallback 标记，口径页据此说明。
"""

from __future__ import annotations

import hashlib
import re
from typing import List

import numpy as np

from common.embed.base import Encoder, EncoderInfo, normalize_rows

_WORD_RE = re.compile(r"[\u4e00-\u9fff]+|[A-Za-z0-9]+")


class HashingNgramEncoder(Encoder):
    """字符 bigram + 英文词 的特征哈希编码器。

    实现要点：
    1. 中文按字符 bigram（`时间` `间管` `管理` …）切分，规避分词依赖
    2. 英文/数字按词切分
    3. 用 blake2b 做稳定哈希（Python 内置 `hash()` 有随机盐，跨进程不一致）
    4. 子线性 TF 加权：1 + log(tf)，抑制高频词主导
    """

    def __init__(self, dim: int = 512, use_bigram: bool = True) -> None:
        self.dim = dim
        self.use_bigram = use_bigram
        self.info = EncoderInfo(
            backend="hashing-ngram",
            model=f"charngram-dim{dim}",
            dim=dim,
            is_fallback=True,
        )

    # ------------------------------------------------------------ 特征

    @staticmethod
    def _stable_bucket(token: str, dim: int) -> int:
        h = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(h, "little") % dim

    def _tokens(self, text: str) -> List[str]:
        tokens: List[str] = []
        for chunk in _WORD_RE.findall(text):
            if chunk[0].isascii():
                tokens.append(chunk.lower())
                continue
            if not self.use_bigram or len(chunk) == 1:
                tokens.extend(chunk)
                continue
            tokens.extend(chunk[i] for i in range(len(chunk)))
            tokens.extend(chunk[i : i + 2] for i in range(len(chunk) - 1))
        return tokens

    # ------------------------------------------------------------ 编码

    def encode(self, texts: List[str]) -> np.ndarray:
        mat = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            counts: dict[int, int] = {}
            for tok in self._tokens(text or ""):
                bucket = self._stable_bucket(tok, self.dim)
                counts[bucket] = counts.get(bucket, 0) + 1
            for bucket, tf in counts.items():
                mat[row, bucket] = 1.0 + np.log(tf)
        return normalize_rows(mat)
