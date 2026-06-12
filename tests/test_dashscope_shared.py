"""lib.dashscope_shared 纯函数单元测试（不打真实 HTTP）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.dashscope_shared import (
    DASHSCOPE_BASE_URL,
    dashscope_failure_reason,
    dashscope_headers,
    dashscope_native_base_url,
    dashscope_text_base_url,
    extract_billing_duration,
    extract_image_url,
    extract_task_id,
    extract_video_url,
    image_to_data_uri,
    is_dashscope_expired,
    is_dashscope_succeeded,
    is_dashscope_terminal,
    resolve_dashscope_api_key,
    safe_body_for_log,
)


class TestBaseUrlDerivation:
    def test_text_base_from_host(self):
        assert dashscope_text_base_url(None) == f"{DASHSCOPE_BASE_URL}/compatible-mode/v1"

    def test_native_base_from_host(self):
        assert dashscope_native_base_url(None) == f"{DASHSCOPE_BASE_URL}/api/v1"

    def test_tolerates_native_suffix(self):
        # 用户填了完整 native base，派生文本 base 时剥后缀再拼
        configured = "https://dashscope.aliyuncs.com/api/v1"
        assert dashscope_text_base_url(configured) == "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert dashscope_native_base_url(configured) == "https://dashscope.aliyuncs.com/api/v1"

    def test_tolerates_text_suffix(self):
        configured = "https://dashscope.aliyuncs.com/compatible-mode/v1"
        assert dashscope_native_base_url(configured) == "https://dashscope.aliyuncs.com/api/v1"

    def test_region_switch_intl(self):
        configured = "https://dashscope-intl.aliyuncs.com/api/v1"
        assert dashscope_native_base_url(configured) == "https://dashscope-intl.aliyuncs.com/api/v1"
        assert dashscope_text_base_url(configured) == "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"

    def test_trailing_slash_stripped(self):
        assert dashscope_native_base_url("https://dashscope.aliyuncs.com/") == f"{DASHSCOPE_BASE_URL}/api/v1"

    def test_whitespace_configured_falls_back_to_default(self):
        # 纯空白 base_url（"   "）是真值会绕过 or，须 strip 后回落默认 host，
        # 不能 strip 成空串派生出 "/api/v1" / "/compatible-mode/v1" 这类非法相对 URL
        assert dashscope_native_base_url("   ") == f"{DASHSCOPE_BASE_URL}/api/v1"
        assert dashscope_text_base_url("  ") == f"{DASHSCOPE_BASE_URL}/compatible-mode/v1"


class TestHeaders:
    def test_sync_headers_no_async_flag(self):
        h = dashscope_headers("sk-abc")
        assert h["Authorization"] == "Bearer sk-abc"
        assert h["Content-Type"] == "application/json"
        assert "X-DashScope-Async" not in h

    def test_async_headers_has_flag(self):
        h = dashscope_headers("sk-abc", async_mode=True)
        assert h["X-DashScope-Async"] == "enable"


class TestResolveApiKey:
    def test_present(self):
        assert resolve_dashscope_api_key("  sk-x ") == "sk-x"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_missing_raises(self, value):
        with pytest.raises(ValueError, match="DashScope API Key"):
            resolve_dashscope_api_key(value)


class TestStatusHelpers:
    def test_succeeded(self):
        assert is_dashscope_succeeded({"output": {"task_status": "SUCCEEDED"}})
        assert not is_dashscope_succeeded({"output": {"task_status": "RUNNING"}})

    @pytest.mark.parametrize("status", ["SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"])
    def test_terminal(self, status):
        assert is_dashscope_terminal({"output": {"task_status": status}})

    @pytest.mark.parametrize("status", ["PENDING", "RUNNING"])
    def test_non_terminal(self, status):
        assert not is_dashscope_terminal({"output": {"task_status": status}})

    def test_expired(self):
        assert is_dashscope_expired({"output": {"task_status": "UNKNOWN"}})
        assert not is_dashscope_expired({"output": {"task_status": "SUCCEEDED"}})

    def test_non_dict_output_does_not_crash(self):
        # output 为非 dict 真值（畸形上游）时状态判定/失败原因不抛 AttributeError
        assert not is_dashscope_terminal({"output": ["weird"]})
        assert not is_dashscope_succeeded({"output": "weird"})
        assert dashscope_failure_reason({"output": ["weird"]}) is None


class TestFailureReason:
    def test_failed_returns_reason(self):
        reason = dashscope_failure_reason(
            {"output": {"task_status": "FAILED", "code": "InternalError", "message": "boom"}}
        )
        assert reason is not None
        assert "FAILED" in reason
        assert "InternalError" in reason
        assert "boom" in reason

    def test_canceled_returns_reason(self):
        reason = dashscope_failure_reason({"output": {"task_status": "CANCELED"}})
        assert reason is not None and "CANCELED" in reason

    def test_top_level_submit_error(self):
        reason = dashscope_failure_reason({"code": "InvalidApiKey", "message": "No API-key provided."})
        assert reason is not None
        assert "InvalidApiKey" in reason

    def test_unknown_is_not_failure(self):
        # UNKNOWN（过期）不算失败，交由 expired 单独处理
        assert dashscope_failure_reason({"output": {"task_status": "UNKNOWN"}}) is None

    def test_success_is_not_failure(self):
        assert dashscope_failure_reason({"output": {"task_status": "SUCCEEDED", "video_url": "x"}}) is None


class TestExtractors:
    def test_extract_task_id(self):
        assert extract_task_id({"output": {"task_id": "t-1", "task_status": "PENDING"}}) == "t-1"

    def test_extract_task_id_missing_raises_with_reason(self):
        with pytest.raises(RuntimeError, match="InvalidApiKey"):
            extract_task_id({"code": "InvalidApiKey", "message": "no key"})

    def test_extract_video_url(self):
        assert extract_video_url({"output": {"video_url": "https://x/o.mp4"}}) == "https://x/o.mp4"

    def test_extract_video_url_missing_raises(self):
        with pytest.raises(RuntimeError, match="video_url"):
            extract_video_url({"output": {"task_status": "SUCCEEDED"}})

    def test_extract_billing_duration(self):
        assert extract_billing_duration({"usage": {"duration": 15}}) == 15
        # 容忍 float / 数字字符串
        assert extract_billing_duration({"usage": {"duration": 4.8}}) == 5
        assert extract_billing_duration({"usage": {"duration": "10"}}) == 10

    def test_extract_billing_duration_half_up_not_bankers(self):
        # .5 边界须 half-up（4.5→5 / 2.5→3），而非 round() 的银行家舍入（会得 4 / 2 少计费）
        assert extract_billing_duration({"usage": {"duration": 4.5}}) == 5
        assert extract_billing_duration({"usage": {"duration": 2.5}}) == 3
        assert extract_billing_duration({"usage": {"duration": "7.5"}}) == 8

    def test_extract_billing_duration_missing(self):
        assert extract_billing_duration({"usage": {}}) is None
        assert extract_billing_duration({}) is None
        # 非数字字符串无法解析 → None
        assert extract_billing_duration({"usage": {"duration": "abc"}}) is None

    def test_extract_billing_duration_non_positive_falls_back(self):
        # 0 / 负值不记账，回 None 由 caller 回落请求时长；(0, 0.5) 取整到 0 同样不记账
        assert extract_billing_duration({"usage": {"duration": 0}}) is None
        assert extract_billing_duration({"usage": {"duration": -3}}) is None
        assert extract_billing_duration({"usage": {"duration": 0.3}}) is None

    def test_extract_billing_duration_over_limit_falls_back(self):
        # 超出合理上限（24h）视为 provider 回报异常，回 None，防超大数值写入 DB Integer 列溢出；
        # 上限基于取整前原始值：86400.4 已超 24h，不得因 half-up 落回上限内被接受
        assert extract_billing_duration({"usage": {"duration": 86401}}) is None
        assert extract_billing_duration({"usage": {"duration": 1e100}}) is None
        assert extract_billing_duration({"usage": {"duration": 86400.4}}) is None
        assert extract_billing_duration({"usage": {"duration": 86400}}) == 86400

    def test_extract_billing_duration_non_dict_usage(self):
        # usage 为非 dict 真值（畸形上游）→ 归一化为空、回 None，不抛 AttributeError
        assert extract_billing_duration({"usage": [1, 2]}) is None
        assert extract_billing_duration({"usage": "oops"}) is None

    def test_extract_image_url(self):
        payload = {
            "output": {"choices": [{"message": {"content": [{"image": "https://x/i.png"}]}}]},
        }
        assert extract_image_url(payload) == "https://x/i.png"

    def test_extract_image_url_missing_choices_raises(self):
        with pytest.raises(RuntimeError):
            extract_image_url({"output": {}})

    def test_extract_image_url_malformed_choice_raises_runtime_not_attribute(self):
        # 上游异常结构（choices[0] 非 dict / message 非 dict）须报 RuntimeError，不能漏出 AttributeError
        with pytest.raises(RuntimeError):
            extract_image_url({"output": {"choices": [None]}})
        with pytest.raises(RuntimeError):
            extract_image_url({"output": {"choices": [{"message": "oops"}]}})

    def test_extract_image_url_non_dict_output_or_non_list_choices(self):
        # output 非 dict / choices 非 list 真值（畸形上游）→ RuntimeError，不漏 AttributeError/TypeError
        with pytest.raises(RuntimeError):
            extract_image_url({"output": "oops"})
        with pytest.raises(RuntimeError):
            extract_image_url({"output": {"choices": "not-a-list"}})

    def test_extract_image_url_truthy_non_list_content_raises_runtime(self):
        # content 为 truthy 非 list（int/bool/dict）时不得 for 迭代抛 TypeError，须落 RuntimeError
        for content in (5, True, {"image": "x"}):
            payload = {"output": {"choices": [{"message": {"content": content}}]}}
            with pytest.raises(RuntimeError):
                extract_image_url(payload)


class TestSafeBodyForLog:
    def test_does_not_leak_media_or_messages(self):
        body = {
            "model": "wan2.7-r2v",
            "input": {
                "prompt": "x" * 500,
                "media": [{"type": "reference_image", "url": "data:image/png;base64,SECRET"}],
            },
            "parameters": {"resolution": "720P", "duration": 5, "watermark": False, "seed": 42},
        }
        view = safe_body_for_log(body)
        serialized = str(view)
        assert "SECRET" not in serialized
        assert "base64" not in serialized
        assert view["model"] == "wan2.7-r2v"
        assert view["resolution"] == "720P"
        assert view["media"] == "<1 item>"
        # 长 prompt 被截断
        assert len(view["prompt"]) < 200

    def test_image_messages_summarized_to_counts(self):
        body = {
            "model": "qwen-image-2.0",
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"image": "data:image/png;base64,AAA"},
                            {"text": "draw"},
                        ],
                    }
                ]
            },
            "parameters": {"size": "2048*2048", "n": 1, "watermark": False, "prompt_extend": False},
        }
        view = safe_body_for_log(body)
        assert "AAA" not in str(view)
        assert view["content"] == "<1 image, 1 text>"
        assert view["size"] == "2048*2048"

    def test_non_dict_first_message_does_not_crash(self):
        # 日志辅助绝不能因畸形 messages[0]（非 dict）抛 AttributeError
        body = {"model": "qwen-image-2.0", "input": {"messages": ["oops"]}, "parameters": {}}
        view = safe_body_for_log(body)
        assert view["model"] == "qwen-image-2.0"
        assert "content" not in view

    def test_non_dict_params_or_input_does_not_crash(self):
        # parameters / input 为 truthy 非 dict（list / str，畸形上游）时 fail-safe 日志辅助
        # 不得抛 AttributeError/TypeError，须归一化为空、只回 model
        for body in (
            {"model": "wan2.7-r2v", "parameters": [1, 2], "input": "oops"},
            {"model": "wan2.7-r2v", "parameters": "seed", "input": ["x"]},
        ):
            view = safe_body_for_log(body)
            assert view["model"] == "wan2.7-r2v"
            assert "prompt" not in view and "size" not in view

    def test_tts_input_text_not_logged(self):
        # TTS 合成文本（input.text）不在白名单内，绝不进日志视图
        secret_text = "用户私密旁白文本SECRETNARRATION"
        body = {
            "model": "qwen3-tts-flash",
            "input": {"text": secret_text, "voice": "Cherry", "language_type": "Chinese"},
        }
        view = safe_body_for_log(body)
        assert secret_text not in str(view)
        assert "text" not in view
        assert view["model"] == "qwen3-tts-flash"


def test_image_to_data_uri(tmp_path: Path):
    p = tmp_path / "x.png"
    p.write_bytes(b"\x89PNG\r\nfake")
    uri = image_to_data_uri(p)
    assert uri.startswith("data:image/png;base64,")
