"""可灵 Kling 共享层单测：JWT token 管理器（注入时钟）+ 异步任务响应解析。

不打真实 HTTP、不调真实墙钟——token 续签语义用可控时间源验证。
"""

from __future__ import annotations

import jwt
import pytest

from lib.kling_shared import (
    KLING_BASE_URL,
    KlingJWTManager,
    extract_kling_image_urls,
    extract_kling_task_id,
    extract_kling_video_url,
    is_kling_task_terminal,
    kling_auth_mode,
    kling_bearer_headers,
    kling_response_error,
    kling_task_failure_reason,
    resolve_kling_api_key,
    resolve_kling_jwt_credentials,
)


class _Clock:
    """可推进的注入时钟。"""

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
        # header 声明 HS256 / JWT
        header = jwt.get_unverified_header(token)
        assert header["alg"] == "HS256"
        assert header["typ"] == "JWT"

    def test_reuses_cached_token_when_far_from_expiry(self):
        clock = _Clock()
        mgr = KlingJWTManager("ak", "s" * 40, clock=clock)
        first = mgr.token()
        clock.advance(100)  # 距过期仍有约 1700s（>60s），复用缓存
        assert mgr.token() == first  # 复用缓存

    def test_resigns_when_within_refresh_margin(self):
        clock = _Clock()
        mgr = KlingJWTManager("ak", "s" * 40, clock=clock)
        first = mgr.token()
        # 推进到距过期 30s（<60s 刷新窗口）→ 重签返回新 token
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
        # bearer 模式旁路 JWT：Authorization 直接是静态 key，不是签名 token
        assert headers["Authorization"] == "Bearer static-api-key"


class TestAuthModeDispatch:
    """kling_auth_mode：内置 provider 构造与连接测试共用的凭证形态判定（api_key 优先）。"""

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
        # 纯空格 api_key 不应误判为已填写 bearer，静默吞掉同时存在的有效 AK/SK。
        assert kling_auth_mode({"api_key": "   ", "access_key": "ak-1", "secret_key": "sk-1"}) == "jwt"


class TestCredentialResolution:
    def test_jwt_credentials_strip_and_return(self):
        assert resolve_kling_jwt_credentials(" ak ", " sk ") == ("ak", "sk")

    @pytest.mark.parametrize("ak,sk", [(None, "sk"), ("ak", None), ("", "sk"), ("  ", "sk")])
    def test_jwt_credentials_missing_raises(self, ak, sk):
        with pytest.raises(ValueError):
            resolve_kling_jwt_credentials(ak, sk)

    def test_api_key_missing_raises(self):
        with pytest.raises(ValueError):
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
        # bearer / 中转 endpoint 可能把 code 序列化成字符串或浮点，归一化为 int 再比较。
        assert kling_response_error({"code": "0", "message": "ok"}) is None
        assert kling_response_error({"code": 0.0, "message": "ok"}) is None
        assert kling_response_error({"code": "5", "message": "boom"}) == "Kling API code=5: boom"
        # 无法解析的 code 视为错误，暴露原值。
        assert kling_response_error({"code": "oops", "message": "bad"}) == "Kling API code=oops: bad"

    def test_inner_data_code_error(self):
        # account/costs 等接口顶层 code=0 通过后，data 内嵌的业务级 code/msg 仍需暴露。
        payload = {"code": 0, "message": "", "data": {"code": 1234, "msg": "resource pack not found"}}
        assert kling_response_error(payload) == "Kling API code=1234: resource pack not found"

    def test_inner_data_code_zero_not_error(self):
        payload = {"code": 0, "message": "", "data": {"code": 0, "msg": ""}}
        assert kling_response_error(payload) is None

    def test_inner_data_without_code_key_not_error(self):
        # 提交/轮询类接口的 data（task_id / task_status 等）不含 code 键，该检查为 no-op。
        assert kling_response_error({"code": 0, "data": {"task_id": "t-1", "task_status": "submitted"}}) is None

    def test_non_dict_payload_normalized_not_error(self):
        # 中转 endpoint / 异常网关可能返回非对象 JSON（数组、字符串等），按空 dict 处理，
        # 不因 .get() 触发 AttributeError。
        assert kling_response_error([]) is None
        assert kling_response_error("oops") is None

    def test_non_dict_nested_data_normalized_not_error(self):
        # data 字段本身也可能是非对象 JSON（如字符串错误信息），同样归一化为空 dict。
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
        assert "t-1" in reason and "nsfw" in reason

    def test_failure_reason_none_on_success(self):
        assert kling_task_failure_reason({"code": 0, "data": {"task_status": "succeed"}}) is None

    def test_failure_reason_top_level_code_error(self):
        assert kling_task_failure_reason({"code": 1200, "message": "bad task"}) == "Kling API code=1200: bad task"

    def test_null_message_not_stringified(self):
        # message / task_status_msg 显式为 null 时归一化为空串，不把字面量 'None' 拼进错误描述。
        assert kling_response_error({"code": 5, "message": None}) == "Kling API code=5:"
        reason = kling_task_failure_reason({"code": 0, "data": {"task_id": "t-2", "task_status": "failed"}})
        assert reason is not None and "None" not in reason

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
        # image / video 共用 failure_reason：消息不应写死「视频」，便于图像任务复用。
        reason = kling_task_failure_reason(
            {"code": 0, "data": {"task_id": "t-9", "task_status": "failed", "task_status_msg": "boom"}}
        )
        assert reason is not None
        assert "视频" not in reason
        assert "t-9" in reason and "boom" in reason
