"""供应商凭证管理 API 测试。"""

from __future__ import annotations

from datetime import UTC, datetime
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
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
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
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/providers/kling/credentials",
                    json={"name": "  可灵账号  ", "access_key": "  AK-new\n", "secret_key": "\tSK-new "},
                )
        assert resp.status_code == 201
        kwargs = mock_repo.create.await_args.kwargs
        # 粘贴密钥常带首尾空白/换行，边界处统一 strip，避免静默鉴权失败
        assert kwargs["name"] == "可灵账号"
        assert kwargs["access_key"] == "AK-new"
        assert kwargs["secret_key"] == "SK-new"

    def test_response_masks_each_secret_independently(self):
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.list_by_provider = AsyncMock(return_value=[_fake_kling_cred()])
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.get("/api/v1/providers/kling/credentials")
        assert resp.status_code == 200
        cred = resp.json()["credentials"][0]
        # 两段各自独立脱敏，互不混用，且不泄漏明文
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
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
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
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.patch(
                    "/api/v1/providers/kling/credentials/1",
                    json={"secret_key": "  SK-rotated\n"},
                )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        # 提供的密钥 strip 首尾空白；未提供的字段不进 kwargs（保留既有值）
        assert kwargs["secret_key"] == "SK-rotated"
        assert "access_key" not in kwargs


class TestCredentialGroupSwitch:
    """凭证切组自动清空：完整覆盖某组即视为切组，自动清空其它组字段。"""

    def test_update_switch_to_dual_secret_clears_api_key(self):
        """先存 api_key，再完整提交 access_key+secret_key → api_key 被清空。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred(provider="kling", api_key="AK-old"))
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
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
        """反向切换：完整提交 api_key → access_key/secret_key 被清空。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
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
        """一次提交同时完整覆盖两组：拒绝，凭证行不变。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.patch(
                    "/api/v1/providers/kling/credentials/1",
                    json={"api_key": "AK-bearer", "access_key": "AK-new", "secret_key": "SK-new"},
                )
        assert resp.status_code == 422
        mock_repo.update.assert_not_awaited()

    def test_update_partial_submission_does_not_clear(self):
        """未完整覆盖任何组（仅轮换 secret_key）：不触发清空，其余字段保持原值。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
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
        """存量两组并存的凭证行：仅改名/改 base_url 不触发清空，两组字段都不动。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.patch(
                    "/api/v1/providers/kling/credentials/1",
                    json={"name": "改名"},
                )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        assert kwargs == {"name": "改名"}

    def test_create_both_groups_submitted_rejected(self):
        """创建端点同样拒绝一次提交同时完整覆盖两组，且不落盘。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.create = AsyncMock(return_value=_fake_kling_cred())
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/providers/kling/credentials",
                    json={"name": "可灵账号", "api_key": "AK-bearer", "access_key": "AK-new", "secret_key": "SK-new"},
                )
        assert resp.status_code == 422
        mock_repo.create.assert_not_awaited()

    def test_provider_without_credential_groups_unaffected(self):
        """未声明 credential_groups 的 provider（回归）：更新 api_key 不触发任何清空逻辑。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_cred(provider="gemini-aistudio"))
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.patch(
                    "/api/v1/providers/gemini-aistudio/credentials/1",
                    json={"api_key": "AIza-new"},
                )
        assert resp.status_code == 204
        kwargs = mock_repo.update.await_args.kwargs
        assert kwargs == {"api_key": "AIza-new"}

    def test_update_rotate_active_group_on_coexistence_row_preserves_other(self):
        """存量共存行（两组凭证并存）上例行轮换 api_key：不视为切组，另一组休眠凭证不被清空。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        # 本功能上线前可创建的共存行：api_key 与 access_key/secret_key 同时留存
        mock_repo.get_by_id = AsyncMock(
            return_value=_fake_kling_cred(api_key="AK-old", access_key="AK-legacy", secret_key="SK-legacy")
        )
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
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
        """一次提交横跨两组（api_key + secret_key）：拒绝，不静默丢弃已填字段，凭证行不变。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=_fake_kling_cred())
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.patch(
                    "/api/v1/providers/kling/credentials/1",
                    json={"api_key": "AK-bearer", "secret_key": "SK-stray"},
                )
        assert resp.status_code == 422
        mock_repo.update.assert_not_awaited()

    def test_create_mixed_group_fields_rejected(self):
        """创建端点同样拒绝横跨多组的提交（api_key + access_key），且不落盘。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.create = AsyncMock(return_value=_fake_kling_cred())
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
                resp = client.post(
                    "/api/v1/providers/kling/credentials",
                    json={"name": "可灵账号", "api_key": "AK-bearer", "access_key": "AK-stray"},
                )
        assert resp.status_code == 422
        mock_repo.create.assert_not_awaited()

    def test_update_nonexistent_credential_returns_404_before_ambiguity(self):
        """凭证不存在时优先返回 404，而非切组歧义的 422。"""
        app, _ = _make_app()
        mock_repo = MagicMock(spec=CredentialRepository)
        mock_repo.get_by_id = AsyncMock(return_value=None)
        mock_repo.update = AsyncMock()
        with patch("server.routers.providers.CredentialRepository", return_value=mock_repo):
            with TestClient(app) as client:
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
