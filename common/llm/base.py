"""LLM Provider 抽象。

只暴露 Demo 真正需要的两个能力：`chat` 与 `health`。
刻意不做成通用 LLM SDK —— Demo B 的 LLM 调用只有「给一段文案 + 参考资料 → 返回改写结果」
一种形态，抽象层越薄越容易在面试里讲清楚，也越不容易出隐藏 bug。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Sequence, Union

MessageLike = Union["Message", Dict[str, str]]


@dataclass
class Message:
    role: str  # system | user | assistant
    content: str

    def as_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


class LLMError(RuntimeError):
    """Provider 调用失败。Router 依赖它区分「可降级」与「不可降级」错误。"""


def normalize(messages: Sequence[MessageLike]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for m in messages:
        out.append(m.as_dict() if isinstance(m, Message) else dict(m))
    return out


class LLMProvider(ABC):
    """所有 Provider 的统一契约。"""

    name: str = "base"

    @abstractmethod
    def chat(
        self,
        messages: Sequence[MessageLike],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """同步对话，返回纯文本回复。失败抛 LLMError。"""

    @abstractmethod
    def health(self) -> Dict[str, Any]:
        """探活。返回 {'ok': bool, ...}，不抛异常。"""
