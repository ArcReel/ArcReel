"""失败调用的机器码分类：四类可识别失败各自的识别依据与参数，认不出的异常只留原文。"""

from __future__ import annotations

import httpx
import pytest
from openai import APITimeoutError, BadRequestError, RateLimitError

from lib.call_failure import CallErrorCode, classify_call_failure
from lib.http_status_errors import ArtifactDownloadError
from lib.video_backends.base import AmbiguousSubmitError, VideoCapabilityError

_REQUEST = httpx.Request("POST", "https://provider.example/v1/videos")


def _status_error(status: int, *, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    response = httpx.Response(status, headers=headers or {}, request=_REQUEST)
    return httpx.HTTPStatusError(f"{status} response", request=_REQUEST, response=response)


class TestRateLimited:
    def test_http_429_carries_integer_retry_after(self) -> None:
        failure = classify_call_failure(_status_error(429, headers={"Retry-After": "30"}))

        assert failure.error_code == CallErrorCode.RATE_LIMITED
        assert failure.error_params == {"retry_after_seconds": 30}

    def test_http_429_without_retry_after_has_no_params(self) -> None:
        failure = classify_call_failure(_status_error(429))

        assert failure.error_code == CallErrorCode.RATE_LIMITED
        assert failure.error_params == {}

    def test_http_date_retry_after_is_not_guessed_into_seconds(self) -> None:
        failure = classify_call_failure(_status_error(429, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}))

        assert failure.error_code == CallErrorCode.RATE_LIMITED
        assert failure.error_params == {}

    def test_openai_rate_limit_error_is_recognised_by_its_response(self) -> None:
        response = httpx.Response(429, headers={"Retry-After": "5"}, request=_REQUEST)
        failure = classify_call_failure(RateLimitError("slow down", response=response, body=None))

        assert failure.error_code == CallErrorCode.RATE_LIMITED
        assert failure.error_params == {"retry_after_seconds": 5}

    def test_status_code_on_the_exception_itself_is_recognised(self) -> None:
        # google 系（api_core.ResourceExhausted / genai.APIError）把状态码挂在异常自身。
        class _ResourceExhausted(Exception):
            code = 429

        failure = classify_call_failure(_ResourceExhausted("quota exceeded"))

        assert failure.error_code == CallErrorCode.RATE_LIMITED


class TestContentPolicy:
    @pytest.mark.parametrize("provider_code", ["content_policy_violation", "moderation_blocked", "content_filter"])
    def test_provider_error_body_code_is_recognised(self, provider_code: str) -> None:
        response = httpx.Response(400, request=_REQUEST)
        exc = BadRequestError("rejected", response=response, body={"code": provider_code})

        failure = classify_call_failure(exc)

        assert failure.error_code == CallErrorCode.CONTENT_POLICY
        assert failure.error_params == {}

    def test_other_provider_error_body_code_stays_unclassified(self) -> None:
        response = httpx.Response(400, request=_REQUEST)
        exc = BadRequestError("bad model", response=response, body={"code": "model_not_found"})

        failure = classify_call_failure(exc)

        assert failure.error_code is None


class TestTimeout:
    def test_poll_timeout_error_is_recognised(self) -> None:
        # 视频轮询耗尽预算抛的就是内置 TimeoutError（asyncio.TimeoutError 是其别名）。
        failure = classify_call_failure(TimeoutError("任务超时（600秒）"))

        assert failure.error_code == CallErrorCode.TIMEOUT
        assert failure.error_params == {}

    def test_httpx_timeout_is_recognised_despite_not_being_a_timeout_error(self) -> None:
        assert not isinstance(httpx.ReadTimeout("read timed out"), TimeoutError)

        failure = classify_call_failure(httpx.ReadTimeout("read timed out"))

        assert failure.error_code == CallErrorCode.TIMEOUT

    def test_timeout_wrapped_by_an_ambiguous_submit_is_still_a_timeout(self) -> None:
        """提交阶段的歧义态包装自身不可分类；根因超时在它包住的那一层，原文仍取外层。"""
        exc = AmbiguousSubmitError(provider="veo")
        exc.__cause__ = httpx.ReadTimeout("read timed out")

        failure = classify_call_failure(exc)

        assert failure.error_code == CallErrorCode.TIMEOUT
        assert failure.error_message == str(exc)

    def test_unclassifiable_chain_stays_unclassified(self) -> None:
        exc = AmbiguousSubmitError(provider="veo")
        exc.__cause__ = httpx.RemoteProtocolError("peer closed connection")

        assert classify_call_failure(exc).error_code is None

    def test_openai_timeout_is_recognised(self) -> None:
        failure = classify_call_failure(APITimeoutError(request=_REQUEST))

        assert failure.error_code == CallErrorCode.TIMEOUT

    @pytest.mark.parametrize("status", [408, 504])
    def test_timeout_status_codes_are_recognised(self, status: int) -> None:
        failure = classify_call_failure(_status_error(status))

        assert failure.error_code == CallErrorCode.TIMEOUT


class TestDownloadFailed:
    def test_http_status_comes_from_the_wrapped_download_error(self) -> None:
        try:
            raise _status_error(403)
        except httpx.HTTPStatusError as cause:
            exc = ArtifactDownloadError(detail=str(cause))
            exc.__cause__ = cause

        failure = classify_call_failure(exc)

        assert failure.error_code == CallErrorCode.DOWNLOAD_FAILED
        assert failure.error_params == {"status": 403}

    def test_without_an_http_cause_only_the_code_is_recorded(self) -> None:
        failure = classify_call_failure(ArtifactDownloadError(detail="download budget exhausted"))

        assert failure.error_code == CallErrorCode.DOWNLOAD_FAILED
        assert failure.error_params == {}

    def test_declarative_backend_download_exhaustion_is_recognised_by_its_code(self) -> None:
        """自定义声明式 backend 的取件耗尽是另一种异常类型，按同一个稳定码认出。"""
        from lib.custom_provider.declarative_backend import DeclarativeRuntimeError

        try:
            raise _status_error(502)
        except httpx.HTTPStatusError as cause:
            exc = DeclarativeRuntimeError("artifact_download_failed", detail=str(cause))
            exc.__cause__ = cause

        failure = classify_call_failure(exc)

        assert failure.error_code == CallErrorCode.DOWNLOAD_FAILED
        assert failure.error_params == {"status": 502}

    def test_other_declarative_codes_stay_unclassified(self) -> None:
        from lib.custom_provider.declarative_backend import DeclarativeRuntimeError

        failure = classify_call_failure(DeclarativeRuntimeError("poll_status_unreadable", detail="no status"))

        assert failure.error_code is None
        assert failure.error_params is None

    def test_download_wins_over_the_timeout_that_caused_it(self) -> None:
        # 取件耗尽的根因常是一次超时，但对用户而言这是可重试取件的「下载失败」。
        exc = ArtifactDownloadError(detail="read timed out")
        exc.__cause__ = httpx.ReadTimeout("read timed out")

        failure = classify_call_failure(exc)

        assert failure.error_code == CallErrorCode.DOWNLOAD_FAILED


class TestUnclassified:
    def test_unknown_exception_keeps_only_its_text(self) -> None:
        failure = classify_call_failure(ValueError("API 未返回图片"))

        assert failure.error_code is None
        assert failure.error_params is None
        assert failure.error_message == "API 未返回图片"

    def test_capability_code_is_not_mistaken_for_a_provider_error_code(self) -> None:
        # 仓库内的能力异常同样带 ``code`` 属性，但那是任务失败码，不属于调用失败分类。
        exc = VideoCapabilityError(code="video_duration_invalid", duration=99)

        failure = classify_call_failure(exc)

        assert failure.error_code is None
