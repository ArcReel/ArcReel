"""URL 规范化工具单元测试。"""

from __future__ import annotations

import pytest

from lib.config.url_utils import (
    InvalidAnthropicBaseUrlError,
    anthropic_endpoint_url,
    normalize_anthropic_base_url,
    strip_base_url_version,
    validate_anthropic_base_url,
)


class TestNormalizeAnthropicBaseUrl:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("https://api.anthropic.com", "https://api.anthropic.com"),
            ("https://x/anthropic/", "https://x/anthropic"),
            ("https://x/anthropic/v1/messages", "https://x/anthropic"),
            ("https://x/anthropic/v1/messages/", "https://x/anthropic"),
            ("https://x//v1/messages", "https://x"),
            ("  https://x  ", "https://x"),
        ],
    )
    def test_normalizes(self, raw: str, expected: str) -> None:
        assert normalize_anthropic_base_url(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            "https://x/anthropic/v1",
            "https://x/messages",
            "https://x/v1beta",
        ],
    )
    def test_keeps_everything_else(self, raw: str) -> None:
        """不再剥 /vN、单独的 /messages，也不识别 anthropic 子路径。"""
        assert normalize_anthropic_base_url(raw) == raw


class TestValidateAnthropicBaseUrl:
    def test_returns_normalized_value(self) -> None:
        assert validate_anthropic_base_url(" https://x/anthropic/v1/messages ") == "https://x/anthropic"

    @pytest.mark.parametrize(
        "raw",
        [
            "https://relay.example.com/anthropic?api_key=sk-x",
            "https://x/a#frag",
            "https://x/a?",
            "https://x/a#",
            "https://u:p@x/a",
            "x.example.com",
            "ftp://x/a",
            "https://x:notaport/a",
            "   ",
        ],
    )
    def test_rejects(self, raw: str) -> None:
        with pytest.raises(InvalidAnthropicBaseUrlError):
            validate_anthropic_base_url(raw)

    def test_message_does_not_echo_input(self) -> None:
        with pytest.raises(InvalidAnthropicBaseUrlError) as exc_info:
            validate_anthropic_base_url("https://x/anthropic?api_key=sk-leaked")
        assert "sk-leaked" not in str(exc_info.value)


class TestAnthropicEndpointUrl:
    @pytest.mark.parametrize(
        ("base", "expected"),
        [
            ("https://x", "https://x/v1/messages"),
            ("https://x/anthropic", "https://x/anthropic/v1/messages"),
            ("https://api.deepseek.com/anthropic/v1", "https://api.deepseek.com/anthropic/v1/v1/messages"),
            # 已转义的路径分隔符逐字保留：运行时 CLI 也是字符串拼接，探测不能打到另一个端点。
            ("https://example.com/gateway%2Ftenant", "https://example.com/gateway%2Ftenant/v1/messages"),
        ],
    )
    def test_appends_to_path(self, base: str, expected: str) -> None:
        assert anthropic_endpoint_url(base, "/v1/messages") == expected

    def test_appends_models_path(self) -> None:
        assert anthropic_endpoint_url("https://api.minimaxi.com/anthropic", "/v1/models") == (
            "https://api.minimaxi.com/anthropic/v1/models"
        )


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://relay.test/v1", "https://relay.test"),
        ("https://relay.test/v2beta", "https://relay.test"),
        ("https://relay.test/v1.5", "https://relay.test"),
        ("https://relay.test/api", "https://relay.test/api"),
        # 前端 RequestAddresses 的 VERSION_SUFFIX 只认 ASCII 数字，两侧用例表同口径
        ("https://relay.test/v\uff11", "https://relay.test/v\uff11"),
    ],
)
def test_strip_version_rule_only_strips_ascii_version_suffix(base_url: str, expected: str):
    assert strip_base_url_version(base_url) == expected
