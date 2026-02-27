"""
认证核心模块测试

测试 server.auth 中的密码生成、JWT token、凭据校验等功能。
"""

import os
import time
from unittest.mock import patch

import server.auth as auth_module


class TestGeneratePassword:
    def test_generate_password(self):
        """默认长度 16，仅字母数字"""
        pwd = auth_module.generate_password()
        assert len(pwd) == 16
        assert pwd.isalnum()

    def test_generate_password_custom_length(self):
        """自定义长度"""
        pwd = auth_module.generate_password(length=32)
        assert len(pwd) == 32
        assert pwd.isalnum()


class TestTokenSecret:
    def setup_method(self):
        """每个测试前重置缓存"""
        auth_module._cached_token_secret = None

    def test_get_token_secret_from_env(self):
        """优先使用 AUTH_TOKEN_SECRET 环境变量"""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "my-secret-key"}):
            secret = auth_module.get_token_secret()
            assert secret == "my-secret-key"

    def test_get_token_secret_auto_generate(self):
        """自动生成并缓存"""
        env = os.environ.copy()
        env.pop("AUTH_TOKEN_SECRET", None)
        with patch.dict(os.environ, env, clear=True):
            secret1 = auth_module.get_token_secret()
            assert secret1 is not None
            assert len(secret1) > 0

            # 再次调用应返回相同的缓存值
            secret2 = auth_module.get_token_secret()
            assert secret1 == secret2


class TestCreateAndVerifyToken:
    def setup_method(self):
        auth_module._cached_token_secret = None

    def test_create_and_verify_token(self):
        """创建后验证往返一致"""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            token = auth_module.create_token("admin")
            payload = auth_module.verify_token(token)
            assert payload is not None
            assert payload["sub"] == "admin"
            assert "iat" in payload
            assert "exp" in payload

    def test_verify_token_invalid(self):
        """无效 token 返回 None"""
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": "test-secret-key-that-is-at-least-32-bytes"}):
            result = auth_module.verify_token("this-is-not-a-valid-jwt")
            assert result is None

    def test_verify_token_expired(self):
        """过期 token 返回 None"""
        import jwt

        secret = "test-secret-key-that-is-at-least-32-bytes"
        with patch.dict(os.environ, {"AUTH_TOKEN_SECRET": secret}):
            # 手动创建一个已过期的 token
            payload = {
                "sub": "admin",
                "iat": time.time() - 3600,
                "exp": time.time() - 1,  # 已过期
            }
            expired_token = jwt.encode(payload, secret, algorithm="HS256")
            result = auth_module.verify_token(expired_token)
            assert result is None


class TestCheckCredentials:
    def test_check_credentials_valid(self):
        """正确凭据返回 True"""
        with patch.dict(
            os.environ, {"AUTH_USERNAME": "admin", "AUTH_PASSWORD": "pass123"}
        ):
            assert auth_module.check_credentials("admin", "pass123") is True

    def test_check_credentials_invalid(self):
        """错误凭据返回 False"""
        with patch.dict(
            os.environ, {"AUTH_USERNAME": "admin", "AUTH_PASSWORD": "pass123"}
        ):
            assert auth_module.check_credentials("admin", "wrong") is False
            assert auth_module.check_credentials("nobody", "pass123") is False

    def test_check_credentials_default_username(self):
        """AUTH_USERNAME 未设置时默认为 admin"""
        env = os.environ.copy()
        env.pop("AUTH_USERNAME", None)
        env["AUTH_PASSWORD"] = "secret"
        with patch.dict(os.environ, env, clear=True):
            assert auth_module.check_credentials("admin", "secret") is True


class TestEnsureAuthPassword:
    def setup_method(self):
        """每个测试前重置缓存"""
        auth_module._cached_token_secret = None

    def test_existing_password_returned(self):
        """AUTH_PASSWORD 已存在时直接返回现有密码"""
        with patch.dict(os.environ, {"AUTH_PASSWORD": "existing-pwd"}):
            result = auth_module.ensure_auth_password()
            assert result == "existing-pwd"

    def test_auto_generate_when_empty(self, tmp_path):
        """AUTH_PASSWORD 为空时自动生成密码并写入 os.environ"""
        env = os.environ.copy()
        env.pop("AUTH_PASSWORD", None)
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_VAR=value\n")
        with patch.dict(os.environ, env, clear=True):
            result = auth_module.ensure_auth_password(env_path=str(env_file))
            assert len(result) == 16
            assert result.isalnum()
            # 应该同步写入 os.environ
            assert os.environ["AUTH_PASSWORD"] == result

    def test_writeback_replace_existing_line(self, tmp_path):
        """回写 .env 文件：替换已有 AUTH_PASSWORD= 行"""
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_VAR=hello\nAUTH_PASSWORD=\nOTHER=world\n")
        env = os.environ.copy()
        env.pop("AUTH_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            password = auth_module.ensure_auth_password(env_path=str(env_file))
            content = env_file.read_text()
            assert f"AUTH_PASSWORD={password}" in content
            # 原有的其他行应保留
            assert "SOME_VAR=hello" in content
            assert "OTHER=world" in content

    def test_writeback_append_when_no_line(self, tmp_path):
        """回写 .env 文件：追加，原文件无 AUTH_PASSWORD 行"""
        env_file = tmp_path / ".env"
        env_file.write_text("SOME_VAR=hello\n")
        env = os.environ.copy()
        env.pop("AUTH_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            password = auth_module.ensure_auth_password(env_path=str(env_file))
            content = env_file.read_text()
            assert f"AUTH_PASSWORD={password}" in content
            # 原有内容应保留
            assert "SOME_VAR=hello" in content

    def test_env_file_not_exist_no_error(self, tmp_path):
        """.env 文件不存在时不抛异常，并创建新文件"""
        env_file = tmp_path / "nonexistent" / ".env"
        env = os.environ.copy()
        env.pop("AUTH_PASSWORD", None)
        with patch.dict(os.environ, env, clear=True):
            # 父目录不存在会触发 OSError，函数不应抛异常
            password = auth_module.ensure_auth_password(env_path=str(env_file))
            assert len(password) == 16
            assert password.isalnum()
