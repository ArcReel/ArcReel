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
from tests.auth_deps import AUTH_DEPENDENCIES

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
    app.include_router(system_config_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
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
    @pytest.mark.unit
    def test_returns_200(self):
        mock_svc = _make_mock_svc()
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        assert res.status_code == 200

    @pytest.mark.unit
    def test_response_has_settings_and_options(self):
        mock_svc = _make_mock_svc()
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        body = res.json()
        assert "settings" in body
        assert "options" in body

    @pytest.mark.unit
    def test_settings_keys(self):
        mock_svc = _make_mock_svc()
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        settings = res.json()["settings"]
        expected_keys = {
            "default_video_backend",
            "default_video_backend_i2v",
            "default_video_backend_r2v",
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
            "text_backend_simple",
            "text_backend_complex",
            "default_audio_backend",
            "narration_voice",
            "narration_speed",
        }
        assert set(settings.keys()) == expected_keys

    @pytest.mark.unit
    def test_options_contain_backend_lists(self):
        mock_svc = _make_mock_svc(ready_providers=["gemini-aistudio"])
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        options = res.json()["options"]
        assert "video_backends" in options
        assert "image_backends" in options
        assert "gemini-aistudio/veo-3.1-generate-preview" in options["video_backends"]
        assert "gemini-aistudio/gemini-3.1-flash-image-preview" in options["image_backends"]

    @pytest.mark.unit
    def test_options_exclude_unconfigured_providers(self):
        mock_svc = _make_mock_svc(ready_providers=[])
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        options = res.json()["options"]
        assert options["video_backends"] == []
        assert options["image_backends"] == []
        assert options["audio_backends"] == []

    @pytest.mark.unit
    def test_options_exclude_hidden_models(self):
        """registry 的 hidden 语义是「从下拉剔除、条目保留供算价」，options 是那个下拉。"""
        from dataclasses import replace
        from unittest.mock import patch

        from lib.config.registry import PROVIDER_REGISTRY

        meta = PROVIDER_REGISTRY["gemini-aistudio"]
        hidden_id = "veo-3.1-generate-preview"
        patched = {**meta.models, hidden_id: replace(meta.models[hidden_id], hidden=True)}
        mock_svc = _make_mock_svc(ready_providers=["gemini-aistudio"])
        with patch.dict(meta.models, patched, clear=True), TestClient(_make_app_with_mock(mock_svc)) as client:
            options = client.get("/api/v1/system/config").json()["options"]
        assert "gemini-aistudio/veo-3.1-generate-preview" not in options["video_backends"]
        assert "gemini-aistudio/veo-3.1-fast-generate-preview" in options["video_backends"]

    @pytest.mark.unit
    def test_options_include_multiple_ready_providers(self):
        mock_svc = _make_mock_svc(ready_providers=["gemini-aistudio", "ark"])
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        options = res.json()["options"]
        assert "gemini-aistudio/veo-3.1-generate-preview" in options["video_backends"]
        assert "ark/doubao-seedance-1-5-pro-251215" in options["video_backends"]

    @pytest.mark.unit
    def test_anthropic_key_masked(self):
        mock_svc = _make_mock_svc(settings={"anthropic_api_key": "sk-ant-test-secret-123456"})
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        ak = res.json()["settings"]["anthropic_api_key"]
        assert ak["is_set"] is True
        assert ak["masked"] is not None
        assert "sk-a" in ak["masked"]
        assert "test-secret-123456" not in ak["masked"]

    @pytest.mark.unit
    def test_anthropic_key_unset(self):
        mock_svc = _make_mock_svc()
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        ak = res.json()["settings"]["anthropic_api_key"]
        assert ak["is_set"] is False
        assert ak["masked"] is None

    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_options_include_audio_backends(self):
        mock_svc = _make_mock_svc(ready_providers=["dashscope"])
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        options = res.json()["options"]
        assert "dashscope/qwen3-tts-flash" in options["audio_backends"]

    @pytest.mark.unit
    def test_audio_settings_reflect_stored_values(self):
        mock_svc = _make_mock_svc(
            settings={
                "default_audio_backend": "dashscope/qwen3-tts-flash",
                "narration_voice": "Ethan",
                "narration_speed": "1.2",
            }
        )
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        settings = res.json()["settings"]
        assert settings["default_audio_backend"] == "dashscope/qwen3-tts-flash"
        assert settings["narration_voice"] == "Ethan"
        assert settings["narration_speed"] == 1.2

    @pytest.mark.unit
    def test_audio_settings_default_empty(self):
        mock_svc = _make_mock_svc()
        with TestClient(_make_app_with_mock(mock_svc)) as client:
            res = client.get("/api/v1/system/config")
        settings = res.json()["settings"]
        assert settings["default_audio_backend"] == ""
        assert settings["narration_voice"] == ""
        assert settings["narration_speed"] is None

    @pytest.mark.unit
    def test_video_generate_audio_defaults_to_true_on_empty_db(self):
        """新装系统 DB 为空时，GET /system/config 应返回 video_generate_audio=True，
        与 ConfigResolver._DEFAULT_VIDEO_GENERATE_AUDIO=True 保持一致。"""
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

        # PATCH 路由内部可能调用 session.execute()（兼容旧 setting key 写入路径）。
        # 默认 stub：scalar_one_or_none() 返回 None；scalars() 返回空迭代器。
        _exec_result = MagicMock()
        _exec_result.scalar_one_or_none.return_value = None
        _exec_result.scalars.return_value = iter([])
        mock_session.execute = AsyncMock(return_value=_exec_result)

        async def _override_session():
            yield mock_session

        app.dependency_overrides[get_async_session] = _override_session
        app.include_router(system_config_router.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        return app

    @pytest.mark.unit
    def test_patch_returns_200(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"video_generate_audio": True},
            )
        assert res.status_code == 200

    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_patch_sets_video_bucket_backends(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={
                    "default_video_backend_i2v": "minimax/MiniMax-Hailuo-2.3",
                    "default_video_backend_r2v": "minimax/S2V-01",
                },
            )
        assert res.status_code == 200
        settings = res.json()["settings"]
        assert settings["default_video_backend_i2v"] == "minimax/MiniMax-Hailuo-2.3"
        assert settings["default_video_backend_r2v"] == "minimax/S2V-01"

    @pytest.mark.unit
    def test_patch_clears_video_bucket_backend(self):
        """空串 = 清空桶键；解析层按空桶回退默认层处理（docs/adr/0054）。"""
        mock_svc = _make_mock_svc(settings={"default_video_backend_r2v": "minimax/S2V-01"})
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"default_video_backend_r2v": ""},
            )
        assert res.status_code == 200
        assert res.json()["settings"]["default_video_backend_r2v"] == ""

    @pytest.mark.unit
    def test_patch_rejects_non_video_model_in_video_bucket(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"default_video_backend_i2v": "gemini-aistudio/gemini-3.1-flash-image-preview"},
            )
        assert res.status_code == 400

    @pytest.mark.unit
    def test_patch_rejects_invalid_backend_format(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"default_video_backend": "invalid-no-slash"},
            )
        assert res.status_code == 400

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_patch_sets_audio_toggle(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"video_generate_audio": False},
            )
        assert res.status_code == 200
        assert res.json()["settings"]["video_generate_audio"] is False

    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_patch_sets_audio_backend_and_voice(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={
                    "default_audio_backend": "dashscope/qwen3-tts-flash",
                    "narration_voice": "Cherry",
                    "narration_speed": 1.5,
                },
            )
        assert res.status_code == 200
        settings = res.json()["settings"]
        assert settings["default_audio_backend"] == "dashscope/qwen3-tts-flash"
        assert settings["narration_voice"] == "Cherry"
        assert settings["narration_speed"] == 1.5

    @pytest.mark.unit
    def test_patch_rejects_non_positive_narration_speed(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"narration_speed": 0},
            )
        assert res.status_code == 422

    @pytest.mark.unit
    def test_patch_rejects_non_finite_narration_speed(self):
        # Pydantic lax 模式会把 "nan"/"inf" 字符串转成 float，必须在卫生校验层拒绝
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            for raw in ("nan", "inf", "-inf"):
                res = client.patch(
                    "/api/v1/system/config",
                    json={"narration_speed": raw},
                )
                assert res.status_code == 422, raw

    @pytest.mark.unit
    def test_patch_clears_narration_speed_with_null(self):
        mock_svc = _make_mock_svc(settings={"narration_speed": "1.5"})
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"narration_speed": None},
            )
        assert res.status_code == 200
        assert res.json()["settings"]["narration_speed"] is None

    @pytest.mark.unit
    def test_patch_rejects_invalid_audio_backend(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"default_audio_backend": "unknown-provider/some-model"},
            )
        assert res.status_code == 400

    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_patch_sets_text_tier_backends(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={
                    "text_backend_simple": "gemini-aistudio/gemini-3-flash-preview",
                    "text_backend_complex": "gemini-aistudio/gemini-3.1-pro-preview",
                },
            )
        assert res.status_code == 200
        settings = res.json()["settings"]
        assert settings["text_backend_simple"] == "gemini-aistudio/gemini-3-flash-preview"
        assert settings["text_backend_complex"] == "gemini-aistudio/gemini-3.1-pro-preview"

    @pytest.mark.unit
    def test_patch_clears_text_tier_backend_with_empty_string(self):
        mock_svc = _make_mock_svc(settings={"text_backend_simple": "gemini-aistudio/gemini-3-flash-preview"})
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch("/api/v1/system/config", json={"text_backend_simple": ""})
        assert res.status_code == 200
        assert res.json()["settings"]["text_backend_simple"] == ""

    @pytest.mark.unit
    def test_patch_rejects_invalid_text_tier_backend(self):
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"text_backend_simple": "invalid-no-slash"},
            )
        assert res.status_code == 400

    @pytest.mark.unit
    def test_patch_rejects_text_tier_backend_with_video_model(self):
        """text_backend_simple 引用一个真实存在但是 video 类型的模型，应被 media_type 校验拒绝。"""
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"text_backend_simple": "gemini-aistudio/veo-3.1-generate-preview"},
            )
        assert res.status_code == 400

    @pytest.mark.unit
    def test_patch_ignores_legacy_text_task_keys(self):
        """旧任务级键已从请求模型移除，提交后既不落库也不出现在响应里。"""
        mock_svc = _make_mock_svc()
        with TestClient(self._make_patch_app(mock_svc)) as client:
            res = client.patch(
                "/api/v1/system/config",
                json={"text_backend_script": "gemini-aistudio/gemini-3-flash-preview"},
            )
        assert res.status_code == 200
        written_keys = {call.args[0] for call in mock_svc.set_setting.call_args_list}
        assert "text_backend_script" not in written_keys
        settings = res.json()["settings"]
        assert "text_backend_script" not in settings
        assert "text_backend_overview" not in settings
        assert "text_backend_style" not in settings
