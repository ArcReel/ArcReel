"""base_url 归一化工具函数测试。"""

from lib.config.url_utils import normalize_base_url


class TestNormalizeBaseUrl:
    def test_none_returns_none(self):
        assert normalize_base_url(None) is None

    def test_empty_string_returns_none(self):
        assert normalize_base_url("") is None

    def test_whitespace_only_returns_none(self):
        assert normalize_base_url("   ") is None

    def test_adds_trailing_slash(self):
        assert normalize_base_url("https://proxy.example.com/v1") == "https://proxy.example.com/v1/"

    def test_preserves_existing_trailing_slash(self):
        assert normalize_base_url("https://proxy.example.com/v1/") == "https://proxy.example.com/v1/"

    def test_strips_whitespace(self):
        assert normalize_base_url("  https://proxy.example.com/v1  ") == "https://proxy.example.com/v1/"

    def test_plain_domain(self):
        assert normalize_base_url("https://example.com") == "https://example.com/"
