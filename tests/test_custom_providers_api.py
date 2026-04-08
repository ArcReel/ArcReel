"""Custom provider management API tests.

Tests for CRUD, model management, model discovery, and connection test endpoints
via TestClient + dependency_overrides. Uses in-memory SQLite database.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.config.service import ConfigService
from lib.db import get_async_session
from lib.db.base import Base
from server.auth import CurrentUserInfo, get_current_user
from server.routers import custom_providers

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db_engine():
    """In-memory SQLite engine."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture()
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture()
def app(session_factory) -> FastAPI:
    """Create a FastAPI application bound to an in-memory database."""
    _app = FastAPI()

    async def _override_session():
        async with session_factory() as session:
            yield session

    _app.dependency_overrides[get_async_session] = _override_session
    _app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    _app.include_router(custom_providers.router, prefix="/api/v1")
    return _app


@pytest.fixture()
async def session(session_factory) -> AsyncSession:
    async with session_factory() as s:
        yield s


@pytest.fixture()
def client(app) -> TestClient:
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Provider CRUD
# ---------------------------------------------------------------------------


class TestCreateProvider:
    def test_returns_201(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Test Provider",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-12345678",
                "models": [
                    {
                        "model_id": "gpt-4",
                        "display_name": "GPT-4",
                        "media_type": "text",
                    }
                ],
            },
        )
        assert resp.status_code == 201

    def test_response_structure(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Test Provider",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-test-key-12345678",
                "models": [
                    {
                        "model_id": "gpt-4",
                        "display_name": "GPT-4",
                        "media_type": "text",
                    }
                ],
            },
        )
        body = resp.json()
        assert body["display_name"] == "Test Provider"
        assert body["api_format"] == "openai"
        assert body["base_url"] == "https://api.example.com/v1"
        # api_key must be masked
        assert "sk-test-key-12345678" not in body["api_key_masked"]
        assert body["api_key_masked"].startswith("sk-t")
        assert len(body["models"]) == 1
        assert body["models"][0]["model_id"] == "gpt-4"
        assert "created_at" in body

    def test_create_without_models(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Empty Provider",
                "api_format": "google",
                "base_url": "https://api.example.com",
                "api_key": "AIza-test-12345678",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["models"] == []


class TestListProviders:
    def test_empty_list(self, client: TestClient):
        resp = client.get("/api/v1/custom-providers")
        assert resp.status_code == 200
        assert resp.json() == {"providers": []}

    def test_lists_created_providers(self, client: TestClient):
        # Create two providers
        client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Provider A",
                "api_format": "openai",
                "base_url": "https://a.example.com/v1",
                "api_key": "sk-aaaa-key-12345678",
            },
        )
        client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Provider B",
                "api_format": "google",
                "base_url": "https://b.example.com",
                "api_key": "AIza-bbbb-12345678",
            },
        )
        resp = client.get("/api/v1/custom-providers")
        assert resp.status_code == 200
        body = resp.json()["providers"]
        assert len(body) == 2
        assert body[0]["display_name"] == "Provider A"
        assert body[1]["display_name"] == "Provider B"


class TestGetProvider:
    def test_returns_provider(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "My Provider",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-get-test-12345678",
                "models": [
                    {
                        "model_id": "gpt-4o",
                        "display_name": "GPT-4o",
                        "media_type": "text",
                    }
                ],
            },
        )
        pid = create_resp.json()["id"]
        resp = client.get(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "My Provider"
        assert len(body["models"]) == 1

    def test_returns_404_for_nonexistent(self, client: TestClient):
        resp = client.get("/api/v1/custom-providers/9999")
        assert resp.status_code == 404


class TestUpdateProvider:
    def test_update_display_name(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Old Name",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-update-test-1234",
            },
        )
        pid = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/custom-providers/{pid}",
            json={"display_name": "New Name"},
        )
        assert resp.status_code == 200
        assert resp.json()["display_name"] == "New Name"

    def test_update_api_key_is_masked(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Key Test",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-old-key-12345678",
            },
        )
        pid = create_resp.json()["id"]
        resp = client.patch(
            f"/api/v1/custom-providers/{pid}",
            json={"api_key": "sk-new-key-87654321"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "sk-new-key-87654321" not in body["api_key_masked"]
        assert body["api_key_masked"].startswith("sk-n")

    def test_returns_404_for_nonexistent(self, client: TestClient):
        resp = client.patch(
            "/api/v1/custom-providers/9999",
            json={"display_name": "Nope"},
        )
        assert resp.status_code == 404

    def test_returns_400_for_empty_body(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Empty Update",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-empty-test-1234",
            },
        )
        pid = create_resp.json()["id"]
        resp = client.patch(f"/api/v1/custom-providers/{pid}", json={})
        assert resp.status_code == 400


class TestDeleteProvider:
    def test_delete_existing(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "To Delete",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-delete-key-1234",
            },
        )
        pid = create_resp.json()["id"]
        resp = client.delete(f"/api/v1/custom-providers/{pid}")
        assert resp.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/api/v1/custom-providers/{pid}")
        assert get_resp.status_code == 404

    def test_returns_404_for_nonexistent(self, client: TestClient):
        resp = client.delete("/api/v1/custom-providers/9999")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Model management
# ---------------------------------------------------------------------------


class TestReplaceModels:
    def test_replace_entire_model_list(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Model Test",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-model-test-1234",
                "models": [
                    {
                        "model_id": "old-model",
                        "display_name": "Old Model",
                        "media_type": "text",
                    }
                ],
            },
        )
        pid = create_resp.json()["id"]

        new_models = [
            {
                "model_id": "new-text",
                "display_name": "New Text Model",
                "media_type": "text",
                "is_default": True,
            },
            {
                "model_id": "new-image",
                "display_name": "New Image Model",
                "media_type": "image",
                "is_default": True,
            },
        ]
        resp = client.put(f"/api/v1/custom-providers/{pid}/models", json={"models": new_models})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        assert {m["model_id"] for m in body} == {"new-text", "new-image"}

    def test_returns_404_for_nonexistent_provider(self, client: TestClient):
        resp = client.put("/api/v1/custom-providers/9999/models", json={"models": []})
        assert resp.status_code == 404

    def test_verify_old_models_removed(self, client: TestClient):
        create_resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Replace Verify",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-replace-test-12",
                "models": [
                    {
                        "model_id": "original",
                        "display_name": "Original",
                        "media_type": "text",
                    }
                ],
            },
        )
        pid = create_resp.json()["id"]

        client.put(
            f"/api/v1/custom-providers/{pid}/models",
            json={
                "models": [
                    {
                        "model_id": "replacement",
                        "display_name": "Replacement",
                        "media_type": "video",
                    }
                ]
            },
        )

        # Verify via get provider
        get_resp = client.get(f"/api/v1/custom-providers/{pid}")
        models = get_resp.json()["models"]
        assert len(models) == 1
        assert models[0]["model_id"] == "replacement"


# ---------------------------------------------------------------------------
# Discover models (mock)
# ---------------------------------------------------------------------------


class TestDiscoverModels:
    def test_discover_openai(self, client: TestClient):
        fake_models = [
            {
                "model_id": "gpt-4",
                "display_name": "gpt-4",
                "media_type": "text",
                "is_default": True,
                "is_enabled": True,
            },
        ]
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            return_value=fake_models,
        ):
            resp = client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "api_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-discover-test",
                },
            )
        assert resp.status_code == 200
        assert len(resp.json()["models"]) == 1
        assert resp.json()["models"][0]["model_id"] == "gpt-4"

    def test_discover_invalid_format(self, client: TestClient):
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            side_effect=ValueError("Unsupported api_format: 'invalid'"),
        ):
            resp = client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "api_format": "invalid",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-test",
                },
            )
        assert resp.status_code == 400

    def test_discover_api_failure(self, client: TestClient):
        with patch(
            "lib.custom_provider.discovery.discover_models",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Connection refused"),
        ):
            resp = client.post(
                "/api/v1/custom-providers/discover",
                json={
                    "api_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-test",
                },
            )
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Connection test (mock)
# ---------------------------------------------------------------------------


class TestConnectionTest:
    def test_openai_success(self, client: TestClient):
        with patch(
            "server.routers.custom_providers._test_openai",
            return_value=custom_providers.ConnectionTestResponse(success=True, message="Connection successful", model_count=5),
        ):
            resp = client.post(
                "/api/v1/custom-providers/test",
                json={
                    "api_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-conn-test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["model_count"] == 5

    def test_google_success(self, client: TestClient):
        with patch(
            "server.routers.custom_providers._test_google",
            return_value=custom_providers.ConnectionTestResponse(success=True, message="Connection successful", model_count=10),
        ):
            resp = client.post(
                "/api/v1/custom-providers/test",
                json={
                    "api_format": "google",
                    "base_url": "https://api.example.com",
                    "api_key": "AIza-test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["model_count"] == 10

    def test_unsupported_format(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers/test",
            json={
                "api_format": "unsupported",
                "base_url": "https://api.example.com",
                "api_key": "test",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert body["success"] is False

    def test_connection_failure(self, client: TestClient):
        with patch(
            "server.routers.custom_providers._test_openai",
            side_effect=RuntimeError("Connection refused"),
        ):
            resp = client.post(
                "/api/v1/custom-providers/test",
                json={
                    "api_format": "openai",
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-fail-test",
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "Connection refused" in body["message"]


# ---------------------------------------------------------------------------
# Regression tests: previously fixed critical bugs
# ---------------------------------------------------------------------------

_PROVIDER_PAYLOAD = {
    "display_name": "Regression Test",
    "api_format": "openai",
    "base_url": "https://api.example.com/v1",
    "api_key": "sk-regression-1234",
    "models": [
        {"model_id": "gpt-4o", "display_name": "GPT-4o", "media_type": "text", "is_default": True, "is_enabled": True},
        {
            "model_id": "dall-e-3",
            "display_name": "DALL-E 3",
            "media_type": "image",
            "is_default": True,
            "is_enabled": True,
        },
    ],
}


class TestDeleteProviderCleansGlobalSettings:
    """Regression: deleting a provider should clean up default_*_backend settings in global DB that reference it."""

    async def test_global_settings_cleaned_on_delete(self, client: TestClient, session: AsyncSession):
        # Create the provider
        resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = resp.json()["id"]

        # Simulate global config referencing this provider
        svc = ConfigService(session)
        await svc.set_setting("default_text_backend", f"custom-{pid}/gpt-4o")
        await svc.set_setting("default_image_backend", f"custom-{pid}/dall-e-3")
        await svc.set_setting("default_video_backend", "gemini-aistudio/veo-3")  # Should not be cleaned
        await session.commit()

        # Delete the provider (mock out project cleanup and cache invalidation)
        with (
            patch("server.routers.custom_providers._cleanup_project_refs"),
            patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock),
        ):
            del_resp = client.delete(f"/api/v1/custom-providers/{pid}")
        assert del_resp.status_code == 204

        # Verify references are cleaned up
        assert await svc.get_setting("default_text_backend", "") == ""
        assert await svc.get_setting("default_image_backend", "") == ""
        # Unrelated setting should be preserved
        assert await svc.get_setting("default_video_backend", "") == "gemini-aistudio/veo-3"


class TestDeleteProviderCleansProjectRefs:
    """Regression: deleting a provider should clean up dangling references in project-level project.json."""

    def test_project_refs_cleaned_on_delete(self, client: TestClient):
        resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = resp.json()["id"]
        prefix = f"custom-{pid}/"

        # Simulate ProjectManager
        mock_pm = MagicMock()
        mock_pm.list_projects.return_value = ["project-a"]
        project_data = {"text_backend_script": f"{prefix}gpt-4o", "title": "Test"}
        mock_pm.load_project.return_value = project_data

        with (
            patch("lib.config.resolver.get_project_manager", return_value=mock_pm),
            patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock),
        ):
            del_resp = client.delete(f"/api/v1/custom-providers/{pid}")
        assert del_resp.status_code == 204

        # Verify update_project was called to clean up references
        mock_pm.update_project.assert_called_once()
        call_args = mock_pm.update_project.call_args
        assert call_args[0][0] == "project-a"
        # Execute mutate_fn to verify cleanup logic
        mutate_fn = call_args[0][1]
        test_proj = {"text_backend_script": f"{prefix}gpt-4o", "title": "Test"}
        mutate_fn(test_proj)
        assert "text_backend_script" not in test_proj
        assert test_proj["title"] == "Test"  # Unrelated field preserved


class TestReplaceModelsCleansStaleRefs:
    """Regression: replacing models should clean up global config that references deleted models."""

    async def test_stale_model_refs_cleaned(self, client: TestClient, session: AsyncSession):
        resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = resp.json()["id"]

        # Simulate global config referencing gpt-4o
        svc = ConfigService(session)
        await svc.set_setting("default_text_backend", f"custom-{pid}/gpt-4o")
        await session.commit()

        # Replace models — remove gpt-4o, keep dall-e-3
        with patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock):
            replace_resp = client.put(
                f"/api/v1/custom-providers/{pid}/models",
                json={
                    "models": [
                        {
                            "model_id": "dall-e-3",
                            "display_name": "DALL-E 3",
                            "media_type": "image",
                            "is_default": True,
                            "is_enabled": True,
                        },
                    ]
                },
            )
        assert replace_resp.status_code == 200

        # gpt-4o was deleted, global config referencing it should be cleared
        assert await svc.get_setting("default_text_backend", "") == ""


class TestEmptyModelIdRejected:
    """Regression: enabled models must have a non-empty model_id."""

    def test_create_with_empty_model_id(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Bad Provider",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-bad",
                "models": [
                    {"model_id": "", "display_name": "Empty", "media_type": "text", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422

    def test_replace_models_with_empty_model_id(self, client: TestClient):
        create_resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        with patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock):
            resp = client.put(
                f"/api/v1/custom-providers/{pid}/models",
                json={
                    "models": [
                        {"model_id": "  ", "display_name": "Blank", "media_type": "text", "is_enabled": True},
                    ]
                },
            )
        assert resp.status_code == 422


class TestDuplicateModelIdRejected:
    """Regression: duplicate model_id under the same provider is not allowed."""

    def test_create_with_duplicate(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Dup Provider",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-dup",
                "models": [
                    {"model_id": "m1", "display_name": "M1a", "media_type": "text", "is_enabled": True},
                    {"model_id": "m1", "display_name": "M1b", "media_type": "text", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] is not None


class TestFullUpdateProvider:
    """Regression: PUT full-update endpoint should atomically update provider + models."""

    def test_full_update(self, client: TestClient):
        create_resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        with patch("server.routers.custom_providers._invalidate_caches", new_callable=AsyncMock):
            resp = client.put(
                f"/api/v1/custom-providers/{pid}",
                json={
                    "display_name": "Updated Name",
                    "base_url": "https://new-api.example.com/v1",
                    "models": [
                        {
                            "model_id": "new-model",
                            "display_name": "New",
                            "media_type": "text",
                            "is_default": True,
                            "is_enabled": True,
                        },
                    ],
                },
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["display_name"] == "Updated Name"
        assert body["base_url"] == "https://new-api.example.com/v1"
        assert len(body["models"]) == 1
        assert body["models"][0]["model_id"] == "new-model"

    def test_full_update_rejects_empty_model_id(self, client: TestClient):
        create_resp = client.post("/api/v1/custom-providers", json=_PROVIDER_PAYLOAD)
        pid = create_resp.json()["id"]
        resp = client.put(
            f"/api/v1/custom-providers/{pid}",
            json={
                "display_name": "X",
                "base_url": "https://x.com",
                "models": [
                    {"model_id": "", "display_name": "Bad", "media_type": "text", "is_enabled": True},
                ],
            },
        )
        assert resp.status_code == 422

    def test_full_update_404_for_nonexistent(self, client: TestClient):
        resp = client.put(
            "/api/v1/custom-providers/9999",
            json={
                "display_name": "X",
                "base_url": "https://x.com",
                "models": [],
            },
        )
        assert resp.status_code == 404


class TestValidateBackendValueCustomPrefix:
    """Regression: validate_backend_value should accept custom-* prefix."""

    def test_custom_prefix_accepted(self):
        from server.routers._validators import validate_backend_value

        # Should not raise an exception
        validate_backend_value("custom-3/gpt-4o", "default_text_backend")

    def test_unknown_provider_rejected(self):
        from fastapi import HTTPException

        from server.routers._validators import validate_backend_value

        with pytest.raises(HTTPException) as exc_info:
            validate_backend_value("nonexistent/model", "default_text_backend")
        assert exc_info.value.status_code == 400


class TestDuplicateDefaultRejected:
    """Regression: at most one is_default=True model per media_type."""

    def test_create_with_duplicate_defaults(self, client: TestClient):
        """Creating a provider with two is_default=true models of the same media_type should return 422."""
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Dup Default Provider",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-dup-default-1234",
                "models": [
                    {
                        "model_id": "text-a",
                        "display_name": "Text A",
                        "media_type": "text",
                        "is_default": True,
                        "is_enabled": True,
                    },
                    {
                        "model_id": "text-b",
                        "display_name": "Text B",
                        "media_type": "text",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 422
        assert resp.json()["detail"] is not None

    def test_single_default_per_type_allowed(self, client: TestClient):
        """One default per different media_type should return 201 successfully."""
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Multi Default Provider",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-multi-default-12",
                "models": [
                    {
                        "model_id": "text-model",
                        "display_name": "Text Model",
                        "media_type": "text",
                        "is_default": True,
                        "is_enabled": True,
                    },
                    {
                        "model_id": "image-model",
                        "display_name": "Image Model",
                        "media_type": "image",
                        "is_default": True,
                        "is_enabled": True,
                    },
                    {
                        "model_id": "video-model",
                        "display_name": "Video Model",
                        "media_type": "video",
                        "is_default": True,
                        "is_enabled": True,
                    },
                ],
            },
        )
        assert resp.status_code == 201


class TestPriceFieldConsistency:
    """Regression: price_output cannot exist without price_input; currency can exist independently."""

    def test_output_without_input_rejected(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Bad Price",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-price-test",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "media_type": "text",
                        "is_enabled": True,
                        "price_output": 0.5,
                    },
                ],
            },
        )
        assert resp.status_code == 422

    def test_currency_without_input_accepted(self, client: TestClient):
        """currency can exist independently (user selects currency first, fills in price later)."""
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Currency Only",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-price-test",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "media_type": "text",
                        "is_enabled": True,
                        "currency": "USD",
                    },
                ],
            },
        )
        assert resp.status_code == 201

    def test_valid_price_fields_accepted(self, client: TestClient):
        resp = client.post(
            "/api/v1/custom-providers",
            json={
                "display_name": "Good Price",
                "api_format": "openai",
                "base_url": "https://api.example.com/v1",
                "api_key": "sk-price-test",
                "models": [
                    {
                        "model_id": "m1",
                        "display_name": "M1",
                        "media_type": "text",
                        "is_enabled": True,
                        "price_input": 0.1,
                        "price_output": 0.2,
                        "currency": "USD",
                    },
                ],
            },
        )
        assert resp.status_code == 201
