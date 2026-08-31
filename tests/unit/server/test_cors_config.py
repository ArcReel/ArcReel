"""``CORS_ORIGINS`` 的解析规则；应用中间件与远程 MCP 挂载共用这一份结果。"""

from __future__ import annotations

import os
from unittest.mock import patch

from server.cors_config import resolve_cors_policy


class TestCorsOriginsParsing:
    def test_unset_defaults_to_wildcard_no_credentials(self):
        with patch.dict(os.environ, {}, clear=True):
            assert resolve_cors_policy() == (["*"], False)

    def test_explicit_wildcard_keeps_credentials_off(self):
        assert resolve_cors_policy("*") == (["*"], False)

    def test_empty_string_falls_back_to_wildcard(self):
        assert resolve_cors_policy("   ") == (["*"], False)

    def test_single_origin_enables_credentials(self):
        assert resolve_cors_policy("http://localhost:5173") == (["http://localhost:5173"], True)

    def test_multiple_origins_parsed_and_stripped(self):
        origins, credentials = resolve_cors_policy(" http://a.example.com , http://b.example.com,http://c.example.com ")
        assert origins == ["http://a.example.com", "http://b.example.com", "http://c.example.com"]
        assert credentials is True

    def test_empty_segments_dropped(self):
        assert resolve_cors_policy("http://a,,http://b,") == (["http://a", "http://b"], True)

    def test_mixed_wildcard_with_specific_origin_collapses_to_wildcard(self):
        """`*` 出现在白名单里时，整体降级为通配 + credentials=False，
        避免 Starlette `RuntimeError` (CORS spec 禁止通配 + credentials 共存)。"""
        assert resolve_cors_policy("http://localhost:5173, *") == (["*"], False)
