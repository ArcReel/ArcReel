"""
供应商配置管理 API 测试。

通过 TestClient + dependency_overrides 测试 GET/PATCH/POST /api/v1/providers 端点，
无需实际数据库或应用启动。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.config.service import ConfigService, ProviderStatus
from lib.db import get_async_session
from server.dependencies import get_config_service
from server.routers import providers


# ---------------------------------------------------------------------------
# 测试应用工厂
# ---------------------------------------------------------------------------


def _make_app(mock_svc: ConfigService) -> FastAPI:
    """创建绑定 mock ConfigService 的最小 FastAPI 应用。"""
    app = FastAPI()

    # 覆盖 get_config_service，直接注入 mock 服务
    app.dependency_overrides[get_config_service] = lambda: mock_svc

    app.include_router(providers.router, prefix="/api/v1")
    return app


def _make_client(mock_svc: ConfigService) -> TestClient:
    return TestClient(_make_app(mock_svc))


# ---------------------------------------------------------------------------
# GET /providers — 供应商列表
# ---------------------------------------------------------------------------


class TestListProviders:
    def _mock_svc(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_all_providers_status = AsyncMock(
            return_value=[
                ProviderStatus(
                    name="gemini-aistudio",
                    display_name="AI Studio",
                    description="Google AI Studio",
                    status="ready",
                    media_types=["video", "image"],
                    capabilities=["text_to_video", "image_to_video"],
                    required_keys=["api_key"],
                    configured_keys=["api_key"],
                    missing_keys=[],
                ),
                ProviderStatus(
                    name="ark",
                    display_name="Ark",
                    description="Ark video and image",
                    status="unconfigured",
                    media_types=["video", "image"],
                    capabilities=["text_to_video"],
                    required_keys=["api_key"],
                    configured_keys=[],
                    missing_keys=["api_key"],
                ),
            ]
        )
        return svc

    def test_returns_200(self):
        with _make_client(self._mock_svc()) as client:
            resp = client.get("/api/v1/providers")
        assert resp.status_code == 200

    def test_contains_providers_key(self):
        with _make_client(self._mock_svc()) as client:
            resp = client.get("/api/v1/providers")
        body = resp.json()
        assert "providers" in body

    def test_provider_count(self):
        with _make_client(self._mock_svc()) as client:
            resp = client.get("/api/v1/providers")
        body = resp.json()
        assert len(body["providers"]) == 2

    def test_provider_structure(self):
        with _make_client(self._mock_svc()) as client:
            resp = client.get("/api/v1/providers")
        first = resp.json()["providers"][0]
        assert first["id"] == "gemini-aistudio"
        assert first["display_name"] == "AI Studio"
        assert first["status"] == "ready"
        assert "video" in first["media_types"]
        assert first["missing_keys"] == []

    def test_unconfigured_provider(self):
        with _make_client(self._mock_svc()) as client:
            resp = client.get("/api/v1/providers")
        second = resp.json()["providers"][1]
        assert second["status"] == "unconfigured"
        assert "api_key" in second["missing_keys"]


# ---------------------------------------------------------------------------
# GET /providers/{id}/config — 单个供应商配置
# ---------------------------------------------------------------------------


class TestGetProviderConfig:
    def _mock_svc_ready(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config_masked = AsyncMock(
            return_value={
                "api_key": {"is_set": True, "masked": "AIza…abcd"},
            }
        )
        return svc

    def _mock_svc_empty(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config_masked = AsyncMock(return_value={})
        return svc

    def test_returns_200_for_known_provider(self):
        with _make_client(self._mock_svc_ready()) as client:
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.status_code == 200

    def test_returns_404_for_unknown_provider(self):
        with _make_client(self._mock_svc_empty()) as client:
            resp = client.get("/api/v1/providers/nonexistent/config")
        assert resp.status_code == 404

    def test_response_structure(self):
        with _make_client(self._mock_svc_ready()) as client:
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        body = resp.json()
        assert body["id"] == "gemini-aistudio"
        assert body["display_name"] == "AI Studio"
        assert body["status"] == "ready"
        assert isinstance(body["fields"], list)

    def test_secret_field_masked(self):
        with _make_client(self._mock_svc_ready()) as client:
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        fields = {f["key"]: f for f in resp.json()["fields"]}
        api_key_field = fields["api_key"]
        assert api_key_field["is_set"] is True
        assert api_key_field["type"] == "secret"
        assert api_key_field["value_masked"] == "AIza…abcd"
        assert "value" not in api_key_field or api_key_field.get("value") is None

    def test_required_field_present(self):
        with _make_client(self._mock_svc_ready()) as client:
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        fields = {f["key"]: f for f in resp.json()["fields"]}
        assert fields["api_key"]["required"] is True

    def test_optional_field_present(self):
        with _make_client(self._mock_svc_ready()) as client:
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        fields = {f["key"]: f for f in resp.json()["fields"]}
        assert "base_url" in fields
        assert fields["base_url"]["required"] is False

    def test_unconfigured_status_when_missing_required_keys(self):
        with _make_client(self._mock_svc_empty()) as client:
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.json()["status"] == "unconfigured"

    def test_field_has_placeholder_for_base_url(self):
        with _make_client(self._mock_svc_ready()) as client:
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        fields = {f["key"]: f for f in resp.json()["fields"]}
        assert "placeholder" in fields["base_url"]
        assert fields["base_url"]["placeholder"] == "默认官方地址"


# ---------------------------------------------------------------------------
# PATCH /providers/{id}/config — 更新配置
# ---------------------------------------------------------------------------


def _make_patch_app(mock_svc_instance: ConfigService) -> FastAPI:
    """创建用于 PATCH 端点测试的应用，通过 patch ConfigService 构造函数注入 mock。"""
    app = FastAPI()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    async def _override_session():
        yield mock_session

    app.dependency_overrides[get_async_session] = _override_session

    with patch("server.routers.providers.ConfigService", return_value=mock_svc_instance):
        app.include_router(providers.router, prefix="/api/v1")

    return app


def _make_mock_svc() -> ConfigService:
    svc = MagicMock(spec=ConfigService)
    svc.set_provider_config = AsyncMock()
    svc.delete_provider_config = AsyncMock()
    return svc  # type: ignore[return-value]


class TestPatchProviderConfig:
    def test_returns_204(self):
        mock_svc = _make_mock_svc()
        with patch("server.routers.providers.ConfigService", return_value=mock_svc):
            app = FastAPI()
            mock_session = AsyncMock()
            mock_session.commit = AsyncMock()

            async def _override():
                yield mock_session

            app.dependency_overrides[get_async_session] = _override
            app.include_router(providers.router, prefix="/api/v1")

            with TestClient(app) as client:
                resp = client.patch(
                    "/api/v1/providers/gemini-aistudio/config",
                    json={"api_key": "AIza-new-key"},
                )
        assert resp.status_code == 204

    def test_returns_404_for_unknown_provider(self):
        app = FastAPI()
        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        async def _override():
            yield mock_session

        app.dependency_overrides[get_async_session] = _override
        app.include_router(providers.router, prefix="/api/v1")

        with TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/nonexistent/config",
                json={"api_key": "somekey"},
            )
        assert resp.status_code == 404

    def test_null_value_calls_delete(self):
        mock_svc = _make_mock_svc()
        with patch("server.routers.providers.ConfigService", return_value=mock_svc):
            app = FastAPI()
            mock_session = AsyncMock()
            mock_session.commit = AsyncMock()

            async def _override():
                yield mock_session

            app.dependency_overrides[get_async_session] = _override
            app.include_router(providers.router, prefix="/api/v1")

            with TestClient(app) as client:
                resp = client.patch(
                    "/api/v1/providers/gemini-aistudio/config",
                    json={"base_url": None},
                )

        assert resp.status_code == 204
        mock_svc.delete_provider_config.assert_awaited_once_with(
            "gemini-aistudio", "base_url", flush=False
        )

    def test_non_null_value_calls_set(self):
        mock_svc = _make_mock_svc()
        with patch("server.routers.providers.ConfigService", return_value=mock_svc):
            app = FastAPI()
            mock_session = AsyncMock()
            mock_session.commit = AsyncMock()

            async def _override():
                yield mock_session

            app.dependency_overrides[get_async_session] = _override
            app.include_router(providers.router, prefix="/api/v1")

            with TestClient(app) as client:
                resp = client.patch(
                    "/api/v1/providers/gemini-aistudio/config",
                    json={"api_key": "AIza-test"},
                )

        assert resp.status_code == 204
        mock_svc.set_provider_config.assert_awaited_once_with(
            "gemini-aistudio", "api_key", "AIza-test", flush=False
        )


# ---------------------------------------------------------------------------
# POST /providers/{id}/test — 连接测试
# ---------------------------------------------------------------------------


class TestTestProviderConnection:
    def _mock_svc_configured(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config_masked = AsyncMock(
            return_value={"api_key": {"is_set": True, "masked": "AIza…abcd"}}
        )
        svc.get_provider_config = AsyncMock(
            return_value={"api_key": "AIzaSyFAKE"}
        )
        return svc

    def _mock_svc_unconfigured(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config_masked = AsyncMock(return_value={})
        return svc

    def _fake_test_fn(self, config: dict) -> providers.ConnectionTestResponse:
        return providers.ConnectionTestResponse(
            success=True,
            available_models=["model-a"],
            message="连接成功",
        )

    def test_returns_200(self):
        with patch.dict(providers._TEST_DISPATCH, {"gemini-aistudio": self._fake_test_fn}):
            with _make_client(self._mock_svc_configured()) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test")
        assert resp.status_code == 200

    def test_returns_404_for_unknown_provider(self):
        with _make_client(self._mock_svc_unconfigured()) as client:
            resp = client.post("/api/v1/providers/nonexistent/test")
        assert resp.status_code == 404

    def test_success_true_when_configured(self):
        with patch.dict(providers._TEST_DISPATCH, {"gemini-aistudio": self._fake_test_fn}):
            with _make_client(self._mock_svc_configured()) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert body["success"] is True
        assert body["available_models"] == ["model-a"]
        assert body["message"] == "连接成功"

    def test_success_false_when_missing_required_keys(self):
        with _make_client(self._mock_svc_unconfigured()) as client:
            resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert body["success"] is False
        assert "api_key" in body["message"]

    def test_response_has_required_fields(self):
        with patch.dict(providers._TEST_DISPATCH, {"gemini-aistudio": self._fake_test_fn}):
            with _make_client(self._mock_svc_configured()) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert "success" in body
        assert "available_models" in body
        assert "message" in body

    def test_connection_failure_returns_error(self):
        def _failing_fn(config: dict) -> providers.ConnectionTestResponse:
            raise RuntimeError("API key invalid")

        with patch.dict(providers._TEST_DISPATCH, {"gemini-aistudio": _failing_fn}):
            with _make_client(self._mock_svc_configured()) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert body["success"] is False
        assert "API key invalid" in body["message"]
