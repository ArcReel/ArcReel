"""lib.minimax_shared 纯函数单元测试（不打真实 HTTP）。"""

from __future__ import annotations

import base64

import pytest

from lib.minimax_shared import (
    MINIMAX_BASE_URL,
    MINIMAX_INTL_BASE_URL,
    MINIMAX_STATUS_FAIL,
    MINIMAX_STATUS_SUCCESS,
    extract_minimax_download_url,
    extract_minimax_file_id,
    extract_minimax_video_task_id,
    image_to_data_uri,
    is_minimax_video_terminal,
    minimax_headers,
    minimax_text_base_url,
    minimax_video_base_url,
    minimax_video_failure_reason,
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
