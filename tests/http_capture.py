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
