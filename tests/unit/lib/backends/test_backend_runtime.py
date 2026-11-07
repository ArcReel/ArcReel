import asyncio
import itertools
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy.exc import OperationalError

from arcreel_market_core.video_backend_contract import (
    ProviderResponseStage,
    ResumeExpiredError,
    VideoGenerationRequest,
)
from lib.backends.artifact_download_guard import (
    ERROR_BODY_MAX_BYTES,
    VIDEO_ARTIFACT_MAX_BYTES,
    ArtifactDestinationRejectedError,
    ArtifactTooLargeError,
)
from lib.backends.backend_runtime import (
    PROVIDER_REASON_MAX_CHARS,
    ProviderJobIdPersistenceMixin,
    _dig,
    _rewrites_to_get,
    download_resumable_video,
    download_video,
    extract_provider_error_message,
    first_mapping_by_paths,
    first_str_by_paths,
    is_retryable_http_status,
    persist_provider_job_id,
    poll_with_retry,
    provider_reason_summary,
    recording_poll,
    request_with_scoped_credentials,
    resume_expiry_gate,
    should_retry_download,
    should_retry_poll,
    should_retry_signed_download,
    should_retry_submit,
    stream_to_file,
    submit_post,
    url_origin,
    with_artifact_retry,
)
from lib.backends.http_status_errors import AmbiguousSubmitError, ProviderRejectedError
from tests.fakes import bounded_poll_clock, captured_provider_job_ids
from tests.http_capture import capture_http, only_request, request_json


class _FakeClock:
    """Polling clock substitute records sleeps without waiting; monotonic follows supplied times or fixed steps.

    Without times, advance indefinitely by step so broken terminal detection reaches max_wait
    within a few rounds and raises TimeoutError rather than busy-looping forever in near-zero real time.
    """

    def __init__(self, times: list[float] | None = None, *, step: float = 1.0) -> None:
        self._times: Iterator[float] = iter(times) if times is not None else itertools.count(0.0, step)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return next(self._times)

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)


def _http_status_error(status_code: int, *, text: str = "boom") -> httpx.HTTPStatusError:
    """Construct real httpx.HTTPStatusError with deliberate URL 503 substrings, proving no string-based misclassification."""
    request = httpx.Request("GET", "https://relay.example/v2/video/generations?generation_id=task-503")
    response = httpx.Response(status_code, request=request, text=text)
    return httpx.HTTPStatusError(f"error '{status_code}'", request=request, response=response)


def _http_status_error_with_headers(status_code: int, headers: dict[str, str]) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://relay.example/tasks/1")
    response = httpx.Response(status_code, request=request, headers=headers)
    return httpx.HTTPStatusError(f"error '{status_code}'", request=request, response=response)


class TestPollWithRetry:
    """poll_with_retry helper tests."""

    async def test_immediate_done(self):
        """poll_fn completes on its first response."""
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
        """Complete after repeated polls."""
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
        """Retry transient polling errors successfully."""
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
        """Nonretryable errors raise immediately."""
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
        """Timeouts raise TimeoutError."""
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
        """is_failed messages raise RuntimeError."""
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
        """on_progress fires once per nonterminal round, never for terminal rounds."""
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
        """retry_if False raises immediately even for otherwise retryable exception types."""
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
        """retry_if True retries even otherwise nonretryable types."""
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
        """Image callers supply cadence: backoff bases on poll_interval, never fixed video 5-second cadence."""
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
        """Clamp backoff to remaining budgets: requested 10 seconds with 5 remaining sleeps 5 and reaches deadline."""
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
    """is_retryable_http_status classification."""

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
    """Relay-video should_retry_submit/should_retry_poll predicates.

    Submit creates/bills nonidempotently: retry only transport failures proving nondelivery.
    Poll uses idempotent GET and retries all transport failures.
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
        # Failed connection creation/acquisition/proxy handshake proves nondelivery, making submit retries safe.
        for exc in (
            httpx.ConnectError("refused"),
            httpx.ConnectTimeout("connect timed out"),
            httpx.PoolTimeout("pool exhausted"),
            httpx.ProxyError("proxy handshake failed"),
        ):
            assert should_retry_submit(exc) is True

    def test_submit_does_not_retry_ambiguous_transport_errors(self):
        # Possible delivered requests may already create/bill tasks; never retry submit and duplicate charges.
        # Real creation wraps these raw errors as AmbiguousSubmitError through submit_post before predicates;
        # runtime should_retry_submit does not directly receive them. Assert defensive raw-error behavior
        # separately as documented fallback, complementing submit_post wrapping.
        for exc in (
            httpx.ReadTimeout("read timed out"),
            httpx.WriteTimeout("write timed out"),
            httpx.ReadError("conn reset mid-read"),
            httpx.WriteError("conn reset mid-write"),
            httpx.RemoteProtocolError("server disconnected"),
            ConnectionError(),  # Built-in ambiguity, never retry.
            TimeoutError(),  # Built-in ambiguity, never retry.
        ):
            assert should_retry_submit(exc) is False

    def test_poll_retries_all_transport_errors(self):
        # Idempotent GET retries connection/read/write transport errors alike.
        for exc in (
            httpx.ConnectError("refused"),
            httpx.ReadTimeout("read timed out"),
            httpx.RemoteProtocolError("server disconnected"),
            ConnectionError(),
            TimeoutError(),
        ):
            assert should_retry_poll(exc) is True

    def test_ambiguous_submit_error_never_retries(self):
        # Terminal AmbiguousSubmitError makes decorator predicates return False.
        assert should_retry_submit(AmbiguousSubmitError(provider="v2")) is False
        assert should_retry_poll(AmbiguousSubmitError(provider="v2")) is False

    def test_local_protocol_errors_fail_fast_both_paths(self):
        # UnsupportedProtocol/LocalProtocolError are pre-send RequestError subclasses; both paths fail fast,
        # neither polling to max_wait nor risking duplicate billed submission.
        for exc in (
            httpx.UnsupportedProtocol("scheme not http(s)"),
            httpx.LocalProtocolError("bad local request"),
        ):
            assert should_retry_poll(exc) is False
            assert should_retry_submit(exc) is False

    def test_poll_still_retries_remote_protocol_error(self):
        # RemoteProtocolError also subclasses ProtocolError but means midrequest server disconnect;
        # idempotent GET safely retries it, verifying local-error exclusions do not overreach.
        assert should_retry_poll(httpx.RemoteProtocolError("server disconnected")) is True

    def test_business_exceptions_fail_fast(self):
        # ResumeExpiredError job_id 503 substrings must not trigger string-fallback retries.
        resume_exc = ResumeExpiredError(job_id="job-503", provider="v2")
        assert should_retry_poll(resume_exc) is False
        assert should_retry_submit(resume_exc) is False
        # Ordinary exceptions never retry solely due to status-like message substrings.
        assert should_retry_poll(ValueError("503 in message")) is False
        assert should_retry_submit(RuntimeError("got 500 somewhere")) is False


class TestShouldRetryDownload:
    """should_retry_download retries unready 403/404 and transient download errors."""

    def test_download_retries_not_ready_403_and_404(self):
        for code in (400, 401, 413, 422):
            assert should_retry_download(_http_status_error(code)) is False
        for code in (403, 404):
            assert should_retry_download(_http_status_error(code)) is True

    def test_transient_http_retries(self):
        for code in (408, 425, 429, 500, 502, 503, 504):
            assert should_retry_download(_http_status_error(code)) is True

    def test_retries_all_transport_errors(self):
        # Idempotent GET downloads retry connection/read failures like polling, without repeat billing.
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
        # Ordinary exceptions never retry solely due to status-like message substrings.
        assert should_retry_download(ValueError("503 in message")) is False


class TestShouldRetrySignedDownload:
    """Signed-download 4xx is deterministic; transient statuses still retry."""

    def test_all_client_errors_fail_fast(self):
        for code in (400, 401, 403, 404, 413, 422):
            assert should_retry_signed_download(_http_status_error(code)) is False

    def test_transient_codes_still_retry(self):
        # 429/408/425 are transient/rate-limit statuses, not deterministic signed failures; retry like 5xx.
        for code in (408, 425, 429, 500, 503):
            assert should_retry_signed_download(_http_status_error(code)) is True

    def test_transport_errors_retry_and_local_protocol_errors_fail_fast(self):
        assert should_retry_signed_download(httpx.ConnectError("refused")) is True
        assert should_retry_signed_download(httpx.UnsupportedProtocol("scheme")) is False


class TestSubmitPost:
    """submit_post routes creation failures by certainty of request delivery."""

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

    async def test_recorded_response_is_sanitized_before_it_is_persisted(self):
        # Traces persist and return through APIs; never store credential-echoing provider values unchanged.
        request_obj = httpx.Request("POST", "https://x/v2")
        resp = httpx.Response(
            200,
            request=request_obj,
            json={
                "id": "ok",
                "api_key": "vda_live_SECRET",
                "note": "callback https://cb.test/x?auth=SUPERSECRET",
                "upload": "PUT https://BARETOKEN@cb.test/put",
            },
        )

        async def _post() -> httpx.Response:
            return resp

        recorded: list[tuple[ProviderResponseStage, object]] = []

        async def _record(stage: ProviderResponseStage, body: object) -> None:
            recorded.append((stage, body))

        request = VideoGenerationRequest(prompt="p", output_path=Path("out.mp4"), on_provider_response=_record)

        await submit_post(_post, provider="v2", request=request)

        # Provider-private query names evade generic masks, requiring removal of entire queries;
        # token-only authorities https://TOKEN@host likewise evade user:password@-only generic masks.
        assert recorded == [
            (
                "submit",
                {
                    "id": "ok",
                    "api_key": "••••",
                    "note": "callback https://cb.test/x",
                    "upload": "PUT https://cb.test/put",
                },
            )
        ]

    async def test_not_sent_error_propagates_for_retry(self):
        # Propagate connection/proxy-handshake failures for should_retry_submit, never terminal wrapping.
        for exc in (httpx.ConnectError("refused"), httpx.ProxyError("proxy handshake failed")):

            async def _post(_exc: httpx.RequestError = exc) -> httpx.Response:
                raise _exc

            with pytest.raises(type(exc)) as excinfo:
                await submit_post(_post, provider="v2")
            assert excinfo.value is exc

    async def test_local_protocol_error_propagates_raw_not_ambiguous(self):
        # Pre-send local/protocol errors have no billing risk; propagate without AmbiguousSubmitError
        # rather than ask operators to verify provider tasks never created.
        for exc in (
            httpx.UnsupportedProtocol("scheme not http(s)"),
            httpx.LocalProtocolError("bad local request"),
        ):

            async def _post(_exc: httpx.RequestError = exc) -> httpx.Response:
                raise _exc

            with pytest.raises(type(exc)):
                await submit_post(_post, provider="v2")

    async def test_ambiguous_error_wrapped_with_manual_retry_hint(self):
        # Possibly delivered ReadTimeout becomes AmbiguousSubmitError with manual-retry guidance.
        async def _post() -> httpx.Response:
            raise httpx.ReadTimeout("read timed out")

        with pytest.raises(AmbiguousSubmitError) as excinfo:
            await submit_post(_post, provider="newapi")
        msg = str(excinfo.value)
        assert "[create_ambiguous]" in msg
        assert "手动重试" in msg
        assert isinstance(excinfo.value.__cause__, httpx.ReadTimeout)

    async def test_http_status_error_propagates_for_status_gate(self):
        # >=400 uses raise_for_status HTTPStatusError for status-based should_retry_submit routing.
        request = httpx.Request("POST", "https://x/v2")
        resp = httpx.Response(503, request=request, text="upstream busy")

        async def _post() -> httpx.Response:
            return resp

        with pytest.raises(httpx.HTTPStatusError):
            await submit_post(_post, provider="v2")

    async def test_deterministic_4xx_carries_the_redacted_provider_reason(self):
        # Deterministic 4xx becomes ProviderRejectedError without query api_key; attach separate
        # provider_reason for independent persistence encoding.
        request = httpx.Request("POST", "https://x/v2?api_key=SECRETKEY")
        resp = httpx.Response(
            400,
            request=request,
            json={"code": "InvalidParameter", "message": "prompt violates the content policy"},
        )

        async def _post() -> httpx.Response:
            return resp

        with pytest.raises(ProviderRejectedError) as excinfo:
            await submit_post(_post, provider="v2")

        assert excinfo.value.provider_reason == "InvalidParameter: prompt violates the content policy"
        assert "SECRETKEY" not in str(excinfo.value)
        assert should_retry_submit(excinfo.value) is False

    async def test_server_error_stays_a_plain_status_error(self):
        # 5xx denotes currently unavailable upstreams; omit response bodies from messages, retaining original exception types.
        request = httpx.Request("POST", "https://x/v2")
        resp = httpx.Response(502, request=request, json={"message": "bad gateway"})

        async def _post() -> httpx.Response:
            return resp

        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await submit_post(_post, provider="v2")

        assert type(excinfo.value) is httpx.HTTPStatusError
        assert str(excinfo.value) == "502 response for https://x/v2"

    async def test_error_message_drops_url_userinfo(self):
        # User base_url may contain https://user:pw@host credentials, which query stripping cannot remove;
        # messages enter task.error_message and return with task lists.
        request = httpx.Request("POST", "https://user:SECRETPW@x/v2?api_key=SECRETKEY")
        resp = httpx.Response(502, request=request, json={"message": "bad gateway"})

        async def _post() -> httpx.Response:
            return resp

        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await submit_post(_post, provider="v2")

        assert str(excinfo.value) == "502 response for https://x/v2"

    @pytest.mark.parametrize(
        ("status", "body"),
        [(401, {"message": "Incorrect API key provided: vda_live_SECRET"}), (400, {}), (403, {"unknown": "shape"})],
    )
    async def test_deterministic_4xx_is_structured_even_without_a_reason(self, status: int, body: dict[str, str]):
        # Even absent summaries (auth nondisclosure, empty bodies, unknown fields) require structured rejections,
        # preserving localized copy/modify-input actions rather than bare status lines.
        request = httpx.Request("POST", "https://x/v2")
        resp = httpx.Response(status, request=request, json=body)

        async def _post() -> httpx.Response:
            return resp

        with pytest.raises(ProviderRejectedError) as excinfo:
            await submit_post(_post, provider="v2")

        assert excinfo.value.provider_reason is None
        assert excinfo.value.response.status_code == status

    async def test_failure_body_is_redacted_before_it_reaches_the_log(self, caplog):
        # Log failed-response bodies for diagnosis without credentials.
        request = httpx.Request("POST", "https://x/v2")
        resp = httpx.Response(
            401,
            request=request,
            json={
                "message": (
                    "Incorrect API key provided: vda_live_SECRET; "
                    "see https://oss.test/o.png?X-Vendor-Credential=AKIASECRET"
                )
            },
        )

        async def _post() -> httpx.Response:
            return resp

        with (
            caplog.at_level(logging.WARNING, logger="lib.backends.backend_runtime"),
            pytest.raises(httpx.HTTPStatusError),
        ):
            await submit_post(_post, provider="v2")

        logged = " ".join(record.getMessage() for record in caplog.records)
        assert "Incorrect API key provided" in logged
        assert "https://oss.test/o.png" in logged
        assert "vda_live_SECRET" not in logged
        assert "AKIASECRET" not in logged

    @pytest.mark.parametrize("status", [408, 425, 429])
    async def test_retryable_client_error_stays_a_plain_status_error(self, status: int):
        request = httpx.Request("POST", "https://x/v2")
        resp = httpx.Response(status, request=request, json={"message": "retry later"})

        async def _post() -> httpx.Response:
            return resp

        with pytest.raises(httpx.HTTPStatusError) as excinfo:
            await submit_post(_post, provider="v2")

        assert type(excinfo.value) is httpx.HTTPStatusError


class TestProviderReasonSummary:
    """provider_reason_summary maps 4xx bodies into sanitized, truncated rejection summaries."""

    @staticmethod
    def _response(status: int, **kwargs: object) -> httpx.Response:
        return httpx.Response(status, request=httpx.Request("POST", "https://x/v2"), **kwargs)

    def test_prefers_the_message_field_over_the_whole_body(self):
        # Error bodies often echo requests; take message fields only, never whole prompts.
        summary = provider_reason_summary(
            self._response(
                400,
                json={
                    "message": "duration 12 is not supported",
                    "input": {"prompt": "一段很长的分镜提示词" * 30},
                },
            )
        )
        assert summary == "duration 12 is not supported"

    def test_digs_into_the_nested_error_object(self):
        summary = provider_reason_summary(
            self._response(422, json={"error": {"code": 1013, "message": "reference image unreadable"}})
        )
        assert summary == "1013: reference image unreadable"

    def test_does_not_fall_back_to_json_that_only_echoes_the_prompt(self):
        summary = provider_reason_summary(
            self._response(400, json={"error": {"code": "Rejected", "input": {"prompt": "FULL_PROMPT"}}})
        )
        assert summary is None

    def test_falls_back_to_the_raw_body_when_no_message_field(self):
        summary = provider_reason_summary(self._response(403, text="  Forbidden\n by  policy  "))
        assert summary == "Forbidden by policy"

    def test_strips_signed_urls_and_masks_credentials(self):
        summary = provider_reason_summary(
            self._response(
                400,
                json={
                    "message": (
                        "cannot fetch https://oss.test/o.png?X-Amz-Signature=deadbeef&X-Amz-Date=1 "
                        'with api_key="sk-proj-abcdefgh12345678"'
                    )
                },
            )
        )
        assert summary is not None
        assert "https://oss.test/o.png" in summary
        assert "X-Amz-Signature" not in summary
        assert "deadbeef" not in summary
        assert "sk-proj-abcdefgh12345678" not in summary

    @pytest.mark.parametrize("url", ["//oss.test/o.png", "oss.test/o.png"])
    def test_strips_query_from_signed_urls_without_a_scheme(self, url: str):
        summary = provider_reason_summary(
            self._response(
                400,
                json={"message": f"cannot fetch {url}?X-Amz-Credential=AKIASECRET&X-Amz-Signature=deadbeef"},
            )
        )
        assert summary == f"cannot fetch {url}"

    def test_truncates_at_the_summary_budget_with_a_marker(self):
        summary = provider_reason_summary(self._response(400, json={"message": "x" * 900}))
        assert summary is not None
        assert len(summary) == PROVIDER_REASON_MAX_CHARS
        assert summary.endswith("…")

    def test_returns_none_for_server_errors_and_empty_bodies(self):
        assert provider_reason_summary(self._response(500, json={"message": "boom"})) is None
        assert provider_reason_summary(self._response(400, text="   ")) is None

    @pytest.mark.parametrize("status", [401, 407])
    def test_returns_none_for_credential_errors(self, status: int):
        # Auth bodies often echo full keys in sentences; never forward those rejection reasons.
        assert (
            provider_reason_summary(
                self._response(status, json={"message": "Incorrect API key provided: vda_live_SECRET"})
            )
            is None
        )

    def test_masks_a_credential_echoed_in_prose(self):
        # Keys can appear in prose rather than key/value form.
        summary = provider_reason_summary(
            self._response(400, json={"message": "Incorrect API key provided: vda_live_SECRET"})
        )
        assert summary is not None
        assert "vda_live_SECRET" not in summary


def _make_operational_error(msg: str) -> OperationalError:
    """Build sqlalchemy OperationalError with params/orig/connection signature placeholders only."""
    return OperationalError(msg, params=None, orig=Exception(msg))


class TestPersistJobIdRetry:
    """persist_provider_job_id retries transient DB errors with structured logs."""

    async def test_retries_on_sqlite_locked(self, caplog):
        """Two OperationalErrors precede third-attempt success; retry actually runs three times."""
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
            patch("lib.generation.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            caplog.at_level(logging.INFO, logger="lib.backends.backend_runtime"),
        ):
            await persist_provider_job_id("task-1", "job-1", provider="openai")

        assert attempts == 3
        assert any("provider_job_id 已持久化" in r.message for r in caplog.records)

    async def test_terminal_failure_logs_structured(self, caplog):
        """Exhaustion logs task_id/provider/job_id through logger.error and reraises."""

        async def _always_fail(_tid: str, _job: str) -> None:
            raise _make_operational_error("database is locked")

        class _FailingQueue:
            async def persist_provider_job_id(
                self, tid: str, job_id: str, *, endpoint: str | None = None, base_url: str | None = None
            ) -> None:
                await _always_fail(tid, job_id)

        fake_queue = _FailingQueue()

        with (
            patch("lib.generation.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            caplog.at_level(logging.ERROR, logger="lib.backends.backend_runtime"),
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
        """ValueError outside retryable_errors raises after one attempt."""
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
            patch("lib.generation.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            pytest.raises(ValueError, match="not retryable"),
        ):
            await persist_provider_job_id("task-V", "job-V", provider="newapi")

        assert attempts == 1

    async def test_no_retry_for_value_error_with_transient_string(self):
        """Business errors containing ``timed out`` / ``503`` must not trigger string-fallback retries.

        `should_retry` falls back to RETRYABLE_STATUS_PATTERNS substrings after isinstance misses;
        explicitly pass `retry_if=lambda e: isinstance(e, _PERSIST_RETRYABLE_ERRORS)` for type-only decisions.
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
            patch("lib.generation.generation_queue.get_generation_queue", return_value=fake_queue),
            bounded_poll_clock(),
            pytest.raises(ValueError, match="timed out"),
        ):
            await persist_provider_job_id("task-T", "job-T", provider="gemini")

        assert attempts == 1, "expects no string-fallback retry for ValueError"


class TestProviderJobIdPersistenceMixin:
    """Single submit/poll-video persistence seam handles None checks, writes, and fail-fast behavior."""

    def _backend(self) -> ProviderJobIdPersistenceMixin:
        # Bare mixins suffice; _persist_provider_job_id needs no subclass state.
        return ProviderJobIdPersistenceMixin()

    def _request(self, *, task_id: str | None) -> VideoGenerationRequest:
        return VideoGenerationRequest(prompt="p", output_path=Path("/tmp/out.mp4"), task_id=task_id)

    async def test_worker_path_persists_via_module_helper(self):
        """Worker nonempty task_id routes through unified module-level persist_provider_job_id."""
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
        """Persist custom-provider endpoint identities with job_ids so resume detects protocol changes."""
        request = self._request(task_id="local-task-1")
        request.execution_endpoint = "openai-video"
        with captured_provider_job_ids() as persisted:
            await self._backend()._persist_provider_job_id(request, "job-1", provider="ark")
        assert [(r["endpoint"], r["base_url"]) for r in persisted] == [("openai-video", None)]

    async def test_worker_path_persists_backend_domain_when_builtin(self):
        """Built-in backends persist actual request domains for resume replay, leaving protocol identities empty."""
        with captured_provider_job_ids() as persisted:
            await self._backend()._persist_provider_job_id(
                self._request(task_id="local-task-1"),
                "job-1",
                provider="dashscope",
                endpoint="https://maas.example.com/api/v1",
            )
        assert [(r["endpoint"], r["base_url"]) for r in persisted] == [(None, "https://maas.example.com/api/v1")]

    async def test_execution_endpoint_and_backend_domain_land_in_separate_columns(self):
        """Custom providers persist protocol under endpoint and replay domains under base_url, never overwriting each other."""
        request = self._request(task_id="local-task-1")
        request.execution_endpoint = "dashscope-video"
        with captured_provider_job_ids() as persisted:
            await self._backend()._persist_provider_job_id(
                request, "job-1", provider="dashscope", endpoint="https://maas.example.com/api/v1"
            )
        assert [(r["endpoint"], r["base_url"]) for r in persisted] == [
            ("dashscope-video", "https://maas.example.com/api/v1")
        ]

    async def test_non_worker_path_skips_persist(self):
        """Nonworker grid/direct/test task_id=None skips persistence without DB access."""
        with captured_provider_job_ids() as persisted:
            await self._backend()._persist_provider_job_id(self._request(task_id=None), "job-1", provider="ark")
        assert persisted == []

    async def test_persist_failure_propagates_fail_fast(self):
        """Persistence failures propagate originals for worker finally mark_failed, never swallowed."""
        boom = _make_operational_error("database is locked")
        with (
            captured_provider_job_ids(fail=boom),
            pytest.raises(OperationalError),
        ):
            await self._backend()._persist_provider_job_id(
                self._request(task_id="local-task-1"), "job-1", provider="gemini"
            )


class TestRedirectMethodRewrite:
    """Redirect method rewriting matches httpx/RFC 9110."""

    @pytest.mark.parametrize(
        ("status_code", "method", "expected"),
        [
            (303, "POST", True),
            (303, "PUT", True),
            (303, "HEAD", False),
            (301, "POST", True),
            (302, "POST", True),
            # PUT 301/302 retains methods/bodies; rewriting would change endpoint semantics.
            (301, "PUT", False),
            (302, "PUT", False),
            (307, "POST", False),
            (308, "POST", False),
        ],
    )
    def test_rewrite_rule(self, status_code: int, method: str, expected: bool):
        assert _rewrites_to_get(status_code, method) is expected


class TestScopedCredentialRedirect:
    """Per-hop data scopes for ``request_with_scoped_credentials`` redirects."""

    @pytest.mark.parametrize("status", [307, 308])
    async def test_a_cross_origin_hop_forwards_neither_credentials_nor_the_body(self, status: int):
        """307/308 retains methods, but bodies/credentials never leave credential scopes."""
        with capture_http() as router:
            router.post("https://relay.test/submit").mock(
                return_value=httpx.Response(status, headers={"Location": "https://other.test/submit"})
            )
            target = router.post("https://other.test/submit").mock(return_value=httpx.Response(200, json={"ok": True}))
            async with httpx.AsyncClient() as client:
                response = await request_with_scoped_credentials(
                    client,
                    "POST",
                    "https://relay.test/submit",
                    headers={"X-API-Key": "secret"},
                    json={"prompt": "paper boat"},
                )

        assert response.status_code == 200
        request = only_request(target)
        assert request.method == "POST"
        assert request.content == b""
        assert "x-api-key" not in request.headers

    @pytest.mark.parametrize("status", [307, 308])
    async def test_a_same_origin_hop_keeps_the_method_body_and_credentials(self, status: int):
        with capture_http() as router:
            router.post("https://relay.test/submit").mock(
                return_value=httpx.Response(status, headers={"Location": "/submit/"})
            )
            target = router.post("https://relay.test/submit/").mock(return_value=httpx.Response(200, json={"ok": True}))
            async with httpx.AsyncClient() as client:
                response = await request_with_scoped_credentials(
                    client,
                    "POST",
                    "https://relay.test/submit",
                    headers={"X-API-Key": "secret"},
                    json={"prompt": "paper boat"},
                )

        assert response.status_code == 200
        request = only_request(target)
        assert request.method == "POST"
        assert request_json(request) == {"prompt": "paper boat"}
        assert request.headers["x-api-key"] == "secret"


class TestWithArtifactRetry:
    async def test_single_slow_attempt_is_cut_off_at_max_wait(self):
        """Wall-clock limits also bound never-returning downloads; poll_with_retry checks budgets only after return.

        Avoid real waits: budget 0 and a never-set event reach deadlines immediately.
        """
        never_ready = asyncio.Event()

        async def never_returns() -> None:
            await never_ready.wait()

        with pytest.raises(TimeoutError):
            await with_artifact_retry(never_returns, label="slow", max_wait=0)

        assert not never_ready.is_set()


_LIMIT = 64 * 1024 * 1024


class TestStreamToFile:
    async def test_writes_body_and_leaves_no_partial_file(self, tmp_path: Path):
        output = tmp_path / "nested" / "out.mp4"
        payload = b"x" * (9 * 1024 * 1024)  # Exceed batch-write thresholds to exercise multiple disk writes.

        with capture_http() as router:
            router.get("https://cdn.test/a.mp4").mock(return_value=httpx.Response(200, content=payload))
            async with httpx.AsyncClient() as client:
                await stream_to_file(client, "https://cdn.test/a.mp4", output, max_bytes=_LIMIT)

        assert output.read_bytes() == payload
        assert not (output.parent / f"{output.name}.part").exists()

    async def test_redirect_without_location_is_not_written_as_the_artifact(self, tmp_path: Path):
        """raise_for_status admits 3xx; without Location redirects have nowhere to go, so never save redirect bodies as videos."""
        output = tmp_path / "out.mp4"

        with capture_http() as router:
            router.get("https://relay.test/a.mp4").mock(return_value=httpx.Response(302, content=b"not a video"))
            async with httpx.AsyncClient() as client:
                with pytest.raises(RuntimeError, match="redirect without a Location header"):
                    await stream_to_file(
                        client,
                        "https://relay.test/a.mp4",
                        output,
                        max_bytes=_LIMIT,
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
                    await stream_to_file(client, "https://cdn.test/a.mp4", output, max_bytes=_LIMIT)

        assert not output.exists()
        assert not (tmp_path / f"{output.name}.part").exists()

    async def test_body_over_limit_is_aborted_without_leftovers(self, tmp_path: Path):
        output = tmp_path / "out.mp4"

        with capture_http() as router:
            router.get("https://cdn.test/a.mp4").mock(
                return_value=httpx.Response(200, headers={"Content-Length": "8"}, content=b"x" * 65)
            )
            async with httpx.AsyncClient() as client:
                with pytest.raises(ArtifactTooLargeError):
                    await stream_to_file(client, "https://cdn.test/a.mp4", output, max_bytes=64)

        assert not output.exists()
        assert not (tmp_path / f"{output.name}.part").exists()

    async def test_error_response_body_is_read_up_to_the_error_limit(self, tmp_path: Path):
        output = tmp_path / "out.mp4"

        with capture_http() as router:
            router.get("https://cdn.test/a.mp4").mock(
                return_value=httpx.Response(500, content=b"e" * (ERROR_BODY_MAX_BYTES * 4))
            )
            async with httpx.AsyncClient() as client:
                with pytest.raises(httpx.HTTPStatusError) as excinfo:
                    await stream_to_file(client, "https://cdn.test/a.mp4", output, max_bytes=_LIMIT)

        assert excinfo.value.response.status_code == 500
        assert len(excinfo.value.response.content) == ERROR_BODY_MAX_BYTES


class TestDownloadVideoDestination:
    async def test_redirect_to_link_local_is_rejected_without_retry(self, tmp_path: Path):
        output = tmp_path / "out.mp4"

        with capture_http() as router:
            first = router.get("http://203.0.113.7/a.mp4").mock(
                return_value=httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/"})
            )
            target = router.get("http://169.254.169.254/latest/").mock(return_value=httpx.Response(200))
            with pytest.raises(ArtifactDestinationRejectedError):
                await download_video("http://203.0.113.7/a.mp4", output, label="test")

        assert first.call_count == 1
        assert target.call_count == 0
        assert not output.exists()

    async def test_resumable_download_keeps_destination_rejection_terminal(self, tmp_path: Path):
        # Retrying invalid destinations cannot help; never wrap them as retryable ArtifactDownloadError.
        with capture_http() as router:
            target = router.get("http://169.254.169.254/a.mp4").mock(return_value=httpx.Response(200))
            with pytest.raises(ArtifactDestinationRejectedError):
                await download_resumable_video("http://169.254.169.254/a.mp4", tmp_path / "out.mp4", label="test")

        assert target.call_count == 0

    async def test_resumable_download_keeps_size_limit_terminal(self, tmp_path: Path):
        with capture_http() as router:
            router.get("https://cdn.test/a.mp4").mock(
                return_value=httpx.Response(200, headers={"Content-Length": str(VIDEO_ARTIFACT_MAX_BYTES + 1)})
            )
            with pytest.raises(ArtifactTooLargeError):
                await download_resumable_video("https://cdn.test/a.mp4", tmp_path / "out.mp4", label="test")

        assert not (tmp_path / "out.mp4").exists()


class TestRecordingPoll:
    """Polling traces persist every received response before status/error interpretation."""

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
        """Terminal is_failed errors inside poll_with_retry still retain response-body traces."""
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
        """poll_fn’s own 4xx/5xx failures are also diagnostically relevant calls."""
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
        """Trace-write failures never interrupt provider-accepted generation; tasks advance normally."""
        request = VideoGenerationRequest(
            prompt="p",
            output_path=Path("out.mp4"),
            task_id="task-R",
            on_provider_response=AsyncMock(side_effect=OperationalError("stmt", {}, Exception("locked"))),
        )

        assert await recording_poll(AsyncMock(return_value={"status": "ok"}), request)() == {"status": "ok"}


class TestResumeExpiryGate:
    """Resume-only 404 becomes ResumeExpiredError; other exceptions match fresh-submission propagation."""

    async def test_resume_404_becomes_resume_expired(self):
        gated = resume_expiry_gate(AsyncMock(side_effect=_http_status_error(404)), resume_job_id="job-1", provider="p")

        with pytest.raises(ResumeExpiredError) as ei:
            await gated()

        assert (ei.value.job_id, ei.value.provider) == ("job-1", "p")

    async def test_resume_non_404_propagates(self):
        gated = resume_expiry_gate(AsyncMock(side_effect=_http_status_error(503)), resume_job_id="job-1", provider="p")

        with pytest.raises(httpx.HTTPStatusError):
            await gated()

    async def test_submit_path_404_propagates_for_retry(self):
        gated = resume_expiry_gate(AsyncMock(side_effect=_http_status_error(404)), resume_job_id=None, provider="p")

        with pytest.raises(httpx.HTTPStatusError) as ei:
            await gated()

        assert should_retry_poll(ei.value) is True
