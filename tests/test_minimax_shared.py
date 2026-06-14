"""lib.minimax_shared 纯函数单元测试（不打真实 HTTP）。"""

from __future__ import annotations

import pytest

from lib.minimax_shared import (
    MINIMAX_BASE_URL,
    MINIMAX_INTL_BASE_URL,
    minimax_headers,
    minimax_text_base_url,
    resolve_minimax_api_key,
)


class TestBaseUrlDerivation:
    def test_default_is_domestic(self):
        assert minimax_text_base_url(None) == MINIMAX_BASE_URL
        assert MINIMAX_BASE_URL == "https://api.minimaxi.com/v1"

    def test_override_to_intl(self):
        assert minimax_text_base_url(MINIMAX_INTL_BASE_URL) == "https://api.minimax.io/v1"

    def test_host_only_gets_v1_suffix(self):
        # 用户只填 host，派生时补 /v1
        assert minimax_text_base_url("https://api.minimax.io") == "https://api.minimax.io/v1"

    def test_full_v1_base_is_idempotent(self):
        assert minimax_text_base_url("https://api.minimaxi.com/v1") == "https://api.minimaxi.com/v1"

    def test_trailing_slash_stripped(self):
        assert minimax_text_base_url("https://api.minimax.io/v1/") == "https://api.minimax.io/v1"
        assert minimax_text_base_url("https://api.minimax.io/") == "https://api.minimax.io/v1"

    def test_whitespace_falls_back_to_default(self):
        # 纯空白 base_url 是真值会绕过 or，须 strip 后回落默认 host，
        # 不能 strip 成空串派生出 "/v1" 这类非法相对 URL
        assert minimax_text_base_url("   ") == MINIMAX_BASE_URL


class TestApiKeyResolution:
    def test_strips_and_returns(self):
        assert resolve_minimax_api_key("  sk-abc  ") == "sk-abc"

    def test_missing_raises(self):
        with pytest.raises(ValueError):
            resolve_minimax_api_key(None)

    def test_blank_raises(self):
        # 不走 env fallback：缺失即明确报错
        with pytest.raises(ValueError):
            resolve_minimax_api_key("   ")


class TestHeaders:
    def test_bearer_and_content_type(self):
        h = minimax_headers("sk-abc")
        assert h["Authorization"] == "Bearer sk-abc"
        assert h["Content-Type"] == "application/json"
