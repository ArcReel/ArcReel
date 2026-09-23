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
from server.routers import custom_endpoints, custom_providers, system_config
from tests.auth_deps import AUTH_DEPENDENCIES
from tests.factories import comfyui_api_workflow, comfyui_endpoint_definition, custom_endpoint_definition

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def endpoints_app(db_engine) -> FastAPI:
    """绑定内存数据库的应用，同时挂端点路由与供应商路由（目录用例要读同一个库）。"""
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)
    app = FastAPI()

    async def _override_session():
        async with session_factory() as db_session:
            yield db_session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    app.dependency_overrides[system_config.get_app_version_reader] = lambda: lambda: "0.30.0"
    app.include_router(custom_endpoints.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    app.include_router(custom_providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    return app


@pytest.fixture
def endpoints_client(endpoints_app: FastAPI) -> Generator[TestClient, None, None]:
    with TestClient(endpoints_app) as client:
        yield client


@pytest.fixture
async def attach_model(db_engine):
    """把一个自定义模型行挂到指定 endpoint 键上，制造删除时的引用。

    ``discovery_format`` 决定这条挂接的供应商协议，用于「端点 × 供应商协议」的配对用例。
    """
    session_factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def _attach(endpoint_key: str, discovery_format: str = "openai") -> int:
        async with session_factory() as session:
            repo = CustomProviderRepository(session)
            provider = await repo.create_provider(
                display_name="中转站",
                discovery_format=discovery_format,
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


def _comfyui_image_definition() -> dict:
    """一份产图的 ComfyUI 定义：视频专属的语义键摘干净，否则媒体类型白名单先拦下它。"""
    definition = comfyui_endpoint_definition(media_type="image")
    for key in ("start_image", "end_image", "frames", "fps"):
        definition["bindings"].pop(key, None)
    return definition


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
        assert body["schema_version"] == "1.1.0"
        assert body["media_type"] == "video"

    def test_stores_definition_verbatim(self, endpoints_client: TestClient):
        definition = custom_endpoint_definition()

        body = _create(endpoints_client, definition)

        assert body["definition"] == definition

    def test_custom_endpoint_key_can_be_attached_to_provider_model(self, endpoints_client: TestClient):
        endpoint = _create(endpoints_client, custom_endpoint_definition())

        response = endpoints_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Relay",
                "discovery_format": "openai",
                "base_url": "https://relay.test",
                "api_key": "secret",
                "models": [
                    {
                        "model_id": "video-x",
                        "display_name": "Video X",
                        "endpoint": endpoint["key"],
                        "is_default": True,
                    }
                ],
            },
        )

        assert response.status_code == 201, response.text
        model = response.json()["models"][0]
        assert model["endpoint"] == endpoint["key"]
        assert model["system_capabilities"]["first_frame"] is True
        assert model["system_capabilities"]["text_to_video"] is True

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
        resp = endpoints_client.get("/api/v1/custom-endpoints/404")

        assert resp.status_code == 404


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

        deleted = endpoints_client.delete(f"/api/v1/custom-endpoints/{created['id']}")
        fetched = endpoints_client.get(f"/api/v1/custom-endpoints/{created['id']}")

        assert deleted.status_code == 204
        assert fetched.status_code == 404

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

        resp = endpoints_client.delete(f"/api/v1/custom-endpoints/{created['id']}")

        assert resp.status_code == 204

    def test_missing_endpoint_returns_404(self, endpoints_client: TestClient):
        resp = endpoints_client.delete("/api/v1/custom-endpoints/404")

        assert resp.status_code == 404


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


class TestValidateMinAppVersion:
    def test_absent_requirement_reports_nothing(self, endpoints_client: TestClient):
        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=custom_endpoint_definition()).json()

        assert body["min_app_version"] is None

    @pytest.mark.parametrize(("required", "satisfied"), [("0.30.0", True), ("0.31.0", False)])
    def test_compares_requirement_with_the_app_version(
        self, endpoints_client: TestClient, required: str, satisfied: bool
    ):
        definition = custom_endpoint_definition()
        definition["meta"]["min_app_version"] = required

        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=definition).json()

        assert body["min_app_version"] == {"required": required, "current": "0.30.0", "satisfied": satisfied}
        assert body["errors"] == [], "版本门槛只是提示，不拦导入"

    def test_unreadable_app_version_skips_the_comparison(self, endpoints_app: FastAPI):
        def _broken() -> str:
            raise OSError("pyproject missing")

        endpoints_app.dependency_overrides[system_config.get_app_version_reader] = lambda: _broken
        definition = custom_endpoint_definition()
        definition["meta"]["min_app_version"] = "0.31.0"

        with TestClient(endpoints_app) as client:
            resp = client.post("/api/v1/custom-endpoints/validate", json=definition)

        assert resp.status_code == 200
        assert resp.json()["min_app_version"] is None


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


class TestComfyuiEndpoint:
    """ComfyUI 定义与声明式定义同走一条 CRUD：镜像列改由定义自己声明媒体类型。"""

    def test_a_bound_definition_is_stored_and_mirrors_its_own_media_type(self, endpoints_client: TestClient):
        definition = comfyui_endpoint_definition(media_type="image")

        body = _create(endpoints_client, definition)

        assert body["kind"] == "comfyui"
        assert body["schema_version"] == "1.0.0"
        assert body["media_type"] == "image"
        assert body["display_name"] == "示例 ComfyUI 端点"

    def test_export_round_trips_the_definition(self, endpoints_client: TestClient):
        definition = comfyui_endpoint_definition()

        created = _create(endpoints_client, definition)
        exported = endpoints_client.get(f"/api/v1/custom-endpoints/{created['id']}").json()

        assert exported["definition"] == definition

    def test_an_unbound_definition_is_refused(self, endpoints_client: TestClient):
        definition = comfyui_endpoint_definition()
        definition["bindings"]["output"] = []

        resp = endpoints_client.post("/api/v1/custom-endpoints", json=definition)

        assert resp.status_code == 422
        assert [e["code"] for e in resp.json()["diagnostic"]["errors"]] == ["comfyui_binding_required"]

    @pytest.mark.parametrize(
        ("mutate", "code"),
        [
            (lambda d: d["bindings"]["prompt"][0].__setitem__("node", "404"), "comfyui_node_not_found"),
            (lambda d: d["bindings"]["prompt"][0].__setitem__("input", "clip"), "comfyui_input_is_link"),
            (lambda d: d.__setitem__("media_type", "image"), "comfyui_binding_key_not_allowed"),
        ],
    )
    def test_a_broken_binding_is_refused_with_its_own_code(self, endpoints_client: TestClient, mutate: Any, code: str):
        definition = comfyui_endpoint_definition()
        mutate(definition)

        resp = endpoints_client.post("/api/v1/custom-endpoints", json=definition)

        assert resp.status_code == 422
        assert code in [e["code"] for e in resp.json()["diagnostic"]["errors"]]


class TestImportRouting:
    """三种载荷形状各有一种反应：定义照旧、API workflow 自动包装、UI 格式结构化拒绝。"""

    def test_a_definition_is_taken_as_a_definition(self, endpoints_client: TestClient):
        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=comfyui_endpoint_definition()).json()

        assert body["import_shape"] == "endpoint_definition"
        assert body["wrapped_definition"] is None
        assert body["errors"] == []

    def test_a_comfyui_definition_is_measured_against_its_own_version_line(self, endpoints_client: TestClient):
        """两种 kind 各有一条版本线：拿声明式那条去比，一份齐整的 ComfyUI 定义会被报成版本落后。"""
        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=comfyui_endpoint_definition()).json()

        assert body["schema_version"] == {"file": "1.0.0", "current": "1.0.0", "level": "direct"}

    @pytest.mark.parametrize("media_type", ["image", "video"])
    def test_a_raw_api_workflow_comes_back_wrapped(self, endpoints_client: TestClient, media_type: str):
        workflow = comfyui_api_workflow()

        body = endpoints_client.post(f"/api/v1/custom-endpoints/validate?media_type={media_type}", json=workflow).json()

        assert body["import_shape"] == "comfyui_api_workflow"
        assert body["wrapped_definition"]["workflow"] == workflow
        assert body["wrapped_definition"]["media_type"] == media_type
        assert body["wrapped_definition"]["bindings"] == {}

    def test_the_wrapped_workflow_reports_only_the_missing_bindings(self, endpoints_client: TestClient):
        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=comfyui_api_workflow()).json()

        assert {e["code"] for e in body["errors"]} == {"comfyui_binding_required"}
        assert body["schema_version"]["level"] == "direct"

    def test_a_ui_format_workflow_is_refused_with_a_structured_reason(self, endpoints_client: TestClient):
        ui_workflow = {"last_node_id": 9, "nodes": [{"id": 6, "type": "CLIPTextEncode"}], "links": []}

        body = endpoints_client.post("/api/v1/custom-endpoints/validate", json=ui_workflow).json()

        assert body["import_shape"] == "comfyui_ui_workflow"
        assert body["wrapped_definition"] is None
        assert [(e["path"], e["code"]) for e in body["errors"]] == [("$", "comfyui_ui_format_workflow")]

    def test_saving_a_raw_api_workflow_is_wrapped_and_then_refused_for_its_bindings(self, endpoints_client: TestClient):
        """创建入口与 validate 同走一条分流：包装结果的节点绑定为空，保存到此为止。"""
        resp = endpoints_client.post("/api/v1/custom-endpoints", json=comfyui_api_workflow())

        assert resp.status_code == 422
        assert [(e["path"], e["code"]) for e in resp.json()["diagnostic"]["errors"]] == [
            ("bindings.prompt", "comfyui_binding_required"),
            ("bindings.output", "comfyui_binding_required"),
        ]

    def test_saving_a_ui_format_workflow_fails_with_the_same_reason(self, endpoints_client: TestClient):
        ui_workflow = {"last_node_id": 9, "nodes": [{"id": 6, "type": "CLIPTextEncode"}], "links": []}

        resp = endpoints_client.post("/api/v1/custom-endpoints", json=ui_workflow)

        assert resp.status_code == 422
        assert [e["code"] for e in resp.json()["diagnostic"]["errors"]] == ["comfyui_ui_format_workflow"]

    def test_replacing_a_definition_never_takes_a_raw_workflow(self, endpoints_client: TestClient):
        """整份替换只认端点定义：用一份原始 workflow 覆盖会把已确认的节点绑定一并抹掉。"""
        created = _create(endpoints_client, comfyui_endpoint_definition())

        resp = endpoints_client.put(f"/api/v1/custom-endpoints/{created['id']}", json=comfyui_api_workflow())

        assert resp.status_code == 422
        assert [e["code"] for e in resp.json()["diagnostic"]["errors"]] == ["missing_field"]

    async def test_replacing_a_declarative_definition_with_comfyui_is_refused_while_attached(
        self, endpoints_client: TestClient, attach_model
    ):
        """整份替换原地改掉 kind、键与引用不变，于是能把挂接弄坏——这条路上同样要拒。"""
        created = _create(endpoints_client, custom_endpoint_definition())
        await attach_model(created["key"])

        resp = endpoints_client.put(f"/api/v1/custom-endpoints/{created['id']}", json=comfyui_endpoint_definition())

        assert resp.status_code == 422
        assert "demo-video" in resp.json()["detail"]
        assert endpoints_client.get(f"/api/v1/custom-endpoints/{created['id']}").json()["kind"] == "declarative"

    async def test_replacing_a_comfyui_definition_with_declarative_is_refused_while_attached(
        self, endpoints_client: TestClient, attach_model
    ):
        created = _create(endpoints_client, comfyui_endpoint_definition())
        await attach_model(created["key"], discovery_format="comfyui")

        resp = endpoints_client.put(f"/api/v1/custom-endpoints/{created['id']}", json=custom_endpoint_definition())

        assert resp.status_code == 422
        assert endpoints_client.get(f"/api/v1/custom-endpoints/{created['id']}").json()["kind"] == "comfyui"

    async def test_replacing_a_definition_with_the_same_kind_stays_allowed_while_attached(
        self, endpoints_client: TestClient, attach_model
    ):
        """配对没被改动就不该多拦一道：改名、改模板都走这条路。"""
        created = _create(endpoints_client, custom_endpoint_definition())
        await attach_model(created["key"])
        renamed = custom_endpoint_definition(meta={"name": "改名后", "author": "ArcReel", "version": "0.2.0"})

        resp = endpoints_client.put(f"/api/v1/custom-endpoints/{created['id']}", json=renamed)

        assert resp.status_code == 200

    async def test_replacing_an_image_definition_with_a_video_one_is_refused_while_attached(
        self, endpoints_client: TestClient, attach_model
    ):
        """模型行不自带媒体类型，归哪一路由端点决定：原地改判会把它们整批挪到另一路去。"""
        created = _create(endpoints_client, _comfyui_image_definition())
        await attach_model(created["key"], discovery_format="comfyui")

        resp = endpoints_client.put(f"/api/v1/custom-endpoints/{created['id']}", json=comfyui_endpoint_definition())

        assert resp.status_code == 422
        assert "demo-video" in resp.json()["detail"]
        assert endpoints_client.get(f"/api/v1/custom-endpoints/{created['id']}").json()["media_type"] == "image"

    def test_changing_the_media_type_is_allowed_while_nothing_references_it(self, endpoints_client: TestClient):
        created = _create(endpoints_client, _comfyui_image_definition())

        resp = endpoints_client.put(f"/api/v1/custom-endpoints/{created['id']}", json=comfyui_endpoint_definition())

        assert resp.status_code == 200
        assert resp.json()["media_type"] == "video"

    def test_changing_the_kind_is_allowed_while_nothing_references_it(self, endpoints_client: TestClient):
        created = _create(endpoints_client, custom_endpoint_definition())

        resp = endpoints_client.put(f"/api/v1/custom-endpoints/{created['id']}", json=comfyui_endpoint_definition())

        assert resp.status_code == 200
        assert resp.json()["kind"] == "comfyui"


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
