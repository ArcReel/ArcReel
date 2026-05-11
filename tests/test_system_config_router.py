"""
Tests for the refactored system_config router.

Uses an in-memory SQLite database and dependency overrides to test
GET/PATCH /api/v1/system/config without real providers.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.config.service import ConfigService, ProviderStatus
from lib.db import get_async_session
from lib.db.base import Base
from server.auth import CurrentUserInfo, get_current_user
from server.dependencies import get_config_service
from server.routers import system_config as system_config_router

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session:
        yield session
    await engine.dispose()


def _make_app_with_mock(mock_svc: ConfigService) -> FastAPI:
    """App with a fully mocked ConfigService + in-memory DB (no real DB)."""
    from contextlib import asynccontextmanager

    mem_engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    mem_factory = async_sessionmaker(mem_engine, expire_on_commit=False)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        async with mem_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        yield

    app = FastAPI(lifespan=_lifespan)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
    app.dependency_overrides[get_config_service] = lambda: mock_svc

    async def _override_session():
        async with mem_factory() as session:
            yield session

    app.dependency_overrides[get_async_session] = _override_session
    app.include_router(system_config_router.router, prefix="/api/v1")
    return app


def _make_mock_svc(
    *,
    settings: dict[str, str] | None = None,
    ready_providers: list[str] | None = None,
) -> ConfigService:
    """Create a mock ConfigService with configurable settings and provider statuses."""
    _settings = dict(settings or {})
    svc = MagicMock(spec=ConfigService)

    async def _get_setting(key: str, default: str = "") -> str:
        return _settings.get(key, default)

    async def _set_setting(key: str, value: str) -> None:
        _settings[key] = value

    async def _get_all_settings() -> dict[str, str]:
        return dict(_settings)

    svc.get_setting = AsyncMock(side_effect=_get_setting)
    svc.get_all_settings = AsyncMock(side_effect=_get_all_settings)
    svc.set_setting = AsyncMock(side_effect=_set_setting)

    ready = set(ready_providers or [])

    async def _get_all_providers_status():
        from lib.config.registry import PROVIDER_REGISTRY

        statuses = []
        for name, meta in PROVIDER_REGISTRY.items():
            status = "ready" if name in ready else "unconfigured"
            statuses.append(
                ProviderStatus(
                    name=name,
                    display_name=meta.display_name,
                    description=meta.description,
                    status=status,
                    media_types=list(meta.media_types),
                    capabilities=list(meta.capabilities),
                    required_keys=list(meta.required_keys),
                    configured_keys=list(meta.required_keys) if name in ready else [],
                    missing_keys=[] if name in ready else list(meta.required_keys),
                )
            )
        return statuses

    svc.get_all_providers_status = AsyncMock(side_effect=_get_all_providers_status)
    return svc


# ---------------------------------------------------------------------------
# GET /system/config
# ---------------------------------------------------------------------------


class TestGetSystemConfig:
    def test_returns_200(self):
        mock_svc = _make_mock_svc()
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        assert res.status_code == 200

    def test_response_has_settings_and_options(self):
        mock_svc = _make_mock_svc()
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        body = res.json()
        assert "settings" in body
        assert "options" in body

    def test_settings_keys(self):
        mock_svc = _make_mock_svc()
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        settings = res.json()["settings"]
        expected_keys = {
            "default_video_backend",
            "default_image_backend",
            "default_image_backend_t2i",
            "default_image_backend_i2i",
            "default_text_backend",
            "video_generate_audio",
            "anthropic_api_key",
            "anthropic_base_url",
            "anthropic_model",
            "anthropic_default_haiku_model",
            "anthropic_default_opus_model",
            "anthropic_default_sonnet_model",
            "claude_code_subagent_model",
            "agent_session_cleanup_delay_seconds",
            "agent_max_concurrent_sessions",
            "text_backend_script",
            "text_backend_overview",
            "text_backend_style",
        }
        assert set(settings.keys()) == expected_keys

    def test_options_contain_backend_lists(self):
        mock_svc = _make_mock_svc(ready_providers=["gemini-aistudio"])
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        options = res.json()["options"]
        assert "video_backends" in options
        assert "image_backends" in options
        assert "gemini-aistudio/veo-3.1-generate-preview" in options["video_backends"]
        assert "gemini-aistudio/gemini-3.1-flash-image-preview" in options["image_backends"]

    def test_options_exclude_unconfigured_providers(self):
        mock_svc = _make_mock_svc(ready_providers=[])
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        options = res.json()["options"]
        assert options["video_backends"] == []
        assert options["image_backends"] == []

    def test_options_include_multiple_ready_providers(self):
        mock_svc = _make_mock_svc(ready_providers=["gemini-aistudio", "ark"])
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        options = res.json()["options"]
        assert "gemini-aistudio/veo-3.1-generate-preview" in options["video_backends"]
        assert "ark/doubao-seedance-1-5-pro-251215" in options["video_backends"]

    def test_anthropic_key_masked(self):
        mock_svc = _make_mock_svc(settings={"anthropic_api_key": "sk-ant-test-secret-123456"})
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        ak = res.json()["settings"]["anthropic_api_key"]
        assert ak["is_set"] is True
        assert ak["masked"] is not None
        assert "sk-a" in ak["masked"]
        assert "test-secret-123456" not in ak["masked"]

    def test_anthropic_key_unset(self):
        mock_svc = _make_mock_svc()
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        ak = res.json()["settings"]["anthropic_api_key"]
        assert ak["is_set"] is False
        assert ak["masked"] is None

    def test_settings_reflect_stored_values(self):
        mock_svc = _make_mock_svc(
            settings={
                "default_video_backend": "gemini-vertex/veo-3.1-fast-generate-001",
                "video_generate_audio": "true",
                "anthropic_base_url": "https://proxy.example.com",
            }
        )
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        settings = res.json()["settings"]
        assert settings["default_video_backend"] == "gemini-vertex/veo-3.1-fast-generate-001"
        assert settings["video_generate_audio"] is True
        assert settings["anthropic_base_url"] == "https://proxy.example.com"

    def test_video_generate_audio_defaults_to_true_on_empty_db(self):
        """新装系统 DB 为空时，GET /system/config 应返回 video_generate_audio=True，
        与 ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO=True 保持一致（PR7 §11）。"""
        mock_svc = _make_mock_svc(settings={})
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        settings = res.json()["settings"]
        assert settings["video_generate_audio"] is True


# ---------------------------------------------------------------------------
# PATCH /system/config
# ---------------------------------------------------------------------------


class TestPatchSystemConfig:
    def _make_patch_app(self, mock_svc: ConfigService) -> FastAPI:
        """App for PATCH tests - needs session override for commit()."""
        app = FastAPI()
        app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="testuser", role="admin")
        app.dependency_overrides[get_config_service] = lambda: mock_svc

        mock_session = AsyncMock()
        mock_session.commit = AsyncMock()

        # sync_anthropic_env は session.execute() を呼ぶ。
        # get_active() → execute → scalar_one_or_none() が None を返すよう設定。
        # get_all() (SystemSettingRepository) → execute → scalars() が空イテラブルを返すよう設定。
        _exec_result = MagicMock()
        _exec_result.scalar_one_or_none.return_value = None
        _exec_result.scalars.return_value = iter([])
        mock_session.execute = AsyncMock(return_value=_exec_result)

        async def _override_session():
            yield mock_session

        app.dependency_overrides[get_async_session] = _override_session
        app.include_router(system_config_router.router, prefix="/api/v1")
        return app

    def test_patch_returns_200(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"video_generate_audio": True},
            )
        assert res.status_code == 200

    def test_patch_sets_backend(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"default_video_backend": "ark/doubao-seedance-1-5-pro-251215"},
            )
        assert res.status_code == 200
        settings = res.json()["settings"]
        assert settings["default_video_backend"] == "ark/doubao-seedance-1-5-pro-251215"

    def test_patch_rejects_invalid_backend_format(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"default_video_backend": "invalid-no-slash"},
            )
        assert res.status_code == 400

    def test_patch_sets_anthropic_key(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"anthropic_api_key": "sk-ant-new-key-12345678"},
            )
        assert res.status_code == 200
        ak = res.json()["settings"]["anthropic_api_key"]
        assert ak["is_set"] is True

    def test_patch_clears_anthropic_key(self):
        mock_svc = _make_mock_svc(settings={"anthropic_api_key": "sk-ant-old"})
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"anthropic_api_key": ""},
            )
        assert res.status_code == 200
        ak = res.json()["settings"]["anthropic_api_key"]
        assert ak["is_set"] is False

    def test_patch_sets_anthropic_base_url(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"anthropic_base_url": "https://proxy.example.com/v1"},
            )
        assert res.status_code == 200
        settings = res.json()["settings"]
        assert settings["anthropic_base_url"] == "https://proxy.example.com/v1"

    def test_patch_sets_audio_toggle(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"video_generate_audio": False},
            )
        assert res.status_code == 200
        assert res.json()["settings"]["video_generate_audio"] is False

    def test_patch_sets_model_fields(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={
                    "anthropic_model": "claude-sonnet-4-20250514",
                    "claude_code_subagent_model": "claude-haiku-4-20250514",
                },
            )
        assert res.status_code == 200
        settings = res.json()["settings"]
        assert settings["anthropic_model"] == "claude-sonnet-4-20250514"
        assert settings["claude_code_subagent_model"] == "claude-haiku-4-20250514"

    def test_patch_returns_full_response(self):
        mock_svc = _make_mock_svc(ready_providers=["gemini-aistudio"])
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"video_generate_audio": True},
            )
        body = res.json()
        assert "settings" in body
        assert "options" in body
