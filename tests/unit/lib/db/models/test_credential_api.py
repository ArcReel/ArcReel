"""Provider-credential management API tests."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.db import get_async_session
from lib.db.models.credential import ProviderCredential
from lib.db.repositories.credential_repository import CredentialRepository
from server.routers import providers
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth


def _make_app() -> tuple[FastAPI, MagicMock]:
    app = FastAPI()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()

    async def _override():
        yield mock_session

    app.dependency_overrides[get_async_session] = _override
    override_auth(app)
    app.include_router(providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    return app, mock_session


def _fake_cred(
    id: int = 1,
    provider: str = "gemini-aistudio",
    name: str = "测试Key",
    api_key: str = "AIzaSyFAKE12345678",
    is_active: bool = True,
    base_url: str | None = None,
    credentials_path: str | None = None,
) -> ProviderCredential:
    cred = ProviderCredential(
        provider=provider,
        name=name,
        api_key=api_key,
        is_active=is_active,
        base_url=base_url,
        credentials_path=credentials_path,
    )
    cred.id = id
    cred.created_at = datetime.now(UTC)
    cred.updated_at = datetime.now(UTC)
    return cred


class TestListCredentials:
    def test_returns_200(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.list_by_provider = AsyncMock(return_value=[_fake_cred()])
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.get("/api/v1/providers/gemini-aistudio/credentials")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["credentials"]) == 1
        assert body["credentials"][0]["name"] == "测试Key"
        assert body["credentials"][0]["api_key_masked"] is not None
        assert "FAKE" not in body["credentials"][0]["api_key_masked"]

    def test_returns_404_for_unknown_provider(self):
        app, _ = _make_app()
        with TestClient(app) as client:
            resp = client.get("/api/v1/providers/nonexistent/credentials")
        assert resp.status_code == 404


class TestCreateCredential:
    def test_returns_201(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.create = AsyncMock(return_value=_fake_cred())
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.post(
                "/api/v1/providers/gemini-aistudio/credentials",
                json={"name": "测试Key", "api_key": "AIza-new"},
            )
        assert resp.status_code == 201

    def test_requires_name(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.post(
                "/api/v1/providers/gemini-aistudio/credentials",
                json={"api_key": "AIza-new"},
            )
        assert resp.status_code == 422


def _fake_kling_cred(
    id: int = 1,
    access_key: str | None = "AKfake12345678",
    secret_key: str | None = "SKsecret87654321",
    api_key: str | None = None,
) -> ProviderCredential:
    cred = ProviderCredential(
        provider="kling",
        name="可灵账号",
        api_key=api_key,
        access_key=access_key,
        secret_key=secret_key,
        is_active=True,
    )
    cred.id = id
    cred.created_at = datetime.now(UTC)
    cred.updated_at = datetime.now(UTC)
    return cred


class TestKlingTwoSecretCredential:
    def test_create_persists_two_secrets(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.create = AsyncMock(return_value=_fake_kling_cred())
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.post(
                "/api/v1/providers/kling/credentials",
                json={"name": "可灵账号", "access_key": "AK-new", "secret_key": "SK-new"},
            )
        assert resp.status_code == 201
        mock_repo.create.assert_awaited_once()
        kwargs = mock_repo.create.await_args.kwargs
        assert kwargs["access_key"] == "AK-new"
        assert kwargs["secret_key"] == "SK-new"

    def test_create_strips_whitespace_from_secrets(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.create = AsyncMock(return_value=_fake_kling_cred())
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.post(
                "/api/v1/providers/kling/credentials",
                json={"name": "  可灵账号  ", "access_key": "  AK-new\n", "secret_key": "\tSK-new "},
            )
        assert resp.status_code == 201
        kwargs = mock_repo.create.await_args.kwargs
        # Pasted keys often have surrounding whitespace/newlines; strip at boundaries to avoid silent authentication failures.
        assert kwargs["name"] == "可灵账号"
        assert kwargs["access_key"] == "AK-new"
        assert kwargs["secret_key"] == "SK-new"

    def test_response_masks_each_secret_independently(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.list_by_provider = AsyncMock(return_value=[_fake_kling_cred()])
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.get("/api/v1/providers/kling/credentials")
        assert resp.status_code == 200
        cred = resp.json()["credentials"][0]
        # Mask each part independently, without cross-use or plaintext leakage.
        assert cred["access_key_masked"] is not None
        assert cred["secret_key_masked"] is not None
        assert cred["access_key_masked"] != cred["secret_key_masked"]
        assert "fake" not in cred["access_key_masked"]
        assert "secret" not in cred["secret_key_masked"]
        assert cred["api_key_masked"] is None

    def test_update_persists_only_provided_secret(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/1",
                json={"secret_key": "SK-rotated"},
            )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        assert kwargs["secret_key"] == "SK-rotated"
        assert "access_key" not in kwargs

    def test_update_strips_whitespace_and_omits_unset_secret(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/1",
                json={"secret_key": "  SK-rotated\n"},
            )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        # Strip supplied keys; omit unspecified fields from kwargs to preserve existing values.
        assert kwargs["secret_key"] == "SK-rotated"
        assert "access_key" not in kwargs


class TestCredentialGroupSwitch:
    """Credential-group switching: fully supplying a group switches to it and clears other groups."""

    def test_update_switch_to_dual_secret_clears_api_key(self):
        """After api_key, supplying complete access_key+secret_key clears api_key."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred(provider="kling", api_key="AK-old"))
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/1",
                json={"access_key": "AK-new", "secret_key": "SK-new"},
            )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        assert kwargs["access_key"] == "AK-new"
        assert kwargs["secret_key"] == "SK-new"
        assert kwargs["api_key"] is None

    def test_update_switch_to_api_key_clears_dual_secret(self):
        """Reverse switch: complete api_key clears access_key/secret_key."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/1",
                json={"api_key": "AK-bearer"},
            )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        assert kwargs["api_key"] == "AK-bearer"
        assert kwargs["access_key"] is None
        assert kwargs["secret_key"] is None

    def test_update_both_groups_submitted_rejected(self):
        """Fully supplying both groups rejects the submission and preserves the row."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/1",
                json={"api_key": "AK-bearer", "access_key": "AK-new", "secret_key": "SK-new"},
            )
        assert resp.status_code == 422
        mock_repo.update.assert_not_awaited()

    def test_update_partial_submission_does_not_clear(self):
        """No fully supplied group (secret_key rotation only) does not clear other values."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/1",
                json={"secret_key": "SK-rotated"},
            )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        assert kwargs["secret_key"] == "SK-rotated"
        assert "api_key" not in kwargs
        assert "access_key" not in kwargs

    def test_update_name_only_does_not_clear(self):
        """Legacy dual-group credentials retain both groups when renaming or changing base_url."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/1",
                json={"name": "改名"},
            )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        assert kwargs == {"name": "改名"}

    def test_create_both_groups_submitted_rejected(self):
        """Creation also rejects two complete groups, without persistence."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.create = AsyncMock(return_value=_fake_kling_cred())
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.post(
                "/api/v1/providers/kling/credentials",
                json={"name": "可灵账号", "api_key": "AK-bearer", "access_key": "AK-new", "secret_key": "SK-new"},
            )
        assert resp.status_code == 422
        mock_repo.create.assert_not_awaited()

    def test_provider_without_credential_groups_unaffected(self):
        """Regression: providers without credential_groups clear nothing on api_key updates."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred(provider="gemini-aistudio"))
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/gemini-aistudio/credentials/1",
                json={"api_key": "AIza-new"},
            )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        assert kwargs == {"api_key": "AIza-new"}

    def test_update_rotate_active_group_on_coexistence_row_preserves_other(self):
        """Routine api_key rotation on legacy coexistence rows is not group switching; dormant credentials remain."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        # Pre-feature coexistence row: api_key and access_key/secret_key all remain stored.
        mock_repo.get_by_id = AsyncMock(
            return_value=_fake_kling_cred(api_key="AK-old", access_key="AK-legacy", secret_key="SK-legacy")
        )
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/1",
                json={"api_key": "AK-rotated"},
            )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        assert kwargs["api_key"] == "AK-rotated"
        assert "access_key" not in kwargs
        assert "secret_key" not in kwargs

    def test_update_mixed_group_fields_rejected(self):
        """Cross-group api_key+secret_key submissions reject without silently dropping supplied fields or changing rows."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/1",
                json={"api_key": "AK-bearer", "secret_key": "SK-stray"},
            )
        assert resp.status_code == 422
        mock_repo.update.assert_not_awaited()

    def test_create_mixed_group_fields_rejected(self):
        """Creation also rejects cross-group api_key+access_key without persistence."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.create = AsyncMock(return_value=_fake_kling_cred())
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.post(
                "/api/v1/providers/kling/credentials",
                json={"name": "可灵账号", "api_key": "AK-bearer", "access_key": "AK-stray"},
            )
        assert resp.status_code == 422
        mock_repo.create.assert_not_awaited()

    def test_update_nonexistent_credential_returns_404_before_ambiguity(self):
        """Missing credentials return 404 before ambiguous-switch 422."""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=None)
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.patch(
                "/api/v1/providers/kling/credentials/999",
                json={"api_key": "AK-bearer", "access_key": "AK-new", "secret_key": "SK-new"},
            )
        assert resp.status_code == 404
        mock_repo.update.assert_not_awaited()


class TestActivateCredential:
    def test_returns_204(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred(provider="gemini-aistudio"))
        mock_repo.activate = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.post("/api/v1/providers/gemini-aistudio/credentials/1/activate")
        assert resp.status_code == 204

    def test_returns_404_for_nonexistent(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=None)
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.post("/api/v1/providers/gemini-aistudio/credentials/999/activate")
        assert resp.status_code == 404


class TestDeleteCredential:
    def test_returns_204(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred())
        mock_repo.delete = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo), TestClient(app) as client:
            resp = client.delete("/api/v1/providers/gemini-aistudio/credentials/1")
        assert resp.status_code == 204
