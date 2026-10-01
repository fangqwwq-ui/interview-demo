"""云端提效通道：阿里云百炼（通义千问），OpenAI 兼容接口。

选它的理由：与简历技术栈一致（原文即「大模型 API 集成（OpenAI/通义千问）」），
中文改写质量明显优于本地 8B 量化模型。
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

from common import config
from common.clients import http as http_client
from common.llm.base import LLMError, LLMProvider, MessageLike, normalize


class CloudProvider(LLMProvider):
    name = "cloud"

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else config.llm.cloud_api_key).strip()
        self.base_url = (base_url or config.llm.cloud_base_url).rstrip("/")
        self.model = model or config.llm.cloud_model
        self.timeout = timeout or config.llm.request_timeout

    def chat(
        self,
        messages: Sequence[MessageLike],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        if not self.api_key:
            # 这是配置缺失，不是网络问题。Router 会据此决定是否降级。
            raise LLMError("cloud provider has no API key (DASHSCOPE_API_KEY is empty)")

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": normalize(messages),
            "temperature": temperature,
        }
        if max_tokens:
            payload["max_tokens"] = max_tokens

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with http_client.client(self.base_url, self.timeout) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions", json=payload, headers=headers
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise LLMError(f"cloud chat failed: {type(exc).__name__}: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise LLMError(f"cloud returned no choices: {str(data)[:200]}")
        content = (choices[0].get("message") or {}).get("content", "")
        if not content:
            raise LLMError("cloud returned empty content")
        return content

    def health(self) -> Dict[str, Any]:
        if not self.api_key:
            return {
                "ok": False,
                "provider": self.name,
                "error": "DASHSCOPE_API_KEY not set",
                "base_url": self.base_url,
                "model": self.model,
            }
        try:
            with http_client.client(self.base_url, 10) as client:
                resp = client.get(
                    f"{self.base_url}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                resp.raise_for_status()
            return {
                "ok": True,
                "provider": self.name,
                "base_url": self.base_url,
                "model": self.model,
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "provider": self.name, "error": f"{type(exc).__name__}: {exc}"}
