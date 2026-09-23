"""市场源索引抓取的边界：只经 https、限时、限体积与嵌套、限重定向，ETag 与绕缓存，代理前缀。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from itertools import pairwise
from typing import Any

import httpx
import pytest

from lib.market.fetch import (
    FETCH_TIMEOUT_SECONDS,
    INDEX_MAX_BYTES,
    MAX_ETAG_LENGTH,
    MAX_JSON_DEPTH,
    MAX_REDIRECTS,
    MarketFetchError,
    MarketTransportError,
    SourceStatus,
    fetch_bytes,
    fetch_index,
    with_proxy_prefix,
)
from tests.http_capture import capture_http, only_request

INDEX_URL = "https://raw.githubusercontent.com/someone/market/HEAD/arcreel-market.json"
MIRROR_URL = "https://mirror.example.com/team/arcreel-market.json"


def _index(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "1.0.0",
        "name": "团队市场",
        "entries": [
            {
                "type": "endpoint",
                "slug": "demo-video",
                "path": "endpoints/demo-video/definition.json",
                "name": "演示视频",
                "author": "someone",
                "version": "1.0.0",
                "media_type": "video",
            }
        ],
    }
    document.update(overrides)
    return document


@pytest.fixture
async def raw_client() -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient(timeout=5.0) as http_client:
        yield http_client


async def _fetch_error(raw_client: httpx.AsyncClient, **kwargs: Any) -> MarketFetchError:
    with pytest.raises(MarketFetchError) as excinfo:
        await fetch_index(raw_client, INDEX_URL, **kwargs)
    return excinfo.value


async def test_fetch_returns_parsed_index_raw_document_and_etag(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        route = http.get(INDEX_URL).respond(json=_index(), headers={"ETag": '"v1"'})

        result = await fetch_index(raw_client, INDEX_URL)

    request = only_request(route)
    assert "if-none-match" not in request.headers
    assert request.extensions["timeout"] == httpx.Timeout(FETCH_TIMEOUT_SECONDS).as_dict()
    assert result.not_modified is False
    assert result.document == _index()
    assert result.index is not None
    assert [entry.slug for entry in result.index.entries] == ["demo-video"]
    assert result.etag == '"v1"'


async def test_etag_is_sent_as_if_none_match_and_304_reports_not_modified(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        route = http.get(INDEX_URL).respond(304)

        result = await fetch_index(raw_client, INDEX_URL, etag='"v1"')

    assert only_request(route).headers["if-none-match"] == '"v1"'
    assert result.not_modified is True
    assert result.document is None


async def test_oversized_etag_is_not_kept(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        http.get(INDEX_URL).respond(json=_index(), headers={"ETag": '"' + "x" * MAX_ETAG_LENGTH + '"'})

        result = await fetch_index(raw_client, INDEX_URL)

    assert result.not_modified is False
    assert result.etag is None


async def test_unexpected_304_without_etag_is_unreachable(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        http.get(INDEX_URL).respond(304)

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.UNREACHABLE
    assert error.detail == "HTTP 304 without If-None-Match"


async def test_cache_bust_appends_timestamp_query_parameter(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        route = http.get(INDEX_URL, params={"_ts": "1789000000000"}).respond(json=_index())

        await fetch_index(raw_client, INDEX_URL, cache_bust=1789000000000)

    assert str(only_request(route).url) == f"{INDEX_URL}?_ts=1789000000000"


async def test_proxy_prefix_is_prepended_to_raw_github_addresses(raw_client: httpx.AsyncClient) -> None:
    proxied = f"https://proxy.example.net/{INDEX_URL}"
    with capture_http() as http:
        route = http.get(proxied).respond(json=_index())

        await fetch_index(raw_client, INDEX_URL, proxy_prefix="https://proxy.example.net/")

    assert str(only_request(route).url) == proxied


def test_proxy_prefix_leaves_other_hosts_untouched() -> None:
    assert with_proxy_prefix(MIRROR_URL, "https://proxy.example.net") == MIRROR_URL
    assert with_proxy_prefix(INDEX_URL, "") == INDEX_URL
    assert with_proxy_prefix(INDEX_URL, "https://proxy.example.net") == f"https://proxy.example.net/{INDEX_URL}"


async def test_timeout_is_unreachable(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        http.get(INDEX_URL).mock(side_effect=httpx.ReadTimeout("timed out"))

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.UNREACHABLE
    assert "timed out" in error.detail


async def test_body_that_keeps_trickling_past_the_deadline_is_a_transport_error(
    raw_client: httpx.AsyncClient,
) -> None:
    first_chunk_sent = asyncio.Event()
    never = asyncio.Event()

    async def trickle() -> AsyncGenerator[bytes]:
        yield b"{"
        first_chunk_sent.set()
        await never.wait()
        yield b"}"

    with capture_http() as http:
        http.get(INDEX_URL).respond(content=trickle())

        with pytest.raises(MarketTransportError) as excinfo:
            await fetch_bytes(raw_client, INDEX_URL, max_bytes=INDEX_MAX_BYTES, deadline_seconds=0)

    assert first_chunk_sent.is_set()
    assert str(excinfo.value) == "request timed out after 0s"


async def test_connection_lost_while_reading_body_is_unreachable(raw_client: httpx.AsyncClient) -> None:
    async def broken() -> AsyncGenerator[bytes]:
        yield b"{"
        raise httpx.ReadError("connection reset")

    with capture_http() as http:
        http.get(INDEX_URL).respond(content=broken())

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.UNREACHABLE
    assert error.detail == "request failed: ReadError"


async def test_url_with_control_character_is_unreachable(raw_client: httpx.AsyncClient) -> None:
    with pytest.raises(MarketFetchError) as excinfo:
        await fetch_index(raw_client, "https://mirror.example.com/team\n/arcreel-market.json")

    assert excinfo.value.status is SourceStatus.UNREACHABLE
    assert excinfo.value.detail.startswith("invalid URL: ")


async def test_http_error_status_is_unreachable(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        http.get(INDEX_URL).respond(404)

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.UNREACHABLE
    assert error.detail == "HTTP 404"


async def test_oversized_response_is_invalid_index(raw_client: httpx.AsyncClient) -> None:
    padding = "x" * INDEX_MAX_BYTES
    with capture_http() as http:
        http.get(INDEX_URL).respond(json=_index(description=padding))

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.INVALID_INDEX
    assert str(INDEX_MAX_BYTES) in error.detail


async def test_oversized_streamed_response_without_length_is_invalid_index(raw_client: httpx.AsyncClient) -> None:
    async def chunks() -> AsyncGenerator[bytes]:
        for _ in range(INDEX_MAX_BYTES // 4096 + 2):
            yield b" " * 4096

    with capture_http() as http:
        http.get(INDEX_URL).respond(content=chunks())

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.INVALID_INDEX


async def test_excessive_json_nesting_is_invalid_index(raw_client: httpx.AsyncClient) -> None:
    nested: Any = "leaf"
    for _ in range(MAX_JSON_DEPTH + 1):
        nested = [nested]
    with capture_http() as http:
        http.get(INDEX_URL).respond(json=_index(extra=nested))

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.INVALID_INDEX
    assert "nest" in error.detail


async def test_non_json_body_is_invalid_index(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        http.get(INDEX_URL).respond(text="<html>not found</html>")

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.INVALID_INDEX


@pytest.mark.parametrize(
    "body",
    [
        b'{"schema_version": "1.0.0", "name": "m", "entries": [], "x": NaN}',
        b'{"schema_version": "1.0.0", "name": "m", "entries": [], "x": -Infinity}',
        b'{"schema_version": "1.0.0", "name": "m", "entries": [], "x": ' + b"9" * 5000 + b"}",
    ],
    ids=["nan", "infinity", "huge-integer"],
)
async def test_non_standard_json_numbers_are_invalid_index(raw_client: httpx.AsyncClient, body: bytes) -> None:
    with capture_http() as http:
        http.get(INDEX_URL).respond(content=body)

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.INVALID_INDEX


async def test_one_invalid_entry_invalidates_the_whole_index(raw_client: httpx.AsyncClient) -> None:
    document = _index()
    document["entries"].append({**document["entries"][0], "slug": "Bad Slug"})
    with capture_http() as http:
        http.get(INDEX_URL).respond(json=document)

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.INVALID_INDEX
    assert "slug_invalid" in error.detail


async def test_unknown_entry_type_does_not_invalidate_the_index(raw_client: httpx.AsyncClient) -> None:
    document = _index()
    document["entries"].append({"type": "prompt-template", "whatever": 1})
    with capture_http() as http:
        http.get(INDEX_URL).respond(json=document)

        result = await fetch_index(raw_client, INDEX_URL)

    assert result.index is not None
    assert len(result.index.entries) == 1


async def test_higher_major_schema_version_is_unsupported_schema(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        http.get(INDEX_URL).respond(json={"schema_version": "2.0.0", "shape": "changed"})

        error = await _fetch_error(raw_client)

    assert error.status is SourceStatus.UNSUPPORTED_SCHEMA


async def test_https_redirects_are_followed(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        http.get(INDEX_URL).respond(302, headers={"Location": MIRROR_URL})
        target = http.get(MIRROR_URL).respond(json=_index())

        result = await fetch_index(raw_client, INDEX_URL)

    assert target.call_count == 1
    assert result.document == _index()


async def test_redirect_to_raw_github_is_also_sent_through_proxy(raw_client: httpx.AsyncClient) -> None:
    proxied = f"https://proxy.example.net/{INDEX_URL}"
    with capture_http() as http:
        http.get(MIRROR_URL).respond(302, headers={"Location": INDEX_URL})
        target = http.get(proxied).respond(json=_index())

        result = await fetch_index(raw_client, MIRROR_URL, proxy_prefix="https://proxy.example.net")

    assert target.call_count == 1
    assert result.document == _index()


async def test_redirect_to_plain_http_is_unreachable(raw_client: httpx.AsyncClient) -> None:
    with capture_http() as http:
        http.get(INDEX_URL).respond(301, headers={"Location": "http://mirror.example.com/team/arcreel-market.json"})
        plain = http.get("http://mirror.example.com/team/arcreel-market.json").respond(json=_index())

        error = await _fetch_error(raw_client)

    assert plain.call_count == 0
    assert error.status is SourceStatus.UNREACHABLE
    assert "https" in error.detail


async def test_redirect_chain_longer_than_limit_is_unreachable(raw_client: httpx.AsyncClient) -> None:
    hops = [f"https://mirror.example.com/hop/{n}/arcreel-market.json" for n in range(MAX_REDIRECTS + 1)]
    with capture_http() as http:
        http.get(INDEX_URL).respond(302, headers={"Location": hops[0]})
        for current, following in pairwise(hops):
            http.get(current).respond(302, headers={"Location": following})
        last = http.get(hops[-1]).respond(json=_index())

        error = await _fetch_error(raw_client)

    assert last.call_count == 0
    assert error.status is SourceStatus.UNREACHABLE
    assert "redirect" in error.detail


async def test_redirect_chain_at_limit_is_followed(raw_client: httpx.AsyncClient) -> None:
    hops = [f"https://mirror.example.com/hop/{n}/arcreel-market.json" for n in range(MAX_REDIRECTS)]
    with capture_http() as http:
        http.get(INDEX_URL).respond(302, headers={"Location": hops[0]})
        for current, following in pairwise(hops):
            http.get(current).respond(302, headers={"Location": following})
        http.get(hops[-1]).respond(content=json.dumps(_index()).encode())

        result = await fetch_index(raw_client, INDEX_URL)

    assert result.document == _index()
