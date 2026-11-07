"""HTTP transport injection seam for call channels.

Path overrides (``docs/adr/0101``) rewrite endpoint requests at HTTP transport level. Channel construction
may accept :class:`TransportLayer` layered over its original httpx client: requests pass through injection,
then the original client, whose outbound destination validation hooks and environment proxies therefore
apply to rewritten requests. Without injection, reuse original clients unchanged.
"""

from __future__ import annotations

from typing import Protocol, overload

import httpx


class TransportLayer(Protocol):
    """Accept underlying transports and return layered transports.

    One channel may own synchronous and asynchronous clients, as google-genai does; support layering both transport types.
    """

    @overload
    def __call__(self, inner: httpx.AsyncBaseTransport, /) -> httpx.AsyncBaseTransport: ...
    @overload
    def __call__(self, inner: httpx.BaseTransport, /) -> httpx.BaseTransport: ...


class _AsyncClientTransport(httpx.AsyncBaseTransport):
    """Send through original clients; outer clients process redirects hop by hop, passing each hop through injection again."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        return await self._client.send(request, stream=True, follow_redirects=False)

    async def aclose(self) -> None:
        await self._client.aclose()


class _SyncClientTransport(httpx.BaseTransport):
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self._client.send(request, stream=True, follow_redirects=False)

    def close(self) -> None:
        self._client.close()


def layer_async_client(client: httpx.AsyncClient, layer: TransportLayer | None) -> httpx.AsyncClient:
    """Return clients routing through ``layer`` before ``client``; None ``layer`` returns ``client`` unchanged.

    Outer clients retain ``client`` base_url/default headers/timeouts/redirect settings and close ``client``
    when closed. httpx omits environment proxies with explicit transports; ``client`` still selects proxies
    using rewritten URLs.
    """
    if layer is None:
        return client
    return httpx.AsyncClient(
        transport=layer(_AsyncClientTransport(client)),
        base_url=client.base_url,
        headers=client.headers,
        timeout=client.timeout,
        follow_redirects=client.follow_redirects,
    )


def layer_sync_client(client: httpx.Client, layer: TransportLayer | None) -> httpx.Client:
    """Synchronous :func:`layer_async_client` for synchronous SDKs (Ark and google-genai sync channels)."""
    if layer is None:
        return client
    return httpx.Client(
        transport=layer(_SyncClientTransport(client)),
        base_url=client.base_url,
        headers=client.headers,
        timeout=client.timeout,
        follow_redirects=client.follow_redirects,
    )
