"""Pure lib.backends.minimax_shared tests, without real HTTP."""

from __future__ import annotations

import base64

import pytest

from lib.backends.minimax_shared import (
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
        # Host-only URLs gain /v1.
        assert minimax_text_base_url("https://api.minimax.io") == "https://api.minimax.io/v1"

    def test_full_v1_base_is_idempotent(self):
        assert minimax_text_base_url("https://api.minimaxi.com/v1") == "https://api.minimaxi.com/v1"

    def test_trailing_slash_stripped(self):
        assert minimax_text_base_url("https://api.minimax.io/v1/") == "https://api.minimax.io/v1"
        assert minimax_text_base_url("https://api.minimax.io/") == "https://api.minimax.io/v1"

    def test_whitespace_falls_back_to_default(self):
        # Truthy whitespace base_url strips then falls back to hosts,
        # never invalid relative /v1 from empty strings.
        assert minimax_text_base_url("   ") == MINIMAX_BASE_URL


class TestApiKeyResolution:
    def test_strips_and_returns(self):
        assert resolve_minimax_api_key("  sk-abc  ") == "sk-abc"

    def test_missing_raises(self):
        with pytest.raises(ValueError, match=r"请到系统配置页填写 MiniMax API Key"):
            resolve_minimax_api_key(None)

    def test_blank_raises(self):
        # No environment fallback; missing keys fail explicitly.
        with pytest.raises(ValueError, match=r"请到系统配置页填写 MiniMax API Key"):
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
        # Relay/error top-level nondicts, list/str, never raise AttributeError.
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
        # Nondict top-level relay/errors return None without AttributeError.
        assert extract_image_base64(["x"]) is None
        assert extract_image_base64(None) is None


class TestFailureReason:
    def test_success_status_zero_returns_none(self):
        assert minimax_failure_reason({"base_resp": {"status_code": 0, "status_msg": "success"}}) is None

    def test_missing_base_resp_returns_none(self):
        assert minimax_failure_reason({}) is None

    def test_non_dict_payload_returns_none(self):
        # Nondicts return no-business-error None without AttributeError.
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
        # Retain allowlisted scalars.
        assert view["model"] == "image-01"
        assert view["width"] == 1152
        assert view["height"] == 2048
        assert view["response_format"] == "url"
        assert view["n"] == 1
        assert view["prompt_optimizer"] is False
        assert view["seed"] == 7
        # Log prompt lengths/reference counts only, never base64/URLs.
        assert view["prompt_len"] == len(body["prompt"])
        assert "prompt" not in view
        assert view["subject_reference"] == "<1 ref>"
        assert "data:image" not in repr(view)

    def test_omits_absent_scalars(self):
        view = safe_body_for_log({"model": "image-01", "prompt": "x"})
        assert view == {"model": "image-01", "prompt_len": 1}

    def test_empty_subject_reference_omitted(self):
        # Explicitly skip empty lists, omitting subject_reference; logging sanitation needs boundary coverage.
        view = safe_body_for_log({"model": "image-01", "subject_reference": []})
        assert "subject_reference" not in view
        assert view == {"model": "image-01"}


class TestVideoBaseUrl:
    def test_shares_v1_base_with_text(self):
        # Native video/text share one /v1 base.
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
        # Query failures such as unknown task_id with nonzero base_resp are terminal.
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

    def test_heic_mime(self, tmp_path):
        img = tmp_path / "x.heic"
        img.write_bytes(b"\x00\x00\x00\x18ftypheic")
        assert image_to_data_uri(img).startswith("data:image/heic;base64,")

    def test_heif_mime(self, tmp_path):
        img = tmp_path / "x.heif"
        img.write_bytes(b"\x00\x00\x00\x18ftypmif1")
        assert image_to_data_uri(img).startswith("data:image/heif;base64,")
