"""供应商凭证管理 API 测试。"""

from __future__ import annotations
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.db import get_async_session
from lib.db.models.credential import ProviderCredential
from lib.db.repositories.credential_repository import CredentialRepository
from server.routers import providers


def _make_app() -> tuple[FastAPI, MagicMock]:
    app = FastAPI()
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock()
    async def _override():
        yield mock_session
    app.dependency_overrides[get_async_session] = _override
    app.include_router(providers.router, prefix="/api/v1")
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
        provider=provider, name=name, api_key=api_key,
        is_active=is_active, base_url=base_url, credentials_path=credentials_path,
    )
    cred.id = id
    cred.created_at = datetime.now(timezone.utc)
    cred.updated_at = datetime.now(timezone.utc)
    return cred


class TestListCredentials:
    def test_returns_200(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.list_by_provider = AsyncMock(return_value=[_fake_cred()])
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
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
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/providers/gemini-aistudio/credentials",
                    json={"name": "测试Key", "api_key": "AIza-new"},
                )
        assert resp.status_code == 201

    def test_requires_name(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/providers/gemini-aistudio/credentials",
                    json={"api_key": "AIza-new"},
                )
        assert resp.status_code == 422


class TestActivateCredential:
    def test_returns_204(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred(provider="gemini-aistudio"))
        mock_repo.activate = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/credentials/1/activate")
        assert resp.status_code == 204

    def test_returns_404_for_nonexistent(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=None)
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post("/api/v1/providers/gemini-aistudio/credentials/999/activate")
        assert resp.status_code == 404


class TestDeleteCredential:
    def test_returns_204(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred())
        mock_repo.delete = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.delete("/api/v1/providers/gemini-aistudio/credentials/1")
        assert resp.status_code == 204
