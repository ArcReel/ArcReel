"""市场条目浏览：启用源条目合并与排序、相对路径解析、定义原文抓取、icon 二次把关与进程内缓存。"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from io import BytesIO
from typing import Any

import httpx
import pytest
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lib.config.repository import SystemSettingRepository
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import MarketSourceRepository
from lib.market.entries import (
    MarketAssetFetchError,
    MarketAssetInvalidError,
    MarketEntryNotFoundError,
    MarketEntryService,
    MarketSourceDisabledError,
    MissingTarget,
    entry_asset_url,
    find_entry,
    merge_entries,
)
from lib.market.sources import PROXY_PREFIX_SETTING
from tests.http_capture import capture_http, only_request

TEAM_URL = "https://raw.githubusercontent.com/someone/market/HEAD/arcreel-market.json"
TEAM_DIR = "https://raw.githubusercontent.com/someone/market/HEAD/"


def _entry(slug: str, name: str | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "type": "endpoint",
        "slug": slug,
        "path": f"endpoints/{slug}/definition.json",
        "name": name or slug,
        "author": "someone",
        "version": "1.0.0",
        "media_type": "video",
        **extra,
    }


def _source(source_id: int, *entries: dict[str, Any], enabled: bool = True, index_url: str = TEAM_URL) -> MarketSource:
    return MarketSource(
        id=source_id,
        kind="custom",
        display_name=f"源 {source_id}",
        address=f"someone/market-{source_id}",
        index_url=index_url,
        canonical_key=f"url:{index_url}#{source_id}",
        is_enabled=enabled,
        position=source_id,
        status="ok",
        cached_index={"schema_version": "1.0.0", "name": f"源 {source_id}", "entries": list(entries)},
    )


def _png(width: int = 32, height: int = 32) -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (width, height), (255, 0, 0, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.fixture
def market_sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def asset_http() -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def entry_service(
    market_sessions: async_sessionmaker[AsyncSession], asset_http: httpx.AsyncClient
) -> MarketEntryService:
    return MarketEntryService(market_sessions, http_client=lambda: asset_http, icon_cache_size=2)


async def _store(market_sessions: async_sessionmaker[AsyncSession], *sources: MarketSource) -> None:
    async with market_sessions() as session:
        for source in sources:
            await MarketSourceRepository(session).add(source)
        await session.commit()


# ---------------------------------------------------------------------------
# 合并与路径解析
# ---------------------------------------------------------------------------


def test_merge_keeps_source_order_sorts_by_name_and_skips_disabled_and_unknown_types() -> None:
    first = _source(1, _entry("zeta", "Zeta"), _entry("alpha", "alpha"), {"type": "prompt-template", "slug": "x"})
    disabled = _source(2, _entry("hidden"), enabled=False)
    third = _source(3, _entry("alpha", "Alpha"))
    never_fetched = _source(4)
    never_fetched.cached_index = None

    merged = merge_entries([first, disabled, third, never_fetched])

    assert [(item.source.id, item.entry.slug) for item in merged] == [(1, "alpha"), (1, "zeta"), (3, "alpha")]


def test_duplicate_slug_in_one_source_keeps_only_the_first_entry() -> None:
    source = _source(1, _entry("demo", "Beta"), _entry("demo", "Alpha"))

    [merged] = merge_entries([source])

    assert merged.entry.name == "Beta"
    found = find_entry(source, "demo")
    assert found is not None
    assert found.name == "Beta"


def test_merge_skips_snapshot_that_no_longer_parses() -> None:
    broken = _source(1)
    broken.cached_index = {"schema_version": "1.0.0", "name": "坏快照", "entries": [{"type": "endpoint"}]}

    assert merge_entries([broken, _source(2, _entry("ok"))])[0].source.id == 2


def test_merge_with_other_entry_type_is_empty() -> None:
    assert merge_entries([_source(1, _entry("demo"))], entry_type="prompt") == []


@pytest.mark.parametrize(
    ("index_url", "relative", "expected"),
    [
        (TEAM_URL, "endpoints/demo/definition.json", f"{TEAM_DIR}endpoints/demo/definition.json"),
        ("https://mirror.example.com/a/b/arcreel-market.json", "icon.svg", "https://mirror.example.com/a/b/icon.svg"),
        (TEAM_URL, "endpoints/demo/icon#1?.png", f"{TEAM_DIR}endpoints/demo/icon%231%3F.png"),
    ],
)
def test_entry_asset_url_resolves_against_index_directory(index_url: str, relative: str, expected: str) -> None:
    assert entry_asset_url(index_url, relative) == expected


# ---------------------------------------------------------------------------
# 定义原文
# ---------------------------------------------------------------------------


async def test_definition_is_fetched_and_returned_as_is(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _store(market_sessions, _source(1, _entry("demo")))
    definition = {"schema_version": "1.1.0", "meta": {"name": "demo"}, "auth": {"type": "bearer"}}

    with capture_http() as http:
        route = http.get(f"{TEAM_DIR}endpoints/demo/definition.json").respond(json=definition)
        assert await entry_service.fetch_definition(1, "demo") == definition

    only_request(route)


async def test_definition_goes_through_proxy_prefix(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _store(market_sessions, _source(1, _entry("demo")))
    async with market_sessions() as session:
        await SystemSettingRepository(session).set(PROXY_PREFIX_SETTING, "https://proxy.example.net/")
        await session.commit()

    with capture_http() as http:
        route = http.get(f"https://proxy.example.net/{TEAM_DIR}endpoints/demo/definition.json").respond(json={})
        assert await entry_service.fetch_definition(1, "demo") == {}

    assert route.call_count == 1


async def test_definition_that_is_not_json_is_invalid(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _store(market_sessions, _source(1, _entry("demo")))

    with capture_http() as http:
        http.get(f"{TEAM_DIR}endpoints/demo/definition.json").respond(text="../shared/definition.json")
        with pytest.raises(MarketAssetInvalidError, match="not valid JSON"):
            await entry_service.fetch_definition(1, "demo")


async def test_definition_fetch_failure_carries_reason(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _store(market_sessions, _source(1, _entry("demo")))

    with capture_http() as http:
        http.get(f"{TEAM_DIR}endpoints/demo/definition.json").respond(404)
        with pytest.raises(MarketAssetFetchError, match="HTTP 404"):
            await entry_service.fetch_definition(1, "demo")


@pytest.mark.parametrize(
    ("source_id", "slug", "target"),
    [(9, "demo", MissingTarget.SOURCE), (1, "missing", MissingTarget.ENTRY)],
)
async def test_definition_of_unknown_source_or_entry_is_not_found(
    entry_service: MarketEntryService,
    market_sessions: async_sessionmaker[AsyncSession],
    source_id: int,
    slug: str,
    target: MissingTarget,
) -> None:
    await _store(market_sessions, _source(1, _entry("demo")))

    with capture_http() as http, pytest.raises(MarketEntryNotFoundError) as caught:
        await entry_service.fetch_definition(source_id, slug)
    assert caught.value.target is target
    assert http.calls.call_count == 0


async def test_disabled_source_is_not_fetched(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _store(market_sessions, _source(1, _entry("demo", icon="endpoints/demo/icon.png"), enabled=False))

    with capture_http() as http:
        with pytest.raises(MarketSourceDisabledError):
            await entry_service.fetch_definition(1, "demo")
        with pytest.raises(MarketSourceDisabledError):
            await entry_service.fetch_icon(1, "demo")
        assert http.calls.call_count == 0


# ---------------------------------------------------------------------------
# icon
# ---------------------------------------------------------------------------


async def test_icon_is_served_with_media_type_and_cached_per_version(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _store(market_sessions, _source(1, _entry("demo", icon="endpoints/demo/icon.png")))
    png = _png()

    with capture_http() as http:
        route = http.get(f"{TEAM_DIR}endpoints/demo/icon.png").respond(content=png)
        first = await entry_service.fetch_icon(1, "demo")
        second = await entry_service.fetch_icon(1, "demo")
        assert route.call_count == 1

        async with market_sessions() as session:
            source = await MarketSourceRepository(session).get(1)
            assert source is not None
            source.cached_index = {
                "schema_version": "1.0.0",
                "name": "源 1",
                "entries": [_entry("demo", icon="endpoints/demo/icon.png", version="1.1.0")],
            }
            await session.commit()
        await entry_service.fetch_icon(1, "demo")
        assert route.call_count == 2

    assert first.content == png
    assert first.media_type == "image/png"
    assert second == first


async def test_icon_cache_evicts_least_recently_used(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    icons = [_entry(slug, icon=f"endpoints/{slug}/icon.png") for slug in ("a", "b", "c")]
    await _store(market_sessions, _source(1, *icons))

    with capture_http() as http:
        route = http.get(url__regex=rf"^{TEAM_DIR}endpoints/[abc]/icon\.png$").respond(content=_png())
        for slug in ("a", "b", "a", "c", "a", "b"):
            await entry_service.fetch_icon(1, slug)

    fetched = [str(call.request.url).removeprefix(TEAM_DIR) for call in route.calls]
    assert fetched == ["endpoints/a/icon.png", "endpoints/b/icon.png", "endpoints/c/icon.png", "endpoints/b/icon.png"]


async def test_entry_without_icon_is_not_found(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _store(market_sessions, _source(1, _entry("demo")))

    with pytest.raises(MarketEntryNotFoundError) as caught:
        await entry_service.fetch_icon(1, "demo")
    assert caught.value.target is MissingTarget.ICON


async def test_oversized_icon_is_not_fetched_beyond_limit(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    await _store(market_sessions, _source(1, _entry("demo", icon="endpoints/demo/icon.png")))

    with capture_http() as http:
        http.get(f"{TEAM_DIR}endpoints/demo/icon.png").respond(content=b"\0" * (64 * 1024 + 1))
        with pytest.raises(MarketAssetFetchError, match="exceeds 65536 bytes"):
            await entry_service.fetch_icon(1, "demo")


@pytest.mark.parametrize(
    ("icon", "body"),
    [
        ("endpoints/demo/icon.png", b"<svg xmlns='http://www.w3.org/2000/svg' width='8' height='8'/>"),
        ("endpoints/demo/icon.svg", b"<svg xmlns='http://www.w3.org/2000/svg' width='8' height='4'/>"),
    ],
)
async def test_icon_failing_inspection_is_rejected_and_not_cached(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession], icon: str, body: bytes
) -> None:
    await _store(market_sessions, _source(1, _entry("demo", icon=icon)))

    with capture_http() as http:
        route = http.get(f"{TEAM_DIR}{icon}").respond(content=body)
        for _ in range(2):
            with pytest.raises(MarketAssetInvalidError, match="icon rejected"):
                await entry_service.fetch_icon(1, "demo")

    assert route.call_count == 2


async def test_icon_with_unsupported_extension_is_rejected_without_fetching(
    entry_service: MarketEntryService, market_sessions: async_sessionmaker[AsyncSession]
) -> None:
    source = _source(1, _entry("demo", icon="endpoints/demo/icon.png"))
    await _store(market_sessions, source)
    async with market_sessions() as session:
        stored = await MarketSourceRepository(session).get(1)
        assert stored is not None
        stored.cached_index = {
            "schema_version": "1.0.0",
            "name": "源 1",
            "entries": [_entry("demo", icon="endpoints/demo/icon.gif")],
        }
        await session.commit()

    with capture_http() as http:
        with pytest.raises(MarketAssetInvalidError, match="extension"):
            await entry_service.fetch_icon(1, "demo")
        assert http.calls.call_count == 0
