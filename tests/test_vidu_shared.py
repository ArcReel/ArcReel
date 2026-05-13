"""lib.vidu_shared 单元测试 — 重点校验凭证解析与连接测试的环境变量回退语义。"""

from __future__ import annotations

import pytest

from lib import vidu_shared


class TestResolveViduApiKey:
    def test_explicit_key_wins(self, monkeypatch: pytest.MonkeyPatch):
        # spec §5.4：即使 env 里有 VIDU_API_KEY，也不会被 fallback；显式参数永远优先且唯一来源。
        monkeypatch.setenv("VIDU_API_KEY", "from-env")
        assert vidu_shared.resolve_vidu_api_key("explicit") == "explicit"

    def test_env_no_longer_falls_back(self, monkeypatch: pytest.MonkeyPatch):
        """spec §5.4：删除 env fallback——即使 VIDU_API_KEY 在环境中，缺失参数仍 raise。"""
        monkeypatch.setenv("VIDU_API_KEY", "from-env")
        with pytest.raises(ValueError, match="Vidu API Key"):
            vidu_shared.resolve_vidu_api_key(None)

    def test_missing_key_without_env(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("VIDU_API_KEY", raising=False)
        with pytest.raises(ValueError, match="Vidu API Key"):
            vidu_shared.resolve_vidu_api_key(None)


class TestViduConnectionTestKeyResolution:
    """连接测试 config 缺失 api_key 时必须在 resolve 阶段直接 raise（不应发起 HTTP 请求）。"""

    def test_missing_config_key_raises_before_http(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("VIDU_API_KEY", "from-env")

        # resolve 阶段就该抛错，httpx.Client 不应被构造
        def _should_not_be_called(*_args, **_kwargs):
            raise AssertionError("connection test should fail before HTTP call")

        monkeypatch.setattr(vidu_shared.httpx, "Client", _should_not_be_called)

        with pytest.raises(ValueError, match="Vidu API Key"):
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
