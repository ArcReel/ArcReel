"""lib.minimax_shared 纯函数单元测试（不打真实 HTTP）。"""

from __future__ import annotations

import base64

import pytest

from lib.minimax_shared import (
    MINIMAX_BASE_URL,
    MINIMAX_INTL_BASE_URL,
    MINIMAX_STATUS_FAIL,
    MINIMAX_STATUS_SUCCESS,
    extract_image_base64,
    extract_image_url,
    extract_minimax_download_url,
    extract_minimax_file_id,
    extract_minimax_video_task_id,
    image_to_data_uri,
    is_minimax_video_terminal,
    minimax_failure_reason,
    minimax_headers,
    minimax_text_base_url,
    minimax_video_base_url,
    minimax_video_failure_reason,
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

    def test_non_dict_payload_tolerated(self):
        # 中转代理 / 错误响应可能返回非 dict 顶层（list / str）：不得抛 AttributeError
        assert extract_image_url(["not", "a", "dict"]) is None
        assert extract_image_url("error") is None
        assert extract_image_url(None) is None


class TestExtractImageBase64:
    def test_first_base64(self):
        payload = {"data": {"image_base64": ["AAAA", "BBBB"]}}
        assert extract_image_base64(payload) == "AAAA"

    def test_missing_returns_none(self):
        assert extract_image_base64({"data": {}}) is None
        assert extract_image_base64({}) is None

    def test_non_dict_payload_tolerated(self):
        # 顶层非 dict（代理 / 错误响应）一律回 None，不抛 AttributeError
        assert extract_image_base64(["x"]) is None
        assert extract_image_base64(None) is None


class TestFailureReason:
    def test_success_status_zero_returns_none(self):
        assert minimax_failure_reason({"base_resp": {"status_code": 0, "status_msg": "success"}}) is None

    def test_missing_base_resp_returns_none(self):
        assert minimax_failure_reason({}) is None

    def test_non_dict_payload_returns_none(self):
        # 顶层非 dict（代理 / 错误响应）不抛 AttributeError，按"无业务错误"回 None
        assert minimax_failure_reason(["x"]) is None
        assert minimax_failure_reason("boom") is None

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

    def test_empty_subject_reference_omitted(self):
        # 空列表显式跳过：不漏 subject_reference 字段（日志脱敏是安全关键路径，边界须显式覆盖）
        view = safe_body_for_log({"model": "image-01", "subject_reference": []})
        assert "subject_reference" not in view
        assert view == {"model": "image-01"}


class TestVideoBaseUrl:
    def test_shares_v1_base_with_text(self):
        # 视频原生端点与文本同走单 /v1 base
        assert minimax_video_base_url(None) == MINIMAX_BASE_URL
        assert minimax_video_base_url(MINIMAX_INTL_BASE_URL) == "https://api.minimax.io/v1"
        assert minimax_video_base_url("https://api.minimax.io") == "https://api.minimax.io/v1"


class TestExtractVideoTaskId:
    def test_extracts_top_level_task_id(self):
        payload = {"task_id": "12345", "base_resp": {"status_code": 0, "status_msg": "success"}}
        assert extract_minimax_video_task_id(payload) == "12345"

    def test_missing_task_id_raises_with_base_resp_reason(self):
        payload = {"base_resp": {"status_code": 1004, "status_msg": "auth failed"}}
        with pytest.raises(RuntimeError, match="1004"):
            extract_minimax_video_task_id(payload)

    def test_missing_task_id_no_base_resp_raises(self):
        with pytest.raises(RuntimeError):
            extract_minimax_video_task_id({})


class TestVideoStateMachine:
    def test_processing_not_terminal(self):
        for status in ("Preparing", "Queueing", "Processing"):
            assert is_minimax_video_terminal({"status": status}) is False
            assert minimax_video_failure_reason({"status": status}) is None

    def test_success_is_terminal(self):
        payload = {"status": MINIMAX_STATUS_SUCCESS, "file_id": "f-1"}
        assert is_minimax_video_terminal(payload) is True
        assert minimax_video_failure_reason(payload) is None

    def test_fail_is_terminal_and_reports_reason(self):
        payload = {"status": MINIMAX_STATUS_FAIL, "base_resp": {"status_code": 2013, "status_msg": "invalid params"}}
        assert is_minimax_video_terminal(payload) is True
        reason = minimax_video_failure_reason(payload)
        assert reason is not None
        assert "2013" in reason

    def test_query_base_resp_hard_error_is_failure(self):
        # 查询接口本身失败（如 task_id 不存在）：base_resp 非 0 即终态失败
        payload = {"status": "", "base_resp": {"status_code": 1004, "status_msg": "invalid task_id"}}
        assert minimax_video_failure_reason(payload) is not None

    def test_non_dict_payload_tolerated(self):
        assert is_minimax_video_terminal({"status": None}) is False


class TestExtractFileIdAndDownloadUrl:
    def test_extract_file_id(self):
        assert extract_minimax_file_id({"status": "Success", "file_id": "f-99"}) == "f-99"

    def test_extract_file_id_missing_raises(self):
        with pytest.raises(RuntimeError):
            extract_minimax_file_id({"status": "Success"})

    def test_extract_download_url(self):
        payload = {"file": {"file_id": "f-1", "download_url": "https://x/o.mp4"}}
        assert extract_minimax_download_url(payload) == "https://x/o.mp4"

    def test_extract_download_url_missing_raises(self):
        with pytest.raises(RuntimeError):
            extract_minimax_download_url({"file": {"file_id": "f-1"}})

    def test_extract_download_url_non_dict_file_tolerated(self):
        with pytest.raises(RuntimeError):
            extract_minimax_download_url({"file": None})


class TestImageToDataUri:
    def test_png_data_uri(self, tmp_path):
        img = tmp_path / "x.png"
        img.write_bytes(b"\x89PNG\r\n")
        uri = image_to_data_uri(img)
        assert uri.startswith("data:image/png;base64,")
        assert base64.b64decode(uri.split(",", 1)[1]) == b"\x89PNG\r\n"

    def test_jpg_mime(self, tmp_path):
        img = tmp_path / "x.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        assert image_to_data_uri(img).startswith("data:image/jpeg;base64,")
