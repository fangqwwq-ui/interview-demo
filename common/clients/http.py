"""HTTP 客户端工厂：区分「本地服务」与「外部服务」的代理策略。

本机实测到的陷阱（值得单独记一笔）：
环境中设置了 `HTTP_PROXY=HTTPS_PROXY=http://127.0.0.1:57249`，且**没有 NO_PROXY**。
httpx 默认 `trust_env=True`，于是连 `http://127.0.0.1:8188/system_stats` 这样的
本地请求也会被送进代理，返回 **502** —— 表现出来就像「ComfyUI 挂了」，
但实际服务是好的（直接起服务时 `/system_stats` 正常返回）。

若不做处理，Demo C 会在这里卡很久。解决办法按目标地址分流：

  - 127.0.0.1 / localhost / ::1  → `trust_env=False`，直连，绕过代理
  - 其他地址                      → 沿用环境代理（沙箱与内网环境常常必须走代理）

统一收敛到本模块，避免每个 provider 各踩一次。
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import urlparse

import httpx

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0", "[::1]"}
DEFAULT_TIMEOUT = 30.0


def is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in LOCAL_HOSTS


def client(url: str, timeout: Optional[float] = None, **kwargs: Any) -> httpx.Client:
    """按目标地址返回配置正确的客户端。

    本地地址强制 `trust_env=False`，忽略 HTTP(S)_PROXY。
    """
    timeout = DEFAULT_TIMEOUT if timeout is None else timeout
    if is_local(url):
        kwargs.setdefault("trust_env", False)
    return httpx.Client(timeout=timeout, **kwargs)


def get_json(url: str, timeout: Optional[float] = None, **kwargs: Any) -> Dict[str, Any]:
    with client(url, timeout, **kwargs) as c:
        resp = c.get(url)
        resp.raise_for_status()
        return resp.json()


def post_json(
    url: str,
    payload: Dict[str, Any],
    timeout: Optional[float] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    with client(url, timeout) as c:
        resp = c.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


def probe(url: str, timeout: float = 4.0) -> Dict[str, Any]:
    """探活，永不抛异常。用于自检与门户的健康面板。"""
    try:
        with client(url, timeout) as c:
            resp = c.get(url)
        return {
            "ok": resp.status_code < 500,
            "status": resp.status_code,
            "local": is_local(url),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "local": is_local(url)}
