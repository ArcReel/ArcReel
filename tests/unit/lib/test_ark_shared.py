"""lib.ark_shared 纯函数单元测试（不打真实 HTTP）。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lib.ark_shared import ARK_BASE_URL, ark_base_url, create_ark_client, resolve_ark_api_key


class TestBaseUrlNormalization:
    def test_default_base(self):
        assert ark_base_url(None) == ARK_BASE_URL

    def test_trailing_slash_stripped(self):
        assert ark_base_url("https://ark.cn-beijing.volces.com/api/v3/") == ARK_BASE_URL

    def test_multiple_trailing_slashes_stripped(self):
        assert ark_base_url("https://ark.cn-beijing.volces.com/api/v3//") == ARK_BASE_URL

    def test_no_trailing_slash_is_idempotent(self):
        assert ark_base_url(ARK_BASE_URL) == ARK_BASE_URL

    def test_whitespace_falls_back_to_default(self):
        # 纯空白 base_url 是真值会绕过 or，须 strip 后回落默认值
        assert ark_base_url("   ") == ARK_BASE_URL

    def test_surrounding_whitespace_stripped(self):
        assert ark_base_url("  https://ark.cn-beijing.volces.com/api/v3/  ") == ARK_BASE_URL

    def test_non_standard_path_preserved(self):
        # ark-agent-plan 走 /api/plan/v3 这类非标准路径，只去尾斜杠，不按已知后缀重建，
        # 否则会拼出 .../api/plan/v3/api/v3 这种错误 URL。
        custom = "https://ark.cn-beijing.volces.com/api/plan/v3"
        assert ark_base_url(custom) == custom
        assert ark_base_url(custom + "/") == custom


class TestApiKeyResolution:
    def test_strips_and_returns(self):
        assert resolve_ark_api_key("  sk-abc  ") == "sk-abc"

    def test_missing_raises(self):
        with pytest.raises(ValueError):
            resolve_ark_api_key(None)

    def test_blank_raises(self):
        with pytest.raises(ValueError):
            resolve_ark_api_key("   ")


class TestCreateArkClient:
    def test_trailing_slash_normalized_before_client_construction(self):
        mock_ark_cls = MagicMock()
        with patch("volcenginesdkarkruntime.Ark", mock_ark_cls):
            create_ark_client(api_key="k", base_url="https://ark.cn-beijing.volces.com/api/v3/")
        mock_ark_cls.assert_called_once_with(base_url=ARK_BASE_URL, api_key="k")

    def test_default_base_url_when_omitted(self):
        mock_ark_cls = MagicMock()
        with patch("volcenginesdkarkruntime.Ark", mock_ark_cls):
            create_ark_client(api_key="k")
        mock_ark_cls.assert_called_once_with(base_url=ARK_BASE_URL, api_key="k")
