"""create_openai_client 客户端工厂行为。"""

import asyncio

import httpx

from lib.backends.openai_shared import create_openai_client
from lib.config.url_utils import OFFICIAL_OPENAI_BASE_URL


def _proxy_url(transport: object) -> object | None:
    """从 httpx transport 取出代理 URL（无代理为 None）。"""
    return getattr(getattr(transport, "_pool", None), "_proxy_url", None)


class TestCreateOpenAIClientBaseURL:
    """base_url 唯一来源为 DB 配置：空值兜死官方端点，环境变量不得静默覆盖路由。"""

    def test_empty_base_url_ignores_env_var(self, monkeypatch):
        # AsyncOpenAI 对空 base_url 会回落读 OPENAI_BASE_URL，工厂须显式兜死官方值
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
        client = create_openai_client(api_key="x", base_url=None)
        assert str(client.base_url).rstrip("/") == OFFICIAL_OPENAI_BASE_URL.rstrip("/")

    def test_whitespace_base_url_ignores_env_var(self, monkeypatch):
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
        client = create_openai_client(api_key="x", base_url="   ")
        assert str(client.base_url).rstrip("/") == OFFICIAL_OPENAI_BASE_URL.rstrip("/")

    def test_explicit_base_url_preserved(self, monkeypatch):
        # 显式 base_url 原样透传，不被环境变量或官方默认值篡改
        monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
        client = create_openai_client(api_key="x", base_url="https://vllm.internal:8000/v1")
        assert str(client.base_url).rstrip("/") == "https://vllm.internal:8000/v1"


class TestCreateOpenAIClientProxyBypass:
    """内网 base_url 注入绕代理 http_client，公网 base_url 维持 SDK 默认走系统代理。"""

    def test_private_base_url_bypasses_proxy(self, monkeypatch):
        # 内网兼容端点：注入的 AsyncClient 对内网 host 直连、对公网 host 仍走代理。
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        client = create_openai_client(api_key="x", base_url="http://192.168.5.149:8001/v1")
        try:
            httpx_client = client._client
            assert isinstance(httpx_client, httpx.AsyncClient)
            priv = httpx_client._transport_for_url(httpx.URL("http://192.168.5.149:8001/x"))
            pub = httpx_client._transport_for_url(httpx.URL("https://api.openai.com/v1"))
            assert _proxy_url(priv) is None
            assert _proxy_url(pub) is not None
        finally:
            asyncio.run(client.close())

    def test_public_base_url_keeps_proxy(self, monkeypatch):
        # 公网 base_url：未注入自定义 mounts，内网 host 也走系统代理（即维持 SDK 默认）。
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        client = create_openai_client(api_key="x", base_url="https://api.openai.com/v1")
        try:
            transport = client._client._transport_for_url(httpx.URL("http://192.168.5.149:8001/x"))
            assert _proxy_url(transport) is not None
        finally:
            asyncio.run(client.close())

    def test_backfilled_official_base_url_keeps_proxy(self, monkeypatch):
        # base_url 为空回填官方端点 → 判为公网不绕代理，公网 host 仍走系统代理。
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        client = create_openai_client(api_key="x", base_url=None)
        try:
            assert str(client.base_url).rstrip("/") == OFFICIAL_OPENAI_BASE_URL.rstrip("/")
            transport = client._client._transport_for_url(httpx.URL("https://api.openai.com/v1"))
            assert _proxy_url(transport) is not None
        finally:
            asyncio.run(client.close())
