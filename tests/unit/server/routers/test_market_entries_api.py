"""市场条目 API：启用源条目合并、版本门槛、条目详情不抓定义、定义原文与 icon 代理的状态码与缓存头。"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from io import BytesIO
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from lib.db import get_async_session
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import MarketSourceRepository
from lib.market.entries import MarketEntryService, get_market_entry_service
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import market, system_config
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.http_capture import capture_http

TEAM_DIR = "https://raw.githubusercontent.com/someone/market/HEAD/"
OFFICIAL_DIR = "https://raw.githubusercontent.com/ArcReel/arcreel-market/HEAD/"


def _entry(slug: str, name: str, **extra: Any) -> dict[str, Any]:
    return {
        "type": "endpoint",
        "slug": slug,
        "path": f"endpoints/{slug}/definition.json",
        "name": name,
        "author": "someone",
        "version": "1.0.0",
        "media_type": "video",
        **extra,
    }


def _source(
    source_id: int, directory: str, *entries: dict[str, Any], enabled: bool = True, kind: str = "custom"
) -> MarketSource:
    index_url = f"{directory}arcreel-market.json"
    return MarketSource(
        id=source_id,
        kind=kind,
        display_name=f"源 {source_id}",
        address=index_url,
        index_url=index_url,
        canonical_key=f"url:{index_url}",
        is_enabled=enabled,
        position=source_id,
        status="ok",
        cached_index={
            "schema_version": "1.0.0",
            "name": f"索引 {source_id}",
            "homepage": "https://example.com/market",
            "entries": [*entries, {"type": "prompt-template", "slug": "future"}],
        },
    )


@pytest.fixture
def entries_sessions(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def entries_client(entries_sessions: async_sessionmaker[AsyncSession]) -> Generator[TestClient]:
    http_client = httpx.AsyncClient()
    service = MarketEntryService(entries_sessions, http_client=lambda: http_client)
    app = FastAPI()

    async def _override_session():
        async with entries_sessions() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    app.dependency_overrides[get_market_entry_service] = lambda: service
    app.dependency_overrides[system_config.get_app_version_reader] = lambda: lambda: "0.30.0"
    app.include_router(market.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(http_client.aclose())


@pytest.fixture
async def catalog(entries_sessions: async_sessionmaker[AsyncSession]) -> None:
    async with entries_sessions() as session:
        repo = MarketSourceRepository(session)
        await repo.add(
            _source(
                1,
                OFFICIAL_DIR,
                _entry("zeta", "Zeta"),
                _entry("alpha", "Alpha", icon="endpoints/alpha/icon.png", min_app_version="0.31.0"),
                kind="official",
            )
        )
        await repo.add(_source(2, TEAM_DIR, _entry("alpha", "Alpha 团队版", description="同 slug 并列")))
        await repo.add(_source(3, TEAM_DIR.replace("someone", "other"), _entry("hidden", "Hidden"), enabled=False))
        await session.commit()


def _png() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (16, 16)).save(buffer, format="PNG")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# 列表与详情
# ---------------------------------------------------------------------------


def test_entries_merge_enabled_sources_in_source_then_name_order(entries_client: TestClient, catalog: None) -> None:
    with capture_http() as http:
        response = entries_client.get("/api/v1/market/entries", params={"type": "endpoint"})
        assert http.calls.call_count == 0

    assert response.status_code == 200
    body = response.json()
    assert body["app_version"] == "0.30.0"
    assert [(e["source_id"], e["source_display_name"], e["slug"]) for e in body["entries"]] == [
        (1, "源 1", "alpha"),
        (1, "源 1", "zeta"),
        (2, "源 2", "alpha"),
    ]
    official_alpha = body["entries"][0]
    assert official_alpha["icon"] == "endpoints/alpha/icon.png"
    assert official_alpha["min_app_version"] == "0.31.0"
    assert official_alpha["min_app_version_satisfied"] is False
    assert body["entries"][2]["min_app_version_satisfied"] is True
    assert body["entries"][2]["description"] == "同 slug 并列"


def test_disabled_source_entries_vanish_but_source_stays_listed(entries_client: TestClient, catalog: None) -> None:
    entries = entries_client.get("/api/v1/market/entries").json()["entries"]
    sources = entries_client.get("/api/v1/market/sources").json()["sources"]

    assert "hidden" not in {entry["slug"] for entry in entries}
    assert [source["id"] for source in sources] == [1, 2, 3]


def test_unknown_entry_type_lists_nothing(entries_client: TestClient, catalog: None) -> None:
    assert entries_client.get("/api/v1/market/entries", params={"type": "prompt"}).json()["entries"] == []


def test_unreadable_app_version_does_not_gate_entries(entries_client: TestClient, catalog: None) -> None:
    def _broken() -> str:
        raise OSError("pyproject missing")

    app = entries_client.app
    assert isinstance(app, FastAPI)
    app.dependency_overrides[system_config.get_app_version_reader] = lambda: _broken

    body = entries_client.get("/api/v1/market/entries").json()

    assert body["app_version"] is None
    assert all(entry["min_app_version_satisfied"] for entry in body["entries"])


def test_entry_detail_returns_index_entry_and_source_summary_without_fetching(
    entries_client: TestClient, catalog: None
) -> None:
    with capture_http() as http:
        response = entries_client.get("/api/v1/market/sources/1/entries/alpha")
        assert http.calls.call_count == 0

    assert response.status_code == 200
    body = response.json()
    assert body["entry"]["name"] == "Alpha"
    assert body["entry"]["path"] == "endpoints/alpha/definition.json"
    assert body["source"]["kind"] == "official"
    assert body["source"]["canonical_key"] == f"url:{OFFICIAL_DIR}arcreel-market.json"
    assert body["source"]["index"]["homepage"] == "https://example.com/market"
    assert body["app_version"] == "0.30.0"


@pytest.mark.parametrize(
    ("url", "detail"),
    [
        ("/api/v1/market/sources/9/entries/alpha", "市场源不存在"),
        ("/api/v1/market/sources/1/entries/nope", "市场条目不存在"),
    ],
)
def test_entry_detail_not_found(entries_client: TestClient, catalog: None, url: str, detail: str) -> None:
    response = entries_client.get(url)

    assert response.status_code == 404
    assert response.json()["detail"] == detail


# ---------------------------------------------------------------------------
# 定义原文
# ---------------------------------------------------------------------------


def test_definition_is_returned_verbatim(entries_client: TestClient, catalog: None) -> None:
    definition = {"schema_version": "1.1.0", "meta": {"name": "Alpha"}, "auth": {"type": "bearer"}}
    with capture_http() as http:
        http.get(f"{TEAM_DIR}endpoints/alpha/definition.json").respond(json=definition)
        response = entries_client.get("/api/v1/market/sources/2/entries/alpha/definition")

    assert response.status_code == 200
    assert response.json() == {"definition": definition}


@pytest.mark.parametrize(
    ("upstream", "reason"),
    [
        (httpx.Response(404), "HTTP 404"),
        (httpx.Response(200, text="../shared/definition.json"), "definition is not valid JSON"),
    ],
)
def test_definition_upstream_failure_is_bad_gateway(
    entries_client: TestClient, catalog: None, upstream: httpx.Response, reason: str
) -> None:
    with capture_http() as http:
        http.get(f"{TEAM_DIR}endpoints/alpha/definition.json").mock(return_value=upstream)
        response = entries_client.get("/api/v1/market/sources/2/entries/alpha/definition")

    assert response.status_code == 502
    assert response.json()["diagnostic"]["reason"].startswith(reason)


def test_definition_of_disabled_source_is_conflict(entries_client: TestClient, catalog: None) -> None:
    with capture_http() as http:
        response = entries_client.get("/api/v1/market/sources/3/entries/hidden/definition")
        assert http.calls.call_count == 0

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# icon
# ---------------------------------------------------------------------------


def test_icon_is_proxied_with_cache_headers(entries_client: TestClient, catalog: None) -> None:
    png = _png()
    with capture_http() as http:
        route = http.get(f"{OFFICIAL_DIR}endpoints/alpha/icon.png").respond(content=png)
        first = entries_client.get("/api/v1/market/sources/1/entries/alpha/icon", params={"v": "1.0.0"})
        second = entries_client.get("/api/v1/market/sources/1/entries/alpha/icon", params={"v": "1.0.0"})

    assert route.call_count == 1
    for response in (first, second):
        assert response.status_code == 200
        assert response.content == png
        assert response.headers["content-type"] == "image/png"
        assert response.headers["cache-control"] == "private, max-age=86400"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "sandbox" in response.headers["content-security-policy"]


def test_icon_of_entry_without_icon_is_not_found(entries_client: TestClient, catalog: None) -> None:
    response = entries_client.get("/api/v1/market/sources/1/entries/zeta/icon")

    assert response.status_code == 404
    assert response.json()["detail"] == "该市场条目没有图标"


def test_icon_failing_inspection_is_bad_gateway(entries_client: TestClient, catalog: None) -> None:
    with capture_http() as http:
        http.get(f"{OFFICIAL_DIR}endpoints/alpha/icon.png").respond(content=b"not a png")
        response = entries_client.get("/api/v1/market/sources/1/entries/alpha/icon")

    assert response.status_code == 502
    assert response.json()["diagnostic"] == {"reason": "icon rejected: icon_format_invalid"}
