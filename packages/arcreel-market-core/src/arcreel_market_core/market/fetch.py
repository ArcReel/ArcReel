"""Remote-fetch boundary for market sources.

The backend fetches arbitrary ``https://`` URLs for users, bounded for the server-side request forgery exposure: require ``https`` at every hop,
follow redirects manually with a limit, bound total fetch time (redirects and body reads included), and count body bytes while reading. Use only raw file URLs, never the
GitHub API; prepend proxy prefixes only to ``raw.githubusercontent.com`` URLs, with no direct-connection fallback on failure.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

import httpx

from arcreel_market_core.validation_messages import Translator, code_translator

from .address import GITHUB_RAW_HOST
from .index import INDEX_SCHEMA_VERSION, InvalidIndexError, MarketIndex, UnsupportedIndexSchemaError, parse_index
from .issues import MarketIssue

#: Total fetch deadline (redirects and body reads included), independent of the shared client default timeout.
FETCH_TIMEOUT_SECONDS = 10.0
#: Response-body limit for indexes and definitions.
INDEX_MAX_BYTES = 1024 * 1024
MAX_REDIRECTS = 5
#: Maximum stored ETag length, matching ``market_source.etag`` width; do not store oversized ETags, so later automatic refreshes fetch in full.
MAX_ETAG_LENGTH = 512
#: Index JSON container nesting limit; valid indexes have only three levels (top-level object to entries to entry).
MAX_JSON_DEPTH = 32
#: Query parameter appended by manual refresh to bypass edge caches.
CACHE_BUST_PARAM = "_ts"

_REPORTED_ISSUES = 3


class SourceStatus(StrEnum):
    """Market-source refresh state."""

    NEVER_FETCHED = "never_fetched"
    OK = "ok"
    UNREACHABLE = "unreachable"
    INVALID_INDEX = "invalid_index"
    UNSUPPORTED_SCHEMA = "unsupported_schema"


class MarketTransportError(Exception):
    """Request received no usable response: network failure, timeout, unsuccessful status, or redirect boundary violation. Message states the reason."""


class MarketPayloadTooLargeError(Exception):
    """Response body exceeds the limit."""


class MarketPayloadNotJsonError(Exception):
    """Response body is not usable JSON: decoding failure or excessive container nesting. Message states the reason."""


class MarketFetchError(Exception):
    """Index fetch failed; ``status`` is the source state for this failure, and ``detail`` is the reason recorded in ``last_error``."""

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
    """One index-fetch result; no body when ``not_modified``."""

    not_modified: bool
    document: Any = None
    index: MarketIndex | None = None
    etag: str | None = None


def with_proxy_prefix(url: str, proxy_prefix: str) -> str:
    """Nonempty proxy prefixes apply only to raw.githubusercontent.com URLs."""
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
    deadline_seconds: float = FETCH_TIMEOUT_SECONDS,
) -> RawResponse:
    """GET a URL within fetch boundaries, returning a 2xx or 304 response; ``deadline_seconds`` is the total fetch deadline.

    Raises:
        MarketTransportError: Non-https, network failure, exceeded total deadline, redirect boundary violation, or unsuccessful status.
        MarketPayloadTooLargeError: Response body exceeds ``max_bytes``.
    """
    try:
        async with asyncio.timeout(deadline_seconds):
            return await _follow_redirects(
                client,
                url,
                max_bytes=max_bytes,
                proxy_prefix=proxy_prefix,
                headers=headers,
                deadline_seconds=deadline_seconds,
            )
    except TimeoutError as exc:
        raise MarketTransportError(_timed_out(deadline_seconds)) from exc


async def _follow_redirects(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    proxy_prefix: str,
    headers: dict[str, str] | None,
    deadline_seconds: float,
) -> RawResponse:
    current = url
    for _hop in range(MAX_REDIRECTS + 1):
        if urlsplit(current).scheme != "https":
            raise MarketTransportError(f"refused non-https URL: {current}")
        request_url = with_proxy_prefix(current, proxy_prefix)
        try:
            request = client.build_request("GET", request_url, headers=headers, timeout=deadline_seconds)
            response = await client.send(request, stream=True, follow_redirects=False)
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
        except httpx.InvalidURL as exc:
            raise MarketTransportError(f"invalid URL: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise MarketTransportError(_timed_out(deadline_seconds)) from exc
        except httpx.HTTPError as exc:
            raise MarketTransportError(f"request failed: {type(exc).__name__}") from exc
    raise MarketTransportError(f"too many redirects (limit {MAX_REDIRECTS})")


async def fetch_index(
    client: httpx.AsyncClient,
    index_url: str,
    *,
    proxy_prefix: str = "",
    etag: str | None = None,
    cache_bust: int | None = None,
    translate: Translator = code_translator,
) -> FetchedIndex:
    """Fetch and validate an entire index (rule ①).

    Include ``If-None-Match`` for nonempty ``etag``; for nonempty ``cache_bust``, append a timestamp query parameter to bypass edge caches.
    ``translate`` renders invalid-index diagnostics written to ``MarketFetchError.detail``; default output is diagnostic codes and parameters.

    Raises:
        MarketFetchError: Fetch or validation failed; ``status`` is the applicable source state.
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

    try:
        document = decode_json_payload(response.content)
    except MarketPayloadNotJsonError as exc:
        raise MarketFetchError(SourceStatus.INVALID_INDEX, f"index {exc}") from exc
    try:
        index = parse_index(document)
    except UnsupportedIndexSchemaError as exc:
        raise MarketFetchError(
            SourceStatus.UNSUPPORTED_SCHEMA,
            f"index schema_version {exc.version} is newer than supported {INDEX_SCHEMA_VERSION}",
        ) from exc
    except InvalidIndexError as exc:
        raise MarketFetchError(SourceStatus.INVALID_INDEX, _describe_issues(exc.issues, translate)) from exc
    etag = response.headers.get("etag")
    if etag is not None and len(etag) > MAX_ETAG_LENGTH:
        etag = None
    return FetchedIndex(not_modified=False, document=document, index=index, etag=etag)


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


def _timed_out(seconds: float) -> str:
    return f"request timed out after {seconds:g}s"


def _with_cache_bust(url: str, token: int) -> str:
    separator = "&" if urlsplit(url).query else "?"
    return f"{url}{separator}{CACHE_BUST_PARAM}={token}"


def decode_json_payload(content: bytes) -> Any:
    """Decode UTF-8 and parse strict JSON: reject ``NaN`` / ``Infinity`` and container nesting beyond :data:`MAX_JSON_DEPTH`.

    Raises:
        MarketPayloadNotJsonError: Not JSON (including nonstandard constants and numbers exceeding the integer digit limit), or excessive nesting.
    """
    try:
        document = json.loads(content.decode("utf-8"), parse_constant=_reject_constant)
    except ValueError as exc:
        raise MarketPayloadNotJsonError(f"is not valid JSON: {exc}") from exc
    except RecursionError as exc:
        raise MarketPayloadNotJsonError(_too_deep()) from exc
    if _nesting_exceeds(document, MAX_JSON_DEPTH):
        raise MarketPayloadNotJsonError(_too_deep())
    return document


def _reject_constant(name: str) -> Any:
    raise ValueError(f"non-standard JSON constant {name}")


def _too_deep() -> str:
    return f"has JSON nesting deeper than {MAX_JSON_DEPTH} levels"


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


def _describe_issues(issues: tuple[MarketIssue, ...], translate: Translator) -> str:
    rendered = [issue.render(translate) for issue in issues[:_REPORTED_ISSUES]]
    if len(issues) > _REPORTED_ISSUES:
        rendered.append(f"(+{len(issues) - _REPORTED_ISSUES} more)")
    return "; ".join(rendered) or "index does not match the market index schema"
