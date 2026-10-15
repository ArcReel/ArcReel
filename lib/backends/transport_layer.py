"""调用通道的 HTTP transport 注入接缝。

路径覆盖（``docs/adr/0101``）在 HTTP transport 层改写调用端点发出的请求。调用通道构造时可以接收一个
:class:`TransportLayer`，叠在它原本使用的 httpx 客户端之上：请求先经过注入层，再交给原客户端发出，
原客户端上的出站目的地校验（request event hook）与环境代理因此作用于改写后的请求。不注入时原客户端原样使用。
"""

from __future__ import annotations

from typing import Protocol, overload

import httpx


class TransportLayer(Protocol):
    """接收下层 transport、返回叠在其上的 transport。

    同一个调用通道可能同时持有同步与异步客户端（如 google-genai），注入层要能叠在两种 transport 上。
    """

    @overload
    def __call__(self, inner: httpx.AsyncBaseTransport, /) -> httpx.AsyncBaseTransport: ...
    @overload
    def __call__(self, inner: httpx.BaseTransport, /) -> httpx.BaseTransport: ...


class _AsyncClientTransport(httpx.AsyncBaseTransport):
    """把请求交给原客户端发出；重定向由外层客户端逐跳处理，每一跳都再经过注入层。"""

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
    """返回请求先经 ``layer`` 再交 ``client`` 发出的客户端；``layer`` 为 None 时原样返回 ``client``。

    外层沿用 ``client`` 的 base_url、默认头、超时与重定向设置，关闭外层即关闭 ``client``。httpx 给了 transport
    就不挂环境代理，代理仍由 ``client`` 按改写后的 URL 选择。
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
    """:func:`layer_async_client` 的同步版本，供同步 SDK 客户端（方舟、google-genai 的同步通道）使用。"""
    if layer is None:
        return client
    return httpx.Client(
        transport=layer(_SyncClientTransport(client)),
        base_url=client.base_url,
        headers=client.headers,
        timeout=client.timeout,
        follow_redirects=client.follow_redirects,
    )
