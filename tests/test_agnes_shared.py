"""lib.agnes_shared 纯函数单元测试（不打真实 HTTP）。"""

from __future__ import annotations

import pytest

from lib.agnes_shared import (
    AGNES_BASE_URL,
    agnes_base_url,
    agnes_headers,
    resolve_agnes_api_key,
)


class TestBaseUrlDerivation:
    def test_default_base(self):
        assert agnes_base_url(None) == AGNES_BASE_URL
        assert AGNES_BASE_URL == "https://apihub.agnes-ai.com/v1"

    def test_host_only_gets_v1_suffix(self):
        # 用户只填 host，派生时补 /v1
        assert agnes_base_url("https://apihub.agnes-ai.com") == "https://apihub.agnes-ai.com/v1"

    def test_full_v1_base_is_idempotent(self):
        assert agnes_base_url("https://apihub.agnes-ai.com/v1") == "https://apihub.agnes-ai.com/v1"

    def test_trailing_slash_stripped(self):
        assert agnes_base_url("https://apihub.agnes-ai.com/v1/") == "https://apihub.agnes-ai.com/v1"
        assert agnes_base_url("https://apihub.agnes-ai.com/") == "https://apihub.agnes-ai.com/v1"

    def test_custom_relay_host(self):
        assert agnes_base_url("https://relay.example.com") == "https://relay.example.com/v1"

    def test_whitespace_falls_back_to_default(self):
        # 纯空白 base_url 是真值会绕过 or，须 strip 后回落默认 host，
        # 不能 strip 成空串派生出 "/v1" 这类非法相对 URL
        assert agnes_base_url("   ") == AGNES_BASE_URL


class TestApiKeyResolution:
    def test_strips_and_returns(self):
        assert resolve_agnes_api_key("  sk-abc  ") == "sk-abc"

    def test_missing_raises(self):
        with pytest.raises(ValueError):
            resolve_agnes_api_key(None)

    def test_blank_raises(self):
        # 不走 env fallback：缺失即明确报错
        with pytest.raises(ValueError):
            resolve_agnes_api_key("   ")


class TestHeaders:
    def test_bearer_and_content_type(self):
        h = agnes_headers("sk-abc")
        assert h["Authorization"] == "Bearer sk-abc"
        assert h["Content-Type"] == "application/json"

    def test_strips_whitespace_key(self):
        # 复用 resolve_agnes_api_key：构造头前先归一化，不把首尾空白带进 Bearer
        assert agnes_headers("  sk-abc  ")["Authorization"] == "Bearer sk-abc"

    def test_blank_key_raises(self):
        # 空白 key 本地即 raise，不拼出 "Bearer " 拖到请求期才 401
        with pytest.raises(ValueError):
            agnes_headers("   ")
