"""混合路由：云端优先 + 本地兜底。

为什么必须显式记录「本次由哪个通道产出」：
Demo B 的效果数字（检测分下降幅度、语义保真度）与通道强相关。
口径页必须能说明某组数字出自云端 qwen-plus 还是本地 qwen3:8b，
否则数字不可复现、面试时经不起追问。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from common import config
from common.llm.base import LLMError, LLMProvider, MessageLike
from common.llm.cloud_provider import CloudProvider
from common.llm.ollama_provider import OllamaProvider


@dataclass
class ChatResult:
    text: str
    provider: str
    model: str
    elapsed_s: float
    fallback_used: bool = False
    attempts: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "elapsed_s": round(self.elapsed_s, 3),
            "fallback_used": self.fallback_used,
            "attempts": self.attempts,
        }


class LLMRouter:
    """按 mode 组装调用顺序，逐个尝试直到成功。"""

    def __init__(
        self,
        mode: Optional[str] = None,
        fallback_to_local: Optional[bool] = None,
        cloud: Optional[LLMProvider] = None,
        local: Optional[LLMProvider] = None,
    ) -> None:
        self.mode = (mode or config.llm.mode).lower()
        if self.mode not in {"local", "cloud", "hybrid"}:
            raise ValueError(f"unknown LLM_MODE: {self.mode!r} (expect local|cloud|hybrid)")

        self.fallback_to_local = (
            config.llm.fallback_to_local if fallback_to_local is None else fallback_to_local
        )
        self._cloud = cloud or CloudProvider()
        self._local = local or OllamaProvider()

    # ---------------------------------------------------------- 通道顺序

    def _order(self) -> List[LLMProvider]:
        if self.mode == "local":
            return [self._local]
        if self.mode == "cloud":
            return [self._cloud]
        # hybrid：云端优先；未配 Key 时直接只用本地，避免无谓报错
        if not config.llm.cloud_ready:
            return [self._local]
        order: List[LLMProvider] = [self._cloud]
        if self.fallback_to_local:
            order.append(self._local)
        return order

    # ---------------------------------------------------------- 调用

    def chat(
        self,
        messages: Sequence[MessageLike],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> ChatResult:
        order = self._order()
        attempts: List[Dict[str, Any]] = []
        started = time.perf_counter()

        for idx, provider in enumerate(order):
            try:
                text = provider.chat(messages, temperature=temperature, max_tokens=max_tokens)
                attempts.append({"provider": provider.name, "ok": True})
                return ChatResult(
                    text=text,
                    provider=provider.name,
                    model=getattr(provider, "model", "?"),
                    elapsed_s=time.perf_counter() - started,
                    fallback_used=idx > 0,
                    attempts=attempts,
                )
            except LLMError as exc:
                attempts.append({"provider": provider.name, "ok": False, "error": str(exc)})

        detail = "; ".join(f"{a['provider']}: {a.get('error')}" for a in attempts if not a["ok"])
        raise LLMError(f"all providers failed ({self.mode}). {detail}")

    # ---------------------------------------------------------- 探活

    def health(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "order": [p.name for p in self._order()],
            "providers": {"cloud": self._cloud.health(), "local": self._local.health()},
        }


def default_router() -> LLMRouter:
    return LLMRouter()
