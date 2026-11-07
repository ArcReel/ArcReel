"""Shared outbound-HTTP assertion helper: respx intercepts real httpx clients at the transport layer.

Unlike `patch("httpx.AsyncClient")`, which records call arguments, respx captures genuinely serialized
requests, covering URL assembly, header merging, and body encoding failures that occur in production.
SDK traffic with its own httpx client, including `AsyncOpenAI`, is captured too.
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
    """Intercept all outbound httpx traffic; undeclared routes raise immediately, never silently reaching real networks.

    `assert_all_called` defaults off: most cases care about one expected request; declared error branches
    often remain unused. Requiring every route on exit would incorrectly fail these cases.
    """
    with respx.mock(assert_all_called=assert_all_called) as router:
        yield router


def request_json(request: httpx.Request) -> Any:
    """Decode the request body as JSON."""
    return json.loads(request.content)


def only_request(route: respx.Route) -> httpx.Request:
    """Assert that the route received exactly one request and return it."""
    assert route.call_count == 1, f"期望 1 次请求，实际 {route.call_count} 次"
    return route.calls.last.request


class PathPrefixRewrite:
    """Inject an invocation-channel transport substitute: replace leading request-path ``old`` with ``new``, delegate, and record URLs/close counts."""

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
