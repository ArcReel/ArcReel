"""市场源 API：列表摘要、添加即抓取、改名与启停、官方源不可删、全排列重排、单源与全部刷新。"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lib.db import get_async_session
from lib.market.sources import MarketSourceService, get_market_source_service, seed_official_source
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import market
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.http_capture import capture_http

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
TEAM_URL = "https://raw.githubusercontent.com/someone/market/HEAD/arcreel-market.json"
OFFICIAL_URL = "https://raw.githubusercontent.com/ArcReel/arcreel-market/HEAD/arcreel-market.json"


def _index(name: str = "团队市场", *, entries: int = 1, **extra: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema_version": "1.0.0",
        "name": name,
        "entries": [
            {
                "type": "endpoint",
                "slug": f"demo-{n}",
                "path": f"endpoints/demo-{n}/definition.json",
                "name": f"演示 {n}",
                "author": "someone",
                "version": "1.0.0",
                "media_type": "video",
            }
            for n in range(entries)
        ]
        + [{"type": "prompt-template", "slug": "future"}],
    }
    document.update(extra)
    return document


@pytest.fixture
def factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def market_client(factory: async_sessionmaker[AsyncSession]) -> Generator[TestClient]:
    http_client = httpx.AsyncClient()
    service = MarketSourceService(factory, http_client=lambda: http_client, clock=lambda: NOW)
    app = FastAPI()

    async def _override_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    app.dependency_overrides[get_market_source_service] = lambda: service
    app.include_router(market.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(http_client.aclose())


@pytest.fixture
async def official(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        await seed_official_source(session)


def _add_team(market_client: TestClient, **body: Any) -> dict[str, Any]:
    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).respond(
            json=_index("团队市场", entries=2, description="同事维护", homepage="https://example.com/market")
        )
        response = market_client.post("/api/v1/market/sources", json={"address": "someone/market", **body})
    assert response.status_code == 201, response.text
    return response.json()


def _list(market_client: TestClient) -> list[dict[str, Any]]:
    response = market_client.get("/api/v1/market/sources")
    assert response.status_code == 200
    return response.json()["sources"]


def test_list_shows_seeded_official_source_before_any_fetch(market_client: TestClient, official: None) -> None:
    [row] = _list(market_client)

    assert row["kind"] == "official"
    assert row["address"] == "ArcReel/arcreel-market"
    assert row["canonical_key"] == "github:ArcReel/arcreel-market@HEAD"
    assert row["status"] == "never_fetched"
    assert row["fetched_at"] is None
    assert row["entry_count"] == 0
    assert row["index"] is None
    assert "cached_index" not in row


def test_add_returns_full_row_with_index_summary(market_client: TestClient, official: None) -> None:
    row = _add_team(market_client)

    assert row["kind"] == "custom"
    assert row["display_name"] == "团队市场"
    assert row["index_url"] == TEAM_URL
    assert row["status"] == "ok"
    assert row["last_error"] is None
    assert row["is_enabled"] is True
    assert row["entry_count"] == 2
    assert row["index"] == {"name": "团队市场", "description": "同事维护", "homepage": "https://example.com/market"}
    assert datetime.fromisoformat(row["fetched_at"]) == NOW
    assert [source["id"] for source in _list(market_client)][-1] == row["id"]


def test_add_with_unreachable_index_is_rejected_with_reason(market_client: TestClient) -> None:
    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).respond(404)
        response = market_client.post("/api/v1/market/sources", json={"address": "someone/market"})

    assert response.status_code == 422
    assert "HTTP 404" in response.json()["detail"]
    assert response.json()["diagnostic"] == {"status": "unreachable", "reason": "HTTP 404"}
    assert _list(market_client) == []


def test_add_with_invalid_index_is_rejected(market_client: TestClient) -> None:
    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).respond(json={"schema_version": "1.0.0", "name": "x", "entries": [{}]})
        response = market_client.post("/api/v1/market/sources", json={"address": "someone/market"})

    assert response.status_code == 422
    assert response.json()["diagnostic"]["status"] == "invalid_index"
    assert _list(market_client) == []


@pytest.mark.parametrize("address", ["http://github.com/someone/market", "git@github.com:someone/market.git", " "])
def test_add_with_rejected_address_form_is_422_without_fetching(market_client: TestClient, address: str) -> None:
    with capture_http() as http:
        response = market_client.post("/api/v1/market/sources", json={"address": address})
        assert http.calls.call_count == 0

    assert response.status_code == 422
    assert response.json()["detail"]


def test_add_same_repository_twice_is_rejected(market_client: TestClient) -> None:
    _add_team(market_client)

    response = market_client.post("/api/v1/market/sources", json={"address": "https://github.com/someone/market"})

    assert response.status_code == 409
    assert len(_list(market_client)) == 1


def test_patch_renames_and_toggles(market_client: TestClient, official: None) -> None:
    [row] = _list(market_client)

    response = market_client.patch(
        f"/api/v1/market/sources/{row['id']}", json={"display_name": "  官方  ", "is_enabled": False}
    )

    assert response.status_code == 200
    assert response.json()["display_name"] == "官方"
    assert response.json()["is_enabled"] is False
    [listed] = _list(market_client)
    assert (listed["display_name"], listed["is_enabled"]) == ("官方", False)


def test_patch_rejects_blank_display_name(market_client: TestClient, official: None) -> None:
    [row] = _list(market_client)

    response = market_client.patch(f"/api/v1/market/sources/{row['id']}", json={"display_name": "   "})

    assert response.status_code == 422
    assert _list(market_client)[0]["display_name"] == row["display_name"]


def test_patch_missing_source_is_404(market_client: TestClient) -> None:
    assert market_client.patch("/api/v1/market/sources/999", json={"is_enabled": False}).status_code == 404


def test_official_source_cannot_be_deleted(market_client: TestClient, official: None) -> None:
    [row] = _list(market_client)

    response = market_client.delete(f"/api/v1/market/sources/{row['id']}")

    assert response.status_code == 409
    assert len(_list(market_client)) == 1


def test_custom_source_delete(market_client: TestClient, official: None) -> None:
    team = _add_team(market_client)

    assert market_client.delete(f"/api/v1/market/sources/{team['id']}").status_code == 204
    assert [source["kind"] for source in _list(market_client)] == ["official"]
    assert market_client.delete(f"/api/v1/market/sources/{team['id']}").status_code == 404


def test_reorder_accepts_full_permutation(market_client: TestClient, official: None) -> None:
    team = _add_team(market_client)
    official_id = _list(market_client)[0]["id"]

    response = market_client.put("/api/v1/market/sources/order", json={"ids": [team["id"], official_id]})

    assert response.status_code == 200
    assert [source["id"] for source in response.json()["sources"]] == [team["id"], official_id]
    assert [source["id"] for source in _list(market_client)] == [team["id"], official_id]


@pytest.mark.parametrize("shape", ["short", "duplicate", "unknown"])
def test_reorder_rejects_non_permutation(market_client: TestClient, official: None, shape: str) -> None:
    team = _add_team(market_client)
    official_id = _list(market_client)[0]["id"]
    ids = {
        "short": [team["id"]],
        "duplicate": [team["id"], team["id"]],
        "unknown": [team["id"], 999],
    }[shape]

    response = market_client.put("/api/v1/market/sources/order", json={"ids": ids})

    assert response.status_code == 400
    assert [source["id"] for source in _list(market_client)] == [official_id, team["id"]]


def test_refresh_single_source_reports_failure_and_keeps_snapshot(market_client: TestClient) -> None:
    team = _add_team(market_client)

    with capture_http() as http:
        http.get(url__startswith=TEAM_URL).mock(side_effect=httpx.ConnectTimeout("slow"))
        response = market_client.post(f"/api/v1/market/sources/{team['id']}/refresh")

    assert response.status_code == 200
    row = response.json()
    assert row["status"] == "unreachable"
    assert row["last_error"]
    assert row["entry_count"] == 2
    assert row["fetched_at"] == team["fetched_at"]


def test_refresh_missing_source_is_404(market_client: TestClient) -> None:
    assert market_client.post("/api/v1/market/sources/999/refresh").status_code == 404


def test_refresh_all_returns_per_source_results_for_enabled_sources(market_client: TestClient, official: None) -> None:
    team = _add_team(market_client)
    official_id = _list(market_client)[0]["id"]
    market_client.patch(f"/api/v1/market/sources/{team['id']}", json={"is_enabled": False})

    with capture_http() as http:
        official_route = http.get(url__startswith=OFFICIAL_URL).respond(json=_index("ArcReel 官方", entries=0))
        team_route = http.get(url__startswith=TEAM_URL).respond(json=_index())
        response = market_client.post("/api/v1/market/refresh")

    assert response.status_code == 200
    [result] = response.json()["sources"]
    assert result["id"] == official_id
    assert result["status"] == "ok"
    assert official_route.call_count == 1
    assert team_route.call_count == 0


def test_stale_refresh_skips_recently_fetched_sources(market_client: TestClient, official: None) -> None:
    _add_team(market_client)

    with capture_http() as http:
        official_route = http.get(url__startswith=OFFICIAL_URL).respond(json=_index("ArcReel 官方", entries=0))
        team_route = http.get(url__startswith=TEAM_URL).respond(304)
        response = market_client.post("/api/v1/market/refresh", params={"stale_only": "true"})

    assert response.status_code == 200
    assert [source["kind"] for source in response.json()["sources"]] == ["official"]
    assert official_route.call_count == 1
    assert team_route.call_count == 0
