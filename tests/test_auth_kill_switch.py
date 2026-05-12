"""AUTH_ENABLED kill-switch behavior tests."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import server.auth as auth_module


@pytest.fixture(autouse=True)
def _isolated_auth_env():
    """Clear cached secret/password hash per-test so env tweaks take effect."""
    auth_module._cached_token_secret = None
    auth_module._cached_password_hash = None
    yield
    auth_module._cached_token_secret = None
    auth_module._cached_password_hash = None


class TestIsAuthEnabled:
    """``is_auth_enabled()`` 解析 AUTH_ENABLED env 值。"""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            (None, True),  # 未设置 → 默认开启（向后兼容）
            ("true", True),
            ("True", True),
            ("TRUE", True),
            ("1", True),
            ("yes", True),
            ("anything-else", True),
            ("false", False),
            ("False", False),
            ("FALSE", False),
            ("0", False),
            ("no", False),
            ("off", False),
            ("", False),  # 空串视为关闭，防 .env 半空配置
            ("  false  ", False),
        ],
    )
    def test_env_value_resolution(self, raw, expected):
        env = os.environ.copy()
        env.pop("AUTH_ENABLED", None)
        if raw is not None:
            env["AUTH_ENABLED"] = raw
        with patch.dict(os.environ, env, clear=True):
            assert auth_module.is_auth_enabled() is expected


class TestEnsureAuthPasswordKillSwitch:
    def test_disabled_returns_empty_and_does_not_touch_env(self, tmp_path):
        env_file = tmp_path / ".env"
        env = os.environ.copy()
        env["AUTH_ENABLED"] = "false"
        env.pop("AUTH_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            result = auth_module.ensure_auth_password(env_path=str(env_file))
            assert result == ""
            assert not env_file.exists()
            assert "AUTH_PASSWORD" not in os.environ

    def test_enabled_still_generates(self, tmp_path):
        env_file = tmp_path / ".env"
        env = os.environ.copy()
        env.pop("AUTH_ENABLED", None)
        env.pop("AUTH_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            result = auth_module.ensure_auth_password(env_path=str(env_file))
        assert result != ""
        assert len(result) == 16


class TestCheckCredentialsKillSwitch:
    def test_disabled_returns_true_for_any_input(self):
        with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
            assert auth_module.check_credentials("anyone", "anything") is True
            assert auth_module.check_credentials("", "") is True

    def test_enabled_still_validates(self):
        env = {
            "AUTH_ENABLED": "true",
            "AUTH_USERNAME": "admin",
            "AUTH_PASSWORD": "pass123",
        }
        with patch.dict(os.environ, env):
            assert auth_module.check_credentials("admin", "pass123") is True
            assert auth_module.check_credentials("admin", "wrong") is False


class TestVerifyDownloadTokenKillSwitch:
    def test_disabled_returns_fake_payload_without_verifying(self):
        with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
            payload = auth_module.verify_download_token("invalid-garbage", "myproject")
        assert payload["project"] == "myproject"
        assert payload["purpose"] == "download"
        assert payload["sub"] == "local"

    def test_enabled_rejects_invalid_token(self):
        env = {
            "AUTH_ENABLED": "true",
            "AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes",
        }
        with patch.dict(os.environ, env):
            import jwt

            with pytest.raises(jwt.InvalidTokenError):
                auth_module.verify_download_token("not-a-jwt", "myproject")


class TestGetCurrentUserKillSwitch:
    """这些 dep 函数是 async；用 asyncio.run 直接调用。"""

    @pytest.mark.asyncio
    async def test_disabled_returns_anonymous_admin_without_token(self):
        with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
            user = await auth_module.get_current_user(token=None)
        assert user.role == "admin"
        assert user.sub == "local"

    @pytest.mark.asyncio
    async def test_disabled_returns_anonymous_admin_even_with_invalid_token(self):
        with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
            user = await auth_module.get_current_user(token="anything-goes")
        assert user.sub == "local"

    @pytest.mark.asyncio
    async def test_enabled_without_token_raises_401(self):
        env = os.environ.copy()
        env.pop("AUTH_ENABLED", None)
        env["AUTH_TOKEN_SECRET"] = "test-secret-key-that-is-at-least-32-bytes"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(HTTPException) as exc_info:
                await auth_module.get_current_user(token=None)
            assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_flexible_disabled_returns_anonymous_without_token(self):
        with patch.dict(os.environ, {"AUTH_ENABLED": "false"}):
            user = await auth_module.get_current_user_flexible(token=None, query_token=None)
        assert user.sub == "local"

    @pytest.mark.asyncio
    async def test_flexible_enabled_without_token_raises_401(self):
        env = os.environ.copy()
        env.pop("AUTH_ENABLED", None)
        env["AUTH_TOKEN_SECRET"] = "test-secret-key-that-is-at-least-32-bytes"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(HTTPException) as exc_info:
                await auth_module.get_current_user_flexible(token=None, query_token=None)
            assert exc_info.value.status_code == 401


class TestLoginRouteKillSwitch:
    @pytest.mark.asyncio
    async def test_disabled_login_succeeds_with_any_password(self):
        """端到端：AUTH_ENABLED=false 时 /auth/token 接受任意凭据。"""
        from fastapi.testclient import TestClient

        from server.app import app

        env = {"AUTH_ENABLED": "false"}
        with patch.dict(os.environ, env):
            with TestClient(app) as client:
                response = client.post(
                    "/api/v1/auth/token",
                    data={"username": "anyone", "password": "wrong"},
                )
        assert response.status_code == 200
        body = response.json()
        assert body["token_type"] == "bearer"
        assert body["access_token"]
