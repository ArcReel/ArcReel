"""lib.minimax_shared 纯函数单元测试（不打真实 HTTP）。"""

from __future__ import annotations

import pytest

from lib.minimax_shared import (
    MINIMAX_BASE_URL,
    MINIMAX_INTL_BASE_URL,
    extract_image_base64,
    extract_image_url,
    minimax_failure_reason,
    minimax_headers,
    minimax_text_base_url,
    resolve_minimax_api_key,
    safe_body_for_log,
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


class TestExtractImageUrl:
    def test_first_url(self):
        payload = {"data": {"image_urls": ["https://a/1.png", "https://a/2.png"]}}
        assert extract_image_url(payload) == "https://a/1.png"

    def test_missing_returns_none(self):
        assert extract_image_url({"data": {}}) is None
        assert extract_image_url({}) is None

    def test_non_list_or_empty_returns_none(self):
        assert extract_image_url({"data": {"image_urls": "not-a-list"}}) is None
        assert extract_image_url({"data": {"image_urls": [""]}}) is None

    def test_non_dict_data_tolerated(self):
        assert extract_image_url({"data": None}) is None
        assert extract_image_url({"data": ["x"]}) is None


class TestExtractImageBase64:
    def test_first_base64(self):
        payload = {"data": {"image_base64": ["AAAA", "BBBB"]}}
        assert extract_image_base64(payload) == "AAAA"

    def test_missing_returns_none(self):
        assert extract_image_base64({"data": {}}) is None
        assert extract_image_base64({}) is None


class TestFailureReason:
    def test_success_status_zero_returns_none(self):
        assert minimax_failure_reason({"base_resp": {"status_code": 0, "status_msg": "success"}}) is None

    def test_missing_base_resp_returns_none(self):
        assert minimax_failure_reason({}) is None

    def test_nonzero_status_returns_reason(self):
        reason = minimax_failure_reason({"base_resp": {"status_code": 1004, "status_msg": "invalid api key"}})
        assert reason is not None
        assert "1004" in reason
        assert "invalid api key" in reason


class TestSafeBodyForLog:
    def test_strips_prompt_base64_url(self):
        body = {
            "model": "image-01",
            "prompt": "a very long prompt describing the scene",
            "width": 1152,
            "height": 2048,
            "response_format": "url",
            "n": 1,
            "prompt_optimizer": False,
            "seed": 7,
            "subject_reference": [{"type": "character", "image_file": "data:image/png;base64,AAAA"}],
        }
        view = safe_body_for_log(body)
        # 白名单标量保留
        assert view["model"] == "image-01"
        assert view["width"] == 1152
        assert view["height"] == 2048
        assert view["response_format"] == "url"
        assert view["n"] == 1
        assert view["prompt_optimizer"] is False
        assert view["seed"] == 7
        # prompt 仅长度、subject_reference 仅计数；base64/URL 不出现
        assert view["prompt_len"] == len(body["prompt"])
        assert "prompt" not in view
        assert view["subject_reference"] == "<1 ref>"
        assert "data:image" not in repr(view)

    def test_omits_absent_scalars(self):
        view = safe_body_for_log({"model": "image-01", "prompt": "x"})
        assert view == {"model": "image-01", "prompt_len": 1}
