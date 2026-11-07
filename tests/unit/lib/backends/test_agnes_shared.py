"""Pure lib.backends.agnes_shared tests, without real HTTP."""

from __future__ import annotations

import pytest

from lib.backends.agnes_shared import (
    AGNES_BASE_URL,
    agnes_base_url,
    agnes_headers,
    agnes_host,
    resolve_agnes_api_key,
)


class TestBaseUrlDerivation:
    def test_default_base(self):
        assert agnes_base_url(None) == AGNES_BASE_URL
        assert AGNES_BASE_URL == "https://apihub.agnes-ai.com/v1"

    def test_host_only_gets_v1_suffix(self):
        # Host-only URLs gain /v1.
        assert agnes_base_url("https://apihub.agnes-ai.com") == "https://apihub.agnes-ai.com/v1"

    def test_full_v1_base_is_idempotent(self):
        assert agnes_base_url("https://apihub.agnes-ai.com/v1") == "https://apihub.agnes-ai.com/v1"

    def test_trailing_slash_stripped(self):
        assert agnes_base_url("https://apihub.agnes-ai.com/v1/") == "https://apihub.agnes-ai.com/v1"
        assert agnes_base_url("https://apihub.agnes-ai.com/") == "https://apihub.agnes-ai.com/v1"

    def test_custom_relay_host(self):
        assert agnes_base_url("https://relay.example.com") == "https://relay.example.com/v1"

    def test_whitespace_falls_back_to_default(self):
        # Truthy whitespace base_url bypasses or; strip then fall back to default hosts,
        # never derive invalid relative /v1 from empty strings.
        assert agnes_base_url("   ") == AGNES_BASE_URL


class TestHostDerivation:
    """agnes_host shares agnes_base_url normalization but omits /v1; gateway output queries use
    root-level /agnesapi and require that host form."""

    def test_default_host(self):
        assert agnes_host(None) == "https://apihub.agnes-ai.com"

    def test_strips_v1_suffix(self):
        assert agnes_host("https://apihub.agnes-ai.com/v1") == "https://apihub.agnes-ai.com"
        assert agnes_host("https://apihub.agnes-ai.com/v1/") == "https://apihub.agnes-ai.com"

    def test_host_only_unchanged(self):
        assert agnes_host("https://apihub.agnes-ai.com") == "https://apihub.agnes-ai.com"
        assert agnes_host("https://relay.example.com/") == "https://relay.example.com"

    def test_whitespace_falls_back_to_default(self):
        assert agnes_host("   ") == "https://apihub.agnes-ai.com"


class TestApiKeyResolution:
    def test_strips_and_returns(self):
        assert resolve_agnes_api_key("  sk-abc  ") == "sk-abc"

    def test_missing_raises(self):
        with pytest.raises(ValueError, match=r"请到系统配置页填写 Agnes API Key"):
            resolve_agnes_api_key(None)

    def test_blank_raises(self):
        # No environment fallback; missing values fail explicitly.
        with pytest.raises(ValueError, match=r"请到系统配置页填写 Agnes API Key"):
            resolve_agnes_api_key("   ")


class TestHeaders:
    def test_bearer_and_content_type(self):
        h = agnes_headers("sk-abc")
        assert h["Authorization"] == "Bearer sk-abc"
        assert h["Content-Type"] == "application/json"

    def test_strips_whitespace_key(self):
        # Reuse resolve_agnes_api_key before headers, trimming Bearer outer whitespace.
        assert agnes_headers("  sk-abc  ")["Authorization"] == "Bearer sk-abc"

    def test_blank_key_raises(self):
        # Whitespace keys fail locally, never produce empty Bearer and defer 401 until requests.
        with pytest.raises(ValueError, match=r"请到系统配置页填写 Agnes API Key"):
            agnes_headers("   ")
