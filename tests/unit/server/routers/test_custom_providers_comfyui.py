"""自定义供应商管理 API 在 ``discovery_format = comfyui`` 下的行为。

与 ``test_custom_providers_api.py`` 同一个被测对象、同一套 app / client 装配，按行为域分文件：
该协议特有的连通性检查、模型发现「不适用」、端点挂接双向校验与能力覆盖关闭集中在这里。
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.httpx_shared import shutdown_http_client, startup_http_client
from tests.factories import comfyui_endpoint_definition, custom_endpoint_definition
from tests.http_capture import capture_http, only_request

_COMFY_URL = "http://comfy.test:8188"
_SYSTEM_STATS = f"{_COMFY_URL}/system_stats"


@pytest.fixture
def comfyui_client(custom_providers_app) -> Generator[TestClient, None, None]:
    """连通性检查走共享 httpx 客户端；它由 server.app 的 lifespan 建立，这里自行起停。"""
    asyncio.run(startup_http_client())
    try:
        with TestClient(custom_providers_app) as c:
            yield c
    finally:
        asyncio.run(shutdown_http_client())


async def _store_endpoint(session_factory, definition: dict[str, Any]) -> str:
    """把一份定义落库并返回它的端点键。镜像列取自定义本身，与保存路由同源。"""
    async with session_factory() as session:
        row = await CustomEndpointRepository(session).create(
            definition=definition,
            kind=str(definition["kind"]),
            schema_version=str(definition["schema_version"]),
            media_type=str(definition.get("media_type", "video")),
            display_name=str(definition["meta"]["name"]),
        )
        await session.commit()
        return f"ce-{row.id}"


def _create_provider(client: TestClient, **overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "display_name": "我的 ComfyUI",
        "discovery_format": "comfyui",
        "base_url": _COMFY_URL,
        "api_key": "",
        "models": [],
    }
    body.update(overrides)
    resp = client.post("/api/v1/custom-providers", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _model(endpoint: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {"model_id": "wan-t2v", "display_name": "Wan T2V", "endpoint": endpoint}
    row.update(overrides)
    return row


# ---------------------------------------------------------------------------
# 供应商创建与端点目录
# ---------------------------------------------------------------------------


class TestComfyuiProviderCreation:
    def test_an_empty_api_key_is_accepted(self, comfyui_client: TestClient):
        """ComfyUI 本体零鉴权，凭据模板在端点定义里：供应商行的 api_key 留空须能保存。"""
        provider = _create_provider(comfyui_client)
        assert provider["discovery_format"] == "comfyui"
        stored = comfyui_client.get(f"/api/v1/custom-providers/{provider['id']}/credentials").json()
        assert stored["api_key"] == ""
        assert stored["base_url"] == _COMFY_URL

    def test_an_unknown_protocol_is_refused_at_the_request_boundary(self, comfyui_client: TestClient):
        """协议名录是封闭的：新增取值只能由 DiscoveryFormatLiteral 放行。"""
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "x",
                "discovery_format": "comfy",
                "base_url": _COMFY_URL,
                "api_key": "",
                "models": [],
            },
        )
        assert resp.status_code == 422

    async def test_a_comfyui_endpoint_reaches_the_endpoint_catalog(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        """ComfyUI 端点在端点目录里：媒体类型读定义，能力位留空，由节点绑定推导补齐。"""
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        catalog = comfyui_client.get("/api/v1/custom-providers/endpoints").json()["endpoints"]
        entry = next(e for e in catalog if e["key"] == key)
        assert entry["kind"] == "comfyui"
        assert entry["media_type"] == "video"
        assert entry["source"] == "custom"
        assert entry["display_name"] == "示例 ComfyUI 端点"
        assert entry["request_method"] == "POST"
        assert entry["request_path_template"] == "/prompt"
        assert entry["end_image_capable"] is False

    async def test_an_image_comfyui_endpoint_carries_its_own_media_type(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        """一份 workflow 产图还是产视频由定义自己说了算，端点键推不出来。"""
        definition = comfyui_endpoint_definition(media_type="image")
        del definition["bindings"]["fps"]
        key = await _store_endpoint(custom_providers_app_session_factory, definition)
        catalog = comfyui_client.get("/api/v1/custom-providers/endpoints").json()["endpoints"]
        entry = next(e for e in catalog if e["key"] == key)
        assert entry["media_type"] == "image"
        assert entry["image_capabilities"] == []


# ---------------------------------------------------------------------------
# 连通性检查
# ---------------------------------------------------------------------------


def _stats_response(version: str | None = "0.3.60") -> httpx.Response:
    system: dict[str, Any] = {"os": "posix", "python_version": "3.12.0"}
    if version is not None:
        system["comfyui_version"] = version
    return httpx.Response(200, json={"system": system, "devices": []})


class TestComfyuiConnectivityCheck:
    def test_a_non_empty_api_key_is_sent_as_a_bare_bearer_token(self, comfyui_client: TestClient):
        with capture_http() as router:
            route = router.get(_SYSTEM_STATS).mock(return_value=_stats_response())
            resp = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": "proxy-token"},
            )
        assert resp.status_code == 200
        assert only_request(route).headers["authorization"] == "Bearer proxy-token"

    def test_an_empty_api_key_sends_no_credentials_at_all(self, comfyui_client: TestClient):
        """留空不该退化成 ``Bearer ``：反向代理会把空凭证当作一次失败的鉴权而非匿名请求。"""
        with capture_http() as router:
            route = router.get(_SYSTEM_STATS).mock(return_value=_stats_response())
            resp = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
            )
        assert resp.status_code == 200
        assert "authorization" not in only_request(route).headers

    def test_the_reported_version_comes_back_in_the_message(self, comfyui_client: TestClient):
        with capture_http() as router:
            router.get(_SYSTEM_STATS).mock(return_value=_stats_response("0.3.60"))
            body = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
            ).json()
        assert body["success"] is True
        assert "0.3.60" in body["message"]

    def test_a_missing_version_still_counts_as_reachable(self, comfyui_client: TestClient):
        """老版本与部分代理不回这一字段；据此判失败会把能用的部署拦在外面。"""
        with capture_http() as router:
            router.get(_SYSTEM_STATS).mock(return_value=_stats_response(version=None))
            body = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
            ).json()
        assert body["success"] is True
        assert body["message"]

    def test_an_unreachable_service_comes_back_as_a_readable_failure(self, comfyui_client: TestClient):
        """软失败回 200 + success=False，与另外两条协议同形，界面直接展示原因。"""
        with capture_http() as router:
            router.get(_SYSTEM_STATS).mock(side_effect=httpx.ConnectError("connection refused"))
            resp = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "connection refused" in body["message"]

    def test_an_error_status_comes_back_as_a_readable_failure(self, comfyui_client: TestClient):
        with capture_http() as router:
            router.get(_SYSTEM_STATS).mock(return_value=httpx.Response(401, json={"error": "unauthorized"}))
            body = comfyui_client.post(
                "/api/v1/custom-providers/test",
                json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": "wrong"},
            ).json()
        assert body["success"] is False
        assert "401" in body["message"]

    def test_the_stored_credential_entry_probes_the_same_way(self, comfyui_client: TestClient):
        """已存储凭证入口与明文入口共用同一条探针，不该各打各的路径。"""
        provider = _create_provider(comfyui_client)
        with capture_http() as router:
            route = router.get(_SYSTEM_STATS).mock(return_value=_stats_response())
            body = comfyui_client.post(f"/api/v1/custom-providers/{provider['id']}/test").json()
        assert body["success"] is True
        assert "authorization" not in only_request(route).headers


# ---------------------------------------------------------------------------
# 模型发现
# ---------------------------------------------------------------------------


class TestComfyuiDiscovery:
    def test_discovery_reports_not_applicable_rather_than_an_empty_list(self, comfyui_client: TestClient):
        """空列表会被读成「一个都没发现」，界面据此提示检查凭证——那是另一回事。"""
        resp = comfyui_client.post(
            "/api/v1/custom-providers/discover",
            json={"discovery_format": "comfyui", "base_url": _COMFY_URL, "api_key": ""},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["not_applicable"] is True
        assert body["models"] == []
        assert body["reason"]

    def test_the_stored_credential_entry_reports_the_same(self, comfyui_client: TestClient):
        provider = _create_provider(comfyui_client)
        body = comfyui_client.post(f"/api/v1/custom-providers/{provider['id']}/discover").json()
        assert body["not_applicable"] is True

    def test_other_protocols_keep_reporting_applicable(self, comfyui_client: TestClient):
        """「不适用」只属于 comfyui；别的协议一次失败的发现仍须是失败，不是不适用。"""
        with capture_http() as router:
            router.get(url__regex=r".*/models.*").mock(return_value=httpx.Response(200, json={"data": []}))
            body = comfyui_client.post(
                "/api/v1/custom-providers/discover",
                json={"discovery_format": "openai", "base_url": "https://api.example.com/v1", "api_key": "sk-x"},
            ).json()
        assert body["not_applicable"] is False


# ---------------------------------------------------------------------------
# 端点挂接双向校验
# ---------------------------------------------------------------------------


class TestComfyuiEndpointAttachment:
    async def test_a_comfyui_endpoint_cannot_hang_on_another_protocol(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OpenAI 中转",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-x",
                "models": [_model(key)],
            },
        )
        assert resp.status_code == 422
        assert key in resp.json()["detail"]

    async def test_a_comfyui_provider_cannot_hang_a_declarative_endpoint(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        key = await _store_endpoint(custom_providers_app_session_factory, custom_endpoint_definition())
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "我的 ComfyUI",
                "discovery_format": "comfyui",
                "base_url": _COMFY_URL,
                "api_key": "",
                "models": [_model(key)],
            },
        )
        assert resp.status_code == 422
        assert key in resp.json()["detail"]

    async def test_a_comfyui_provider_cannot_hang_a_builtin_endpoint(self, comfyui_client: TestClient):
        """内置端点的 kind 是 ``python``，同样不是 ComfyUI 端点。"""
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "我的 ComfyUI",
                "discovery_format": "comfyui",
                "base_url": _COMFY_URL,
                "api_key": "",
                "models": [_model("openai-chat")],
            },
        )
        assert resp.status_code == 422

    async def test_a_matching_pair_is_accepted(self, comfyui_client, custom_providers_app_session_factory):
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        provider = _create_provider(comfyui_client, models=[_model(key)])
        assert provider["models"][0]["endpoint"] == key

    async def test_other_protocols_keep_their_free_attachment(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        """约束只在 comfyui 上生效：声明式端点挂 openai 供应商照旧。"""
        key = await _store_endpoint(custom_providers_app_session_factory, custom_endpoint_definition())
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "OpenAI 中转",
                "discovery_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-x",
                "models": [_model(key)],
            },
        )
        assert resp.status_code == 201

    async def test_the_full_update_path_checks_the_pair_too(self, comfyui_client, custom_providers_app_session_factory):
        """协议读库里那一行而非请求体（PUT 不接受改协议），换端点同样要重判。"""
        comfyui_key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        declarative_key = await _store_endpoint(custom_providers_app_session_factory, custom_endpoint_definition())
        provider = _create_provider(comfyui_client, models=[_model(comfyui_key)])
        resp = comfyui_client.put(
            f"/api/v1/custom-providers/{provider['id']}",
            json={
                "display_name": "我的 ComfyUI",
                "base_url": _COMFY_URL,
                "models": [_model(declarative_key)],
                "image_max_workers": None,
                "video_max_workers": None,
                "audio_max_workers": None,
            },
        )
        assert resp.status_code == 422
        # 整批不入库：原来那一行还在。
        kept = comfyui_client.get(f"/api/v1/custom-providers/{provider['id']}").json()
        assert [m["endpoint"] for m in kept["models"]] == [comfyui_key]

    async def test_the_replace_models_path_checks_the_pair_too(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        comfyui_key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        declarative_key = await _store_endpoint(custom_providers_app_session_factory, custom_endpoint_definition())
        provider = _create_provider(comfyui_client, models=[_model(comfyui_key)])
        resp = comfyui_client.put(
            f"/api/v1/custom-providers/{provider['id']}/models",
            json={"models": [_model(declarative_key)]},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 能力覆盖关闭
# ---------------------------------------------------------------------------


class TestComfyuiCapabilityOverrides:
    @pytest.mark.parametrize("override", [{"last_frame": True}, {"last_frame": False}])
    async def test_writing_an_override_is_refused_for_the_protocol_reason(
        self, comfyui_client, custom_providers_app_session_factory, override: dict[str, Any]
    ):
        """拒因必须是「该协议不支持覆盖」。

        ComfyUI 端点的能力位全空，``last_frame=True`` 同样过不了通用的尾帧校验；协议判定若排在
        后面，用户读到的是「endpoint 不支持尾帧」，会去找一个开得起尾帧的 ComfyUI 端点——而该协
        议根本没有覆盖这回事。两种取值都断言，顺序一旦回退就红。
        """
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        resp = comfyui_client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "我的 ComfyUI",
                "discovery_format": "comfyui",
                "base_url": _COMFY_URL,
                "api_key": "",
                "models": [_model(key, capability_overrides=override)],
            },
        )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert "wan-t2v" in detail
        assert "节点绑定" in detail

    async def test_the_replace_models_path_refuses_it_for_the_same_reason(
        self, comfyui_client, custom_providers_app_session_factory
    ):
        """整表替换与建档走同一条判定顺序，不该只有建档路径给得出专用文案。"""
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        provider = _create_provider(comfyui_client, models=[_model(key)])
        resp = comfyui_client.put(
            f"/api/v1/custom-providers/{provider['id']}/models",
            json={"models": [_model(key, capability_overrides={"last_frame": True})]},
        )
        assert resp.status_code == 422
        assert "节点绑定" in resp.json()["detail"]

    async def test_a_stored_override_is_ignored_on_read(self, comfyui_client, custom_providers_app_session_factory):
        """写入侧拦不住手工改库；回显侧一并忽略，界面才不会显示一条执行层不认的覆盖。"""
        key = await _store_endpoint(custom_providers_app_session_factory, comfyui_endpoint_definition())
        provider = _create_provider(comfyui_client, models=[_model(key)])
        async with custom_providers_app_session_factory() as session:
            from lib.db.repositories.custom_provider_repo import CustomProviderRepository

            rows = await CustomProviderRepository(session).list_models(provider["id"])
            rows[0].capability_overrides = {"last_frame": True}
            await session.commit()
        read_back = comfyui_client.get(f"/api/v1/custom-providers/{provider['id']}").json()
        assert read_back["models"][0]["capability_overrides"] is None
