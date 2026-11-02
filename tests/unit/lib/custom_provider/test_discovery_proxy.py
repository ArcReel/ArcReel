"""模型发现三条路径按 base_url 决定是否绕系统代理：内网 base_url 直连、公网 base_url 走代理。

8001 这类自建兼容端点是本次连通性失败的直接路径：OpenAI / Google SDK 自建的 httpx client 默认读
系统代理，发往内网会被劫持回 502。本文件用 spy 捕获 SDK 构造参数，断言内网 base_url 时注入了绕代理
配置、公网 base_url 时未注入（维持 SDK 默认），并回归「公网供应商路径未被改成绕代理」。不打真实网络。
"""

from __future__ import annotations

from typing import Any, ClassVar

import httpx
import pytest

from lib.custom_provider import discovery

_INTERNAL_OPENAI = "http://192.168.5.149:8001/v1"
_PUBLIC_OPENAI = "https://api.openai.com/v1"
_INTERNAL_GOOGLE = "http://192.168.5.149:8001"
_PUBLIC_GOOGLE = "https://generativelanguage.googleapis.com"


class _FakeModel:
    def __init__(self, model_id: str) -> None:
        self.id = model_id
        self.name = model_id


class _FakeOpenAI:
    """记录构造 kwargs 的 OpenAI 替身；``models.list()`` 回一条假模型。"""

    last_kwargs: ClassVar[dict[str, Any]] = {}

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_kwargs = kwargs

        class _Models:
            def list(self) -> list[_FakeModel]:
                return [_FakeModel("gpt-x")]

        self.models = _Models()


class _FakeGenaiClient:
    last_http_options: Any = None

    def __init__(self, **kwargs: Any) -> None:
        type(self).last_http_options = kwargs.get("http_options")

        class _Models:
            def list(self) -> list[_FakeModel]:
                return [_FakeModel("gemini-x")]

        self.models = _Models()


class TestDiscoverOpenaiProxy:
    async def test_internal_base_url_injects_bypassing_http_client(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(discovery, "OpenAI", _FakeOpenAI)
        _FakeOpenAI.last_kwargs = {}
        await discovery.discover_models(discovery_format="openai", base_url=_INTERNAL_OPENAI, api_key="k")
        http_client = _FakeOpenAI.last_kwargs.get("http_client")
        assert isinstance(http_client, httpx.Client)
        http_client.close()

    async def test_public_base_url_omits_http_client(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(discovery, "OpenAI", _FakeOpenAI)
        _FakeOpenAI.last_kwargs = {}
        await discovery.discover_models(discovery_format="openai", base_url=_PUBLIC_OPENAI, api_key="k")
        # 公网 base_url 不注入绕代理 client：http_client 为 None（即 SDK 默认，读系统代理），
        # 与完全不传等价。
        assert _FakeOpenAI.last_kwargs.get("http_client") is None


class TestDiscoverGoogleProxy:
    async def test_internal_base_url_injects_client_args_mounts(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(discovery.genai, "Client", _FakeGenaiClient)
        _FakeGenaiClient.last_http_options = None
        await discovery.discover_models(discovery_format="google", base_url=_INTERNAL_GOOGLE, api_key="k")
        opts = _FakeGenaiClient.last_http_options
        assert opts is not None
        assert "client_args" in opts
        assert "mounts" in opts["client_args"]

    async def test_public_base_url_omits_client_args(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(discovery.genai, "Client", _FakeGenaiClient)
        _FakeGenaiClient.last_http_options = None
        await discovery.discover_models(discovery_format="google", base_url=_PUBLIC_GOOGLE, api_key="k")
        opts = _FakeGenaiClient.last_http_options
        assert opts is not None
        assert "client_args" not in opts


class TestDiscoverAnthropicProxy:
    async def test_internal_base_url_bypasses_proxy(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        captured: list[httpx.AsyncClient] = []
        real = discovery.artifact_http_client

        def _record(**kwargs: Any) -> Any:
            client = real(**kwargs)
            captured.append(client)
            return client

        monkeypatch.setattr(discovery, "artifact_http_client", _record)
        from tests.http_capture import capture_http

        with capture_http() as router:
            router.get("http://192.168.5.149:8001/v1/models").mock(
                return_value=httpx.Response(200, json={"data": [{"id": "m-1"}]})
            )
            await discovery.discover_models(
                discovery_format="anthropic", base_url="http://192.168.5.149:8001", api_key="k"
            )

        assert captured
        transport = captured[0]._transport_for_url(httpx.URL("http://192.168.5.149:8001/v1/models"))
        assert getattr(getattr(transport, "_pool", None), "_proxy_url", None) is None

    async def test_public_base_url_keeps_proxy(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        captured: list[httpx.AsyncClient] = []
        real = discovery.artifact_http_client

        def _record(**kwargs: Any) -> Any:
            client = real(**kwargs)
            captured.append(client)
            return client

        monkeypatch.setattr(discovery, "artifact_http_client", _record)
        from tests.http_capture import capture_http

        with capture_http() as router:
            router.get("https://api.anthropic.com/v1/models").mock(
                return_value=httpx.Response(200, json={"data": [{"id": "m-1"}]})
            )
            await discovery.discover_models(
                discovery_format="anthropic", base_url="https://api.anthropic.com", api_key="k"
            )

        assert captured
        # 回归：公网 anthropic 网关仍走系统代理。
        transport = captured[0]._transport_for_url(httpx.URL("https://api.anthropic.com/v1/models"))
        assert getattr(getattr(transport, "_pool", None), "_proxy_url", None) is not None
