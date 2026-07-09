"""
供应商配置管理 API 测试。

通过 TestClient + dependency_overrides 测试 GET/PATCH/POST /api/v1/providers 端点，
无需实际数据库或应用启动。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.config.service import ConfigService, ProviderStatus
from lib.db import get_async_session
from lib.db.models.credential import ProviderCredential
from lib.db.repositories.credential_repository import CredentialRepository
from lib.i18n import get_translator
from server.dependencies import get_config_service
from server.routers import providers
from tests.conftest import make_translator

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
                    display_name="火山方舟",
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

    def _mock_svc_with_models(self) -> ConfigService:
        """构造带 models 字段的 ProviderStatus，用于校验 ModelInfoResponse 透传。"""
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
                    models={
                        "veo-3.1-fast-generate-preview": {
                            "display_name": "Veo 3.1 Fast",
                            "media_type": "video",
                            "capabilities": ["text_to_video"],
                            "default": False,
                            "supported_durations": [4, 6, 8],
                            "duration_resolution_constraints": {},
                            "resolutions": ["720p", "1080p"],
                        },
                        "imagen-4.0-generate-001": {
                            "display_name": "Imagen 4",
                            "media_type": "image",
                            "capabilities": ["text_to_image"],
                            "default": True,
                            "supported_durations": [],
                            "duration_resolution_constraints": {},
                            "resolutions": [],
                        },
                    },
                ),
            ]
        )
        return svc

    def test_models_expose_resolutions_field(self):
        """ModelInfoResponse 必须包含 resolutions 字段（即便为空列表）。"""
        with _make_client(self._mock_svc_with_models()) as client:
            resp = client.get("/api/v1/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["providers"]) == 1
        models = body["providers"][0]["models"]
        assert models, "providers[0].models should not be empty"
        for _mid, minfo in models.items():
            assert "resolutions" in minfo
            assert isinstance(minfo["resolutions"], list)

    def test_models_resolutions_values_passthrough(self):
        """resolutions 的具体值应按原样透传到 response。"""
        with _make_client(self._mock_svc_with_models()) as client:
            resp = client.get("/api/v1/providers")
        models = resp.json()["providers"][0]["models"]
        assert models["veo-3.1-fast-generate-preview"]["resolutions"] == ["720p", "1080p"]
        assert models["imagen-4.0-generate-001"]["resolutions"] == []


# ---------------------------------------------------------------------------
# GET /providers/{id}/config — 单个供应商配置
# ---------------------------------------------------------------------------


def _make_session_app() -> tuple[FastAPI, AsyncMock]:
    """创建只覆盖 session 依赖的基础应用，供需要进一步 patch 的测试使用。"""
    app = FastAPI()
    mock_session = AsyncMock()

    async def _override_session():
        yield mock_session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_translator] = lambda: make_translator()
    app.include_router(providers.router, prefix="/api/v1")
    return app, mock_session


class TestGetProviderConfig:
    def _mock_svc_ready(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config_masked = AsyncMock(
            return_value={
                "image_rpm": {"is_set": True, "value": "10"},
            }
        )
        return svc

    def _mock_svc_empty(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config_masked = AsyncMock(return_value={})
        return svc

    def _mock_cred_repo_active(self) -> CredentialRepository:
        repo = MagicMock(spec=CredentialRepository)
        repo.has_active_credential = AsyncMock(return_value=True)
        return repo

    def _mock_cred_repo_empty(self) -> CredentialRepository:
        repo = MagicMock(spec=CredentialRepository)
        repo.has_active_credential = AsyncMock(return_value=False)
        return repo

    def test_returns_200_for_known_provider(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_ready()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_active()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.status_code == 200

    def test_returns_404_for_unknown_provider(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/nonexistent/config")
        assert resp.status_code == 404

    def test_response_structure(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_ready()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_active()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-aistudio/config")
        body = resp.json()
        assert body["id"] == "gemini-aistudio"
        assert body["display_name"] == "AI Studio"
        assert body["status"] == "ready"
        assert isinstance(body["fields"], list)

    def test_credential_fields_not_in_response(self):
        """api_key / base_url / credentials_path 不应出现在 fields 中。"""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_ready()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_active()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-aistudio/config")
        field_keys = {f["key"] for f in resp.json()["fields"]}
        assert "api_key" not in field_keys
        assert "base_url" not in field_keys
        assert "credentials_path" not in field_keys

    def test_optional_non_credential_field_present(self):
        """非凭证 optional key（如 image_rpm）应出现在 fields 中。"""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_ready()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_active()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-aistudio/config")
        fields = {f["key"]: f for f in resp.json()["fields"]}
        assert "image_rpm" in fields
        assert fields["image_rpm"]["required"] is False

    def test_ready_status_when_active_credential(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_ready()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_active()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.json()["status"] == "ready"

    def test_unconfigured_status_when_no_active_credential(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.json()["status"] == "unconfigured"

    @pytest.mark.parametrize(
        ("provider_id", "expected"),
        [
            ("gemini-aistudio", True),
            ("openai", True),
            ("vidu", True),
            ("dashscope", True),
            ("ark", False),
            ("grok", False),
            ("gemini-vertex", False),
        ],
    )
    def test_supports_base_url_derived_from_optional_keys(self, provider_id: str, expected: bool):
        """supports_base_url 取自 registry optional_keys 是否含 base_url，前端据此渲染凭证 URL 输入。"""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
        ):
            with TestClient(app) as client:
                resp = client.get(f"/api/v1/providers/{provider_id}/config")
        assert resp.status_code == 200
        assert resp.json()["supports_base_url"] is expected

    def test_secret_fields_single_secret_provider(self):
        """单 secret provider（如 gemini-aistudio）→ secret_fields = [api_key]。"""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.status_code == 200
        assert [f["key"] for f in resp.json()["secret_fields"]] == ["api_key"]

    def test_secret_fields_kling_three_ordered_secrets(self):
        """可灵 → secret_fields = [api_key, access_key, secret_key]（保留 required_keys 顺序）。"""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/kling/config")
        assert resp.status_code == 200
        secret_fields = resp.json()["secret_fields"]
        assert [f["key"] for f in secret_fields] == ["api_key", "access_key", "secret_key"]
        assert [f["label"] for f in secret_fields] == ["API Key", "Access Key", "Secret Key"]
        # 三 secret 走凭证表单，不进 advanced fields
        field_keys = {f["key"] for f in resp.json()["fields"]}
        assert "api_key" not in field_keys
        assert "access_key" not in field_keys
        assert "secret_key" not in field_keys

    def test_secret_field_groups_kling_api_key_or_dual_secret(self):
        """可灵 secret_field_groups 二选一分组：[api_key] 或 [access_key, secret_key]。"""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/kling/config")
        assert resp.status_code == 200
        assert resp.json()["secret_field_groups"] == [["api_key"], ["access_key", "secret_key"]]

    def test_secret_field_groups_single_secret_provider_falls_back_to_one_group(self):
        """未声明 credential_groups 的 provider → secret_field_groups 回退为 [全部 secret_fields] 单组。"""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.status_code == 200
        assert resp.json()["secret_field_groups"] == [["api_key"]]

    def test_secret_fields_vertex_empty(self):
        """gemini-vertex 凭证是文件路径（非 secret）→ secret_fields 为空，前端走文件上传。

        secret_field_groups 回退不合成 [[]]（与 registry.py 的空分组 fail-fast 语义对称，
        避免未来"无 secret 字段但走普通凭证表单"的 provider 因空分组恒真而绕过校验）。
        """
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
        ):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/gemini-vertex/config")
        assert resp.status_code == 200
        assert resp.json()["secret_fields"] == []
        assert resp.json()["secret_field_groups"] == []


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
        mock_svc.delete_provider_config.assert_awaited_once_with("gemini-aistudio", "base_url", flush=False)

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
        mock_svc.set_provider_config.assert_awaited_once_with("gemini-aistudio", "api_key", "AIza-test", flush=False)


class TestPatchProviderConfigMaxWorkersValidation:
    """容量键（*_max_workers）写入校验：非法值 422 + 可读消息，合法值正常保存。

    走真实 ConfigService + 内存 DB，覆盖 router → service → repository 全链路。
    """

    @staticmethod
    def _make_db_app(locale: str = "zh") -> FastAPI:
        from contextlib import asynccontextmanager

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from lib.db.base import Base

        # engine 由 lifespan 持有：TestClient 上下文退出时必然 dispose——若放在
        # session 依赖的 yield 之后，路由抛 HTTPException 时会被跳过而泄漏 engine
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        sm = async_sessionmaker(engine, expire_on_commit=False)

        @asynccontextmanager
        async def _lifespan(_app: FastAPI):
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            try:
                yield
            finally:
                await engine.dispose()

        app = FastAPI(lifespan=_lifespan)

        async def _override_session():
            async with sm() as s:
                yield s

        app.dependency_overrides[get_async_session] = _override_session
        app.dependency_overrides[get_translator] = lambda: make_translator(locale)
        app.include_router(providers.router, prefix="/api/v1")
        return app

    @pytest.mark.parametrize("bad_value", ["", "3.7", "abc", "-1", "0"])
    @pytest.mark.parametrize("key", ["image_max_workers", "video_max_workers", "audio_max_workers"])
    def test_invalid_value_returns_422(self, key: str, bad_value: str):
        with TestClient(self._make_db_app()) as client:
            resp = client.patch("/api/v1/providers/dashscope/config", json={key: bad_value})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        # 消息已经过 Translator 渲染（非裸 key id），且包含 UI 同款字段 Label 便于定位
        assert detail != "max_workers_must_be_positive_integer"
        assert providers._FIELD_META[key]["label"] in detail

    @pytest.mark.parametrize("locale", ["zh", "en", "vi"])
    def test_error_message_renders_in_all_locales(self, locale: str):
        with TestClient(self._make_db_app(locale)) as client:
            resp = client.patch("/api/v1/providers/dashscope/config", json={"video_max_workers": "abc"})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail != "max_workers_must_be_positive_integer"
        assert providers._FIELD_META["video_max_workers"]["label"] in detail
        assert "abc" in detail

    @pytest.mark.parametrize("good_value", ["1", "5"])
    def test_valid_value_returns_204(self, good_value: str):
        with TestClient(self._make_db_app()) as client:
            resp = client.patch("/api/v1/providers/dashscope/config", json={"audio_max_workers": good_value})
        assert resp.status_code == 204


# ---------------------------------------------------------------------------
# POST /providers/{id}/test — 连接测试
# ---------------------------------------------------------------------------


class TestTestProviderConnection:
    def _fake_cred(self):
        cred = MagicMock()
        cred.provider = "gemini-aistudio"
        cred.api_key = "AIzaSyFAKE"
        cred.credentials_path = None
        cred.base_url = None
        return cred

    def _mock_cred_repo_configured(self):
        repo = MagicMock(spec=CredentialRepository)
        repo.get_by_id = AsyncMock(return_value=self._fake_cred())
        repo.get_active = AsyncMock(return_value=self._fake_cred())
        return repo

    def _mock_cred_repo_unconfigured(self):
        repo = MagicMock(spec=CredentialRepository)
        repo.get_by_id = AsyncMock(return_value=None)
        repo.get_active = AsyncMock(return_value=None)
        return repo

    def _mock_svc(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config = AsyncMock(return_value={})
        return svc

    def _fake_test_fn(self, config: dict, _t=None) -> providers.ConnectionTestResponse:
        return providers.ConnectionTestResponse(
            success=True,
            available_models=["model-a"],
            message="连接成功",
        )

    def test_returns_200(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_configured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._TEST_DISPATCH, {"gemini-aistudio": self._fake_test_fn}),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test")
        assert resp.status_code == 200

    def test_returns_404_for_unknown_provider(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_unconfigured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/nonexistent/test")
        assert resp.status_code == 404

    def test_success_true_when_configured(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_configured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._TEST_DISPATCH, {"gemini-aistudio": self._fake_test_fn}),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert body["success"] is True
        assert body["available_models"] == ["model-a"]
        assert body["message"] == "连接成功"

    def test_success_false_when_no_credential(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_unconfigured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert body["success"] is False
        assert "凭证" in body["message"]

    def test_response_has_required_fields(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_configured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._TEST_DISPATCH, {"gemini-aistudio": self._fake_test_fn}),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert "success" in body
        assert "available_models" in body
        assert "message" in body

    def test_connection_failure_returns_error(self):
        def _failing_fn(config: dict, _t=None) -> providers.ConnectionTestResponse:
            raise RuntimeError("API key invalid")

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_configured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._TEST_DISPATCH, {"gemini-aistudio": _failing_fn}),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert body["success"] is False
        assert "API key invalid" in body["message"]

    def test_dashscope_registered_in_dispatch(self):
        # dashscope 作为内置 provider 暴露在设置页，连接测试必须有 dispatcher，
        # 否则点"测试连接"会落到 unsupported_test 分支（即便 API Key 有效）
        assert "dashscope" in providers._TEST_DISPATCH

    def test_dashscope_test_fn_uses_compatible_mode_and_filters_models(self):
        from types import SimpleNamespace

        captured: dict = {}

        class _FakeModels:
            def list(self):
                return SimpleNamespace(
                    data=[
                        SimpleNamespace(id="qwen-plus"),
                        SimpleNamespace(id="wan2.7-image"),
                        SimpleNamespace(id="text-embedding-v3"),
                    ]
                )

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.models = _FakeModels()

        with patch("openai.OpenAI", _FakeOpenAI):
            resp = providers._test_dashscope(
                {"api_key": "sk", "base_url": "https://dashscope.aliyuncs.com"}, lambda k, **kw: k
            )
        # host → compatible-mode base（OpenAI 协议），api_key 透传
        assert captured["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert captured["api_key"] == "sk"
        # 仅暴露 qwen/wan 模型，过滤掉 embedding 等
        assert resp.available_models == ["qwen-plus", "wan2.7-image"]
        assert resp.success is True

    def test_minimax_registered_in_dispatch(self):
        # minimax 作为内置 provider 暴露在设置页，连接测试必须有 dispatcher，
        # 否则点"测试连接"会落到 unsupported_test 分支（即便 API Key 有效）
        assert "minimax" in providers._TEST_DISPATCH

    def test_minimax_test_fn_uses_v1_base_and_filters_models(self):
        from types import SimpleNamespace

        captured: dict = {}

        class _FakeModels:
            def list(self):
                return SimpleNamespace(
                    data=[
                        SimpleNamespace(id="MiniMax-M2.7"),
                        SimpleNamespace(id="abab6.5s-chat"),
                        SimpleNamespace(id="text-embedding-v1"),
                    ]
                )

        class _FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.models = _FakeModels()

        with patch("openai.OpenAI", _FakeOpenAI):
            resp = providers._test_minimax({"api_key": "sk", "base_url": "https://api.minimax.io"}, lambda k, **kw: k)
        # host → {host}/v1（OpenAI 协议），api_key 透传
        assert captured["base_url"] == "https://api.minimax.io/v1"
        assert captured["api_key"] == "sk"
        # 仅暴露 minimax/abab 模型，过滤掉 embedding 等
        assert resp.available_models == ["MiniMax-M2.7", "abab6.5s-chat"]
        assert resp.success is True

    def test_kling_registered_in_dispatch(self):
        # kling 作为内置 provider 暴露在设置页，连接测试必须有 dispatcher，
        # 否则点"测试连接"会落到 unsupported_test 分支（即便凭证有效）
        assert "kling" in providers._TEST_DISPATCH

    class _FakeKlingResponse:
        def __init__(self, payload: dict, *, content_type: str = "application/json"):
            self._payload = payload
            self.headers = {"content-type": content_type}

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return self._payload

    def test_kling_test_fn_uses_bearer_when_api_key_set(self):
        """填了 api_key → bearer 静态头，不走 JWT（与 _build_kling 的优先级一致）。"""
        captured: dict = {}

        def _fake_get(url, params=None, headers=None, timeout=None):
            captured.update(url=url, params=params, headers=headers, timeout=timeout)
            return self._FakeKlingResponse({"code": 0, "message": "", "data": {}})

        with patch("httpx.get", _fake_get):
            resp = providers._test_kling({"api_key": "sk-kling"}, lambda k, **kw: k)
        assert resp.success is True
        assert captured["headers"]["Authorization"] == "Bearer sk-kling"
        # account/costs 挂域名根路径，不带 /v1；默认 base_url 已迁移至 api-beijing
        assert captured["url"] == "https://api-beijing.klingai.com/account/costs"
        assert "start_time" in captured["params"]
        assert "end_time" in captured["params"]

    def test_kling_test_fn_uses_jwt_when_only_dual_secret_set(self):
        """只填 access_key + secret_key（无 api_key）→ JWT Bearer token（三段式）。"""
        captured: dict = {}

        def _fake_get(url, params=None, headers=None, timeout=None):
            captured.update(headers=headers)
            return self._FakeKlingResponse({"code": 0, "message": "", "data": {}})

        with patch("httpx.get", _fake_get):
            resp = providers._test_kling({"access_key": "ak-1", "secret_key": "s" * 40}, lambda k, **kw: k)
        assert resp.success is True
        auth = captured["headers"]["Authorization"]
        assert auth.startswith("Bearer ")
        assert auth.removeprefix("Bearer ").count(".") == 2  # JWT: header.payload.signature

    def test_kling_test_fn_api_key_takes_priority_when_both_set(self):
        """两者都填时 api_key 优先，与 _build_kling 分派顺序一致。"""
        captured: dict = {}

        def _fake_get(url, params=None, headers=None, timeout=None):
            captured.update(headers=headers)
            return self._FakeKlingResponse({"code": 0, "message": "", "data": {}})

        with patch("httpx.get", _fake_get):
            providers._test_kling(
                {"api_key": "sk-kling", "access_key": "ak-1", "secret_key": "s" * 40}, lambda k, **kw: k
            )
        assert captured["headers"]["Authorization"] == "Bearer sk-kling"

    def test_kling_test_fn_strips_v1_suffix_for_account_costs_root_path(self):
        """自定义 base_url 带 /v1 后缀 → account/costs 请求剥离该后缀，落到域名根路径。"""
        captured: dict = {}

        def _fake_get(url, params=None, headers=None, timeout=None):
            captured.update(url=url)
            return self._FakeKlingResponse({"code": 0, "message": "", "data": {}})

        with patch("httpx.get", _fake_get):
            providers._test_kling(
                {"api_key": "sk-kling", "base_url": "https://relay.example.com/v1"}, lambda k, **kw: k
            )
        assert captured["url"] == "https://relay.example.com/account/costs"

    def test_kling_test_fn_raises_clear_error_when_no_credentials(self):
        """两种凭证形态都未填 → 明确报错（不静默发出无鉴权请求）。"""
        with pytest.raises(ValueError, match="Access Key"):
            providers._test_kling({}, lambda k, **kw: k)

    def test_kling_test_fn_raises_on_code_error(self):
        """响应 code != 0 → RuntimeError 携带官方错误信息，经上层转 connection_failed 文案。"""

        def _fake_get(url, params=None, headers=None, timeout=None):
            return self._FakeKlingResponse({"code": 1101, "message": "auth failed", "data": {}})

        with patch("httpx.get", _fake_get):
            with pytest.raises(RuntimeError, match="auth failed"):
                providers._test_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

    def test_kling_test_fn_prefers_json_body_error_over_raise_for_status(self):
        """HTTP 状态非 2xx 但响应体带 JSON 业务错误 → 优先暴露具体原因，而非泛化的 HTTP 状态文案。"""

        class _FailingResponse(self._FakeKlingResponse):
            def raise_for_status(self) -> None:
                raise httpx.HTTPStatusError("401 Unauthorized", request=MagicMock(), response=MagicMock())

        def _fake_get(url, params=None, headers=None, timeout=None):
            return _FailingResponse({"code": 1101, "message": "access key not found", "data": {}})

        with patch("httpx.get", _fake_get):
            with pytest.raises(RuntimeError, match="access key not found"):
                providers._test_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

    def test_kling_test_fn_raises_http_status_error_when_body_not_json(self):
        """非 JSON 响应体（如网关错误页）跳过业务错误解析，走 raise_for_status 兜底暴露 HTTP 状态。"""

        class _FailingResponse(self._FakeKlingResponse):
            def raise_for_status(self) -> None:
                raise httpx.HTTPStatusError("502 Bad Gateway", request=MagicMock(), response=MagicMock())

        def _fake_get(url, params=None, headers=None, timeout=None):
            return _FailingResponse({}, content_type="text/html")

        with patch("httpx.get", _fake_get):
            with pytest.raises(httpx.HTTPStatusError, match="502 Bad Gateway"):
                providers._test_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

    def test_kling_test_fn_falls_back_to_raise_for_status_on_malformed_json(self):
        """content-type 声称 JSON 但响应体非法/空（如异常网关截断）→ 解析失败不崩溃，走 raise_for_status 兜底。"""

        class _MalformedJsonResponse(self._FakeKlingResponse):
            def json(self) -> dict:
                raise ValueError("Expecting value: line 1 column 1 (char 0)")

            def raise_for_status(self) -> None:
                raise httpx.HTTPStatusError("504 Gateway Timeout", request=MagicMock(), response=MagicMock())

        def _fake_get(url, params=None, headers=None, timeout=None):
            return _MalformedJsonResponse({})

        with patch("httpx.get", _fake_get):
            with pytest.raises(httpx.HTTPStatusError, match="504 Gateway Timeout"):
                providers._test_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

    def test_kling_test_fn_raises_on_inner_data_code_error(self):
        """顶层 code=0 但 data 内嵌业务级 code != 0（如资源包查询失败）→ 同样 RuntimeError。"""

        def _fake_get(url, params=None, headers=None, timeout=None):
            return self._FakeKlingResponse(
                {"code": 0, "message": "", "data": {"code": 1234, "msg": "resource pack not found"}}
            )

        with patch("httpx.get", _fake_get):
            with pytest.raises(RuntimeError, match="resource pack not found"):
                providers._test_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

    def test_kling_connection_test_via_router_with_api_key_only(self):
        """端到端：只填 api_key 的可灵凭证通过 /test 端点即可测通（验收标准之一）。

        用真实 ProviderCredential（而非 MagicMock）：路由调用 cred.overlay_config(config) 靠
        真实方法把 api_key 合入 config dict，MagicMock 的 overlay_config 不会真的修改 config。
        """
        cred = ProviderCredential(provider="kling", name="可灵账号", api_key="sk-kling", is_active=True)
        repo = MagicMock(spec=CredentialRepository)
        repo.get_by_id = AsyncMock(return_value=cred)
        repo.get_active = AsyncMock(return_value=cred)

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=repo),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch("httpx.get", return_value=self._FakeKlingResponse({"code": 0, "message": "", "data": {}})),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/kling/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_specific_credential_id(self):
        """使用 credential_id 参数测试特定凭证。"""
        repo = MagicMock(spec=CredentialRepository)
        cred = self._fake_cred()
        repo.get_by_id = AsyncMock(return_value=cred)
        repo.get_active = AsyncMock(return_value=None)

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=repo),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._TEST_DISPATCH, {"gemini-aistudio": self._fake_test_fn}),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/test?credential_id=1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestArkAgentPlanConnectionTest:
    """ark-agent-plan 必须复用 _test_ark 并自动注入 default_base_url。"""

    def _fake_cred(self):
        cred = MagicMock()
        cred.provider = "ark-agent-plan"
        cred.api_key = "ark-fake"
        cred.credentials_path = None
        cred.base_url = None
        return cred

    def _mock_cred_repo(self):
        repo = MagicMock(spec=CredentialRepository)
        repo.get_active = AsyncMock(return_value=self._fake_cred())
        return repo

    def _mock_svc(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config = AsyncMock(return_value={"api_key": "ark-fake"})
        return svc

    def test_ark_agent_plan_is_dispatched(self):
        assert "ark-agent-plan" in providers._TEST_DISPATCH
        assert providers._TEST_DISPATCH["ark-agent-plan"] is providers._test_ark

    def test_default_base_url_injected_when_user_did_not_set(self):
        captured: dict = {}

        def _capture(config: dict, _t=None) -> providers.ConnectionTestResponse:
            captured["base_url"] = config.get("base_url")
            return providers.ConnectionTestResponse(success=True, available_models=[], message="ok")

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._TEST_DISPATCH, {"ark-agent-plan": _capture}),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/ark-agent-plan/test")
        assert resp.status_code == 200
        assert captured["base_url"] == "https://ark.cn-beijing.volces.com/api/plan/v3"

    def test_user_base_url_overrides_default(self):
        captured: dict = {}

        def _capture(config: dict, _t=None) -> providers.ConnectionTestResponse:
            captured["base_url"] = config.get("base_url")
            return providers.ConnectionTestResponse(success=True, available_models=[], message="ok")

        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config = AsyncMock(
            return_value={"api_key": "ark-fake", "base_url": "https://custom.example.com/v9"}
        )

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo()),
            patch("server.routers.providers.ConfigService", return_value=svc),
            patch.dict(providers._TEST_DISPATCH, {"ark-agent-plan": _capture}),
        ):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/ark-agent-plan/test")
        assert resp.status_code == 200
        assert captured["base_url"] == "https://custom.example.com/v9"
