"""官方服务设置的 HTTP 契约：总开关、首次告知标记与实例标识重置。"""

import json
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_market_core.market.entry import project_meta
from lib.db import get_async_session
from lib.db.models.market_source import MarketSource
from lib.market.official_service import OfficialServiceGateway, get_official_service_gateway
from server.error_handlers import register_error_handlers
from server.routers import market, official_service
from tests.factories import custom_endpoint_definition

OFFICIAL_SERVICE = "https://official.test"
AGGREGATES = f"{OFFICIAL_SERVICE}/api/v1/market/aggregates"

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


@pytest.fixture
async def settings_client_factory(session_factory: async_sessionmaker[AsyncSession]) -> ClientFactory:
    async with session_factory() as session:
        session.add(
            MarketSource(
                id=1,
                kind="official",
                display_name="ArcReel Market",
                address="ArcReel/arcreel-market",
                index_url="https://raw.githubusercontent.com/ArcReel/arcreel-market/HEAD/arcreel-market.json",
                canonical_key="github:ArcReel/arcreel-market@HEAD",
                is_enabled=True,
                position=1,
                status="ok",
                cached_index={
                    "schema_version": "1.0.0",
                    "name": "Market",
                    "entries": [
                        {
                            "type": "endpoint",
                            "slug": "example",
                            "path": "endpoints/example/definition.json",
                            **project_meta(custom_endpoint_definition()),
                        }
                    ],
                },
            )
        )
        await session.commit()

    async def session_override():
        async with session_factory() as session:
            yield session

    @asynccontextmanager
    async def factory(base_url: str = OFFICIAL_SERVICE) -> AsyncGenerator[httpx.AsyncClient]:
        app = FastAPI()
        async with httpx.AsyncClient() as network:
            app.dependency_overrides[get_async_session] = session_override
            app.dependency_overrides[get_official_service_gateway] = lambda: OfficialServiceGateway(
                session_factory, base_url=base_url, http_client=lambda: network
            )
            app.include_router(official_service.router)
            app.include_router(market.router)
            register_error_handlers(app)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client

    return factory


async def _query_aggregates(client: httpx.AsyncClient) -> str:
    """查询聚合一次，返回官方服务收到的实例标识。"""
    with respx.mock(assert_all_called=True) as remote:
        route = remote.post(AGGREGATES).mock(
            side_effect=lambda request: httpx.Response(
                200,
                json={
                    "items": [
                        {**ref, "installs": 0, "rating_count": 0, "rating_average": None}
                        for ref in json.loads(request.content)["items"]
                    ]
                },
            )
        )
        assert (await client.get("/market/entries/aggregates")).status_code == 200
        return route.calls.last.request.headers["X-ArcReel-Instance"]


@pytest.mark.parametrize(
    ("base_url", "available"),
    [(OFFICIAL_SERVICE, True), ("", False)],
)
async def test_default_state(settings_client_factory: ClientFactory, base_url: str, available: bool):
    async with settings_client_factory(base_url) as client:
        response = await client.get("/official-service")
    assert response.status_code == 200
    assert response.json() == {"available": available, "enabled": available, "notice_seen": False, "instance_id": None}


async def test_turning_off_from_notice_disables_outbound(settings_client_factory: ClientFactory):
    async with settings_client_factory() as client:
        response = await client.patch("/official-service", json={"enabled": False, "notice_seen": True})
        assert response.json() == {"available": True, "enabled": False, "notice_seen": True, "instance_id": None}
        assert (await client.get("/official-service")).json() == response.json()
        with respx.mock() as remote:
            rating = await client.put("/market/sources/1/entries/example/rating", json={"stars": 5})
            assert remote.calls.call_count == 0
        assert rating.status_code == 409
        turned_on = await client.patch("/official-service", json={"enabled": True})
        assert turned_on.json()["enabled"] is True
        assert turned_on.json()["notice_seen"] is True


async def test_reset_instance_id_starts_a_new_instance(settings_client_factory: ClientFactory):
    async with settings_client_factory() as client:
        first = await _query_aggregates(client)
        assert (await client.get("/official-service")).json()["instance_id"] == first
        reset = await client.post("/official-service/instance-id/reset")
        assert reset.status_code == 200
        assert reset.json()["instance_id"] is None
        second = await _query_aggregates(client)
        assert (await client.get("/official-service")).json()["instance_id"] == second
    assert second != first
