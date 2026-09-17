"""市场源登记与刷新：官方源 seed、添加即抓取、五态转换与失败保留快照、ETag 与绕缓存、并发去重。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lib.config.repository import SystemSettingRepository
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import MarketSourceRepository
from lib.market.address import SourceAddressError
from lib.market.fetch import MarketFetchError, SourceStatus
from lib.market.sources import (
    OFFICIAL_SOURCE_ADDRESS,
    PROXY_PREFIX_SETTING,
    DuplicateSourceError,
    MarketSourceService,
    seed_official_source,
)
from tests.http_capture import capture_http, only_request

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
TEAM_URL = "https://raw.githubusercontent.com/someone/market/HEAD/arcreel-market.json"
OFFICIAL_URL = "https://raw.githubusercontent.com/ArcReel/arcreel-market/HEAD/arcreel-market.json"


def _index(name: str = "团队市场", *slugs: str) -> dict[str, Any]:
    return {
        "schema_version": "1.0.0",
        "name": name,
        "entries": [
            {
                "type": "endpoint",
                "slug": slug,
                "path": f"endpoints/{slug}/definition.json",
                "name": slug,
                "author": "someone",
                "version": "1.0.0",
                "media_type": "video",
            }
            for slug in slugs
        ],
    }


@pytest.fixture
def factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def http_client() -> AsyncGenerator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def service(
    factory: async_sessionmaker[AsyncSession], http_client: httpx.AsyncClient, clock: Clock
) -> MarketSourceService:
    return MarketSourceService(factory, http_client=lambda: http_client, clock=clock)


async def _sources(factory: async_sessionmaker[AsyncSession]) -> list[MarketSource]:
    async with factory() as session:
        return await MarketSourceRepository(session).list_ordered()


async def _add_team_source(service: MarketSourceService) -> MarketSource:
    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).respond(json=_index("团队市场", "demo-video"), headers={"ETag": '"v1"'})
        return await service.add_source("someone/market")


# ---------------------------------------------------------------------------
# 官方源 seed
# ---------------------------------------------------------------------------


async def test_seed_inserts_official_source_once_without_fetching(factory: async_sessionmaker[AsyncSession]) -> None:
    with capture_http() as http:
        async with factory() as session:
            await seed_official_source(session)
        async with factory() as session:
            await seed_official_source(session)
        assert http.calls.call_count == 0

    [official] = await _sources(factory)
    assert official.kind == "official"
    assert official.address == OFFICIAL_SOURCE_ADDRESS
    assert official.index_url == OFFICIAL_URL
    assert official.canonical_key == "github:ArcReel/arcreel-market@HEAD"
    assert official.status == SourceStatus.NEVER_FETCHED
    assert official.is_enabled is True


async def test_seed_updates_address_when_constant_changes_and_keeps_user_choices(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as session:
        await seed_official_source(session)
    async with factory() as session:
        official = await MarketSourceRepository(session).get_official()
        assert official is not None
        official.display_name = "官方"
        official.is_enabled = False
        await session.commit()

    async with factory() as session:
        await seed_official_source(session, address="ArcReel/market-index@main")

    async with factory() as session:
        official = await MarketSourceRepository(session).get_official()
        assert official is not None
        official.etag = '"old"'
        await session.commit()
    equivalent_address = "https://github.com/ArcReel/market-index/tree/main"
    async with factory() as session:
        await seed_official_source(session, address=equivalent_address)

    [official] = await _sources(factory)
    assert official.address == equivalent_address
    assert official.index_url == "https://raw.githubusercontent.com/ArcReel/market-index/main/arcreel-market.json"
    assert official.canonical_key == "github:ArcReel/market-index@main"
    assert official.etag is None
    assert official.display_name == "官方"
    assert official.is_enabled is False


# ---------------------------------------------------------------------------
# 添加
# ---------------------------------------------------------------------------


async def test_add_fetches_once_and_stores_ok_snapshot(
    service: MarketSourceService, factory: async_sessionmaker[AsyncSession]
) -> None:
    with capture_http() as http:
        route = http.get(url__startswith=TEAM_URL).respond(
            json=_index("团队市场", "demo-video"), headers={"ETag": '"v1"'}
        )
        added = await service.add_source("  https://github.com/someone/market  ")

    request = only_request(route)
    assert request.url.params["_ts"] == str(int(NOW.timestamp() * 1000))
    assert added.kind == "custom"
    assert added.address == "https://github.com/someone/market"
    assert added.display_name == "团队市场"
    assert added.status == SourceStatus.OK
    assert added.cached_index == _index("团队市场", "demo-video")
    assert added.etag == '"v1"'
    assert [source.id for source in await _sources(factory)] == [added.id]


async def test_add_uses_given_display_name_and_appends_after_existing_sources(
    service: MarketSourceService, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as session:
        await seed_official_source(session)

    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).respond(json=_index())
        added = await service.add_source("someone/market", display_name=" 同事的源 ")

    assert added.display_name == "同事的源"
    assert [source.kind for source in await _sources(factory)] == ["official", "custom"]


async def test_add_rejects_failed_fetch_without_storing(
    service: MarketSourceService, factory: async_sessionmaker[AsyncSession]
) -> None:
    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).respond(404)

        with pytest.raises(MarketFetchError) as excinfo:
            await service.add_source("someone/market")

    assert excinfo.value.status is SourceStatus.UNREACHABLE
    assert await _sources(factory) == []


async def test_add_rejects_invalid_index_without_storing(
    service: MarketSourceService, factory: async_sessionmaker[AsyncSession]
) -> None:
    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).respond(json={"schema_version": "1.0.0", "entries": []})

        with pytest.raises(MarketFetchError) as excinfo:
            await service.add_source("someone/market")

    assert excinfo.value.status is SourceStatus.INVALID_INDEX
    assert await _sources(factory) == []


async def test_add_rejects_same_canonical_key_before_fetching(service: MarketSourceService) -> None:
    await _add_team_source(service)

    with capture_http() as http:
        with pytest.raises(DuplicateSourceError):
            await service.add_source("https://github.com/someone/market/tree/HEAD")
        assert http.calls.call_count == 0


async def test_add_compares_github_repository_case_insensitively_but_ref_case_sensitively(
    service: MarketSourceService,
) -> None:
    await _add_team_source(service)

    with capture_http() as http:
        with pytest.raises(DuplicateSourceError):
            await service.add_source("SomeOne/MARKET@HEAD")
        assert http.calls.call_count == 0

    lowercase_ref_url = "https://raw.githubusercontent.com/SomeOne/MARKET/head/arcreel-market.json"
    with capture_http() as http:
        http.get(url__startswith=lowercase_ref_url).respond(json=_index("另一个 ref"))
        added = await service.add_source("SomeOne/MARKET@head")

    assert added.canonical_key == "github:SomeOne/MARKET@head"


async def test_add_rejects_malformed_address(service: MarketSourceService) -> None:
    with pytest.raises(SourceAddressError):
        await service.add_source("git@github.com:someone/market.git")


# ---------------------------------------------------------------------------
# 刷新
# ---------------------------------------------------------------------------


async def test_failed_refresh_keeps_last_snapshot_and_records_status(
    service: MarketSourceService, clock: Clock
) -> None:
    added = await _add_team_source(service)
    clock.now = NOW + timedelta(hours=2)

    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).respond(json={"schema_version": "9.0.0"})
        refreshed = await service.refresh_source(added.id, manual=True)

    assert refreshed is not None
    assert refreshed.status == SourceStatus.UNSUPPORTED_SCHEMA
    assert refreshed.last_error
    assert refreshed.cached_index == _index("团队市场", "demo-video")
    assert refreshed.fetched_at is not None
    assert refreshed.fetched_at.replace(tzinfo=UTC) == NOW


async def test_successful_refresh_after_failure_clears_error(service: MarketSourceService, clock: Clock) -> None:
    added = await _add_team_source(service)
    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).mock(side_effect=httpx.ConnectError("down"))
        failed = await service.refresh_source(added.id, manual=True)
    assert failed is not None
    assert failed.status == SourceStatus.UNREACHABLE

    clock.now = NOW + timedelta(minutes=5)
    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).respond(json=_index("团队市场", "demo-video", "other-video"))
        recovered = await service.refresh_source(added.id, manual=True)

    assert recovered is not None
    assert recovered.status == SourceStatus.OK
    assert recovered.last_error is None
    assert recovered.cached_index == _index("团队市场", "demo-video", "other-video")
    assert recovered.fetched_at is not None
    assert recovered.fetched_at.replace(tzinfo=UTC) == NOW + timedelta(minutes=5)


async def test_manual_refresh_skips_etag_and_busts_cache(service: MarketSourceService, clock: Clock) -> None:
    added = await _add_team_source(service)
    clock.now = NOW + timedelta(seconds=30)

    with capture_http() as http:
        route = http.get(url__startswith=TEAM_URL).respond(json=_index())
        await service.refresh_source(added.id, manual=True)

    request = only_request(route)
    assert "if-none-match" not in request.headers
    assert request.url.params["_ts"] == str(int((NOW + timedelta(seconds=30)).timestamp() * 1000))


async def test_auto_refresh_sends_etag_and_not_modified_only_moves_fetched_at(
    service: MarketSourceService, clock: Clock
) -> None:
    added = await _add_team_source(service)
    clock.now = NOW + timedelta(hours=3)

    with capture_http() as http:
        route = http.get(TEAM_URL).respond(304)
        refreshed = await service.refresh_source(added.id, manual=False)

    request = only_request(route)
    assert request.headers["if-none-match"] == '"v1"'
    assert "_ts" not in request.url.params
    assert refreshed is not None
    assert refreshed.status == SourceStatus.OK
    assert refreshed.cached_index == _index("团队市场", "demo-video")
    assert refreshed.etag == '"v1"'
    assert refreshed.fetched_at is not None
    assert refreshed.fetched_at.replace(tzinfo=UTC) == NOW + timedelta(hours=3)


async def test_refresh_uses_proxy_prefix_setting(
    service: MarketSourceService, factory: async_sessionmaker[AsyncSession]
) -> None:
    added = await _add_team_source(service)
    async with factory() as session:
        await SystemSettingRepository(session).set(PROXY_PREFIX_SETTING, "https://proxy.example.net/")
        await session.commit()

    with capture_http() as http:
        route = http.get(url__startswith=f"https://proxy.example.net/{TEAM_URL}").respond(json=_index())
        refreshed = await service.refresh_source(added.id, manual=True)

    assert route.call_count == 1
    assert refreshed is not None
    assert refreshed.status == SourceStatus.OK


async def test_refresh_of_missing_source_returns_none(service: MarketSourceService) -> None:
    assert await service.refresh_source(404, manual=True) is None


async def test_single_refresh_skips_disabled_source(
    service: MarketSourceService, factory: async_sessionmaker[AsyncSession]
) -> None:
    added = await _add_team_source(service)
    async with factory() as session:
        stored = await MarketSourceRepository(session).get(added.id)
        assert stored is not None
        stored.is_enabled = False
        await session.commit()

    with capture_http() as http:
        unchanged = await service.refresh_source(added.id, manual=True)
        assert http.calls.call_count == 0

    assert unchanged is not None
    assert unchanged.is_enabled is False


async def test_refresh_all_skips_disabled_sources(
    service: MarketSourceService, factory: async_sessionmaker[AsyncSession]
) -> None:
    async with factory() as session:
        await seed_official_source(session)
    team = await _add_team_source(service)
    async with factory() as session:
        official = await MarketSourceRepository(session).get_official()
        assert official is not None
        official.is_enabled = False
        await session.commit()

    with capture_http() as http:
        team_route = http.get(url__startswith=TEAM_URL).respond(json=_index())
        official_route = http.get(url__startswith=OFFICIAL_URL).respond(json=_index("官方"))
        results = await service.refresh_all(stale_only=False)

    assert [source.id for source in results] == [team.id]
    assert team_route.call_count == 1
    assert official_route.call_count == 0


async def test_stale_refresh_only_touches_sources_older_than_an_hour(
    service: MarketSourceService, factory: async_sessionmaker[AsyncSession], clock: Clock
) -> None:
    async with factory() as session:
        await seed_official_source(session)
    await _add_team_source(service)
    clock.now = NOW + timedelta(minutes=59)

    with capture_http() as http:
        team_route = http.get(url__startswith=TEAM_URL).respond(304)
        official_route = http.get(url__startswith=OFFICIAL_URL).respond(json=_index("官方"))
        results = await service.refresh_all(stale_only=True)

    assert [source.kind for source in results] == ["official"]
    assert team_route.call_count == 0
    assert official_route.call_count == 1

    clock.now = NOW + timedelta(minutes=61)
    with capture_http() as http:
        team_route = http.get(url__startswith=TEAM_URL).respond(304)
        results = await service.refresh_all(stale_only=True)

    assert [source.kind for source in results] == ["custom"]
    assert team_route.call_count == 1


async def test_concurrent_refreshes_of_one_source_share_a_single_request(service: MarketSourceService) -> None:
    added = await _add_team_source(service)
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_index(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, json=_index())

    with capture_http() as http:
        route = http.get(url__startswith=TEAM_URL).mock(side_effect=slow_index)
        first = asyncio.create_task(service.refresh_source(added.id, manual=True))
        await started.wait()
        second = asyncio.create_task(service.refresh_source(added.id, manual=True))
        release.set()
        single, together = await asyncio.gather(first, second)

    assert route.call_count == 1
    assert single is not None
    assert together is not None
    assert together.id == single.id
