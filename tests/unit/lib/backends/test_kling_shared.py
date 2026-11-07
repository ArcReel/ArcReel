"""Kling shared tests: injected-clock JWT management and async task-response parsing.

No real HTTP/wall clocks; verify token renewal through controlled time sources.
"""

from __future__ import annotations

import jwt
import pytest

from lib.backends.kling_shared import (
    KLING_BASE_URL,
    KlingJWTManager,
    extract_kling_image_urls,
    extract_kling_model_path_video_url,
    extract_kling_task_id,
    extract_kling_video_url,
    is_kling_model_path_terminal,
    is_kling_task_terminal,
    kling_auth_mode,
    kling_bearer_headers,
    kling_model_path_failure_reason,
    kling_model_path_output_duration,
    kling_model_path_task_id,
    kling_response_error,
    kling_task_failure_reason,
    resolve_kling_api_key,
    resolve_kling_jwt_credentials,
)


class _Clock:
    """Advanceable injected clock."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _decode(token: str, secret: str) -> dict:
    return jwt.decode(token, secret, algorithms=["HS256"], options={"verify_nbf": False, "verify_exp": False})


class TestKlingJWTManager:
    def test_token_structure(self):
        clock = _Clock()
        mgr = KlingJWTManager("ak-123", "s" * 40, clock=clock)
        token = mgr.token()
        claims = _decode(token, "s" * 40)
        assert claims["iss"] == "ak-123"
        assert claims["exp"] == int(clock.now) + 1800
        assert claims["nbf"] == int(clock.now) - 5
        # Headers declare HS256/JWT.
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "HS256"
        assert header["typ"] == "JWT"

    def test_reuses_cached_token_when_far_from_expiry(self):
        clock = _Clock()
        mgr = KlingJWTManager("ak", "s" * 40, clock=clock)
        first = mgr.token()
        clock.advance(100)  # About 1700s remain, above 60s; reuse cache.
        assert mgr.token() == first  # Reuse cache.

    def test_resigns_when_within_refresh_margin(self):
        clock = _Clock()
        mgr = KlingJWTManager("ak", "s" * 40, clock=clock)
        first = mgr.token()
        # Advance to 30s remaining, below 60s refresh window; resign with new tokens.
        clock.advance(1800 - 30)
        second = mgr.token()
        assert second != first
        claims = _decode(second, "s" * 40)
        assert claims["exp"] == int(clock.now) + 1800

    def test_signed_with_secret_key(self):
        mgr = KlingJWTManager("ak", "c" * 40, clock=_Clock())
        token = mgr.token()
        with pytest.raises(jwt.InvalidSignatureError):
            jwt.decode(token, "w" * 40, algorithms=["HS256"], options={"verify_exp": False})

    def test_auth_headers_carry_bearer_token(self):
        mgr = KlingJWTManager("ak", "s" * 40, clock=_Clock())
        headers = mgr.auth_headers()
        assert headers["Authorization"].startswith("Bearer ")
        assert headers["Content-Type"] == "application/json"


class TestBearerMode:
    def test_bearer_headers_use_static_key_no_jwt(self):
        headers = kling_bearer_headers("static-api-key")
        # Bearer bypasses JWT: Authorization is static keys, not signed tokens.
        assert headers["Authorization"] == "Bearer static-api-key"


class TestAuthModeDispatch:
    """kling_auth_mode shares built-in construction/probe credential-shape decisions, prioritizing api_key."""

    def test_api_key_present_dispatches_bearer(self):
        assert kling_auth_mode({"api_key": "sk-1", "access_key": None, "secret_key": None}) == "bearer"

    def test_only_dual_secret_dispatches_jwt(self):
        assert kling_auth_mode({"api_key": None, "access_key": "ak-1", "secret_key": "sk-1"}) == "jwt"

    def test_both_set_prefers_bearer(self):
        assert kling_auth_mode({"api_key": "sk-1", "access_key": "ak-1", "secret_key": "sk-1"}) == "bearer"

    def test_empty_api_key_falls_back_to_jwt(self):
        assert kling_auth_mode({"api_key": "", "access_key": "ak-1", "secret_key": "sk-1"}) == "jwt"

    def test_neither_set_falls_back_to_jwt(self):
        assert kling_auth_mode({}) == "jwt"

    def test_whitespace_only_api_key_falls_back_to_jwt(self):
        # Whitespace api_key must not imply bearer and silently discard valid simultaneous AK/SK.
        assert kling_auth_mode({"api_key": "   ", "access_key": "ak-1", "secret_key": "sk-1"}) == "jwt"


class TestCredentialResolution:
    def test_jwt_credentials_strip_and_return(self):
        assert resolve_kling_jwt_credentials(" ak ", " sk ") == ("ak", "sk")

    @pytest.mark.parametrize(("ak", "sk"), [(None, "sk"), ("ak", None), ("", "sk"), ("  ", "sk")])
    def test_jwt_credentials_missing_raises(self, ak, sk):
        with pytest.raises(ValueError, match=r"请到系统配置页填写可灵 Kling 的 Access Key 与 Secret Key"):
            resolve_kling_jwt_credentials(ak, sk)

    def test_api_key_missing_raises(self):
        with pytest.raises(ValueError, match=r"请填写可灵 Kling 的 API Key"):
            resolve_kling_api_key("  ")


class TestResponseParsing:
    def test_base_url_constant(self):
        assert KLING_BASE_URL == "https://api-beijing.klingai.com/v1"

    def test_extract_task_id(self):
        payload = {"code": 0, "message": "SUCCEED", "data": {"task_id": "t-1", "task_status": "submitted"}}
        assert extract_kling_task_id(payload) == "t-1"

    def test_extract_task_id_missing_uses_code_error(self):
        payload = {"code": 1101, "message": "auth failed", "data": {}}
        with pytest.raises(RuntimeError, match="1101"):
            extract_kling_task_id(payload)

    def test_code_error_nonzero(self):
        assert kling_response_error({"code": 5, "message": "boom"}) == "Kling API code=5: boom"
        assert kling_response_error({"code": 0, "message": "ok"}) is None

    def test_code_normalized_before_compare(self):
        # Bearer/relay codes may be strings/floats; normalize integers before comparison.
        assert kling_response_error({"code": "0", "message": "ok"}) is None
        assert kling_response_error({"code": 0.0, "message": "ok"}) is None
        assert kling_response_error({"code": "5", "message": "boom"}) == "Kling API code=5: boom"
        # Unparseable codes report errors with original values.
        assert kling_response_error({"code": "oops", "message": "bad"}) == "Kling API code=oops: bad"

    def test_inner_data_code_error(self):
        # After top-level account/costs code 0, nested business code/msg still surfaces.
        payload = {"code": 0, "message": "", "data": {"code": 1234, "msg": "resource pack not found"}}
        assert kling_response_error(payload) == "Kling API code=1234: resource pack not found"

    def test_inner_data_code_zero_not_error(self):
        payload = {"code": 0, "message": "", "data": {"code": 0, "msg": ""}}
        assert kling_response_error(payload) is None

    def test_inner_data_without_code_key_not_error(self):
        # Task submission/poll data lacks code, so this check is a no-op.
        assert kling_response_error({"code": 0, "data": {"task_id": "t-1", "task_status": "submitted"}}) is None

    def test_non_dict_payload_normalized_not_error(self):
        # Nonobject relay/gateway JSON, arrays/strings, normalizes to empty dicts,
        # never .get() AttributeError.
        assert kling_response_error([]) is None
        assert kling_response_error("oops") is None

    def test_non_dict_nested_data_normalized_not_error(self):
        # Nonobject data, e.g. error strings, likewise normalizes empty.
        assert kling_response_error({"code": 0, "message": "", "data": "unexpected"}) is None

    def test_terminal_states(self):
        assert is_kling_task_terminal({"data": {"task_status": "succeed"}}) is True
        assert is_kling_task_terminal({"data": {"task_status": "failed"}}) is True
        assert is_kling_task_terminal({"data": {"task_status": "processing"}}) is False
        assert is_kling_task_terminal({"data": {"task_status": "submitted"}}) is False

    def test_failure_reason_on_failed(self):
        payload = {"code": 0, "data": {"task_id": "t-1", "task_status": "failed", "task_status_msg": "nsfw"}}
        reason = kling_task_failure_reason(payload)
        assert reason is not None
        assert "t-1" in reason
        assert "nsfw" in reason

    def test_failure_reason_none_on_success(self):
        assert kling_task_failure_reason({"code": 0, "data": {"task_status": "succeed"}}) is None

    def test_failure_reason_top_level_code_error(self):
        assert kling_task_failure_reason({"code": 1200, "message": "bad task"}) == "Kling API code=1200: bad task"

    def test_null_message_not_stringified(self):
        # Null message/task_status_msg becomes empty strings, never literal None in errors.
        assert kling_response_error({"code": 5, "message": None}) == "Kling API code=5:"
        reason = kling_task_failure_reason({"code": 0, "data": {"task_id": "t-2", "task_status": "failed"}})
        assert reason is not None
        assert "None" not in reason

    def test_extract_video_url(self):
        payload = {
            "code": 0,
            "data": {"task_status": "succeed", "task_result": {"videos": [{"id": "v1", "url": "https://x/v.mp4"}]}},
        }
        assert extract_kling_video_url(payload) == "https://x/v.mp4"

    def test_extract_video_url_missing_raises(self):
        with pytest.raises(RuntimeError):
            extract_kling_video_url({"code": 0, "data": {"task_status": "succeed", "task_result": {"videos": []}}})

    def test_extract_image_urls_in_order(self):
        payload = {
            "code": 0,
            "data": {
                "task_status": "succeed",
                "task_result": {
                    "images": [{"index": 0, "url": "https://x/0.png"}, {"index": 1, "url": "https://x/1.png"}]
                },
            },
        }
        assert extract_kling_image_urls(payload) == ["https://x/0.png", "https://x/1.png"]

    def test_extract_image_urls_skips_blank(self):
        payload = {
            "code": 0,
            "data": {"task_status": "succeed", "task_result": {"images": [{"url": ""}, {"url": "https://x/ok.png"}]}},
        }
        assert extract_kling_image_urls(payload) == ["https://x/ok.png"]

    def test_extract_image_urls_missing_raises(self):
        with pytest.raises(RuntimeError):
            extract_kling_image_urls({"code": 0, "data": {"task_status": "succeed", "task_result": {"images": []}}})

    def test_failure_reason_message_modality_neutral(self):
        # Shared image/video failure_reason must not hardcode 视频, allowing image reuse.
        reason = kling_task_failure_reason(
            {"code": 0, "data": {"task_id": "t-9", "task_status": "failed", "task_status_msg": "boom"}}
        )
        assert reason is not None
        assert "视频" not in reason
        assert "t-9" in reason
        assert "boom" in reason


def _tasks(**task) -> dict:
    return {"code": 0, "message": "SUCCEED", "data": [task]}


class TestModelPathResponses:
    """New model-in-path API submission/``GET /tasks`` response parsing."""

    def test_task_id_from_data_id(self):
        assert kling_model_path_task_id({"code": 0, "data": {"id": "893605946402811985", "status": "submitted"}}) == (
            "893605946402811985"
        )

    def test_task_id_missing_raises_with_code_error(self):
        with pytest.raises(RuntimeError, match="code=1102"):
            kling_model_path_task_id({"code": 1102, "message": "resource pack exhausted", "data": None})

    @pytest.mark.parametrize(
        ("status", "terminal"),
        [("submitted", False), ("processing", False), ("succeeded", True), ("failed", True), ("succeed", False)],
    )
    def test_terminal_states(self, status, terminal):
        assert is_kling_model_path_terminal(_tasks(id="t", status=status)) is terminal

    def test_empty_query_result_keeps_polling(self):
        assert is_kling_model_path_terminal({"code": 0, "data": []}) is False

    def test_failure_reason_reads_task_message(self):
        reason = kling_model_path_failure_reason(_tasks(id="t-9", status="failed", message="risk control"))
        assert reason is not None
        assert "t-9" in reason
        assert "risk control" in reason
        assert kling_model_path_failure_reason(_tasks(id="t-9", status="processing")) is None

    def test_video_url_skips_non_video_outputs(self):
        payload = _tasks(
            id="t",
            status="succeeded",
            outputs=[{"type": "audio", "mp3_url": "https://x/a.mp3"}, {"type": "video", "url": "https://x/v.mp4"}],
        )
        assert extract_kling_model_path_video_url(payload) == "https://x/v.mp4"

    def test_video_url_missing_raises(self):
        with pytest.raises(RuntimeError, match="缺少视频 URL"):
            extract_kling_model_path_video_url(_tasks(id="t", status="succeeded", outputs=[]))

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("5", 5), ("12.5", 13), (8, 8), ("0", None), ("-1", None), ("abc", None), (None, None), ("90000", None)],
    )
    def test_output_duration(self, raw, expected):
        usage = {} if raw is None else {"output_duration": raw}
        assert kling_model_path_output_duration(_tasks(id="t", status="succeeded", usage=usage)) == expected
