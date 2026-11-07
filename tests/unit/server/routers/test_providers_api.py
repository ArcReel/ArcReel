"""
Provider-configuration management API tests.

Test GET/PATCH/POST /api/v1/providers through TestClient + dependency_overrides,
without real databases or application startup.
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
from server.dependencies import get_config_service
from server.i18n import get_translator
from server.routers import providers
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.factories import make_translator
from tests.http_capture import capture_http, only_request

# ---------------------------------------------------------------------------
# Test application factory
# ---------------------------------------------------------------------------


def _make_app(mock_svc: ConfigService) -> FastAPI:
    """Create minimal FastAPI apps bound to mock ConfigService."""
    app = FastAPI()

    # Override get_config_service, injecting mock services directly.
    app.dependency_overrides[get_config_service] = lambda: mock_svc

    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app


def _make_client(mock_svc: ConfigService) -> TestClient:
    return TestClient(_make_app(mock_svc))


# ---------------------------------------------------------------------------
# GET /providers: provider lists
# ---------------------------------------------------------------------------


class TestListProviders:
    def _mock_svc(self) -> ConfigService:
        svc = MagicMock(spec=ConfigService)
        svc.get_all_providers_status = AsyncMock(
            return_value=[
                ProviderStatus(
                    name="gemini-aistudio",
                    display_name="AI Studio",
                    status="ready",
                    media_types=["video", "image"],
                    capabilities=["text_to_video", "image_to_video"],
                    required_keys=["api_key"],
                    credential_count=1,
                ),
                ProviderStatus(
                    name="ark",
                    display_name="火山方舟",
                    status="unconfigured",
                    media_types=["video", "image"],
                    capabilities=["text_to_video"],
                    required_keys=["api_key"],
                    credential_count=0,
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

    def test_provider_without_translation_keeps_source_name(self):
        svc = MagicMock(spec=ConfigService)
        svc.get_all_providers_status = AsyncMock(
            return_value=[
                ProviderStatus(
                    name="untranslated-provider",
                    display_name="Original Provider Name",
                    status="ready",
                    media_types=[],
                    capabilities=[],
                    required_keys=[],
                    credential_count=0,
                )
            ]
        )

        with _make_client(svc) as client:
            resp = client.get("/api/v1/providers", headers={"accept-language": "en"})

        assert resp.json()["providers"][0]["display_name"] == "Original Provider Name"

    def test_unconfigured_provider(self):
        with _make_client(self._mock_svc()) as client:
            resp = client.get("/api/v1/providers")
        second = resp.json()["providers"][1]
        assert second["status"] == "unconfigured"

    def _mock_svc_with_models(self) -> ConfigService:
        """Construct ProviderStatus with models to verify ModelInfoResponse forwarding."""
        svc = MagicMock(spec=ConfigService)
        svc.get_all_providers_status = AsyncMock(
            return_value=[
                ProviderStatus(
                    name="gemini-aistudio",
                    display_name="AI Studio",
                    status="ready",
                    media_types=["video", "image"],
                    capabilities=["text_to_video", "image_to_video"],
                    required_keys=["api_key"],
                    credential_count=1,
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
        """ModelInfoResponse must include resolutions, even empty lists."""
        with _make_client(self._mock_svc_with_models()) as client:
            resp = client.get("/api/v1/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["providers"]) == 1
        models = body["providers"][0]["models"]
        assert models, "providers[0].models should not be empty"
        for minfo in models.values():
            assert "resolutions" in minfo
            assert isinstance(minfo["resolutions"], list)

    def test_models_do_not_expose_duration_constraints(self):
        """Catalogs omit duration-linked constraints; only video-capabilities returns narrowed results/causes to frontend."""
        with _make_client(self._mock_svc_with_models()) as client:
            resp = client.get("/api/v1/providers")
        for minfo in resp.json()["providers"][0]["models"].values():
            assert "duration_resolution_constraints" not in minfo
            assert "reference_image_durations" not in minfo

    def test_models_resolutions_values_passthrough(self):
        """Forward exact resolutions values unchanged to responses."""
        with _make_client(self._mock_svc_with_models()) as client:
            resp = client.get("/api/v1/providers")
        models = resp.json()["providers"][0]["models"]
        assert models["veo-3.1-fast-generate-preview"]["resolutions"] == ["720p", "1080p"]
        assert models["imagen-4.0-generate-001"]["resolutions"] == []

    def test_always_audible_model_reports_switch_not_controllable(self):
        """AI Studio Veo always includes audio without request switches; both paths report always_on, disabling settings audio switches."""
        with _make_client(self._mock_svc_with_models()) as client:
            resp = client.get("/api/v1/providers")
        model = resp.json()["providers"][0]["models"]["veo-3.1-fast-generate-preview"]
        assert model["audio_track"] == "always_on"
        assert model["reference_route_audio_track"] == "always_on"

    def test_non_video_model_reports_always_off(self):
        """Image models always report always_off; audio-track decisions are meaningless for nonvideo models."""
        with _make_client(self._mock_svc_with_models()) as client:
            resp = client.get("/api/v1/providers")
        models = resp.json()["providers"][0]["models"]
        assert models["imagen-4.0-generate-001"]["audio_track"] == "always_off"
        assert models["imagen-4.0-generate-001"]["voice_consistency"] == "none"

    def _mock_svc_with_catalog_names(self) -> ConfigService:
        """One Chinese-display-name model (可灵 v3) and one pure-brand-name model (Kling 2.1)."""
        svc = MagicMock(spec=ConfigService)
        svc.get_all_providers_status = AsyncMock(
            return_value=[
                ProviderStatus(
                    name="kling",
                    display_name="可灵 Kling",
                    status="ready",
                    media_types=["video"],
                    capabilities=["text_to_video"],
                    required_keys=["api_key"],
                    credential_count=1,
                    models={
                        "kling-v3": {
                            "display_name": "可灵 v3",
                            "media_type": "video",
                            "capabilities": [],
                            "default": False,
                            "supported_durations": [5, 10],
                            "duration_resolution_constraints": {},
                            "resolutions": [],
                        },
                        "kling-v2-1": {
                            "display_name": "Kling 2.1",
                            "media_type": "video",
                            "capabilities": [],
                            "default": False,
                            "supported_durations": [5, 10],
                            "duration_resolution_constraints": {},
                            "resolutions": [],
                        },
                    },
                ),
            ]
        )
        return svc

    @pytest.mark.parametrize("accept_language", ["zh", "en", "vi"])
    def test_model_display_name_is_the_catalog_name_in_every_locale(self, accept_language: str):
        """Model display names come only from catalog entries, never translated by request language."""
        with _make_client(self._mock_svc_with_catalog_names()) as client:
            resp = client.get("/api/v1/providers", headers={"accept-language": accept_language})
        models = resp.json()["providers"][0]["models"]
        assert (models["kling-v3"]["display_name"], models["kling-v2-1"]["display_name"]) == ("可灵 v3", "Kling 2.1")

    def test_unknown_mocked_model_id_defaults_safely(self):
        """Mock model_id values absent from real PROVIDER_REGISTRY (for example imagen-4.0-generate-001 here)
        must safely default both new fields without raising."""
        with _make_client(self._mock_svc_with_models()) as client:
            resp = client.get("/api/v1/providers")
        assert resp.status_code == 200

    def test_ark_seedance_2_catalog_voice_consistency_degrades_to_soft(self):
        """Catalog endpoints lack project context: even direct reference-audio support can provide only soft with unknown generation_mode.

        native requires both direct reference-audio forwarding and reference-video paths; the latter is a project property. Catalog endpoints
        derive nonreference paths, avoiding global-settings promises of tiers not necessarily available in current projects.
        """
        svc = MagicMock(spec=ConfigService)
        svc.get_all_providers_status = AsyncMock(
            return_value=[
                ProviderStatus(
                    name="ark",
                    display_name="火山方舟",
                    status="ready",
                    media_types=["video"],
                    capabilities=["text_to_video"],
                    required_keys=["api_key"],
                    credential_count=1,
                    models={
                        "doubao-seedance-2-0-260128": {
                            "display_name": "Seedance 2.0",
                            "media_type": "video",
                            "capabilities": ["text_to_video", "generate_audio"],
                            "default": True,
                            "supported_durations": [4, 8],
                            "duration_resolution_constraints": {},
                            "resolutions": ["720p"],
                        },
                    },
                ),
            ]
        )
        with _make_client(svc) as client:
            resp = client.get("/api/v1/providers")
        model = resp.json()["providers"][0]["models"]["doubao-seedance-2-0-260128"]
        assert model["audio_track"] == "controllable"
        assert model["voice_consistency"] == "soft"


# ---------------------------------------------------------------------------
# GET /providers/{id}/config: individual provider configuration
# ---------------------------------------------------------------------------


def _make_session_app() -> tuple[FastAPI, AsyncMock]:
    """Create base apps overriding session dependencies only, for tests needing further patches."""
    app = FastAPI()
    mock_session = AsyncMock()

    async def _override_session():
        yield mock_session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_translator] = lambda: make_translator()
    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
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
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.status_code == 200

    def test_returns_404_for_unknown_provider(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/nonexistent/config")
        assert resp.status_code == 404

    def test_response_structure(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_ready()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_active()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        body = resp.json()
        assert body["id"] == "gemini-aistudio"
        assert body["display_name"] == "AI Studio"
        assert body["status"] == "ready"
        assert isinstance(body["fields"], list)

    def test_credential_fields_not_in_response(self):
        """api_key / base_url / credentials_path must not appear in fields."""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_ready()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_active()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        field_keys = {f["key"] for f in resp.json()["fields"]}
        assert "api_key" not in field_keys
        assert "base_url" not in field_keys
        assert "credentials_path" not in field_keys

    def test_optional_non_credential_field_present(self):
        """Noncredential optional keys, such as image_rpm, must appear in fields."""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_ready()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_active()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        fields = {f["key"]: f for f in resp.json()["fields"]}
        assert "image_rpm" in fields
        assert fields["image_rpm"]["required"] is False

    def test_ready_status_when_active_credential(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_ready()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_active()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.json()["status"] == "ready"

    def test_unconfigured_status_when_no_active_credential(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.json()["status"] == "unconfigured"

    @pytest.mark.parametrize(
        ("provider_id", "expected"),
        [
            ("gemini-aistudio", True),
            ("openai", True),
            ("vidu", True),
            ("dashscope", True),
            ("ark", True),
            ("grok", False),
            ("gemini-vertex", False),
        ],
    )
    def test_supports_base_url_derived_from_optional_keys(self, provider_id: str, expected: bool):
        """supports_base_url reflects base_url membership in registry optional_keys; frontend uses it for credential URL inputs."""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
            TestClient(app) as client,
        ):
            resp = client.get(f"/api/v1/providers/{provider_id}/config")
        assert resp.status_code == 200
        assert resp.json()["supports_base_url"] is expected

    def test_secret_fields_single_secret_provider(self):
        """Single-secret providers, such as gemini-aistudio, yield secret_fields = [api_key]."""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.status_code == 200
        assert [f["key"] for f in resp.json()["secret_fields"]] == ["api_key"]

    def test_secret_fields_kling_three_ordered_secrets(self):
        """Kling yields secret_fields = [api_key, access_key, secret_key], retaining required_keys order."""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/kling/config")
        assert resp.status_code == 200
        secret_fields = resp.json()["secret_fields"]
        assert [f["key"] for f in secret_fields] == ["api_key", "access_key", "secret_key"]
        assert [f["label"] for f in secret_fields] == ["API Key", "Access Key", "Secret Key"]
        # Three secret fields use credential forms, never advanced fields.
        field_keys = {f["key"] for f in resp.json()["fields"]}
        assert "api_key" not in field_keys
        assert "access_key" not in field_keys
        assert "secret_key" not in field_keys

    def test_secret_field_groups_kling_api_key_or_dual_secret(self):
        """Kling secret_field_groups alternatives: [api_key] or [access_key, secret_key]."""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/kling/config")
        assert resp.status_code == 200
        assert resp.json()["secret_field_groups"] == [["api_key"], ["access_key", "secret_key"]]

    def test_secret_field_groups_single_secret_provider_falls_back_to_one_group(self):
        """Providers without credential_groups fall back to one secret_field_groups group containing all secret_fields."""
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/gemini-aistudio/config")
        assert resp.status_code == 200
        assert resp.json()["secret_field_groups"] == [["api_key"]]

    def test_secret_fields_vertex_empty(self):
        """gemini-vertex credentials are file paths, not secrets: secret_fields is empty and frontend uses uploads.

        secret_field_groups fallback must not synthesize [[]], matching registry.py empty-group fail-fast semantics,
        preventing future nonsecret ordinary credential forms from bypassing validation through vacuously true empty groups.
        """
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc_empty()),
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_empty()),
            TestClient(app) as client,
        ):
            resp = client.get("/api/v1/providers/gemini-vertex/config")
        assert resp.status_code == 200
        assert resp.json()["secret_fields"] == []
        assert resp.json()["secret_field_groups"] == []


# ---------------------------------------------------------------------------
# PATCH /providers/{id}/config: configuration updates
# ---------------------------------------------------------------------------


def _make_mock_svc() -> ConfigService:
    svc = MagicMock(spec=ConfigService)
    svc.set_provider_config = AsyncMock()
    svc.delete_provider_config = AsyncMock()
    return svc


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
            override_auth(app)
            app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

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
        override_auth(app)
        app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

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
            override_auth(app)
            app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

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
            override_auth(app)
            app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)

            with TestClient(app) as client:
                resp = client.patch(
                    "/api/v1/providers/gemini-aistudio/config",
                    json={"api_key": "AIza-test"},
                )

        assert resp.status_code == 204
        mock_svc.set_provider_config.assert_awaited_once_with("gemini-aistudio", "api_key", "AIza-test", flush=False)


class TestPatchProviderConfigMaxWorkersValidation:
    """Capacity-key (*_max_workers) validation: invalid values return 422/readable messages; valid values save normally.

    Use real ConfigService + in-memory DBs, covering router → service → repository end to end.
    """

    @staticmethod
    def _make_db_app(locale: str = "zh") -> FastAPI:
        from contextlib import asynccontextmanager

        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from lib.db.base import Base

        # Lifespan owns engines, ensuring disposal on TestClient context exit. Placing disposal after
        # session dependency yields skips it when routes raise HTTPException, leaking engines.
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
        override_auth(app)
        app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
        return app

    @pytest.mark.parametrize("bad_value", ["", "3.7", "abc", "-1", "0"])
    @pytest.mark.parametrize("key", ["image_max_workers", "video_max_workers", "audio_max_workers"])
    def test_invalid_value_returns_422(self, key: str, bad_value: str):
        with TestClient(self._make_db_app()) as client:
            resp = client.patch("/api/v1/providers/dashscope/config", json={key: bad_value})
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        # Translator-rendered messages, not bare key IDs, contain matching UI field Labels for identification.
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
# POST /providers/{id}/test: connection tests
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

    def _fake_test_fn(self, config: dict, _t=None) -> providers.ConnectivityCheckResponse:
        return providers.ConnectivityCheckResponse(
            success=True,
            available_models=["model-a"],
            message="连通正常",
        )

    def test_returns_200(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_configured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._CONNECTIVITY_CHECK_DISPATCH, {"gemini-aistudio": self._fake_test_fn}),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/gemini-aistudio/test")
        assert resp.status_code == 200

    def test_returns_404_for_unknown_provider(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_unconfigured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/nonexistent/test")
        assert resp.status_code == 404

    def test_success_true_when_configured(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_configured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._CONNECTIVITY_CHECK_DISPATCH, {"gemini-aistudio": self._fake_test_fn}),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert body["success"] is True
        assert body["available_models"] == ["model-a"]
        assert body["message"] == "连通正常"

    def test_success_false_when_no_credential(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_unconfigured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert body["success"] is False
        assert "凭证" in body["message"]

    def test_response_has_required_fields(self):
        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_configured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._CONNECTIVITY_CHECK_DISPATCH, {"gemini-aistudio": self._fake_test_fn}),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert "success" in body
        assert "available_models" in body
        assert "message" in body

    def test_connection_failure_returns_error(self):
        def _failing_fn(config: dict, _t=None) -> providers.ConnectivityCheckResponse:
            raise RuntimeError("API key invalid")

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_configured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._CONNECTIVITY_CHECK_DISPATCH, {"gemini-aistudio": _failing_fn}),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/gemini-aistudio/test")
        body = resp.json()
        assert body["success"] is False
        assert "API key invalid" in body["message"]

    def test_unsupported_check_survives_a_failing_display_name_catalog(self):
        """Providers without connectivity checks use connectivity_check_unsupported; catalog-query failures still return truthful results."""
        app, session = _make_session_app()
        session.execute.side_effect = RuntimeError("catalog unavailable")
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo_configured()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._CONNECTIVITY_CHECK_DISPATCH, clear=True),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/gemini-aistudio/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is False
        session.rollback.assert_awaited_once()

    def test_dashscope_registered_in_dispatch(self):
        # Built-in dashscope appears in settings and requires a connection-test dispatcher,
        # or Test connection reaches connectivity_check_unsupported even with valid API Keys.
        assert "dashscope" in providers._CONNECTIVITY_CHECK_DISPATCH

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
            resp = providers._check_dashscope(
                {"api_key": "sk", "base_url": "https://dashscope.aliyuncs.com"}, lambda k, **kw: k
            )
        # host → compatible-mode base (OpenAI protocol), forwarding api_key unchanged
        assert captured["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert captured["api_key"] == "sk"
        # Expose qwen/wan models only, filtering embedding and others.
        assert resp.available_models == ["qwen-plus", "wan2.7-image"]
        assert resp.success is True

    def test_minimax_registered_in_dispatch(self):
        # Built-in minimax appears in settings and requires a connection-test dispatcher,
        # or Test connection reaches connectivity_check_unsupported even with valid API Keys.
        assert "minimax" in providers._CONNECTIVITY_CHECK_DISPATCH

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
            resp = providers._check_minimax({"api_key": "sk", "base_url": "https://api.minimax.io"}, lambda k, **kw: k)
        # host → {host}/v1 (OpenAI protocol), forwarding api_key unchanged
        assert captured["base_url"] == "https://api.minimax.io/v1"
        assert captured["api_key"] == "sk"
        # Expose minimax/abab models only, filtering embedding and others.
        assert resp.available_models == ["MiniMax-M2.7", "abab6.5s-chat"]
        assert resp.success is True

    def test_kling_registered_in_dispatch(self):
        # Built-in kling appears in settings and requires a connection-test dispatcher,
        # or Test connection reaches connectivity_check_unsupported even with valid credentials.
        assert "kling" in providers._CONNECTIVITY_CHECK_DISPATCH

    class _FakeKlingResponse:
        def __init__(self, payload: dict, *, content_type: str = "application/json"):
            self._payload = payload
            self.headers = {"content-type": content_type}

        def raise_for_status(self) -> None:
            pass

        def json(self) -> dict:
            return self._payload

    def test_kling_test_fn_uses_bearer_when_api_key_set(self):
        """Supplied api_key uses static bearer headers, never JWT, matching _build_kling precedence."""
        captured: dict = {}

        def _fake_get(url, params=None, headers=None, timeout=None):
            captured.update(url=url, params=params, headers=headers, timeout=timeout)
            return self._FakeKlingResponse({"code": 0, "message": "", "data": {}})

        with patch("httpx.get", _fake_get):
            resp = providers._check_kling({"api_key": "sk-kling"}, lambda k, **kw: k)
        assert resp.success is True
        assert captured["headers"]["Authorization"] == "Bearer sk-kling"
        # account/costs uses domain-root paths without /v1; default base_url migrated to api-beijing.
        assert captured["url"] == "https://api-beijing.klingai.com/account/costs"
        assert "start_time" in captured["params"]
        assert "end_time" in captured["params"]

    def test_kling_test_fn_uses_jwt_when_only_dual_secret_set(self):
        """Only access_key + secret_key, without api_key, uses three-part JWT Bearer tokens."""
        captured: dict = {}

        def _fake_get(url, params=None, headers=None, timeout=None):
            captured.update(headers=headers)
            return self._FakeKlingResponse({"code": 0, "message": "", "data": {}})

        with patch("httpx.get", _fake_get):
            resp = providers._check_kling({"access_key": "ak-1", "secret_key": "s" * 40}, lambda k, **kw: k)
        assert resp.success is True
        auth = captured["headers"]["Authorization"]
        assert auth.startswith("Bearer ")
        assert auth.removeprefix("Bearer ").count(".") == 2  # JWT: header.payload.signature

    def test_kling_test_fn_api_key_takes_priority_when_both_set(self):
        """Supplying both prioritizes api_key, matching _build_kling dispatch order."""
        captured: dict = {}

        def _fake_get(url, params=None, headers=None, timeout=None):
            captured.update(headers=headers)
            return self._FakeKlingResponse({"code": 0, "message": "", "data": {}})

        with patch("httpx.get", _fake_get):
            providers._check_kling(
                {"api_key": "sk-kling", "access_key": "ak-1", "secret_key": "s" * 40}, lambda k, **kw: k
            )
        assert captured["headers"]["Authorization"] == "Bearer sk-kling"

    def test_kling_test_fn_strips_v1_suffix_for_account_costs_root_path(self):
        """Custom base_url with /v1 suffix strips it for account/costs, requesting domain-root paths."""
        captured: dict = {}

        def _fake_get(url, params=None, headers=None, timeout=None):
            captured.update(url=url)
            return self._FakeKlingResponse({"code": 0, "message": "", "data": {}})

        with patch("httpx.get", _fake_get):
            providers._check_kling(
                {"api_key": "sk-kling", "base_url": "https://relay.example.com/v1"}, lambda k, **kw: k
            )
        assert captured["url"] == "https://relay.example.com/account/costs"

    def test_kling_test_fn_raises_clear_error_when_no_credentials(self):
        """Neither credential form supplied raises explicit errors, never silently sending unauthenticated requests."""
        with pytest.raises(ValueError, match="Access Key"):
            providers._check_kling({}, lambda k, **kw: k)

    def test_kling_test_fn_raises_on_code_error(self):
        """Response code != 0 raises RuntimeError with official error information, converted above into connectivity_check_failed text."""

        def _fake_get(url, params=None, headers=None, timeout=None):
            return self._FakeKlingResponse({"code": 1101, "message": "auth failed", "data": {}})

        with patch("httpx.get", _fake_get), pytest.raises(RuntimeError, match="auth failed"):
            providers._check_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

    def test_kling_test_fn_prefers_json_body_error_over_raise_for_status(self):
        """Non-2xx HTTP with JSON business errors exposes specific causes before generic HTTP-status text."""

        class _FailingResponse(self._FakeKlingResponse):
            def raise_for_status(self) -> None:
                raise httpx.HTTPStatusError("401 Unauthorized", request=MagicMock(), response=MagicMock())

        def _fake_get(url, params=None, headers=None, timeout=None):
            return _FailingResponse({"code": 1101, "message": "access key not found", "data": {}})

        with patch("httpx.get", _fake_get), pytest.raises(RuntimeError, match="access key not found"):
            providers._check_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

    def test_kling_test_fn_raises_http_status_error_when_body_not_json(self):
        """Non-JSON bodies, such as gateway error pages, skip business parsing and expose HTTP status through fallback."""
        with capture_http() as http:
            http.get(host="api-beijing.klingai.com", path="/account/costs").respond(
                status_code=502, text="<html>bad gateway</html>", headers={"content-type": "text/html"}
            )
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                providers._check_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

        assert exc_info.value.response.status_code == 502
        assert "502" in str(exc_info.value)

    def test_kling_test_fn_falls_back_to_raise_for_status_on_malformed_json(self):
        """JSON content-type with invalid/empty bodies, such as gateway truncation, must not crash parsing; use status fallback."""
        with capture_http() as http:
            http.get(host="api-beijing.klingai.com", path="/account/costs").respond(
                status_code=504, text="", headers={"content-type": "application/json"}
            )
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                providers._check_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

        assert exc_info.value.response.status_code == 504
        assert "504" in str(exc_info.value)

    def test_kling_test_fn_keeps_credentials_out_of_status_error_message(self):
        """Status fallback raises redacted HTTPStatusError: passwords in base_url userinfo never enter exception messages.

        Users enter base_url; with https://user:pw@host, httpx retains passwords in request URL userinfo.
        Bare raise_for_status includes whole URLs in messages, returned unchanged to settings by connection-test endpoints.
        """
        with capture_http() as http:
            http.get(host="relay.example.com", path="/account/costs").respond(
                status_code=401, text="unauthorized", headers={"content-type": "text/plain"}
            )
            with pytest.raises(httpx.HTTPStatusError) as exc_info:
                providers._check_kling(
                    {"api_key": "sk-kling", "base_url": "https://kl-user:sk-leaked-kling@relay.example.com/v1"},
                    lambda k, **kw: k,
                )

        # Real leak surface: exception-retained request URLs contain passwords/query strings; messages contain neither.
        assert "sk-leaked-kling" in str(exc_info.value.request.url)
        assert str(exc_info.value) == "401 response for https://relay.example.com/account/costs"
        assert exc_info.value.response.status_code == 401

    def test_kling_connection_test_via_router_keeps_credentials_out_of_message(self):
        """End to end: connection-test failure messages returned to frontend exclude base_url userinfo passwords."""
        cred = ProviderCredential(
            provider="kling",
            name="可灵账号",
            api_key="sk-kling",
            base_url="https://kl-user:sk-leaked-kling@relay.example.com/v1",
            is_active=True,
        )
        repo = MagicMock(spec=CredentialRepository)
        repo.get_by_id = AsyncMock(return_value=cred)
        repo.get_active = AsyncMock(return_value=cred)

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=repo),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            capture_http() as http,
        ):
            route = http.get(host="relay.example.com", path="/account/costs").respond(
                status_code=401, text="unauthorized", headers={"content-type": "text/plain"}
            )
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/kling/test")

        # Requests actually went out; message is produced by the status-code fallback. Passwords in exception messages are checked by
        # test_kling_test_fn_keeps_credentials_out_of_status_error_message on exc.request.url:
        # httpx moves userinfo into Authorization before dispatch, removing it from outgoing URLs.
        only_request(route)
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "sk-leaked-kling" not in body["message"]

    def test_kling_test_fn_raises_on_inner_data_code_error(self):
        """Top-level code=0 with nested data business code != 0, such as resource-package query failure, also raises RuntimeError."""

        def _fake_get(url, params=None, headers=None, timeout=None):
            return self._FakeKlingResponse(
                {"code": 0, "message": "", "data": {"code": 1234, "msg": "resource pack not found"}}
            )

        with patch("httpx.get", _fake_get), pytest.raises(RuntimeError, match="resource pack not found"):
            providers._check_kling({"api_key": "sk-kling"}, lambda k, **kw: k)

    def test_kling_connection_test_via_router_with_api_key_only(self):
        """End to end: Kling credentials with api_key alone pass /test, one acceptance criterion.

        Use real ProviderCredential rather than MagicMock: routes call cred.overlay_config(config), relying on
        real methods to merge api_key into config dicts; MagicMock overlay_config never actually modifies config.
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
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/kling/test")
        assert resp.status_code == 200
        assert resp.json()["success"] is True

    def test_specific_credential_id(self):
        """Test specific credentials using credential_id."""
        repo = MagicMock(spec=CredentialRepository)
        cred = self._fake_cred()
        repo.get_by_id = AsyncMock(return_value=cred)
        repo.get_active = AsyncMock(return_value=None)

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=repo),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._CONNECTIVITY_CHECK_DISPATCH, {"gemini-aistudio": self._fake_test_fn}),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/gemini-aistudio/test?credential_id=1")
        assert resp.status_code == 200
        assert resp.json()["success"] is True


class TestArkAgentPlanConnectionTest:
    """ark-agent-plan must reuse _check_ark and automatically inject default_base_url."""

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
        assert "ark-agent-plan" in providers._CONNECTIVITY_CHECK_DISPATCH
        assert providers._CONNECTIVITY_CHECK_DISPATCH["ark-agent-plan"] is providers._check_ark

    def test_default_base_url_injected_when_user_did_not_set(self):
        captured: dict = {}

        def _capture(config: dict, _t=None) -> providers.ConnectivityCheckResponse:
            captured["base_url"] = config.get("base_url")
            return providers.ConnectivityCheckResponse(success=True, available_models=[], message="ok")

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo()),
            patch("server.routers.providers.ConfigService", return_value=self._mock_svc()),
            patch.dict(providers._CONNECTIVITY_CHECK_DISPATCH, {"ark-agent-plan": _capture}),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/ark-agent-plan/test")
        assert resp.status_code == 200
        assert captured["base_url"] == "https://ark.cn-beijing.volces.com/api/plan/v3"

    def test_user_base_url_overrides_default(self):
        captured: dict = {}

        def _capture(config: dict, _t=None) -> providers.ConnectivityCheckResponse:
            captured["base_url"] = config.get("base_url")
            return providers.ConnectivityCheckResponse(success=True, available_models=[], message="ok")

        svc = MagicMock(spec=ConfigService)
        svc.get_provider_config = AsyncMock(
            return_value={"api_key": "ark-fake", "base_url": "https://custom.example.com/v9"}
        )

        app, _ = _make_session_app()
        with (
            patch("server.routers.providers.CredentialRepository", return_value=self._mock_cred_repo()),
            patch("server.routers.providers.ConfigService", return_value=svc),
            patch.dict(providers._CONNECTIVITY_CHECK_DISPATCH, {"ark-agent-plan": _capture}),
            TestClient(app) as client,
        ):
            resp = client.post("/api/v1/providers/ark-agent-plan/test")
        assert resp.status_code == 200
        assert captured["base_url"] == "https://custom.example.com/v9"
