"""
Core authentication module tests.

Cover server.auth password generation, JWT tokens, credential validation, and related behavior.
"""

import os
import time
from unittest.mock import patch

import pytest
from fastapi import HTTPException

import server.auth as auth_module


class TestGeneratePassword:
    def test_generate_password(self):
        """Default length 16, alphanumeric only."""
        pwd = auth_module.generate_password()
        assert len(pwd) == 16
        assert pwd.isalnum()

    def test_generate_password_custom_length(self):
        """Custom length."""
        pwd = auth_module.generate_password(length=32)
        assert len(pwd) == 32
        assert pwd.isalnum()


class TestTokenSecret:
    def setup_method(self):
        """Reset caches before each test."""
        auth_module._cached_token_secret = None

    def test_get_token_secret_from_env(self):
        """Prefer AUTH_TOKEN_SECRET environment values."""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "my-secret-key"}):
            secret = auth_module.get_token_secret()
            assert secret == "my-secret-key"

    def test_get_token_secret_auto_generate(self):
        """Generate automatically and cache."""
        env = os.environ.copy()
        env.pop("AUTH_TOKEN_SECRET", None)
        with patch.dict(os.environ, env, clear=True):
            secret1 = auth_module.get_token_secret()
            assert secret1 is not None
            assert len(secret1) > 0

            # Repeated calls return the same cached value.
            secret2 = auth_module.get_token_secret()
            assert secret1 == secret2


def _simulate_restart() -> None:
    """Process restart loses all in-memory caches while data directories/.env remain."""
    auth_module.reset_token_secret_for_tests()
    auth_module._cached_password_hash = None


class TestTokenSecretPersistence:
    """Without AUTH_TOKEN_SECRET, persist signing keys under data directories and reuse after restart."""

    def setup_method(self):
        _simulate_restart()

    def teardown_method(self):
        _simulate_restart()

    @pytest.mark.parametrize("unset", ["missing", "empty"])
    def test_token_survives_restart_without_configured_secret(self, monkeypatch, unset):
        if unset == "missing":
            monkeypatch.delenv("AUTH_TOKEN_SECRET", raising=False)
        else:
            monkeypatch.setenv("AUTH_TOKEN_SECRET", "")
        token = auth_module.create_token("admin")

        _simulate_restart()

        payload = auth_module.verify_token(token)
        assert payload is not None
        assert payload["sub"] == "admin"

    def test_persisted_secret_file_is_owner_only(self, monkeypatch):
        from lib.infra.data_root_layout import DataRootLayout

        monkeypatch.delenv("AUTH_TOKEN_SECRET", raising=False)
        auth_module.ensure_token_secret()

        secret_path = DataRootLayout.current().auth_token_secret_path
        assert secret_path.read_text(encoding="utf-8").strip() == auth_module.get_token_secret()
        if os.name == "posix":
            assert secret_path.stat().st_mode & 0o777 == 0o600

    def test_existing_secret_file_is_reused(self, monkeypatch):
        from lib.infra.data_root_layout import DataRootLayout

        monkeypatch.delenv("AUTH_TOKEN_SECRET", raising=False)
        secret_path = DataRootLayout.current().auth_token_secret_path
        secret_path.parent.mkdir(parents=True, exist_ok=True)
        secret_path.write_text("previously-persisted-secret\n", encoding="utf-8")

        assert auth_module.get_token_secret() == "previously-persisted-secret"

    def test_configured_secret_wins_over_persisted_file(self, monkeypatch):
        monkeypatch.delenv("AUTH_TOKEN_SECRET", raising=False)
        token_signed_with_persisted = auth_module.create_token("admin")

        _simulate_restart()
        monkeypatch.setenv("AUTH_TOKEN_SECRET", "explicit-secret-that-is-at-least-32-bytes")

        assert auth_module.get_token_secret() == "explicit-secret-that-is-at-least-32-bytes"
        assert auth_module.verify_token(token_signed_with_persisted) is None

    def test_unwritable_data_dir_falls_back_to_process_secret(self, monkeypatch, tmp_path, caplog):
        """Unwritable key storage never blocks startup; use process-random keys, requiring login again after restart."""
        from lib.infra.app_data_dir import reset_for_tests

        monkeypatch.delenv("AUTH_TOKEN_SECRET", raising=False)
        data_root = tmp_path / "data-root"
        data_root.mkdir()
        (data_root / "runtime").write_text("", encoding="utf-8")  # Cannot create runtime/.
        monkeypatch.setenv("ARCREEL_DATA_DIR", str(data_root))
        reset_for_tests()

        with caplog.at_level("WARNING", logger=auth_module.__name__):
            secret = auth_module.get_token_secret()

        assert secret
        assert auth_module.get_token_secret() == secret
        assert any("AUTH_TOKEN_SECRET" in r.getMessage() for r in caplog.records)
        assert all(secret not in r.getMessage() for r in caplog.records)


class TestCreateAndVerifyToken:
    def setup_method(self):
        auth_module._cached_token_secret = None

    def test_create_and_verify_token(self):
        """Creation/verification round trip."""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            token = auth_module.create_token("admin")
            payload = auth_module.verify_token(token)
            assert payload is not None
            assert payload["sub"] == "admin"
            assert "iat" in payload
            assert "exp" in payload

    def test_create_token_accepts_a_shorter_expiry(self):
        """Embedded Agents issue short-lived session JWTs without changing default Web-login lifetimes."""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            token = auth_module.create_token("embedded-agent", expiry_seconds=900)
            payload = auth_module.verify_token(token)

        assert payload is not None
        assert payload["exp"] - payload["iat"] == 900

    def test_verify_token_invalid(self):
        """Invalid tokens return None."""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            result = auth_module.verify_token("this-is-not-a-valid-jwt")
            assert result is None

    def test_verify_token_expired(self):
        """Expired tokens return None."""
        import jwt

        secret = "test-secret-key-that-is-at-least-32-bytes"
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": secret}):
            # Manually create an expired token.
            payload = {
                "sub": "admin",
                "iat": time.time() - 3600,
                "exp": time.time() - 1,  # Already expired.
            }
            expired_token = jwt.encode(payload, secret, algorithm="HS256")
            result = auth_module.verify_token(expired_token)
            assert result is None

    @pytest.mark.parametrize("purpose", ["download", "other"])
    def test_verify_token_rejects_purpose_bound_token(self, purpose):
        """Session validation rejects tokens with purpose claims of any value."""
        import jwt

        secret = "test-secret-key-that-is-at-least-32-bytes"
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": secret}):
            payload = {"sub": "admin", "purpose": purpose, "iat": time.time(), "exp": time.time() + 300}
            token = jwt.encode(payload, secret, algorithm="HS256")
            assert auth_module.verify_token(token) is None

    def test_verify_token_accepts_session_token(self):
        """Purpose-free session tokens from create_token, for login/embedded Agents, remain valid."""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            payload = auth_module.verify_token(auth_module.create_token("embedded-agent", expiry_seconds=60))
            assert payload is not None
            assert payload["sub"] == "embedded-agent"


class TestCheckCredentials:
    def setup_method(self):
        auth_module._cached_password_hash = None

    def test_check_credentials_valid(self):
        """Correct credentials return True."""
        with patch.dict(os.environ, {"AUTH_USERNAME": "admin", "AUTH_PASSWORD": "pass123"}):
            assert auth_module.check_credentials("admin", "pass123") is True

    def test_check_credentials_invalid(self):
        """Wrong credentials return False."""
        with patch.dict(os.environ, {"AUTH_USERNAME": "admin", "AUTH_PASSWORD": "pass123"}):
            assert auth_module.check_credentials("admin", "wrong") is False
            assert auth_module.check_credentials("nobody", "pass123") is False

    def test_check_credentials_default_username(self):
        """Unset AUTH_USERNAME defaults to admin."""
        env = os.environ.copy()
        env.pop("AUTH_USERNAME", None)
        env["AUTH_PASSWORD"] = "secret"
        with patch.dict(os.environ, env, clear=True):
            assert auth_module.check_credentials("admin", "secret") is True


class TestEnsureAuthPassword:
    def setup_method(self):
        """Reset caches before each test."""
        auth_module._cached_token_secret = None

    def test_existing_password_returned(self):
        """Existing AUTH_PASSWORD returns unchanged."""
        with patch.dict(os.environ, {"AUTH_PASSWORD": "existing-pwd"}):
            result = auth_module.ensure_auth_password()
            assert result == "existing-pwd"

    def test_auto_generate_when_empty(self, tmp_path):
        """Empty AUTH_PASSWORD generates passwords and writes os.environ."""
        env = os.environ.copy()
        env.pop("AUTH_PASSWORD", None)
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_VAR=value\n")
        with patch.dict(os.environ, env, clear=True):
            result = auth_module.ensure_auth_password(env_path=str(env_file))
            assert len(result) == 16
            assert result.isalnum()
            # Also write os.environ.
            assert os.environ["AUTH_PASSWORD"] == result

    def test_writeback_replace_existing_line(self, tmp_path):
        """Write .env back by replacing existing AUTH_PASSWORD= lines."""
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_VAR=hello\nAUTH_PASSWORD=\nOTHER=world\n")
        env = os.environ.copy()
        env.pop("AUTH_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            password = auth_module.ensure_auth_password(env_path=str(env_file))
            content = env_file.read_text()
            assert f"AUTH_PASSWORD={password}" in content
            # Preserve other existing lines.
            assert "SOME_VAR=hello" in content
            assert "OTHER=world" in content

    def test_writeback_append_when_no_line(self, tmp_path):
        """Append when existing .env lacks AUTH_PASSWORD lines."""
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_VAR=hello\n")
        env = os.environ.copy()
        env.pop("AUTH_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            password = auth_module.ensure_auth_password(env_path=str(env_file))
            content = env_file.read_text()
            assert f"AUTH_PASSWORD={password}" in content
            # Preserve existing content.
            assert "SOME_VAR=hello" in content

    def test_env_file_not_exist_no_error(self, tmp_path):
        """Absent .env creates new files without raising."""
        env_file = tmp_path / "nonexistent" / ".env"
        env = os.environ.copy()
        env.pop("AUTH_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            # Missing parents trigger OSError; functions must not raise.
            password = auth_module.ensure_auth_password(env_path=str(env_file))
            assert len(password) == 16
            assert password.isalnum()

    def test_env_file_non_utf8_does_not_block_startup(self, tmp_path):
        """Legacy .env in cp936/ANSI local encodings makes ``read_text(encoding="utf-8")``
        raise ``UnicodeDecodeError``. Server lifespan calls this during startup, so catch/fall back
        without persistence while retaining process passwords; otherwise startup fails.
        """
        env_file = tmp_path / ".env"
        # cp936 Chinese comments plus existing AUTH_PASSWORD guarantee UTF-8 decoding failure.
        env_file.write_bytes("# 注释\nAUTH_PASSWORD=old\n".encode("cp936"))
        env = os.environ.copy()
        env.pop("AUTH_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            password = auth_module.ensure_auth_password(env_path=str(env_file))
            assert len(password) == 16
            assert password.isalnum()
            # Preserve .env bytes; never force overwrite and lose user content.
            assert env_file.read_bytes() == "# 注释\nAUTH_PASSWORD=old\n".encode("cp936")


class TestPasswordAcrossRestarts:
    """Docker restart without recreation keeps compose-injected AUTH_PASSWORD empty."""

    def _restart(self, env_file, monkeypatch) -> str:
        from lib.infra.env_init import load_env_file

        _simulate_restart()
        monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)  # Root conftest disables .env reads.
        monkeypatch.setenv("AUTH_PASSWORD", "")  # Container-creation template value fixed by `AUTH_PASSWORD=`.
        load_env_file(env_file)
        return auth_module.ensure_auth_password(env_path=str(env_file))

    def test_password_edited_in_env_file_is_kept(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("AUTH_USERNAME=admin\nAUTH_PASSWORD=my-new-pass\n", encoding="utf-8")

        assert self._restart(env_file, monkeypatch) == "my-new-pass"
        assert env_file.read_text(encoding="utf-8") == "AUTH_USERNAME=admin\nAUTH_PASSWORD=my-new-pass\n"
        assert auth_module.check_credentials("admin", "my-new-pass") is True

    def test_generated_once_on_first_start_then_stable(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text("AUTH_USERNAME=admin\nAUTH_PASSWORD=\n", encoding="utf-8")

        generated = self._restart(env_file, monkeypatch)
        assert len(generated) == 16
        written = env_file.read_text(encoding="utf-8")
        assert f"AUTH_PASSWORD={generated}" in written

        assert self._restart(env_file, monkeypatch) == generated
        assert self._restart(env_file, monkeypatch) == generated
        assert env_file.read_text(encoding="utf-8") == written


class TestDownloadToken:
    def setup_method(self):
        auth_module._cached_token_secret = None

    def test_create_and_verify_download_token(self):
        """Issue/verify download tokens."""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            token = auth_module.create_download_token("admin", "my-project")
            payload = auth_module.verify_download_token(token, "my-project")
            assert payload["sub"] == "admin"
            assert payload["project"] == "my-project"
            assert payload["purpose"] == "download"

    def test_verify_download_token_wrong_project(self):
        """Project mismatches raise ValueError."""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            token = auth_module.create_download_token("admin", "project-a")
            import pytest

            with pytest.raises(ValueError, match="project 不匹配"):
                auth_module.verify_download_token(token, "project-b")

    def test_verify_download_token_expired(self):
        """Expired tokens raise ExpiredSignatureError."""
        import jwt

        secret = "test-secret-key-that-is-at-least-32-bytes"
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": secret}):
            payload = {
                "sub": "admin",
                "project": "demo",
                "purpose": "download",
                "iat": time.time() - 600,
                "exp": time.time() - 1,
            }
            expired_token = jwt.encode(payload, secret, algorithm="HS256")
            with pytest.raises(jwt.ExpiredSignatureError):
                auth_module.verify_download_token(expired_token, "demo")

    def test_verify_download_token_wrong_purpose(self):
        """Purpose mismatches raise ValueError."""
        import jwt

        secret = "test-secret-key-that-is-at-least-32-bytes"
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": secret}):
            payload = {
                "sub": "admin",
                "project": "demo",
                "purpose": "other",
                "iat": time.time(),
                "exp": time.time() + 300,
            }
            token = jwt.encode(payload, secret, algorithm="HS256")
            with pytest.raises(ValueError, match="purpose 不匹配"):
                auth_module.verify_download_token(token, "demo")


class TestPasswordHash:
    """Password hashing tests."""

    def setup_method(self):
        auth_module._cached_password_hash = None

    def test_check_credentials_wrong_password_with_hash(self):
        """Wrong password hashes fail comparisons."""
        with patch.dict(os.environ, {"AUTH_USERNAME": "admin", "AUTH_PASSWORD": "pass123"}):
            assert auth_module.check_credentials("admin", "wrong") is False

    def test_check_credentials_wrong_username_timing_safe(self):
        """Wrong usernames still verify hashes, preventing timing attacks."""
        with patch.dict(os.environ, {"AUTH_USERNAME": "admin", "AUTH_PASSWORD": "pass123"}):
            assert auth_module.check_credentials("nobody", "pass123") is False

    def test_password_hash_cached(self):
        """Cache hash values."""
        with patch.dict(os.environ, {"AUTH_USERNAME": "admin", "AUTH_PASSWORD": "pass123"}):
            auth_module.check_credentials("admin", "pass123")
            first_hash = auth_module._cached_password_hash
            auth_module.check_credentials("admin", "pass123")
            assert auth_module._cached_password_hash is first_hash  # Same object.


class TestGetCurrentUser:
    """FastAPI dependency tests."""

    def setup_method(self):
        auth_module._cached_token_secret = None

    async def test_get_current_user_valid_token(self):
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            token = auth_module.create_token("admin")
            result = await auth_module.get_current_user(token)
            assert isinstance(result, auth_module.CurrentUserInfo)
            assert result.sub == "admin"
            assert result.id == "default"
            assert result.role == "admin"

    async def test_get_current_user_invalid_token(self):

        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            with pytest.raises(HTTPException) as exc_info:
                await auth_module.get_current_user("invalid-token")
            assert exc_info.value.status_code == 401
