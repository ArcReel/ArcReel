"""出站 HTTP 断言的统一去向：respx 在 transport 层拦截真实 httpx 客户端。

与 `patch("httpx.AsyncClient")` 的区别是断言对象：替身记录的是调用参数，respx 捕获的是
真实序列化后的请求，因此 URL 拼接、header 合并、body 编码这些出错在线上的环节都在断言
范围内；`AsyncOpenAI` 等自带 httpx 客户端的 SDK 流量同样被捕获。
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

import httpx
import respx


@contextmanager
def capture_http(*, assert_all_called: bool = False) -> Generator[respx.MockRouter]:
    """拦截全部出站 httpx 流量；未声明路由的请求直接抛错，不会静默放行到真实网络。

    `assert_all_called` 缺省关闭：多数用例只关心其中一条路由是否收到预期请求，声明了
    错误分支路由却不触发是常态，退出时强制全部命中会把这类用例判红。
    """
    with respx.mock(assert_all_called=assert_all_called) as router:
        yield router


def request_json(request: httpx.Request) -> Any:
    """请求体按 JSON 解码。"""
    return json.loads(request.content)


def only_request(route: respx.Route) -> httpx.Request:
    """断言该路由恰好收到一次请求并返回它。"""
    assert route.call_count == 1, f"期望 1 次请求，实际 {route.call_count} 次"
    return route.calls.last.request


class PathPrefixRewrite:
    """注入调用通道的 transport 层替身：把请求路径开头的 ``old`` 换成 ``new`` 再交给下层，并记下所见的 URL 与关闭次数。"""

    def __init__(self, old: str, new: str) -> None:
        self.old = old
        self.new = new
        self.seen: list[str] = []
        self.closed = 0

    def __call__(self, inner: httpx.AsyncBaseTransport | httpx.BaseTransport, /) -> _PathPrefixRewriting:
        return _PathPrefixRewriting(inner, self)


class _PathPrefixRewriting(httpx.BaseTransport, httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport | httpx.BaseTransport, layer: PathPrefixRewrite) -> None:
        self._inner = inner
        self._layer = layer

    def _rewrite(self, request: httpx.Request) -> None:
        self._layer.seen.append(str(request.url))
        path = request.url.path
        if path.startswith(self._layer.old):
            request.url = request.url.copy_with(path=self._layer.new + path[len(self._layer.old) :])

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(self._inner, httpx.BaseTransport)
        self._rewrite(request)
        return self._inner.handle_request(request)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert isinstance(self._inner, httpx.AsyncBaseTransport)
        self._rewrite(request)
        return await self._inner.handle_async_request(request)

    def close(self) -> None:
        assert isinstance(self._inner, httpx.BaseTransport)
        self._layer.closed += 1
        self._inner.close()

    async def aclose(self) -> None:
        assert isinstance(self._inner, httpx.AsyncBaseTransport)
        self._layer.closed += 1
        await self._inner.aclose()
