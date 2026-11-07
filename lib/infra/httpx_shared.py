"""Shared httpx AsyncClient singleton.

server/app.py lifespan initializes it at startup and releases it at shutdown.
Avoid creating a client for every external request and losing connection-pool
reuse. Callers obtain it through ``get_http_client()``.
"""

from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def get_http_client() -> httpx.AsyncClient:
    if _client is None:
        raise RuntimeError("httpx client not initialized; call startup_http_client() first")
    return _client


async def startup_http_client(timeout: float = 5.0) -> None:  # noqa: ASYNC109 -- httpx.AsyncClient I/O timeout, not an async cancellation deadline
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=timeout)


async def shutdown_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
