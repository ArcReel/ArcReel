import asyncio
import itertools
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from lib.video_backends.base import (
    TERMINAL_PROVIDER_STATUSES,
    AmbiguousSubmitError,
    ProviderJobIdPersistenceMixin,
    ProviderJobStatus,
    ProviderResponseStage,
    ResumeExpiredError,
    VideoGenerationRequest,
    VideoGenerationResult,
    _dig,
    _rewrites_to_get,
    extract_provider_error_message,
    first_mapping_by_paths,
    first_str_by_paths,
    is_retryable_http_status,
    normalize_provider_status,
    persist_api_call_id,
    persist_provider_job_id,
    poll_with_retry,
    recording_poll,
    should_retry_download,
    should_retry_poll,
    should_retry_signed_download,
    should_retry_submit,
    stream_to_file,
    submit_post,
    url_origin,
    with_artifact_retry,
)
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http


class _FakeClock:
    """轮询时钟替身：sleep 只记不等，monotonic 按给定序列或固定步长推进。

    不传 times 时表按 step 无限推进——终态判定失灵的回归会在若干轮内撞上 max_wait 抛
    TimeoutError，而不是以近乎为零的真实耗时空转成挂起。
    """

    def __init__(self, times: list[float] | None = None, *, step: float = 1.0) -> None:
        self._times: Iterator[float] = iter(times) if times is not None else itertools.count(0.0, step)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return next(self._times)

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)


def _http_status_error(status_code: int, *, text: str = "boom") -> httpx.HTTPStatusError:
    """构造真实 httpx.HTTPStatusError；URL 故意含 "503" 子串以验证不再走字符串误判。"""
    request = httpx.Request("GET", "https://relay.example/v2/video/generations?generation_id=task-503")
    response = httpx.Response(status_code, request=request, text=text)
    return httpx.HTTPStatusError(f"error '{status_code}'", request=request, response=response)


def _http_status_error_with_headers(status_code: int, headers: dict[str, str]) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://relay.example/tasks/1")
    response = httpx.Response(status_code, request=request, headers=headers)
    return httpx.HTTPStatusError(f"error '{status_code}'", request=request, response=response)


class TestVideoGenerationRequest:
    def test_defaults(self):
        req = VideoGenerationRequest(prompt="test", output_path=Path("/tmp/out.mp4"))
        assert req.aspect_ratio == "9:16"
        assert req.duration_seconds == 5
        assert req.resolution is None
        assert req.start_image is None
        assert req.generate_audio is True
        assert req.reference_audio_files is None
        assert req.poll_timeout_seconds == 3600
        assert req.service_tier == "default"
        assert req.seed is None

    def test_all_fields(self):
        req = VideoGenerationRequest(
            prompt="action",
            output_path=Path("/tmp/out.mp4"),
            aspect_ratio="16:9",
            duration_seconds=8,
            resolution="720p",
            start_image=Path("/tmp/frame.png"),
            generate_audio=False,
            service_tier="flex",
            seed=42,
        )
        assert req.duration_seconds == 8
        assert req.seed == 42
        assert req.service_tier == "flex"


class TestVideoGenerationResult:
    def test_required_fields(self):
        result = VideoGenerationResult(
            video_path=Path("/tmp/out.mp4"),
            provider="gemini",
            model="veo-3.1-generate-001",
            duration_seconds=8,
        )
        assert result.video_uri is None
        assert result.seed is None
        assert result.usage_tokens is None
        assert result.task_id is None

    def test_optional_fields(self):
        result = VideoGenerationResult(
            video_path=Path("/tmp/out.mp4"),
            provider="ark",
            model="doubao-seedance-1-5-pro-251215",
            duration_seconds=5,
            video_uri="https://cdn.example.com/video.mp4",
            seed=58944,
            usage_tokens=246840,
            task_id="cgt-20250101",
        )
        assert result.usage_tokens == 246840
        assert result.task_id == "cgt-20250101"


class TestPollWithRetry:
    """poll_with_retry 通用轮询辅助函数测试。"""

    async def test_immediate_done(self):
        """poll_fn 首次返回即完成。"""
        poll_fn = AsyncMock(return_value="done_result")

        result = await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done_result",
            is_failed=lambda r: None,
            poll_interval=1,
            max_wait=10,
            clock=_FakeClock(),
        )

        assert result == "done_result"
        assert poll_fn.await_count == 1

    async def test_polls_until_done(self):
        """多次轮询后完成。"""
        poll_fn = AsyncMock(side_effect=["pending", "pending", "done"])

        result = await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done",
            is_failed=lambda r: None,
            poll_interval=1,
            max_wait=60,
            clock=_FakeClock(),
        )

        assert result == "done"
        assert poll_fn.await_count == 3

    async def test_transient_error_retries(self):
        """轮询瞬态错误后重试成功。"""
        poll_fn = AsyncMock(side_effect=[ConnectionError("reset"), "done"])

        result = await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done",
            is_failed=lambda r: None,
            poll_interval=1,
            max_wait=60,
            clock=_FakeClock(),
        )

        assert result == "done"
        assert poll_fn.await_count == 2

    async def test_non_retryable_error_propagates(self):
        """不可重试的错误立即抛出。"""
        poll_fn = AsyncMock(side_effect=ValueError("invalid"))

        with pytest.raises(ValueError, match="invalid"):
            await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda r: True,
                is_failed=lambda r: None,
                poll_interval=1,
                max_wait=60,
                clock=_FakeClock(),
            )

        assert poll_fn.await_count == 1

    async def test_timeout_raises(self):
        """超时抛出 TimeoutError。"""
        poll_fn = AsyncMock(return_value="pending")
        clock = _FakeClock([0.0, 0.0, 100.0])

        with pytest.raises(TimeoutError, match="超时"):
            await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda r: False,
                is_failed=lambda r: None,
                poll_interval=1,
                max_wait=10,
                clock=clock,
            )

        assert clock.sleeps == [1]

    async def test_sleeps_poll_interval_on_injected_clock_between_polls(self):
        poll_fn = AsyncMock(side_effect=["pending", "done"])
        clock = _FakeClock([0.0, 1.0, 2.0, 3.0])

        result = await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done",
            is_failed=lambda r: None,
            poll_interval=5,
            max_wait=10,
            on_progress=lambda _result, _elapsed: None,
            clock=clock,
        )

        assert result == "done"
        assert clock.sleeps == [5]

    async def test_failed_status_raises(self):
        """is_failed 返回错误信息时抛出 RuntimeError。"""
        poll_fn = AsyncMock(return_value="failed_result")

        with pytest.raises(RuntimeError, match="任务失败"):
            await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda r: False,
                is_failed=lambda r: "任务失败" if r == "failed_result" else None,
                poll_interval=1,
                max_wait=60,
                clock=_FakeClock(),
            )

    async def test_on_progress_called(self):
        """on_progress 每轮非终态回调一次，终态轮不回调。"""
        poll_fn = AsyncMock(side_effect=["pending", "done"])
        progress_calls = []
        clock = _FakeClock([0.0, 1.0, 2.0, 3.0])

        result = await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done",
            is_failed=lambda r: None,
            poll_interval=1,
            max_wait=60,
            on_progress=lambda r, elapsed: progress_calls.append(r),
            clock=clock,
        )

        assert result == "done"
        assert clock.sleeps == [1]
        assert progress_calls == ["pending"]

    async def test_retry_if_overrides_default_and_fails_fast(self):
        """retry_if 返回 False 时即便异常属"可重试类型"也立即抛，不重试。"""
        poll_fn = AsyncMock(side_effect=ConnectionError("would normally retry"))

        with pytest.raises(ConnectionError):
            await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda r: True,
                is_failed=lambda r: None,
                poll_interval=1,
                max_wait=60,
                retry_if=lambda e: False,
                clock=_FakeClock(),
            )

        assert poll_fn.await_count == 1

    async def test_retry_if_overrides_default_and_retries(self):
        """retry_if 返回 True 时重试，即便异常类型默认不可重试。"""
        poll_fn = AsyncMock(side_effect=[ValueError("transient"), "done"])

        result = await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done",
            is_failed=lambda r: None,
            poll_interval=1,
            max_wait=60,
            retry_if=lambda e: isinstance(e, ValueError),
            clock=_FakeClock(),
        )

        assert result == "done"
        assert poll_fn.await_count == 2

    async def test_ten_consecutive_retryable_failures_exhaust_budget(self):
        poll_fn = AsyncMock(side_effect=[ConnectionError(f"failure-{n}") for n in range(1, 11)])
        clock = _FakeClock(step=0)

        with pytest.raises(RuntimeError, match="failure-10"):
            await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda _r: False,
                is_failed=lambda _r: None,
                max_wait=3600,
                clock=clock,
            )

        assert poll_fn.await_count == 10
        assert clock.sleeps == [5, 10, 20, 40, 60, 60, 60, 60, 60]

    async def test_successful_response_resets_failure_backoff(self):
        poll_fn = AsyncMock(side_effect=[ConnectionError("first"), "pending", ConnectionError("second"), "done"])
        clock = _FakeClock(step=0)

        result = await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done",
            is_failed=lambda _r: None,
            max_wait=3600,
            clock=clock,
        )

        assert result == "done"
        assert clock.sleeps == [5, 5, 5]

    async def test_retry_after_integer_seconds_takes_precedence(self):
        poll_fn = AsyncMock(side_effect=[_http_status_error_with_headers(429, {"Retry-After": "37"}), "done"])
        clock = _FakeClock(step=0)

        result = await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done",
            is_failed=lambda _r: None,
            max_wait=3600,
            retry_if=should_retry_poll,
            clock=clock,
        )

        assert result == "done"
        assert clock.sleeps == [37]

    @pytest.mark.parametrize("retry_after", ["61", "Wed, 21 Oct 2015 07:28:00 GMT", "invalid"])
    async def test_invalid_retry_after_falls_back_to_exponential_backoff(self, retry_after: str):
        poll_fn = AsyncMock(side_effect=[_http_status_error_with_headers(429, {"Retry-After": retry_after}), "done"])
        clock = _FakeClock(step=0)

        await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done",
            is_failed=lambda _r: None,
            max_wait=3600,
            retry_if=should_retry_poll,
            clock=clock,
        )

        assert clock.sleeps == [5]

    async def test_backoff_base_follows_caller_poll_interval(self):
        """图片通道等调用方自带节奏：退避以调用方的 poll_interval 为基数，不是视频通道的 5 秒。"""
        poll_fn = AsyncMock(side_effect=[ConnectionError("a"), ConnectionError("b"), "done"])
        clock = _FakeClock(step=0)

        await poll_with_retry(
            poll_fn=poll_fn,
            is_done=lambda r: r == "done",
            is_failed=lambda _r: None,
            max_wait=3600,
            poll_interval=3,
            clock=clock,
        )

        assert clock.sleeps == [3, 6]

    async def test_backoff_never_sleeps_past_the_deadline(self):
        """退避等待受剩余预算限制：第二次退避要 10 秒而只剩 5 秒，睡 5 秒后即到截止时刻。"""
        poll_fn = AsyncMock(side_effect=[ConnectionError("a"), ConnectionError("b"), ConnectionError("c")])
        clock = _FakeClock([0.0, 0.0, 55.0, 60.0])

        with pytest.raises(TimeoutError, match="超时"):
            await poll_with_retry(
                poll_fn=poll_fn,
                is_done=lambda r: r == "done",
                is_failed=lambda _r: None,
                max_wait=60,
                poll_interval=5,
                clock=clock,
            )

        assert clock.sleeps == [5, 5]
        assert poll_fn.await_count == 3


class TestNormalizeProviderStatus:
    """跨厂商状态串归一：OpenAI 兼容代理会把底层厂商的状态串原样透传。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("completed", ProviderJobStatus.SUCCEEDED),
            ("succeeded", ProviderJobStatus.SUCCEEDED),
            ("succeed", ProviderJobStatus.SUCCEEDED),
            ("success", ProviderJobStatus.SUCCEEDED),
            ("SUCCEEDED", ProviderJobStatus.SUCCEEDED),
            ("  succeeded  ", ProviderJobStatus.SUCCEEDED),
            ("failed", ProviderJobStatus.FAILED),
            ("fail", ProviderJobStatus.FAILED),
            ("error", ProviderJobStatus.FAILED),
            ("FAILED", ProviderJobStatus.FAILED),
            ("canceled", ProviderJobStatus.FAILED),
            ("cancelled", ProviderJobStatus.FAILED),
            ("in_progress", ProviderJobStatus.RUNNING),
            ("Processing", ProviderJobStatus.RUNNING),
            ("generating", ProviderJobStatus.RUNNING),
            ("PENDING", ProviderJobStatus.QUEUED),
            ("submitted", ProviderJobStatus.QUEUED),
            # 未知 / 非字符串 → 当 running 继续轮询（保守：不对未就绪任务触发下载）
            ("NOT_START", ProviderJobStatus.RUNNING),
            ("weird-status", ProviderJobStatus.RUNNING),
            (None, ProviderJobStatus.RUNNING),
            (99, ProviderJobStatus.RUNNING),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_provider_status(raw) is expected

    @pytest.mark.parametrize("raw", ["expired", "EXPIRED", " Expired "])
    def test_expired_is_its_own_bucket(self, raw):
        """expired 不得折进 failed：caller 据其按 generate / resume 分流抛不同异常。"""
        assert normalize_provider_status(raw) is ProviderJobStatus.EXPIRED

    def test_terminal_set(self):
        assert (
            frozenset({ProviderJobStatus.SUCCEEDED, ProviderJobStatus.FAILED, ProviderJobStatus.EXPIRED})
            == TERMINAL_PROVIDER_STATUSES
        )
        assert ProviderJobStatus.RUNNING not in TERMINAL_PROVIDER_STATUSES
        assert ProviderJobStatus.QUEUED not in TERMINAL_PROVIDER_STATUSES


class TestDigAndFirstStrByPaths:
    def test_walks_dict_and_list_index(self):
        payload = {"data": {"videos": [{"url": "u0"}, {"url": "u1"}]}}
        assert _dig(payload, ("data", "videos", 1, "url")) == "u1"

    def test_missing_segment_returns_none(self):
        assert _dig({"a": 1}, ("a", "b")) is None

    def test_first_str_by_paths_priority(self):
        paths = (("url",), ("data", "result_url"))
        assert first_str_by_paths({"url": "a", "data": {"result_url": "b"}}, paths) == "a"
        assert first_str_by_paths({"data": {"result_url": "b"}}, paths) == "b"
        assert first_str_by_paths({"url": "   ", "data": {"result_url": "b"}}, paths) == "b"
        assert first_str_by_paths({"foo": "bar"}, paths) is None

    def test_first_mapping_by_paths_priority(self):
        paths = (("metadata",), ("data", "metadata"))
        assert first_mapping_by_paths({"metadata": {"seed": 1}, "data": {"metadata": {"seed": 2}}}, paths) == {
            "seed": 1
        }
        assert first_mapping_by_paths({"data": {"metadata": {"seed": 2}}}, paths) == {"seed": 2}
        assert first_mapping_by_paths({"metadata": "not-a-mapping", "data": {"metadata": {"seed": 2}}}, paths) == {
            "seed": 2
        }
        assert first_mapping_by_paths({"foo": "bar"}, paths) is None


class TestExtractProviderErrorMessage:
    def test_dict_error_message(self):
        assert extract_provider_error_message({"error": {"code": 500, "message": "upstream down"}}) == "upstream down"

    def test_dict_error_name_fallback(self):
        assert extract_provider_error_message({"error": {"name": "moderation"}}) == "moderation"

    def test_blank_message_falls_back_to_name(self):
        assert extract_provider_error_message({"error": {"message": "   ", "name": "moderation"}}) == "moderation"

    def test_non_string_message_falls_back_to_name(self):
        assert (
            extract_provider_error_message({"error": {"message": {"detail": "x"}, "name": "moderation"}})
            == "moderation"
        )

    def test_string_error(self):
        assert extract_provider_error_message({"error": " boom "}) == "boom"

    def test_wrapped_error(self):
        assert extract_provider_error_message({"data": {"error": {"message": "nested"}}}) == "nested"

    def test_missing_error_is_unknown(self):
        assert extract_provider_error_message({"status": "failed"}) == "unknown"


class TestIsRetryableHttpStatus:
    """is_retryable_http_status 状态码分类。"""

    def test_transient_statuses_retry(self):
        for code in (408, 425, 429, 500, 502, 503, 504):
            assert is_retryable_http_status(code) is True
            assert is_retryable_http_status(code, retry_not_found=True) is True

    def test_deterministic_4xx_fail_fast(self):
        for code in (400, 401, 403, 405, 409, 422):
            assert is_retryable_http_status(code) is False
            assert is_retryable_http_status(code, retry_not_found=True) is False

    def test_404_depends_on_retry_not_found(self):
        assert is_retryable_http_status(404) is False
        assert is_retryable_http_status(404, retry_not_found=True) is True


class TestRetryPredicates:
    """should_retry_submit / should_retry_poll 中转视频后端重试谓词。

    submit 是非幂等的「创建 + 计费」：传输错误只重试「请求确定未送达」的子集；
    poll 是幂等 GET：传输错误一律重试。
    """

    def test_deterministic_4xx_fail_fast(self):
        for code in (400, 401, 403, 422):
            err = _http_status_error(code)
            assert should_retry_submit(err) is False
            assert should_retry_poll(err) is False

    def test_404_submit_fail_fast_poll_retries(self):
        err = _http_status_error(404)
        assert should_retry_submit(err) is False
        assert should_retry_poll(err) is True

    def test_transient_http_retries(self):
        for code in (408, 425, 429, 500, 503):
            err = _http_status_error(code)
            assert should_retry_submit(err) is True
            assert should_retry_poll(err) is True

    def test_submit_retries_only_not_sent_transport_errors(self):
        # 连接建立失败 / 从未取得连接 / 代理握手失败 → 请求确定未送达 → submit 重试安全。
        for exc in (
            httpx.ConnectError("refused"),
            httpx.ConnectTimeout("connect timed out"),
            httpx.PoolTimeout("pool exhausted"),
            httpx.ProxyError("proxy handshake failed"),
        ):
            assert should_retry_submit(exc) is True

    def test_submit_does_not_retry_ambiguous_transport_errors(self):
        # 请求可能已送达服务端（已建任务 / 已计费）→ submit 不重试，避免重复计费。
        # 注意：实际 create 路径中这些原始异常会先被 submit_post 包成 AmbiguousSubmitError 再
        # 进重试谓词，should_retry_submit 运行时并不会直接收到它们；此处单独断言谓词对原始异常
        # 的防御性行为（文档化兜底），与 submit_post 的包装互为双保险。
        for exc in (
            httpx.ReadTimeout("read timed out"),
            httpx.WriteTimeout("write timed out"),
            httpx.ReadError("conn reset mid-read"),
            httpx.WriteError("conn reset mid-write"),
            httpx.RemoteProtocolError("server disconnected"),
            ConnectionError(),  # 内建：歧义，不重试
            TimeoutError(),  # 内建：歧义，不重试
        ):
            assert should_retry_submit(exc) is False

    def test_poll_retries_all_transport_errors(self):
        # 幂等 GET：连接建立失败与读/写阶段错误一律重试。
        for exc in (
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("read timed out"),
            httpx.RemoteProtocolError("server disconnected"),
            ConnectionError(),
            TimeoutError(),
        ):
            assert should_retry_poll(exc) is True

    def test_ambiguous_submit_error_never_retries(self):
        # AmbiguousSubmitError 是终态：被装饰器捕获后谓词须返回 False，不再重试。
        assert should_retry_submit(AmbiguousSubmitError(provider="v2")) is False
        assert should_retry_poll(AmbiguousSubmitError(provider="v2")) is False

    def test_local_protocol_errors_fail_fast_both_paths(self):
        # UnsupportedProtocol / LocalProtocolError 在请求发出前就确定失败（均为 RequestError
        # 子类），两条路径都快速失败——poll 不该重试到 max_wait，submit 也无重复计费风险。
        for exc in (
            httpx.UnsupportedProtocol("scheme not http(s)"),
            httpx.LocalProtocolError("bad local request"),
        ):
            assert should_retry_poll(exc) is False
            assert should_retry_submit(exc) is False

    def test_poll_still_retries_remote_protocol_error(self):
        # RemoteProtocolError 与 LocalProtocolError 同为 ProtocolError 子类，但属「服务端中途
        # 断开」，幂等 GET 重试安全——确认本地错误的排除没有误伤它。
        assert should_retry_poll(httpx.RemoteProtocolError("server disconnected")) is True

    def test_business_exceptions_fail_fast(self):
        # ResumeExpiredError 的 job_id 含 "503" 子串：旧字符串兜底会误判重试，新谓词不会。
        resume_exc = ResumeExpiredError(job_id="job-503", provider="v2")
        assert should_retry_poll(resume_exc) is False
        assert should_retry_submit(resume_exc) is False
        # 普通异常即便消息含状态码子串也不重试（绕开字符串误判）。
        assert should_retry_poll(ValueError("503 in message")) is False
        assert should_retry_submit(RuntimeError("got 500 somewhere")) is False


class TestShouldRetryDownload:
    """should_retry_download 下载阶段谓词：403/404 未就绪及瞬态错误重试。"""

    def test_download_retries_not_ready_403_and_404(self):
        for code in (400, 401, 413, 422):
            assert should_retry_download(_http_status_error(code)) is False
        for code in (403, 404):
            assert should_retry_download(_http_status_error(code)) is True

    def test_transient_http_retries(self):
        for code in (408, 425, 429, 500, 502, 503, 504):
            assert should_retry_download(_http_status_error(code)) is True

    def test_retries_all_transport_errors(self):
        # 幂等 GET 下载：连接建立失败与读阶段错误一律重试（与 poll 一致，无重复计费风险）。
        for exc in (
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("read timed out"),
            httpx.RemoteProtocolError("server disconnected"),
            ConnectionError(),
            TimeoutError(),
        ):
            assert should_retry_download(exc) is True

    def test_local_protocol_errors_and_business_exceptions_fail_fast(self):
        assert should_retry_download(httpx.UnsupportedProtocol("scheme")) is False
        assert should_retry_download(httpx.LocalProtocolError("bad")) is False
        # 普通异常即便消息含状态码子串也不重试（绕开字符串误判）。
        assert should_retry_download(ValueError("503 in message")) is False


class TestShouldRetrySignedDownload:
    """should_retry_signed_download：预签发 URL 的 4xx 一律确定性失败，瞬态码照常重试。"""

    def test_all_client_errors_fail_fast(self):
        for code in (400, 401, 403, 404, 413, 422):
            assert should_retry_signed_download(_http_status_error(code)) is False

    def test_transient_codes_still_retry(self):
        # 429/408/425 是限流与瞬态，不属于「签名 URL 确定性失败」，与 5xx 同样重试。
        for code in (408, 425, 429, 500, 503):
            assert should_retry_signed_download(_http_status_error(code)) is True

    def test_transport_errors_retry_and_local_protocol_errors_fail_fast(self):
        assert should_retry_signed_download(httpx.ConnectError("refused")) is True
        assert should_retry_signed_download(httpx.UnsupportedProtocol("scheme")) is False


class TestSubmitPost:
    """submit_post：create/提交阶段按「请求是否确定送达」给失败分流。"""

    async def test_returns_response_on_success(self):
        resp = httpx.Response(200, request=httpx.Request("POST", "https://x/v2"), json={"id": "ok"})
        recorded: list[tuple[str, object]] = []

        async def _post() -> httpx.Response:
            return resp

        async def _record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

        request = VideoGenerationRequest(prompt="p", output_path=Path("out.mp4"), on_provider_response=_record)

        assert await submit_post(_post, provider="v2", request=request) is resp
        assert recorded == [("submit", {"id": "ok"})]

    async def test_not_sent_error_propagates_for_retry(self):
        # 连接建立失败 / 代理握手失败原样抛出，交 should_retry_submit 重试（不包成终态）。
        for exc in (httpx.ConnectError("refused"), httpx.ProxyError("proxy handshake failed")):

            async def _post(_exc: httpx.RequestError = exc) -> httpx.Response:
                raise _exc

            with pytest.raises(type(exc)):
                await submit_post(_post, provider="v2")

    async def test_local_protocol_error_propagates_raw_not_ambiguous(self):
        # 本地/协议错误请求发出前就失败、无计费风险 → 原样抛出，不包成 AmbiguousSubmitError
        # （否则会误导运维去供应商侧确认一个从未创建的任务）。
        for exc in (
            httpx.UnsupportedProtocol("scheme not http(s)"),
            httpx.LocalProtocolError("bad local request"),
        ):

            async def _post(_exc: httpx.RequestError = exc) -> httpx.Response:
                raise _exc

            with pytest.raises(type(exc)):
                await submit_post(_post, provider="v2")

    async def test_ambiguous_error_wrapped_with_manual_retry_hint(self):
        # ReadTimeout（请求可能已送达）包成 AmbiguousSubmitError，消息含手动重试提示。
        async def _post() -> httpx.Response:
            raise httpx.ReadTimeout("read timed out")

        with pytest.raises(AmbiguousSubmitError) as excinfo:
            await submit_post(_post, provider="newapi")
        msg = str(excinfo.value)
        assert "[create_ambiguous]" in msg
        assert "手动重试" in msg
        assert isinstance(excinfo.value.__cause__, httpx.ReadTimeout)

    async def test_http_status_error_propagates_for_status_gate(self):
        # >=400 响应经 raise_for_status 抛 HTTPStatusError，交 should_retry_submit 按 status_code 分流。
        request = httpx.Request("POST", "https://x/v2")
        resp = httpx.Response(503, request=request, text="upstream busy")

        async def _post() -> httpx.Response:
            return resp

        with pytest.raises(httpx.HTTPStatusError):
            await submit_post(_post, provider="v2")


def _make_operational_error(msg: str) -> OperationalError:
    """构造 sqlalchemy OperationalError（params/orig/connection 仅签名形式占位）。"""
    return OperationalError(msg, params=None, orig=Exception(msg))


class TestPersistJobIdRetry:
    """persist_provider_job_id 在 DB 瞬态错误下重试 + 结构化日志。"""

    async def test_retries_on_sqlite_locked(self, caplog):
        """前 2 次 OperationalError → 第 3 次成功；retry 实际执行 3 次。"""
        attempts = 0

        async def _flaky_persist(_tid: str, _job: str) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise _make_operational_error("database is locked")

        class _FakeQueue:
            async def persist_provider_job_id(
                self, tid: str, job_id: str, *, endpoint: str | None = None, base_url: str | None = None
            ) -> None:
                await _flaky_persist(tid, job_id)

        fake_queue = _FakeQueue()

        with (
            patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            caplog.at_level(logging.INFO, logger="lib.video_backends.base"),
        ):
            await persist_provider_job_id("task-1", "job-1", provider="openai")

        assert attempts == 3
        assert any("provider_job_id 已持久化" in r.message for r in caplog.records)

    async def test_terminal_failure_logs_structured(self, caplog):
        """全部重试失败 → logger.error 记录 task_id / provider / job_id 三键 + 重抛。"""

        async def _always_fail(_tid: str, _job: str) -> None:
            raise _make_operational_error("database is locked")

        class _FailingQueue:
            async def persist_provider_job_id(
                self, tid: str, job_id: str, *, endpoint: str | None = None, base_url: str | None = None
            ) -> None:
                await _always_fail(tid, job_id)

        fake_queue = _FailingQueue()

        with (
            patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            caplog.at_level(logging.ERROR, logger="lib.video_backends.base"),
            pytest.raises(OperationalError),
        ):
            await persist_provider_job_id("task-X", "job-X", provider="ark")

        terminal = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert terminal, "expected logger.error call"
        msg = terminal[-1].message
        assert "task_id=task-X" in msg
        assert "provider=ark" in msg
        assert "job_id=job-X" in msg

    async def test_no_retry_for_value_error(self):
        """ValueError 不在 retryable_errors 内 → 立即抛出，retry 仅尝试 1 次。"""
        attempts = 0

        async def _bad(_tid: str, _job: str) -> None:
            nonlocal attempts
            attempts += 1
            raise ValueError("not retryable")

        class _BadQueue:
            async def persist_provider_job_id(
                self, tid: str, job_id: str, *, endpoint: str | None = None, base_url: str | None = None
            ) -> None:
                await _bad(tid, job_id)

        fake_queue = _BadQueue()

        with (
            patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            pytest.raises(ValueError, match="not retryable"),
        ):
            await persist_provider_job_id("task-V", "job-V", provider="newapi")

        assert attempts == 1

    async def test_no_retry_for_value_error_with_transient_string(self):
        """业务异常即使消息含 ``timed out`` / ``503`` 等串，也不该被字符串兜底吞掉重试。

        默认 `_should_retry` 在 isinstance 不匹配时做 RETRYABLE_STATUS_PATTERNS 字符串
        子串兜底，会把 `ValueError("Connection timed out: rate")` 当瞬态错误重试；
        改用 `retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS)` 后严格 isinstance。
        """
        attempts = 0

        async def _bad(_tid: str, _job: str) -> None:
            nonlocal attempts
            attempts += 1
            raise ValueError("Connection timed out: rate limited at upstream")

        class _BadQueue:
            async def persist_provider_job_id(
                self, tid: str, job_id: str, *, endpoint: str | None = None, base_url: str | None = None
            ) -> None:
                await _bad(tid, job_id)

        fake_queue = _BadQueue()

        with (
            patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            pytest.raises(ValueError, match="timed out"),
        ):
            await persist_provider_job_id("task-T", "job-T", provider="gemini")

        assert attempts == 1, "expects no string-fallback retry for ValueError"


class TestProviderJobIdPersistenceMixin:
    """提交-轮询型 video backend 的持久化收口点：单一统一调用点承接 None 判断 + 写回 + fail-fast。"""

    def _backend(self) -> ProviderJobIdPersistenceMixin:
        # 裸 mixin 实例即可——_persist_provider_job_id 不依赖任何子类状态。
        return ProviderJobIdPersistenceMixin()

    def _request(self, *, task_id: str | None) -> VideoGenerationRequest:
        return VideoGenerationRequest(prompt="p", output_path=Path("/tmp/out.mp4"), task_id=task_id)

    async def test_worker_path_persists_via_module_helper(self):
        """worker 路径（task_id 非空）经统一点转调模块级 persist_provider_job_id。"""
        with captured_provider_job_ids() as persisted:
            await self._backend()._persist_provider_job_id(
                self._request(task_id="local-task-1"), "job-1", provider="ark"
            )
        assert persisted == [
            {
                "task_id": "local-task-1",
                "job_id": "job-1",
                "provider": "ark",
                "endpoint": None,
                "base_url": None,
            }
        ]

    async def test_worker_path_persists_execution_endpoint(self):
        """自定义供应商包装层注入的 endpoint 与 job_id 一并落库，供续跑比对协议是否被换掉。"""
        request = self._request(task_id="local-task-1")
        request.execution_endpoint = "openai-video"
        with captured_provider_job_ids() as persisted:
            await self._backend()._persist_provider_job_id(request, "job-1", provider="ark")
        assert [(r["endpoint"], r["base_url"]) for r in persisted] == [("openai-video", None)]

    async def test_worker_path_persists_backend_domain_when_builtin(self):
        """内置供应商由 backend 传入实际请求域名 → 落域名列供续跑回放，协议标识位保持空。"""
        with captured_provider_job_ids() as persisted:
            await self._backend()._persist_provider_job_id(
                self._request(task_id="local-task-1"),
                "job-1",
                provider="dashscope",
                endpoint="https://maas.example.com/api/v1",
            )
        assert [(r["endpoint"], r["base_url"]) for r in persisted] == [(None, "https://maas.example.com/api/v1")]

    async def test_execution_endpoint_and_backend_domain_land_in_separate_columns(self):
        """自定义供应商：协议标识走 endpoint 位供比对，域名走 base_url 位供回放，互不覆盖。"""
        request = self._request(task_id="local-task-1")
        request.execution_endpoint = "dashscope-async-video"
        with captured_provider_job_ids() as persisted:
            await self._backend()._persist_provider_job_id(
                request, "job-1", provider="dashscope", endpoint="https://maas.example.com/api/v1"
            )
        assert [(r["endpoint"], r["base_url"]) for r in persisted] == [
            ("dashscope-async-video", "https://maas.example.com/api/v1")
        ]

    async def test_non_worker_path_skips_persist(self):
        """非 worker 路径（grid / 直生 / 测试，task_id=None）跳过持久化，不触碰 DB。"""
        with captured_provider_job_ids() as persisted:
            await self._backend()._persist_provider_job_id(self._request(task_id=None), "job-1", provider="ark")
        assert persisted == []

    async def test_persist_failure_propagates_fail_fast(self):
        """持久化失败抛出原异常，由 worker finally 兜底 mark_failed（fail-fast，不吞）。"""
        boom = _make_operational_error("database is locked")
        with (
            patch("lib.video_backends.base.persist_provider_job_id", new=AsyncMock(side_effect=boom)),
            pytest.raises(OperationalError),
        ):
            await self._backend()._persist_provider_job_id(
                self._request(task_id="local-task-1"), "job-1", provider="gemini"
            )


class TestPersistApiCallIdRetry:
    """persist_api_call_id 与 persist_provider_job_id 对齐：DB 瞬态错误重试 + fail-fast 抛异常。

    Fail-fast 理由：submit 已经把 provider 端任务排队（cost 已扣），caller media_generator
    在 try 块内捕获到本异常会 finish_call(failed) 把 pending ApiCall 翻 failed 再 raise，
    异常冒泡到 worker finally 兜底 mark_failed；若这里吞掉异常，crash window 内 resume
    路径无 api_call_id 锚定将永远留 pending 账目。
    """

    async def test_retries_on_sqlite_locked(self, caplog):
        """前 2 次 OperationalError → 第 3 次成功；retry 实际执行 3 次。"""
        attempts = 0

        async def _flaky_persist(_tid: str, _call_id: int) -> None:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise _make_operational_error("database is locked")

        class _FakeQueue:
            async def persist_api_call_id(self, tid: str, call_id: int) -> None:
                await _flaky_persist(tid, call_id)

        fake_queue = _FakeQueue()

        with (
            patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            caplog.at_level(logging.INFO, logger="lib.video_backends.base"),
        ):
            await persist_api_call_id("task-1", 42)

        assert attempts == 3
        assert any("api_call_id 已持久化" in r.message for r in caplog.records)

    async def test_terminal_failure_raises_and_logs(self, caplog):
        """全部重试失败 → logger.error 记录 + 重抛（fail-fast，对齐 persist_provider_job_id）。"""

        async def _always_fail(_tid: str, _call_id: int) -> None:
            raise _make_operational_error("database is locked")

        class _FailingQueue:
            async def persist_api_call_id(self, tid: str, call_id: int) -> None:
                await _always_fail(tid, call_id)

        fake_queue = _FailingQueue()

        with (
            patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            caplog.at_level(logging.ERROR, logger="lib.video_backends.base"),
            pytest.raises(OperationalError),
        ):
            await persist_api_call_id("task-X", 99)

        terminal = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert terminal, "expected logger.error call"
        msg = terminal[-1].message
        assert "task_id=task-X" in msg
        assert "call_id=99" in msg

    async def test_no_retry_for_value_error(self):
        """ValueError 不在 retryable_errors 内 → 立即抛出，retry 仅尝试 1 次。"""
        attempts = 0

        async def _bad(_tid: str, _call_id: int) -> None:
            nonlocal attempts
            attempts += 1
            raise ValueError("not retryable")

        class _BadQueue:
            async def persist_api_call_id(self, tid: str, call_id: int) -> None:
                await _bad(tid, call_id)

        fake_queue = _BadQueue()

        with (
            patch("lib.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            pytest.raises(ValueError, match="not retryable"),
        ):
            await persist_api_call_id("task-V", 7)

        assert attempts == 1


class TestRedirectMethodRewrite:
    """跟随重定向时的方法改写规则，与 httpx / RFC 9110 同口径。"""

    @pytest.mark.parametrize(
        ("status_code", "method", "expected"),
        [
            (303, "POST", True),
            (303, "PUT", True),
            (303, "HEAD", False),
            (301, "POST", True),
            (302, "POST", True),
            # PUT 在 301/302 上保留方法与请求体：改写会让端点收到语义完全不同的请求。
            (301, "PUT", False),
            (302, "PUT", False),
            (307, "POST", False),
            (308, "POST", False),
        ],
    )
    def test_rewrite_rule(self, status_code: int, method: str, expected: bool):
        assert _rewrites_to_get(status_code, method) is expected


class TestWithArtifactRetry:
    async def test_single_slow_attempt_is_cut_off_at_max_wait(self):
        """一次取件迟迟不返回时也要受墙钟上限约束——poll_with_retry 只在返回后才比对预算。

        判据不靠真实等待：预算给 0，取件停在一个永不置位的事件上，截止时刻即刻到达。
        """
        never_ready = asyncio.Event()

        async def never_returns() -> None:
            await never_ready.wait()

        with pytest.raises(TimeoutError):
            await with_artifact_retry(never_returns, label="slow", max_wait=0)

        assert not never_ready.is_set()


class TestStreamToFile:
    async def test_writes_body_and_leaves_no_partial_file(self, tmp_path: Path):
        output = tmp_path / "nested" / "out.mp4"
        payload = b"x" * (9 * 1024 * 1024)  # 跨过攒批阈值，走多次落盘

        with capture_http() as router:
            router.get("https://cdn.test/a.mp4").mock(return_value=httpx.Response(200, content=payload))
            async with httpx.AsyncClient() as client:
                await stream_to_file(client, "https://cdn.test/a.mp4", output)

        assert output.read_bytes() == payload
        assert not (output.parent / f"{output.name}.part").exists()

    async def test_redirect_without_location_is_not_written_as_the_artifact(self, tmp_path: Path):
        """raise_for_status 放行 3xx：没有 Location 就无处可跳，跳转响应体不能当成片存下来。"""
        output = tmp_path / "out.mp4"

        with capture_http() as router:
            router.get("https://relay.test/a.mp4").mock(return_value=httpx.Response(302, content=b"not a video"))
            async with httpx.AsyncClient() as client:
                with pytest.raises(RuntimeError, match="redirect without a Location header"):
                    await stream_to_file(
                        client,
                        "https://relay.test/a.mp4",
                        output,
                        headers={"X-API-Key": "secret"},
                        credential_origin=url_origin("https://relay.test"),
                    )

        assert not output.exists()

    async def test_failed_download_leaves_the_artifact_path_untouched(self, tmp_path: Path):
        output = tmp_path / "out.mp4"

        with capture_http() as router:
            router.get("https://cdn.test/a.mp4").mock(return_value=httpx.Response(404, text="gone"))
            async with httpx.AsyncClient() as client:
                with pytest.raises(httpx.HTTPStatusError):
                    await stream_to_file(client, "https://cdn.test/a.mp4", output)

        assert not output.exists()
        assert not (tmp_path / f"{output.name}.part").exists()


class TestRecordingPoll:
    """轮询留痕：每收到一次响应即写入，早于状态与错误解读。"""

    @staticmethod
    def _request(recorded: list[tuple[str, object]]) -> VideoGenerationRequest:
        async def _record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

        return VideoGenerationRequest(
            prompt="p",
            output_path=Path("out.mp4"),
            task_id="task-R",
            on_provider_response=_record,
        )

    async def test_terminal_failure_body_is_recorded_before_it_raises(self):
        """终态失败由 is_failed 在 poll_with_retry 内抛出，留痕仍须拿到该响应体。"""
        recorded: list[tuple[str, object]] = []
        body = {"status": "failed", "error": "provider said no"}

        with pytest.raises(RuntimeError, match="provider said no"):
            await poll_with_retry(
                poll_fn=recording_poll(AsyncMock(return_value=body), self._request(recorded)),
                is_done=lambda r: r["status"] == "failed",
                is_failed=lambda r: r.get("error"),
                poll_interval=1,
                max_wait=60,
                clock=_FakeClock(),
            )

        assert recorded == [("poll", body)]

    async def test_http_error_response_body_is_recorded(self):
        """4xx/5xx 响应从 poll_fn 自己抛出，同样是需要诊断的那次调用。"""
        recorded: list[tuple[str, object]] = []
        response = httpx.Response(500, json={"detail": "boom"}, request=httpx.Request("GET", "https://x/t"))
        error = httpx.HTTPStatusError("500", request=response.request, response=response)

        with pytest.raises(httpx.HTTPStatusError):
            await recording_poll(AsyncMock(side_effect=error), self._request(recorded))()

        assert recorded == [("poll", {"detail": "boom"})]

    async def test_non_json_http_error_body_is_recorded_as_text(self):
        recorded: list[tuple[str, object]] = []
        response = httpx.Response(502, text="<html>gateway</html>", request=httpx.Request("GET", "https://x/t"))
        error = httpx.HTTPStatusError("502", request=response.request, response=response)

        with pytest.raises(httpx.HTTPStatusError):
            await recording_poll(AsyncMock(side_effect=error), self._request(recorded))()

        assert recorded == [("poll", "<html>gateway</html>")]

    async def test_result_response_uses_requested_stage(self):
        recorded: list[tuple[str, object]] = []

        await recording_poll(
            AsyncMock(return_value={"video_url": "https://cdn.test/video.mp4"}),
            self._request(recorded),
            stage="result",
        )()

        assert recorded == [("result", {"video_url": "https://cdn.test/video.mp4"})]

    async def test_diagnostic_write_failure_does_not_fail_the_generation(self):
        """留痕列写不进去时任务照常推进——供应商已受理的生成不因诊断数据失败。"""
        request = VideoGenerationRequest(
            prompt="p",
            output_path=Path("out.mp4"),
            task_id="task-R",
            on_provider_response=AsyncMock(side_effect=OperationalError("stmt", {}, Exception("locked"))),
        )

        assert await recording_poll(AsyncMock(return_value={"status": "ok"}), request)() == {"status": "ok"}
