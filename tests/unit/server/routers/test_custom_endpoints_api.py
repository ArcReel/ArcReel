"""自定义调用端点 API：定义 CRUD、被引用拒删、保存前确认。

零封套贯穿全部用例——请求体与 ``definition`` 响应字段都是定义 JSON 原样。
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.custom_provider.endpoint_definition import CURRENT_SCHEMA_VERSION
from lib.db import get_async_session
from lib.db.repositories.custom_provider_repo import CustomProviderRepository
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import custom_endpoints, custom_providers
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import custom_endpoint_definition

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def endpoints_app(db_engine) -> FastAPI:
    """绑定内存数据库的应用，同时挂端点路由与供应商路由（目录用例要读同一个库）。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    app = FastAPI()

    async def _override_session():
        async with session_factory() as db_session:
            yield db_session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    app.include_router(custom_endpoints.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(custom_providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    return app


@pytest.fixture()
def endpoints_client(endpoints_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(endpoints_app) as client:
        yield client


@pytest.fixture()
async def attach_model(db_engine):
    """把一个自定义模型行挂到指定 endpoint 键上，制造删除时的引用。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _attach(endpoint_key: str) -> int:
        async with session_factory() as session:
            repo = CustomProviderRepository(session)
            provider = await repo.create_provider(
                display_name="中转站",
                discovery_format="openai",
                base_url="https://api.example.com",
                api_key="sk-test",
                models=[
                    {
                        "model_id": "demo-video",
                        "display_name": "演示视频模型",
                        "endpoint": endpoint_key,
                    }
                ],
            )
            await session.commit()
            models = await repo.list_models(provider.id)
            return models[0].id

    return _attach


def _create(client: TestClient, definition: dict[str, Any]) -> dict[str, Any]:
    resp = client.post("/api/v1/custom-endpoints", json=definition)
    assert resp.status_code == 201, resp.text
    return resp.json()


# ---------------------------------------------------------------------------
# 生命周期
# ---------------------------------------------------------------------------


class TestCreate:
    def test_assigns_key_and_mirrors_columns(self, endpoints_client: TestClient):
        body = _create(endpoints_client, custom_endpoint_definition())

        assert body["key"] == f"ce-{body['id']}"
        assert body["display_name"] == "示例端点"
        assert body["kind"] == "declarative"
        assert body["schema_version"] == "1.0.0"
        assert body["media_type"] == "video"

    def test_stores_definition_verbatim(self, endpoints_client: TestClient):
        definition = custom_endpoint_definition()

        body = _create(endpoints_client, definition)

        assert body["definition"] == definition

    def test_invalid_definition_returns_diagnostics(self, endpoints_client: TestClient):
        definition = custom_endpoint_definition()
        definition["auth"] = {"headers": {"Authorization": "Bearer sk-live-1234"}}

        resp = endpoints_client.post("/api/v1/custom-endpoints", json=definition)

        assert resp.status_code == 422
        codes = [error["code"] for error in resp.json()["diagnostic"]["errors"]]
        assert "auth_without_api_key" in codes

    def test_non_object_body_goes_through_the_shared_validator(self, endpoints_client: TestClient):
        resp = endpoints_client.post("/api/v1/custom-endpoints", json=["not", "a", "definition"])

        assert resp.status_code == 422
        assert resp.json()["diagnostic"]["errors"], "非对象输入也应拿到定位到字段的诊断"

    def test_warning_does_not_block_saving(self, endpoints_client: TestClient):
        """轮询没引用 task_id 只是警告：拦下它等于把「有意为之」的定义也堵死。"""
        definition = custom_endpoint_definition()
        definition["poll"]["url"] = "{{ base_url }}/v1/video/latest"

        resp = endpoints_client.post("/api/v1/custom-endpoints", json=definition)

        assert resp.status_code == 201


class TestReadAndList:
    def test_get_returns_definition_for_export(self, endpoints_client: TestClient):
        created = _create(endpoints_client, custom_endpoint_definition())

        resp = endpoints_client.get(f"/api/v1/custom-endpoints/{created['id']}")

        assert resp.status_code == 200
        assert resp.json()["definition"] == created["definition"]

    def test_list_returns_all(self, endpoints_client: TestClient):
        _create(endpoints_client, custom_endpoint_definition())
        _create(
            endpoints_client, custom_endpoint_definition(meta={"name": "另一个", "author": "别人", "version": "1.0.0"})
        )

        resp = endpoints_client.get("/api/v1/custom-endpoints")

        assert [item["display_name"] for item in resp.json()["endpoints"]] == ["示例端点", "另一个"]

    def test_missing_endpoint_returns_404(self, endpoints_client: TestClient):
        assert endpoints_client.get("/api/v1/custom-endpoints/404").status_code == 404


class TestUpdate:
    def test_replaces_definition_in_place(self, endpoints_client: TestClient):
        created = _create(endpoints_client, custom_endpoint_definition())
        renamed = custom_endpoint_definition(meta={"name": "改名后", "author": "ArcReel", "version": "0.2.0"})

        resp = endpoints_client.put(f"/api/v1/custom-endpoints/{created['id']}", json=renamed)

        assert resp.status_code == 200
        assert resp.json()["key"] == created["key"], "键与模型行挂接必须原地保留"
        assert resp.json()["display_name"] == "改名后"

    def test_rejects_invalid_definition(self, endpoints_client: TestClient):
        created = _create(endpoints_client, custom_endpoint_definition())
        broken = custom_endpoint_definition()
        del broken["poll"]

        resp = endpoints_client.put(f"/api/v1/custom-endpoints/{created['id']}", json=broken)

        assert resp.status_code == 422
        assert endpoints_client.get(f"/api/v1/custom-endpoints/{created['id']}").json()["definition"]["poll"]

    def test_missing_endpoint_returns_404(self, endpoints_client: TestClient):
        resp = endpoints_client.put("/api/v1/custom-endpoints/404", json=custom_endpoint_definition())

        assert resp.status_code == 404


class TestDelete:
    def test_deletes_unreferenced_endpoint(self, endpoints_client: TestClient):
        created = _create(endpoints_client, custom_endpoint_definition())

        assert endpoints_client.delete(f"/api/v1/custom-endpoints/{created['id']}").status_code == 204
        assert endpoints_client.get(f"/api/v1/custom-endpoints/{created['id']}").status_code == 404

    async def test_referenced_endpoint_returns_409_with_reference_list(
        self, endpoints_client: TestClient, attach_model
    ):
        created = _create(endpoints_client, custom_endpoint_definition())
        await attach_model(created["key"])

        resp = endpoints_client.delete(f"/api/v1/custom-endpoints/{created['id']}")

        assert resp.status_code == 409
        assert resp.json()["diagnostic"]["references"] == [
            {
                "provider_id": 1,
                "provider_display_name": "中转站",
                "model_id": "demo-video",
                "model_display_name": "演示视频模型",
            }
        ]

    async def test_deletes_after_reference_is_removed(self, endpoints_client: TestClient, attach_model, db_engine):
        created = _create(endpoints_client, custom_endpoint_definition())
        model_id = await attach_model(created["key"])
        session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
        async with session_factory() as session:
            await CustomProviderRepository(session).delete_model(model_id)
            await session.commit()

        assert endpoints_client.delete(f"/api/v1/custom-endpoints/{created['id']}").status_code == 204

    def test_missing_endpoint_returns_404(self, endpoints_client: TestClient):
        assert endpoints_client.delete("/api/v1/custom-endpoints/404").status_code == 404


# ---------------------------------------------------------------------------
# 保存前确认
# ---------------------------------------------------------------------------


class TestValidate:
    def test_valid_definition_reports_no_errors(self, endpoints_client: TestClient):
        resp = endpoints_client.post("/api/v1/custom-endpoints/validate", json=custom_endpoint_definition())

        body = resp.json()
        assert resp.status_code == 200
        assert body["errors"] == []
        assert body["warnings"] == []
        assert body["duplicates"] == []

    def test_reports_warnings_without_errors(self, endpoints_client: TestClient):
        definition = custom_endpoint_definition()
        definition["poll"]["url"] = "{{ base_url }}/v1/video/latest"

        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=definition).json()

        assert body["errors"] == []
        assert [warning["code"] for warning in body["warnings"]] == ["poll_without_task_id"]

    def test_shares_the_error_codes_with_saving(self, endpoints_client: TestClient):
        definition = custom_endpoint_definition()
        definition["status_map"]["pending"] = "expired"

        validated = endpoints_client.post("/api/v1/custom-endpoints/validate", json=definition).json()
        saved = endpoints_client.post("/api/v1/custom-endpoints", json=definition)

        assert saved.status_code == 422
        assert [error["code"] for error in validated["errors"]] == [
            error["code"] for error in saved.json()["diagnostic"]["errors"]
        ]

    def test_echoes_hints(self, endpoints_client: TestClient):
        definition = custom_endpoint_definition()
        definition["meta"]["hints"] = {
            "base_url": "https://api.example.com",
            "suggested_models": [{"id": "demo-video", "label": "演示"}],
        }

        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=definition).json()

        assert body["hints"] == definition["meta"]["hints"]

    def test_reports_schema_version_level(self, endpoints_client: TestClient):
        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=custom_endpoint_definition()).json()

        assert body["schema_version"] == {
            "file": CURRENT_SCHEMA_VERSION,
            "current": CURRENT_SCHEMA_VERSION,
            "level": "direct",
        }

    def test_newer_schema_version_needs_confirmation(self, endpoints_client: TestClient):
        definition = custom_endpoint_definition(schema_version="9.0.0")

        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=definition).json()

        assert body["schema_version"]["level"] == "confirm"
        assert body["errors"] == [], "版本档位只是提示，闸门始终是 schema 校验器"


class TestValidateDuplicates:
    def test_same_author_and_name_is_a_duplicate(self, endpoints_client: TestClient):
        created = _create(endpoints_client, custom_endpoint_definition())
        incoming = custom_endpoint_definition(meta={"name": "示例端点", "author": "ArcReel", "version": "0.3.0"})

        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=incoming).json()

        assert body["duplicates"] == [
            {
                "id": created["id"],
                "key": created["key"],
                "display_name": "示例端点",
                "version": "0.1.0",
                "relation": "older",
            }
        ]

    def test_same_name_from_another_author_is_not_a_duplicate(self, endpoints_client: TestClient):
        _create(endpoints_client, custom_endpoint_definition())
        incoming = custom_endpoint_definition(meta={"name": "示例端点", "author": "别人", "version": "0.1.0"})

        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=incoming).json()

        assert body["duplicates"] == []

    def test_exclude_id_drops_the_endpoint_being_edited(self, endpoints_client: TestClient):
        created = _create(endpoints_client, custom_endpoint_definition())

        body = endpoints_client.post(
            "/api/v1/custom-endpoints/validate",
            params={"exclude_id": created["id"]},
            json=custom_endpoint_definition(),
        ).json()

        assert body["duplicates"] == []


# ---------------------------------------------------------------------------
# 目录
# ---------------------------------------------------------------------------


class TestEndpointCatalog:
    def test_lists_custom_endpoints_alongside_builtins(self, endpoints_client: TestClient):
        created = _create(endpoints_client, custom_endpoint_definition())

        catalog = endpoints_client.get("/api/v1/custom-providers/endpoints").json()["endpoints"]

        descriptor = next(item for item in catalog if item["key"] == created["key"])
        assert descriptor["source"] == "custom"
        assert descriptor["kind"] == "declarative"
        assert descriptor["display_name"] == "示例端点"
        # 声明式端点（随版与用户自定义同此约定）的显示名写在定义里，没有可翻译的 key
        assert descriptor["display_name_key"] == ""

    def test_builtin_descriptors_declare_their_source(self, endpoints_client: TestClient):
        catalog = endpoints_client.get("/api/v1/custom-providers/endpoints").json()["endpoints"]

        descriptor = next(item for item in catalog if item["key"] == "openai-video")
        assert descriptor["source"] == "builtin"
        assert descriptor["kind"] == "python"
        assert descriptor["display_name"] is None
