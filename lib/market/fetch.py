"""市场源的远程抓取边界。

后端代用户抓取任意 ``https://`` 地址，按服务端请求伪造的暴露面设边界：每一跳都须 ``https``、
重定向手动跟随且有上限、单次请求有独立超时、响应体边读边计数。只经 raw 文件地址，不调用
GitHub API；代理前缀只拼在 ``raw.githubusercontent.com`` 地址前，失败不回退直连。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import httpx

from .address import GITHUB_RAW_HOST
from .index import INDEX_SCHEMA_VERSION, InvalidIndexError, MarketIndex, UnsupportedIndexSchemaError, parse_index
from .issues import MarketIssue

#: 单次请求超时，独立于共享客户端的默认值。
FETCH_TIMEOUT_SECONDS = 10.0
#: 索引与定义响应体上限。
INDEX_MAX_BYTES = 1024 * 1024
MAX_REDIRECTS = 5
#: 索引 JSON 的容器嵌套上限；合规索引只有三层（顶层对象 → entries → 条目）。
MAX_JSON_DEPTH = 32
#: 手动刷新绕过边缘缓存时追加的查询参数名。
CACHE_BUST_PARAM = "_ts"

_REPORTED_ISSUES = 3


class SourceStatus(StrEnum):
    """市场源的刷新状态。"""

    NEVER_FETCHED = "never_fetched"
    OK = "ok"
    UNREACHABLE = "unreachable"
    INVALID_INDEX = "invalid_index"
    UNSUPPORTED_SCHEMA = "unsupported_schema"


class MarketTransportError(Exception):
    """请求没有拿到可用响应：网络、超时、非成功状态码、重定向越界。消息即原因。"""


class MarketPayloadTooLargeError(Exception):
    """响应体超过上限。"""


class MarketFetchError(Exception):
    """抓取索引失败；``status`` 是该失败应落的源状态，``detail`` 是记入 ``last_error`` 的原因。"""

    def __init__(self, status: SourceStatus, detail: str) -> None:
        super().__init__(f"{status.value}: {detail}")
        self.status = status
        self.detail = detail


@dataclass(frozen=True)
class RawResponse:
    status_code: int
    content: bytes
    headers: httpx.Headers


@dataclass(frozen=True)
class FetchedIndex:
    """一次索引抓取的结果；``not_modified`` 时没有正文。"""

    not_modified: bool
    document: Any = None
    index: MarketIndex | None = None
    etag: str | None = None


def with_proxy_prefix(url: str, proxy_prefix: str) -> str:
    """非空代理前缀只作用于 raw.githubusercontent.com 地址。"""
    prefix = proxy_prefix.strip()
    if not prefix or urlsplit(url).hostname != GITHUB_RAW_HOST:
        return url
    return f"{prefix.rstrip('/')}/{url}"


async def fetch_bytes(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    proxy_prefix: str = "",
    headers: dict[str, str] | None = None,
) -> RawResponse:
    """按抓取边界 GET 一个地址，返回 2xx 或 304 响应。

    Raises:
        MarketTransportError: 非 https、网络失败、超时、重定向越界或非成功状态码。
        MarketPayloadTooLargeError: 响应体超过 ``max_bytes``。
    """
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if urlsplit(current).scheme != "https":
            raise MarketTransportError(f"refused non-https URL: {current}")
        request_url = with_proxy_prefix(current, proxy_prefix)
        request = client.build_request("GET", request_url, headers=headers, timeout=FETCH_TIMEOUT_SECONDS)
        try:
            response = await client.send(request, stream=True, follow_redirects=False)
        except httpx.TimeoutException as exc:
            raise MarketTransportError(f"request timed out after {FETCH_TIMEOUT_SECONDS:g}s") from exc
        except httpx.HTTPError as exc:
            raise MarketTransportError(f"request failed: {type(exc).__name__}") from exc
        try:
            if response.has_redirect_location:
                current = str(response.url.join(response.headers["location"]))
                continue
            if response.status_code != 304 and not response.is_success:
                raise MarketTransportError(f"HTTP {response.status_code}")
            content = await _read_limited(response, max_bytes)
            return RawResponse(status_code=response.status_code, content=content, headers=response.headers)
        finally:
            await response.aclose()
    raise MarketTransportError(f"too many redirects (limit {MAX_REDIRECTS})")


async def fetch_index(
    client: httpx.AsyncClient,
    index_url: str,
    *,
    proxy_prefix: str = "",
    etag: str | None = None,
    cache_bust: int | None = None,
) -> FetchedIndex:
    """抓取并整份判定一份索引（规则 ①）。

    ``etag`` 非空时带 ``If-None-Match``；``cache_bust`` 非空时追加时间戳查询参数绕过边缘缓存。

    Raises:
        MarketFetchError: 抓取或判定失败，``status`` 为应落的源状态。
    """
    url = _with_cache_bust(index_url, cache_bust) if cache_bust is not None else index_url
    headers = {"If-None-Match": etag} if etag else None
    try:
        response = await fetch_bytes(client, url, max_bytes=INDEX_MAX_BYTES, proxy_prefix=proxy_prefix, headers=headers)
    except MarketTransportError as exc:
        raise MarketFetchError(SourceStatus.UNREACHABLE, str(exc)) from exc
    except MarketPayloadTooLargeError as exc:
        raise MarketFetchError(SourceStatus.INVALID_INDEX, str(exc)) from exc
    if response.status_code == 304:
        if etag is None:
            raise MarketFetchError(SourceStatus.UNREACHABLE, "HTTP 304 without If-None-Match")
        return FetchedIndex(not_modified=True, etag=etag)

    document = _decode_json(response.content)
    try:
        index = parse_index(document)
    except UnsupportedIndexSchemaError as exc:
        raise MarketFetchError(
            SourceStatus.UNSUPPORTED_SCHEMA,
            f"index schema_version {exc.version} is newer than supported {INDEX_SCHEMA_VERSION}",
        ) from exc
    except InvalidIndexError as exc:
        raise MarketFetchError(SourceStatus.INVALID_INDEX, _describe_issues(exc.issues)) from exc
    return FetchedIndex(not_modified=False, document=document, index=index, etag=response.headers.get("etag"))


async def _read_limited(response: httpx.Response, max_bytes: int) -> bytes:
    too_large = MarketPayloadTooLargeError(f"response exceeds {max_bytes} bytes")
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise too_large
    buffer = bytearray()
    async for chunk in response.aiter_bytes():
        buffer.extend(chunk)
        if len(buffer) > max_bytes:
            raise too_large
    return bytes(buffer)


def _with_cache_bust(url: str, token: int) -> str:
    separator = "&" if urlsplit(url).query else "?"
    return f"{url}{separator}{CACHE_BUST_PARAM}={token}"


def _decode_json(content: bytes) -> Any:
    try:
        document = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MarketFetchError(SourceStatus.INVALID_INDEX, f"index is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise MarketFetchError(SourceStatus.INVALID_INDEX, _too_deep()) from exc
    if _nesting_exceeds(document, MAX_JSON_DEPTH):
        raise MarketFetchError(SourceStatus.INVALID_INDEX, _too_deep())
    return document


def _too_deep() -> str:
    return f"JSON nesting deeper than {MAX_JSON_DEPTH} levels"


def _nesting_exceeds(document: Any, limit: int) -> bool:
    stack: list[tuple[Any, int]] = [(document, 1)]
    while stack:
        value, depth = stack.pop()
        if isinstance(value, dict):
            children = value.values()
        elif isinstance(value, list):
            children = value
        else:
            continue
        if depth > limit:
            return True
        stack.extend((child, depth + 1) for child in children)
    return False


def _describe_issues(issues: tuple[MarketIssue, ...]) -> str:
    from lib.i18n import _

    def translate(key: str, **params: Any) -> str:
        return _(key, locale="en", **params)

    rendered = [issue.render(translate) for issue in issues[:_REPORTED_ISSUES]]
    if len(issues) > _REPORTED_ISSUES:
        rendered.append(f"(+{len(issues) - _REPORTED_ISSUES} more)")
    return "; ".join(rendered) or "index does not match the market index schema"
