"""真实安装事务、来源绑定、原地覆盖与卸载的 HTTP 契约。"""

from collections.abc import AsyncIterator

import httpx
import pytest
import respx
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db import get_async_session
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.models.market_installation import MarketInstallation
from lib.db.models.market_source import MarketSource
from lib.market.entries import MarketEntryService, get_market_entry_service
from lib.market.entry import project_meta
from lib.market.installations import definition_digest
from server.error_handlers import register_error_handlers
from server.routers import custom_endpoints, market, system_config
from tests.factories import custom_endpoint_definition

BASE = "/market/sources/1/entries/example"
URL = "https://example.com/endpoints/example/definition.json"


@pytest.fixture
async def install_client(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[httpx.AsyncClient]:
    definition = custom_endpoint_definition()
    async with session_factory() as session:
        for source_id in (1, 2):
            session.add(
                MarketSource(
                    id=source_id,
                    kind="custom",
                    display_name=f"Source {source_id}",
                    address=f"https://example.com/{source_id}/arcreel-market.json",
                    index_url="https://example.com/arcreel-market.json",
                    canonical_key=f"url:source-{source_id}",
                    is_enabled=True,
                    position=source_id,
                    status="ok",
                    cached_index={
                        "schema_version": "1.0.0",
                        "name": "Market",
                        "entries": [
                            {
                                "type": "endpoint",
                                "slug": "example",
                                "path": "endpoints/example/definition.json",
                                **project_meta(definition["meta"]),
                            }
                        ],
                    },
                )
            )
        await session.commit()
    app = FastAPI()

    async def session_override():
        async with session_factory() as session:
            yield session

    async with httpx.AsyncClient() as network:
        service = MarketEntryService(session_factory, http_client=lambda: network)
        app.dependency_overrides[get_async_session] = session_override
        app.dependency_overrides[get_market_entry_service] = lambda: service
        app.dependency_overrides[system_config.get_app_version_reader] = lambda: lambda: "0.30.0"
        app.include_router(market.router)
        app.include_router(custom_endpoints.router)
        register_error_handlers(app)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            yield client


async def test_install_record_responses_and_uninstall_cascade(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
):
    definition = custom_endpoint_definition()
    local = (await install_client.post("/custom-endpoints", json=definition)).json()
    assert local["installation"] is None
    assert (await install_client.get(BASE)).json()["entry"]["installation"] is None
    with respx.mock(assert_all_called=True) as remote:
        route = remote.get(URL).respond(json=definition)
        response = await install_client.post(f"{BASE}/install", json={})
        assert response.status_code == 200, response.text
        assert route.call_count == 1
    payload = response.json()
    endpoint = payload["endpoint"]
    assert endpoint["id"] != local["id"]
    assert endpoint["definition"] == definition
    assert endpoint["installation"]["source_key"] == "url:source-1"
    assert payload["installation"]["state"] == "current"
    assert payload["installation"]["modified"] is False
    assert (await install_client.get(BASE)).json()["entry"]["installation"] == payload["installation"]
    assert (await install_client.get("/market/entries")).json()["entries"][0]["installation"] == payload["installation"]
    assert (await install_client.get(f"/custom-endpoints/{endpoint['id']}")).json() == endpoint
    listed = (await install_client.get("/custom-endpoints")).json()["endpoints"]
    assert listed[1]["installation"] == endpoint["installation"]
    async with session_factory() as session:
        record = await session.get(MarketInstallation, endpoint["id"])
        stored = await session.get(CustomEndpoint, endpoint["id"])
        assert record is not None
        assert stored is not None
        assert record.installed_digest == definition_digest(stored.definition) == definition_digest(definition)
        assert record.installed_version == definition["meta"]["version"]
    assert (await install_client.delete(f"/custom-endpoints/{endpoint['id']}")).status_code == 204
    async with session_factory() as session:
        assert await session.get(MarketInstallation, endpoint["id"]) is None
    assert (await install_client.get(BASE)).json()["entry"]["installation"] is None


@pytest.mark.parametrize("failure", ["mismatch", "version", "invalid"])
async def test_rejected_install_leaves_no_endpoint_or_record(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession], failure: str
):
    definition = custom_endpoint_definition()
    detail = "调用端点定义未通过校验，请按诊断逐条修正后重试"
    if failure == "mismatch":
        definition["meta"]["version"] = "2.0.0"
        detail = "索引与定义不一致，无法安装"
    elif failure == "version":
        definition["meta"]["min_app_version"] = "99.0.0"
        async with session_factory() as session:
            source = await session.get(MarketSource, 1)
            assert source is not None
            assert source.cached_index is not None
            source.cached_index = {
                **source.cached_index,
                "entries": [{**source.cached_index["entries"][0], "min_app_version": "99.0.0"}],
            }
            await session.commit()
        detail = "此条目需要应用版本 ≥ 99.0.0"
    else:
        del definition["submit"]
    with respx.mock() as remote:
        remote.get(URL).respond(json=definition)
        response = await install_client.post(f"{BASE}/install", json={})
    assert response.status_code == 422
    assert response.json()["detail"] == detail
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CustomEndpoint)) == 0
        assert await session.scalar(select(func.count()).select_from(MarketInstallation)) == 0


async def test_duplicate_install_and_cross_source_overwrite_conflict(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
):
    with respx.mock() as remote:
        remote.get(URL).respond(json=custom_endpoint_definition())
        installed = (await install_client.post(f"{BASE}/install", json={})).json()
        duplicate = await install_client.post(f"{BASE}/install", json={})
        other_source = await install_client.post(
            "/market/sources/2/entries/example/install", json={"overwrite_endpoint_id": installed["endpoint"]["id"]}
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["detail"] == "此条目已安装，请选择已安装的端点进行更新"
        assert other_source.status_code == 409
        assert other_source.json()["detail"] == "该端点已有其他安装记录，无法覆盖"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CustomEndpoint)) == 1
        record = await session.get(MarketInstallation, installed["endpoint"]["id"])
        assert record is not None
        assert record.source_key == "url:source-1"


async def test_overwrite_preserves_key_references_and_update_refreshes_record(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
):
    definition = custom_endpoint_definition()
    local = (await install_client.post("/custom-endpoints", json=definition)).json()
    async with session_factory() as session:
        provider = CustomProvider(
            display_name="Provider", base_url="https://example.com", discovery_format="openai", api_key=""
        )
        session.add(provider)
        await session.flush()
        session.add(
            CustomProviderModel(
                provider_id=provider.id,
                model_id="video",
                display_name="Video",
                endpoint=local["key"],
            )
        )
        await session.commit()
    with respx.mock() as remote:
        remote.get(URL).respond(json=definition)
        response = await install_client.post(f"{BASE}/install", json={"overwrite_endpoint_id": local["id"]})
        assert response.status_code == 200, response.text
        assert response.json()["endpoint"]["key"] == local["key"]
        blocked_delete = await install_client.delete(f"/custom-endpoints/{local['id']}")
        assert blocked_delete.status_code == 409
        assert blocked_delete.json()["diagnostic"]["references"][0]["model_id"] == "video"
        renamed = custom_endpoint_definition(meta={**definition["meta"], "name": "Renamed locally"})
        assert (await install_client.put(f"/custom-endpoints/{local['id']}", json=renamed)).status_code == 200
        definition["meta"]["version"] = "0.2.0"
        async with session_factory() as session:
            source = await session.get(MarketSource, 1)
            assert source is not None
            assert source.cached_index is not None
            source.cached_index = {
                **source.cached_index,
                "entries": [{**source.cached_index["entries"][0], "version": "0.2.0"}],
            }
            await session.commit()
        remote.get(URL).respond(json=definition)
        updated = await install_client.post(f"{BASE}/install", json={"overwrite_endpoint_id": local["id"]})
        assert updated.status_code == 200, updated.text
        assert updated.json()["installation"]["installed_version"] == "0.2.0"
    async with session_factory() as session:
        record = await session.get(MarketInstallation, local["id"])
        assert record is not None
        assert record.installed_digest == definition_digest(definition)
        model = await session.scalar(select(CustomProviderModel))
        assert model is not None
        assert model.endpoint == local["key"]
        source = await session.get(MarketSource, 1)
        assert source is not None
        await session.delete(source)
        await session.commit()
    retained = (await install_client.get(f"/custom-endpoints/{local['id']}")).json()["installation"]
    assert retained["source_key"] == "url:source-1"
    assert retained["source_id"] is None
    assert retained["source_display_name"] is None


async def test_overwrite_rejects_endpoint_without_same_author_and_name(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
):
    definition = custom_endpoint_definition()
    unrelated = (
        await install_client.post(
            "/custom-endpoints", json=custom_endpoint_definition(meta={**definition["meta"], "name": "Other"})
        )
    ).json()
    with respx.mock() as remote:
        remote.get(URL).respond(json=definition)
        response = await install_client.post(f"{BASE}/install", json={"overwrite_endpoint_id": unrelated["id"]})
    assert response.status_code == 409
    assert response.json()["detail"] == "只能覆盖与该条目同作者、同名的端点"
    async with session_factory() as session:
        stored = await session.get(CustomEndpoint, unrelated["id"])
        assert stored is not None
        assert stored.definition["meta"]["name"] == "Other"
        assert await session.scalar(select(func.count()).select_from(MarketInstallation)) == 0
