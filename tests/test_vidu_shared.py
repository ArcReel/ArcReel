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


class TestViduConnectionTestUrl:
    """验证连接测试用数字 task id（Vidu 服务端把 id 当 int 解析，非数字会 400 CODEC）。"""

    @staticmethod
    def _patched_client(monkeypatch: pytest.MonkeyPatch, *, status_code: int, body: str = ""):
        captured: dict[str, str] = {}

        class _FakeResp:
            def __init__(self):
                self.status_code = status_code
                self.text = body

        class _FakeClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def get(self, url, **_kwargs):
                captured["url"] = url
                return _FakeResp()

        monkeypatch.setattr(vidu_shared.httpx, "Client", _FakeClient)
        return captured

    def test_url_uses_numeric_bogus_id(self, monkeypatch: pytest.MonkeyPatch):
        captured = self._patched_client(monkeypatch, status_code=404)
        vidu_shared.test_vidu_connection({"api_key": "vda_test"})
        assert captured["url"].endswith("/tasks/0/creations")

    def test_404_is_success(self, monkeypatch: pytest.MonkeyPatch):
        self._patched_client(monkeypatch, status_code=404)
        vidu_shared.test_vidu_connection({"api_key": "vda_test"})  # 不抛错即成功

    def test_401_is_invalid_credential(self, monkeypatch: pytest.MonkeyPatch):
        self._patched_client(monkeypatch, status_code=401)
        with pytest.raises(RuntimeError, match="凭证无效"):
            vidu_shared.test_vidu_connection({"api_key": "vda_test"})

    def test_400_is_undecidable(self, monkeypatch: pytest.MonkeyPatch):
        self._patched_client(monkeypatch, status_code=400, body="CODEC parse error")
        with pytest.raises(RuntimeError, match="无法判定"):
            vidu_shared.test_vidu_connection({"api_key": "vda_test"})
