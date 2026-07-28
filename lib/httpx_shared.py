"""共享 httpx AsyncClient 单例。

由 server/app.py 的 lifespan 在启动时初始化、关闭时释放，避免每次外部请求
都新建客户端、损失连接池复用。调用方使用 `get_http_client()` 获取实例。
"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("httpx client not initialized; call startup_http_client() first")
    return _client


async def startup_http_client(timeout: float = 5.0) -> None:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=timeout)


async def shutdown_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
