"""真实安装事务、来源绑定、原地覆盖与卸载的 HTTP 契约。"""

from collections.abc import AsyncIterator
from typing import Any

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


def _install_body(definition: dict[str, Any], overwrite_endpoint_id: int | None = None) -> dict[str, Any]:
    """确认页核对过的就是 ``definition``。"""
    body: dict[str, Any] = {"definition_digest": definition_digest(definition)}
    if overwrite_endpoint_id is not None:
        body["overwrite_endpoint_id"] = overwrite_endpoint_id
    return body


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
        response = await install_client.post(f"{BASE}/install", json=_install_body(definition))
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
        response = await install_client.post(f"{BASE}/install", json=_install_body(definition))
    assert response.status_code == 422
    assert response.json()["detail"] == detail
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CustomEndpoint)) == 0
        assert await session.scalar(select(func.count()).select_from(MarketInstallation)) == 0


async def test_install_rejects_definition_changed_after_review(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
):
    reviewed = custom_endpoint_definition()
    swapped = custom_endpoint_definition()
    swapped["submit"]["url"] = "https://attacker.example.com/submit"
    with respx.mock() as remote:
        remote.get(URL).mock(side_effect=[httpx.Response(200, json=reviewed), httpx.Response(200, json=swapped)])
        preview = (await install_client.get(f"{BASE}/definition")).json()
        response = await install_client.post(
            f"{BASE}/install", json={"definition_digest": preview["definition_digest"]}
        )
    assert preview["definition_digest"] == definition_digest(reviewed)
    assert response.status_code == 409
    assert response.json()["detail"] == "市场条目的定义在你核对后已变化，请重新打开确认页核对后再安装"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CustomEndpoint)) == 0
        assert await session.scalar(select(func.count()).select_from(MarketInstallation)) == 0


async def test_duplicate_install_and_cross_source_overwrite_conflict(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
):
    definition = custom_endpoint_definition()
    with respx.mock() as remote:
        remote.get(URL).respond(json=definition)
        installed = (await install_client.post(f"{BASE}/install", json=_install_body(definition))).json()
        duplicate = await install_client.post(f"{BASE}/install", json=_install_body(definition))
        other_source = await install_client.post(
            "/market/sources/2/entries/example/install",
            json=_install_body(definition, installed["endpoint"]["id"]),
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
        response = await install_client.post(f"{BASE}/install", json=_install_body(definition, local["id"]))
        assert response.status_code == 200, response.text
        assert response.json()["endpoint"]["key"] == local["key"]
        blocked_delete = await install_client.delete(f"/custom-endpoints/{local['id']}")
        assert blocked_delete.status_code == 409
        assert blocked_delete.json()["diagnostic"]["references"][0]["model_id"] == "video"
        renamed = custom_endpoint_definition(meta={**definition["meta"], "name": "Renamed locally"})
        assert (await install_client.put(f"/custom-endpoints/{local['id']}", json=renamed)).status_code == 200
        modified = (await install_client.get(f"/custom-endpoints/{local['id']}")).json()["installation"]
        assert (modified["state"], modified["modified"]) == ("current", True)
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
        updated = await install_client.post(f"{BASE}/install", json=_install_body(definition, local["id"]))
        assert updated.status_code == 200, updated.text
        assert updated.json()["installation"] == {
            **updated.json()["installation"],
            "installed_version": "0.2.0",
            "state": "current",
            "modified": False,
        }
        assert updated.json()["endpoint"]["definition"]["meta"]["name"] == definition["meta"]["name"]
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
    assert retained["source_enabled"] is None


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
        response = await install_client.post(f"{BASE}/install", json=_install_body(definition, unrelated["id"]))
    assert response.status_code == 409
    assert response.json()["detail"] == "只能覆盖与该条目同作者、同名的端点"
    async with session_factory() as session:
        stored = await session.get(CustomEndpoint, unrelated["id"])
        assert stored is not None
        assert stored.definition["meta"]["name"] == "Other"
        assert await session.scalar(select(func.count()).select_from(MarketInstallation)) == 0


async def test_installation_reconnects_to_github_source_re_added_with_other_casing(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
):
    definition = custom_endpoint_definition()
    async with session_factory() as session:
        source = await session.get(MarketSource, 1)
        assert source is not None
        source.canonical_key = "github:Team/Market@HEAD"
        await session.commit()
    with respx.mock() as remote:
        remote.get(URL).respond(json=definition)
        installed = (await install_client.post(f"{BASE}/install", json=_install_body(definition))).json()
    endpoint_id = installed["endpoint"]["id"]
    async with session_factory() as session:
        source = await session.get(MarketSource, 1)
        assert source is not None
        cached_index = source.cached_index
        await session.delete(source)
        await session.commit()
        session.add(
            MarketSource(
                id=3,
                kind="custom",
                display_name="Re-added",
                address="team/market",
                index_url="https://example.com/arcreel-market.json",
                canonical_key="github:team/market@HEAD",
                is_enabled=True,
                position=3,
                status="ok",
                cached_index=cached_index,
            )
        )
        await session.commit()

    endpoint_side = (await install_client.get(f"/custom-endpoints/{endpoint_id}")).json()["installation"]
    assert (endpoint_side["source_id"], endpoint_side["state"]) == (3, "current")
    entry_side = (await install_client.get("/market/sources/3/entries/example")).json()["entry"]["installation"]
    assert entry_side["endpoint_id"] == endpoint_id
    with respx.mock() as remote:
        remote.get(URL).respond(json=definition)
        duplicate = await install_client.post(
            "/market/sources/3/entries/example/install", json=_install_body(definition)
        )
        updated = await install_client.post(
            "/market/sources/3/entries/example/install", json=_install_body(definition, endpoint_id)
        )
    assert duplicate.status_code == 409
    assert updated.status_code == 200, updated.text
    async with session_factory() as session:
        records = (await session.scalars(select(MarketInstallation))).all()
        assert [(record.custom_endpoint_id, record.source_key) for record in records] == [
            (endpoint_id, "github:team/market@HEAD")
        ]


async def _set_index_entry(session_factory: async_sessionmaker[AsyncSession], source_id: int, **fields: object) -> None:
    async with session_factory() as session:
        source = await session.get(MarketSource, source_id)
        assert source is not None
        assert source.cached_index is not None
        entries = [{**source.cached_index["entries"][0], **fields}] if fields else []
        source.cached_index = {**source.cached_index, "entries": entries}
        await session.commit()


async def test_install_rejects_entry_moved_by_refresh_during_definition_fetch(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
):
    definition = custom_endpoint_definition()

    async def refreshed_while_fetching(_request: httpx.Request) -> httpx.Response:
        await _set_index_entry(session_factory, 1, path="endpoints/moved/definition.json")
        return httpx.Response(200, json=definition)

    with respx.mock() as remote:
        remote.get(URL).mock(side_effect=refreshed_while_fetching)
        response = await install_client.post(f"{BASE}/install", json=_install_body(definition))
    assert response.status_code == 409
    assert response.json()["detail"] == "市场源在读取定义期间已刷新，请重试"
    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(CustomEndpoint)) == 0
        assert await session.scalar(select(func.count()).select_from(MarketInstallation)) == 0


async def test_installation_states_follow_index_version_source_availability_and_local_edits(
    install_client: httpx.AsyncClient, session_factory: async_sessionmaker[AsyncSession]
):
    definition = custom_endpoint_definition()
    with respx.mock() as remote:
        remote.get(URL).respond(json=definition)
        installed = (await install_client.post(f"{BASE}/install", json=_install_body(definition))).json()
    endpoint_url = f"/custom-endpoints/{installed['endpoint']['id']}"

    async def states() -> tuple[tuple[str, bool], tuple[str, bool] | None]:
        endpoint_side = (await install_client.get(endpoint_url)).json()["installation"]
        listed = (await install_client.get("/custom-endpoints")).json()["endpoints"][0]["installation"]
        assert listed == endpoint_side
        entries = (await install_client.get("/market/entries")).json()["entries"]
        entry_side = next(
            (e["installation"] for e in entries if e["source_id"] == 1 and e["installation"] is not None), None
        )
        return (
            (endpoint_side["state"], endpoint_side["modified"]),
            None if entry_side is None else (entry_side["state"], entry_side["modified"]),
        )

    edited = custom_endpoint_definition()
    edited["meta"]["version"] = "9.9.9"
    assert (await install_client.put(endpoint_url, json=edited)).status_code == 200
    assert await states() == (("current", True), ("current", True))

    await _set_index_entry(session_factory, 1, version="0.0.1")
    assert await states() == (("update_available", True), ("update_available", True))
    detail = (await install_client.get(BASE)).json()["entry"]["installation"]
    assert (detail["state"], detail["modified"]) == ("update_available", True)

    assert (await install_client.get(endpoint_url)).json()["installation"]["source_enabled"] is True
    assert (await install_client.patch("/market/sources/1", json={"is_enabled": False})).status_code == 200
    assert await states() == (("unavailable", True), None)
    assert (await install_client.get(endpoint_url)).json()["installation"]["source_enabled"] is False
    disabled_detail = (await install_client.get(BASE)).json()["entry"]["installation"]
    assert disabled_detail["state"] == "update_available"
    assert (await install_client.patch("/market/sources/1", json={"is_enabled": True})).status_code == 200

    await _set_index_entry(session_factory, 1)
    assert await states() == (("unavailable", True), None)

    assert (await install_client.delete("/market/sources/1")).status_code == 204
    assert (await install_client.put(endpoint_url, json=definition)).status_code == 200
    endpoint_side = (await install_client.get(endpoint_url)).json()["installation"]
    assert (endpoint_side["state"], endpoint_side["modified"]) == ("unavailable", False)
    assert endpoint_side["installed_version"] == definition["meta"]["version"]
