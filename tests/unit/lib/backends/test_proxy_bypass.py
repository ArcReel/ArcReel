"""按目标地址决定出站是否绕系统代理：判定矩阵、host 解析、mounts 构造与 SDK 工厂。

内网 / 私有目标直连（绕代理），公网目标走系统代理。判定一律用 ``ipaddress`` 严格判网段，含
Tailscale CGNAT（``100.64/10``）与几段边界（``192.0.2.1`` / ``172.15`` vs ``172.16`` /
``100.63`` vs ``100.64``）。SDK 工厂（OpenAI 同步 / 异步 ``http_client`` / genai
``client_args``）在内网 base_url 时注入绕代理配置、公网时返回 ``None`` 以维持 SDK 默认。
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from lib.backends.proxy_bypass import (
    genai_client_args,
    host_from_base_url,
    is_private_host,
    openai_async_proxy_http_client,
    openai_proxy_http_client,
    proxy_bypass_mounts,
)


class TestIsPrivateHost:
    @pytest.mark.parametrize(
        "host",
        [
            "192.168.5.149",  # RFC 1918
            "10.0.0.5",  # RFC 1918
            "172.16.0.1",  # RFC 1918 下界
            "172.31.255.255",  # RFC 1918 上界
            "127.0.0.1",  # 环回
            "100.64.0.1",  # CGNAT 下界（Tailscale）
            "100.127.255.255",  # CGNAT 上界
            "localhost",
            "foo.local",
            "bar.ts.net",  # Tailscale MagicDNS
            "svc.localhost",
            "::1",  # IPv6 环回
            "::ffff:192.168.1.1",  # IPv4-mapped 私网
            "fd12:3456::1",  # ULA
            "fe80::1",  # 链路本地
        ],
    )
    def test_private_hosts_bypass_proxy(self, host: str):
        assert is_private_host(host) is True

    @pytest.mark.parametrize(
        "host",
        [
            "api.openai.com",
            "generativelanguage.googleapis.com",
            "1.1.1.1",
            "192.0.2.1",  # TEST-NET-1，非私网
            "172.15.0.1",  # 172.16/12 下界之外
            "172.32.0.1",  # 172.16/12 上界之外
            "100.63.255.255",  # CGNAT 下界之外
            "100.128.0.0",  # CGNAT 上界之外
            "2606:4700:4700::1111",  # 公网 IPv6
            "example.ts.net.evil.com",  # 后缀不匹配（不以 .ts.net 结尾）
            "notlocalhost",
        ],
    )
    def test_public_hosts_use_proxy(self, host: str):
        assert is_private_host(host) is False

    def test_empty_host_is_not_private(self):
        assert is_private_host("") is False


class TestHostFromBaseUrl:
    @pytest.mark.parametrize(
        ("base_url", "expected"),
        [
            ("http://192.168.5.149:8001/v1", "192.168.5.149"),
            ("https://api.openai.com/v1", "api.openai.com"),
            ("http://[::1]:8188/view", "::1"),
            ("HTTP://192.168.5.149:8001", "192.168.5.149"),  # 归一化小写
        ],
    )
    def test_extracts_host(self, base_url: str, expected: str):
        assert host_from_base_url(base_url) == expected

    @pytest.mark.parametrize("base_url", [None, "", "nope", "192.168.5.149:8001"])
    def test_unparseable_returns_none(self, base_url: str | None):
        # 空 / 缺 scheme → 解析不出 host → None（判不准当公网，fail-safe）。
        assert host_from_base_url(base_url) is None


class TestProxyBypassMounts:
    def test_private_base_url_yields_all_scheme_mount(self):
        assert proxy_bypass_mounts("http://192.168.5.149:8001/v1") == {"all://192.168.5.149": None}

    @pytest.mark.parametrize(
        "base_url",
        ["https://api.openai.com/v1", None, "", "nope"],
    )
    def test_public_or_unparseable_yields_empty(self, base_url: str | None):
        assert proxy_bypass_mounts(base_url) == {}


class TestOpenaiProxyHttpClient:
    def test_private_base_url_returns_client_bypassing_proxy(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        client = openai_proxy_http_client("http://192.168.5.149:8001/v1")
        try:
            assert isinstance(client, httpx.Client)
            # 内网 host 的 transport 不走代理，公网 host 仍走代理（client 本身 trust_env 默认 True）。
            priv = client._transport_for_url(httpx.URL("http://192.168.5.149:8001/x"))
            pub = client._transport_for_url(httpx.URL("https://api.openai.com/v1"))
            assert _proxy_url(priv) is None
            assert _proxy_url(pub) is not None
        finally:
            if client is not None:
                client.close()

    @pytest.mark.parametrize("base_url", ["https://api.openai.com/v1", None, ""])
    def test_public_or_empty_base_url_returns_none(self, base_url: str | None):
        assert openai_proxy_http_client(base_url) is None


class TestOpenaiAsyncProxyHttpClient:
    """给 AsyncOpenAI 用的异步工厂：与同步版对称，内网返回 AsyncClient、公网返回 None。"""

    def test_private_base_url_returns_async_client_bypassing_proxy(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7890")
        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7890")
        client = openai_async_proxy_http_client("http://192.168.5.149:8001/v1")
        try:
            # 类型必须是异步 client，不能是同步 httpx.Client（AsyncOpenAI 要求异步）。
            assert isinstance(client, httpx.AsyncClient)
            # 内网 host 的 transport 不走代理，公网 host 仍走代理（trust_env 默认 True）。
            priv = client._transport_for_url(httpx.URL("http://192.168.5.149:8001/x"))
            pub = client._transport_for_url(httpx.URL("https://api.openai.com/v1"))
            assert _proxy_url(priv) is None
            assert _proxy_url(pub) is not None
        finally:
            if client is not None:
                asyncio.run(client.aclose())

    @pytest.mark.parametrize("base_url", ["https://api.openai.com/v1", None, ""])
    def test_public_or_empty_base_url_returns_none(self, base_url: str | None):
        assert openai_async_proxy_http_client(base_url) is None


class TestGenaiClientArgs:
    def test_private_base_url_injects_mounts(self):
        args = genai_client_args("http://192.168.5.149:8001")
        assert args == {"mounts": {"all://192.168.5.149": None}}

    @pytest.mark.parametrize("base_url", ["https://generativelanguage.googleapis.com", None, ""])
    def test_public_or_empty_base_url_returns_none(self, base_url: str | None):
        assert genai_client_args(base_url) is None


def _proxy_url(transport: object) -> object | None:
    """从 httpx transport 取出代理 URL（无代理为 None）。"""
    return getattr(getattr(transport, "_pool", None), "_proxy_url", None)
