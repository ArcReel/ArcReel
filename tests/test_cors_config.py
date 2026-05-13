"""Tests for env-driven CORS / network binding configuration."""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest


@pytest.fixture()
def reload_app_with_env(monkeypatch: pytest.MonkeyPatch):
    """Reload server.app under controlled env so module-level CORS config rebuilds."""

    def _reload(env_overrides: dict[str, str | None]):
        env = os.environ.copy()
        for k, v in env_overrides.items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        with patch.dict(os.environ, env, clear=True):
            import server.app as module

            return importlib.reload(module)

    return _reload


class TestCorsOriginsParsing:
    """The CORS middleware values come from CORS_ORIGINS at module import time."""

    def test_unset_defaults_to_wildcard_no_credentials(self, reload_app_with_env):
        mod = reload_app_with_env({"CORS_ORIGINS": None})
        assert mod._allow_origins == ["*"]
        assert mod._allow_credentials is False

    def test_explicit_wildcard_keeps_credentials_off(self, reload_app_with_env):
        mod = reload_app_with_env({"CORS_ORIGINS": "*"})
        assert mod._allow_origins == ["*"]
        assert mod._allow_credentials is False

    def test_empty_string_falls_back_to_wildcard(self, reload_app_with_env):
        mod = reload_app_with_env({"CORS_ORIGINS": "   "})
        assert mod._allow_origins == ["*"]
        assert mod._allow_credentials is False

    def test_single_origin_enables_credentials(self, reload_app_with_env):
        mod = reload_app_with_env({"CORS_ORIGINS": "http://localhost:5173"})
        assert mod._allow_origins == ["http://localhost:5173"]
        assert mod._allow_credentials is True

    def test_multiple_origins_parsed_and_stripped(self, reload_app_with_env):
        mod = reload_app_with_env(
            {"CORS_ORIGINS": " http://a.example.com , http://b.example.com,http://c.example.com "}
        )
        assert mod._allow_origins == [
            "http://a.example.com",
            "http://b.example.com",
            "http://c.example.com",
        ]
        assert mod._allow_credentials is True

    def test_empty_segments_dropped(self, reload_app_with_env):
        mod = reload_app_with_env({"CORS_ORIGINS": "http://a,,http://b,"})
        assert mod._allow_origins == ["http://a", "http://b"]
        assert mod._allow_credentials is True

    def test_mixed_wildcard_with_specific_origin_collapses_to_wildcard(self, reload_app_with_env):
        """`*` 出现在白名单里时，整体降级为通配 + credentials=False，
        避免 Starlette `RuntimeError` (CORS spec 禁止通配 + credentials 共存)。"""
        mod = reload_app_with_env({"CORS_ORIGINS": "http://localhost:5173, *"})
        assert mod._allow_origins == ["*"]
        assert mod._allow_credentials is False


class TestListenEnvVars:
    """``LISTEN_HOST`` / ``LISTEN_PORT`` 的解析仅在 ``__main__`` 块被 uvicorn 消费，
    导入 ``server.app`` 不会触发；通过共用的模块级 ``_resolve_listen_addr()`` 函数
    测试同一份生产解析逻辑，避免测试 / 生产代码漂移。"""

    @staticmethod
    def _resolve() -> tuple[str, int]:
        import server.app as module

        return module._resolve_listen_addr()

    def test_defaults_match_existing_behavior(self):
        env = os.environ.copy()
        env.pop("LISTEN_HOST", None)
        env.pop("LISTEN_PORT", None)
        with patch.dict(os.environ, env, clear=True):
            host, port = self._resolve()
        assert host == "0.0.0.0"
        assert port == 1241

    def test_env_overrides_take_effect(self):
        with patch.dict(os.environ, {"LISTEN_HOST": "127.0.0.1", "LISTEN_PORT": "18080"}):
            host, port = self._resolve()
        assert host == "127.0.0.1"
        assert port == 18080

    def test_empty_listen_port_falls_back_to_default(self):
        """`.env` 误写 `LISTEN_PORT=`（空值）不应让 `int("")` 抛 ValueError。"""
        with patch.dict(os.environ, {"LISTEN_HOST": "", "LISTEN_PORT": ""}):
            host, port = self._resolve()
        assert host == "0.0.0.0"
        assert port == 1241
