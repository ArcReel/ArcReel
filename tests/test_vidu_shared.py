"""lib.vidu_shared 单元测试 — 重点校验凭证解析与连接测试的环境变量回退语义。"""

from __future__ import annotations

import pytest

from lib import vidu_shared


class TestResolveViduApiKey:
    def test_explicit_key_wins(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VIDU_API_KEY", "from-env")
        assert vidu_shared.resolve_vidu_api_key("explicit") == "explicit"

    def test_env_fallback_when_allowed(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VIDU_API_KEY", "from-env")
        assert vidu_shared.resolve_vidu_api_key(None) == "from-env"

    def test_env_fallback_disabled(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VIDU_API_KEY", "from-env")
        with pytest.raises(ValueError, match="Vidu API Key 未提供"):
            vidu_shared.resolve_vidu_api_key(None, allow_env_fallback=False)

    def test_missing_key_without_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VIDU_API_KEY", raising=False)
        with pytest.raises(ValueError, match="Vidu API Key 未提供"):
            vidu_shared.resolve_vidu_api_key(None)


class TestViduConnectionTestKeyResolution:
    """连接测试不应回退到环境变量——否则用户没填 key 时会被环境变量"假成功"。"""

    def test_missing_config_key_does_not_fall_back_to_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VIDU_API_KEY", "from-env")

        # 若 fallback 关闭生效，httpx.Client 不会被构造，直接在 resolve 阶段抛错。
        def _should_not_be_called(*_args, **_kwargs):
            raise AssertionError("connection test should fail before HTTP call")

        monkeypatch.setattr(vidu_shared.httpx, "Client", _should_not_be_called)

        with pytest.raises(ValueError, match="Vidu API Key 未提供"):
            vidu_shared.test_vidu_connection({})
