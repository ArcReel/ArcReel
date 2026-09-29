"""市场路由与官方服务的 HTTP 契约：安装上报、评分代理与聚合代理。

官方服务出站由 respx 在 transport 层拦截；安装上报是后台任务，ASGI 传输下随响应一并完成。
"""

import json
import uuid
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_market_core.market.entry import project_meta
from lib.config.repository import SystemSettingRepository
from lib.db import get_async_session
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.market_installation import MarketInstallation
from lib.db.models.market_source import MarketSource
from lib.market.entries import MarketEntryService, get_market_entry_service
from lib.market.installations import definition_digest
from lib.market.official_service import (
    ENABLED_SETTING,
    INSTANCE_ID_SETTING,
    OfficialServiceGateway,
    get_official_service_gateway,
)
from server.error_handlers import register_error_handlers
from server.routers import custom_endpoints, market, system_config
from tests.factories import custom_endpoint_definition

OFFICIAL_SERVICE = "https://official.test"
OFFICIAL_KEY = "github:ArcReel/arcreel-market@HEAD"
OFFICIAL_INDEX = "https://raw.githubusercontent.com/ArcReel/arcreel-market/HEAD/arcreel-market.json"
OFFICIAL_DEFINITION = "https://raw.githubusercontent.com/ArcReel/arcreel-market/HEAD/endpoints/example/definition.json"
CUSTOM_DEFINITION = "https://example.com/endpoints/example/definition.json"

ClientFactory = Callable[..., AbstractAsyncContextManager[httpx.AsyncClient]]


def _source(source_id: int, *, kind: str, canonical_key: str, index_url: str, slugs: list[str]) -> MarketSource:
    definition = custom_endpoint_definition()
    return MarketSource(
        id=source_id,
        kind=kind,
        display_name=f"Source {source_id}",
        address=index_url,
        index_url=index_url,
        canonical_key=canonical_key,
        is_enabled=True,
        position=source_id,
        status="ok",
        cached_index={
            "schema_version": "1.0.0",
            "name": "Market",
            "entries": [
                {
                    "type": "endpoint",
                    "slug": slug,
                    "path": f"endpoints/{slug}/definition.json",
                    **project_meta(definition),
                }
                for slug in slugs
            ],
        },
    )


@pytest.fixture
async def market_client_factory(session_factory: async_sessionmaker[AsyncSession]) -> ClientFactory:
    """官方市场源 1 与第三方源 2 各列出 ``example``；``base_url`` 为官方服务地址（空串即关闭）。"""
    async with session_factory() as session:
        session.add(
            _source(
                1, kind="official", canonical_key=OFFICIAL_KEY, index_url=OFFICIAL_INDEX, slugs=["example", "other"]
            )
        )
        session.add(
            _source(
                2,
                kind="custom",
                canonical_key="url:https://example.com/arcreel-market.json",
                index_url="https://example.com/arcreel-market.json",
                slugs=["example"],
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
            app.dependency_overrides[get_market_entry_service] = lambda: MarketEntryService(
                session_factory, http_client=lambda: network
            )
            app.dependency_overrides[get_official_service_gateway] = lambda: OfficialServiceGateway(
                session_factory, base_url=base_url, http_client=lambda: network
            )
            app.dependency_overrides[system_config.get_app_version_reader] = lambda: lambda: "0.30.0"
            app.include_router(market.router)
            app.include_router(custom_endpoints.router)
            register_error_handlers(app)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                yield client

    return factory


async def _install(
    client: httpx.AsyncClient, source_id: int = 1, overwrite_endpoint_id: int | None = None
) -> httpx.Response:
    body: dict[str, Any] = {"definition_digest": definition_digest(custom_endpoint_definition())}
    if overwrite_endpoint_id is not None:
        body["overwrite_endpoint_id"] = overwrite_endpoint_id
    return await client.post(f"/market/sources/{source_id}/entries/example/install", json=body)


async def _install_for_rating(client: httpx.AsyncClient) -> int:
    with respx.mock(assert_all_called=True) as remote:
        remote.get(OFFICIAL_DEFINITION).respond(json=custom_endpoint_definition())
        remote.post(f"{OFFICIAL_SERVICE}/api/v1/market/installs").respond(204)
        response = await _install(client)
    assert response.status_code == 200, response.text
    return response.json()["endpoint"]["id"]


@pytest.mark.parametrize("previously_installed", [False, True])
async def test_rating_requires_current_local_installation(
    market_client_factory: ClientFactory, previously_installed: bool
):
    async with market_client_factory() as client:
        endpoint_id = await _install_for_rating(client) if previously_installed else None
        with respx.mock(assert_all_called=False) as remote:
            if endpoint_id is not None:
                response = await client.delete(f"/custom-endpoints/{endpoint_id}")
                assert response.status_code == 204, response.text
            remote.put(f"{OFFICIAL_SERVICE}/api/v1/market/ratings").respond(204)
            response = await client.put("/market/sources/1/entries/example/rating", json={"stars": 4})
            assert response.status_code == 409, response.text
            assert response.json()["detail"] == "安装此条目后才能评分"
            assert not remote.calls


async def _setting(session_factory: async_sessionmaker[AsyncSession], key: str) -> str:
    async with session_factory() as session:
        return await SystemSettingRepository(session).get(key)


async def test_official_install_reports_once_with_instance_header(
    market_client_factory: ClientFactory, session_factory: async_sessionmaker[AsyncSession]
):
    async with market_client_factory() as client:
        with respx.mock(assert_all_called=True) as remote:
            remote.get(OFFICIAL_DEFINITION).respond(json=custom_endpoint_definition())
            report = remote.post(f"{OFFICIAL_SERVICE}/api/v1/market/installs").respond(204)
            installed = await _install(client)
            assert installed.status_code == 200, installed.text
            updated = await _install(client, overwrite_endpoint_id=installed.json()["endpoint"]["id"])
            assert updated.status_code == 200, updated.text
    assert report.call_count == 1
    request = report.calls.last.request
    assert json.loads(request.content) == {
        "type": "endpoint",
        "source": OFFICIAL_KEY,
        "slug": "example",
        "version": "0.1.0",
        "app_version": "0.30.0",
    }
    instance_id = request.headers["X-ArcReel-Instance"]
    assert uuid.UUID(instance_id).version == 4
    assert await _setting(session_factory, INSTANCE_ID_SETTING) == instance_id


@pytest.mark.parametrize(
    "failure",
    [httpx.Response(503, json={"code": "instance_hash_unconfigured", "params": {}}), httpx.ReadTimeout("timed out")],
)
async def test_install_succeeds_when_official_service_fails(
    market_client_factory: ClientFactory,
    session_factory: async_sessionmaker[AsyncSession],
    failure: httpx.Response | Exception,
):
    async with market_client_factory() as client:
        with respx.mock(assert_all_called=True) as remote:
            remote.get(OFFICIAL_DEFINITION).respond(json=custom_endpoint_definition())
            report = remote.post(f"{OFFICIAL_SERVICE}/api/v1/market/installs").mock(side_effect=[failure])
            response = await _install(client)
    assert response.status_code == 200, response.text
    assert response.json()["installation"]["state"] == "current"
    assert report.call_count == 1
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(MarketInstallation)) == 1
        assert await session.scalar(select(func.count()).select_from(CustomEndpoint)) == 1


@pytest.mark.parametrize("case", ["toggle_off", "empty_url", "third_party_source"])
async def test_no_outbound_to_official_service(
    market_client_factory: ClientFactory, session_factory: async_sessionmaker[AsyncSession], case: str
):
    if case == "toggle_off":
        async with session_factory() as session:
            await SystemSettingRepository(session).set(ENABLED_SETTING, "false")
            await session.commit()
    source_id, definition_url = (2, CUSTOM_DEFINITION) if case == "third_party_source" else (1, OFFICIAL_DEFINITION)
    async with market_client_factory("" if case == "empty_url" else OFFICIAL_SERVICE) as client:
        with respx.mock() as remote:
            remote.get(definition_url).respond(json=custom_endpoint_definition())
            response = await _install(client, source_id)
            assert response.status_code == 200, response.text
            if case != "third_party_source":
                rating = await client.put("/market/sources/1/entries/example/rating", json={"stars": 5})
                assert rating.status_code == 409
                assert rating.json()["detail"] == "官方服务已关闭"
                aggregates = await client.get("/market/entries/aggregates")
                assert aggregates.status_code == 409
            assert [str(call.request.url) for call in remote.calls] == [definition_url]
    assert await _setting(session_factory, INSTANCE_ID_SETTING) == ""


async def test_rating_is_forwarded_with_instance_header(market_client_factory: ClientFactory):
    async with market_client_factory() as client:
        await _install_for_rating(client)
        with respx.mock(assert_all_called=True) as remote:
            rating = remote.put(f"{OFFICIAL_SERVICE}/api/v1/market/ratings").respond(204)
            response = await client.put("/market/sources/1/entries/example/rating", json={"stars": 4})
            assert response.status_code == 204, response.text
            request = rating.calls.last.request
    assert json.loads(request.content) == {"type": "endpoint", "source": OFFICIAL_KEY, "slug": "example", "stars": 4}
    assert uuid.UUID(request.headers["X-ArcReel-Instance"]).version == 4


@pytest.mark.parametrize(
    ("status", "code", "params", "detail"),
    [
        (409, "not_installed", {}, "官方服务没有此实例安装该条目的记录，无法评分"),
        (429, "rate_limited", {}, "请求过于频繁，请稍后重试"),
        (422, "brand_new_code", {"field": "stars"}, "官方服务返回了错误（brand_new_code）"),
    ],
)
async def test_rating_error_code_lands_in_local_response(
    market_client_factory: ClientFactory, status: int, code: str, params: dict[str, Any], detail: str
):
    async with market_client_factory() as client:
        await _install_for_rating(client)
        with respx.mock(assert_all_called=True) as remote:
            remote.put(f"{OFFICIAL_SERVICE}/api/v1/market/ratings").respond(
                status, json={"code": code, "params": params}
            )
            response = await client.put("/market/sources/1/entries/example/rating", json={"stars": 3})
    assert response.status_code == status
    assert response.json() == {"detail": detail, "diagnostic": {"official_service": {"code": code, "params": params}}}


@pytest.mark.parametrize("failure", [httpx.Response(502, text="Bad Gateway"), httpx.ConnectTimeout("timed out")])
async def test_rating_reports_unreachable_official_service_without_retry(
    market_client_factory: ClientFactory, failure: httpx.Response | Exception
):
    async with market_client_factory() as client:
        await _install_for_rating(client)
        with respx.mock(assert_all_called=True) as remote:
            rating = remote.put(f"{OFFICIAL_SERVICE}/api/v1/market/ratings").mock(side_effect=[failure])
            response = await client.put("/market/sources/1/entries/example/rating", json={"stars": 3})
            assert rating.call_count == 1
    assert response.status_code == 502
    assert response.json()["detail"] == "暂时无法连接官方服务，请稍后重试"


@pytest.mark.parametrize(
    ("path", "stars", "status", "detail"),
    [
        ("/market/sources/2/entries/example/rating", 5, 409, "只有官方市场源的条目可以评分"),
        ("/market/sources/1/entries/missing/rating", 5, 404, "市场条目不存在"),
        ("/market/sources/1/entries/example/rating", 6, 422, None),
    ],
)
async def test_rating_rejected_locally_without_outbound(
    market_client_factory: ClientFactory, path: str, stars: int, status: int, detail: str | None
):
    async with market_client_factory() as client:
        with respx.mock() as remote:
            response = await client.put(path, json={"stars": stars})
            assert remote.calls.call_count == 0
    assert response.status_code == status
    if detail is not None:
        assert response.json()["detail"] == detail


async def test_aggregates_only_query_official_entries(market_client_factory: ClientFactory):
    async with market_client_factory() as client:
        with respx.mock(assert_all_called=True) as remote:
            route = remote.post(f"{OFFICIAL_SERVICE}/api/v1/market/aggregates").respond(
                json={
                    "items": [
                        {"type": "endpoint", "source": OFFICIAL_KEY, "slug": slug, **numbers}
                        for slug, numbers in [
                            ("example", {"installs": 1000, "rating_count": 5, "rating_average": 4.33}),
                            ("other", {"installs": 0, "rating_count": 2, "rating_average": None}),
                        ]
                    ]
                }
            )
            response = await client.get("/market/entries/aggregates")
            request = route.calls.last.request
    assert response.status_code == 200, response.text
    assert json.loads(request.content) == {
        "items": [
            {"type": "endpoint", "source": OFFICIAL_KEY, "slug": "example"},
            {"type": "endpoint", "source": OFFICIAL_KEY, "slug": "other"},
        ]
    }
    assert response.json() == {
        "items": [
            {"source_id": 1, "slug": "example", "installs": 1000, "rating_count": 5, "rating_average": 4.33},
            {"source_id": 1, "slug": "other", "installs": 0, "rating_count": 2, "rating_average": None},
        ]
    }


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(200, json={"items": []}),
        httpx.Response(503, json={"code": "instance_hash_unconfigured", "params": {}}),
        httpx.ReadTimeout("timed out"),
    ],
)
async def test_aggregates_failure_is_an_error_response(
    market_client_factory: ClientFactory, failure: httpx.Response | Exception
):
    async with market_client_factory() as client:
        with respx.mock(assert_all_called=True) as remote:
            remote.post(f"{OFFICIAL_SERVICE}/api/v1/market/aggregates").mock(side_effect=[failure])
            response = await client.get("/market/entries/aggregates")
    assert response.status_code in {502, 503}
    assert "items" not in response.json()


async def test_aggregates_split_into_batches_of_one_hundred(
    market_client_factory: ClientFactory, session_factory: async_sessionmaker[AsyncSession]
):
    slugs = [f"entry-{index:03d}" for index in range(101)]
    async with session_factory() as session:
        official = await session.get(MarketSource, 1)
        assert official is not None
        official.cached_index = _source(
            1, kind="official", canonical_key=OFFICIAL_KEY, index_url=OFFICIAL_INDEX, slugs=slugs
        ).cached_index
        await session.commit()

    def respond(request: httpx.Request) -> httpx.Response:
        items = json.loads(request.content)["items"]
        return httpx.Response(
            200, json={"items": [{**item, "installs": 1, "rating_count": 0, "rating_average": None} for item in items]}
        )

    async with market_client_factory() as client:
        with respx.mock(assert_all_called=True) as remote:
            route = remote.post(f"{OFFICIAL_SERVICE}/api/v1/market/aggregates").mock(side_effect=respond)
            response = await client.get("/market/entries/aggregates")
            batch_sizes = [len(json.loads(call.request.content)["items"]) for call in route.calls]
    assert response.status_code == 200, response.text
    assert batch_sizes == [100, 1]
    assert sorted(item["slug"] for item in response.json()["items"]) == slugs
