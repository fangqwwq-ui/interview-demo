"""本地兜底通道：Ollama。

选它的理由：零 API 成本、断网可演示、面试现场不依赖网络。
代价：8 GB 显存下只能跑 7–8B 量化模型，质量低于云端。
"""

from __future__ import annotations

from typing import Any, Dict, Sequence

from common import config
from common.clients import http as http_client
from common.llm.base import LLMError, LLMProvider, MessageLike, normalize


class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.base_url = (base_url or config.llm.ollama_base_url).rstrip("/")
        self.model = model or config.llm.ollama_model
        self.timeout = timeout or config.llm.request_timeout

    def chat(
        self,
        messages: Sequence[MessageLike],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": normalize(messages),
            "stream": False,
            "options": {"temperature": temperature},
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens

        try:
            with http_client.client(self.base_url, self.timeout) as client:
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001 - 统一转成 LLMError 供 Router 判断
            raise LLMError(f"ollama chat failed: {type(exc).__name__}: {exc}") from exc

        content = (data.get("message") or {}).get("content", "")
        if not content:
            raise LLMError(f"ollama returned empty content: {str(data)[:200]}")
        return content

    def health(self) -> Dict[str, Any]:
        try:
            with http_client.client(self.base_url, 5) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                resp.raise_for_status()
                tags = [m.get("name", "") for m in resp.json().get("models", [])]
            return {
                "ok": True,
                "provider": self.name,
                "base_url": self.base_url,
                "model": self.model,
                "model_present": any(t.split(":")[0] == self.model.split(":")[0] for t in tags),
                "models": tags,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "provider": self.name, "error": f"{type(exc).__name__}: {exc}"}
