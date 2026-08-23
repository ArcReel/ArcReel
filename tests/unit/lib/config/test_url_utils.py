"""URL 规范化工具单元测试。"""

from __future__ import annotations

import pytest

from lib.config.url_utils import ensure_anthropic_base_url


class TestEnsureAnthropicBaseUrl:
    def test_official_root_unchanged(self):
        assert ensure_anthropic_base_url("https://api.anthropic.com") == "https://api.anthropic.com"

    def test_strips_trailing_v1(self):
        assert ensure_anthropic_base_url("https://example.com/v1") == "https://example.com"

    def test_strips_trailing_v1_messages(self):
        assert ensure_anthropic_base_url("https://example.com/v1/messages") == "https://example.com"

    def test_strips_trailing_slash_after_v1_messages(self):
        assert ensure_anthropic_base_url("https://example.com/v1/messages/") == "https://example.com"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_blank_returns_none(self, value):
        assert ensure_anthropic_base_url(value) is None
