"""Unit tests for lib/infra/retry.py generic retry decorators."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from lib.infra.retry import (
    BASE_RETRYABLE_ERRORS,
    RETRYABLE_STATUS_PATTERNS,
    NonRetryableError,
    SystemClock,
    bind_clock,
    resolve_clock,
    retry_async,
    should_retry,
    with_retry_async,
)


class _FakeClock:
    """Clock-seam substitute records backoff durations without real sleeps."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return 0.0

    async def sleep(self, delay: float) -> None:
        self.sleeps.append(delay)


def _no_jitter(_low: float, _high: float) -> float:
    return 0.0


class TestShouldRetry:
    """should_retry decision tests."""

    def test_retryable_error_type(self):
        assert should_retry(ConnectionError("reset"), BASE_RETRYABLE_ERRORS) is True
        assert should_retry(TimeoutError("deadline"), BASE_RETRYABLE_ERRORS) is True

    def test_non_retryable_error_type(self):
        assert should_retry(ValueError("bad"), BASE_RETRYABLE_ERRORS) is False
        assert should_retry(RuntimeError("crash"), BASE_RETRYABLE_ERRORS) is False

    def test_string_pattern_429(self):
        exc = RuntimeError("Error code: 429 - rate limited")
        assert should_retry(exc, ()) is True

    def test_string_pattern_500(self):
        exc = RuntimeError("HTTP 500 Internal Server Error")
        assert should_retry(exc, ()) is True

    def test_string_pattern_503(self):
        exc = RuntimeError("503 Service Unavailable")
        assert should_retry(exc, ()) is True

    def test_string_pattern_resource_exhausted(self):
        exc = RuntimeError("RESOURCE_EXHAUSTED: quota exceeded")
        assert should_retry(exc, ()) is True

    def test_all_patterns_covered(self):
        for pattern in RETRYABLE_STATUS_PATTERNS:
            exc = RuntimeError(f"Error: {pattern}")
            assert should_retry(exc, ()) is True, f"Pattern '{pattern}' not detected"

    def test_string_pattern_502(self):
        exc = RuntimeError("502 Bad Gateway")
        assert should_retry(exc, ()) is True

    def test_string_pattern_504(self):
        exc = RuntimeError("504 Gateway Timeout")
        assert should_retry(exc, ()) is True

    def test_case_insensitive_matching(self):
        assert should_retry(RuntimeError("RESOURCE_EXHAUSTED: quota"), ()) is True
        assert should_retry(RuntimeError("Internal Server Error"), ()) is True
        assert should_retry(RuntimeError("InternalServerError"), ()) is True
        assert should_retry(RuntimeError("Service Unavailable"), ()) is True

    def test_timeout_patterns(self):
        assert should_retry(RuntimeError("Connection timed out"), ()) is True
        assert should_retry(RuntimeError("read timeout"), ()) is True
        assert should_retry(RuntimeError("httpx.ReadTimeout"), ()) is True

    def test_unrelated_error_message(self):
        exc = RuntimeError("Invalid API key")
        assert should_retry(exc, ()) is False

    def test_custom_retryable_errors(self):
        class MyError(Exception):
            pass

        assert should_retry(MyError("oops"), (MyError,)) is True
        assert should_retry(MyError("oops"), ()) is False

    def test_non_retryable_error_short_circuits_pattern_match(self):
        """NonRetryableError never retries even if messages happen to match transient substrings.

        Structured-output truncation embeds arbitrary output_tokens integers; decimal strings
        containing "500"/"429" could otherwise incorrectly match transient patterns and retry.
        """

        class _Truncated(NonRetryableError):
            pass

        exc = _Truncated("provider/model 在 output_tokens=8500 处被截断")
        assert "500" in str(exc)
        assert should_retry(exc, ()) is False


class TestWithRetryAsync:
    """with_retry_async decorator tests."""

    async def test_success_no_retry(self):
        mock_fn = AsyncMock(return_value="ok")
        clock = _FakeClock()

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), clock=clock, jitter=_no_jitter)
        async def fn():
            return await mock_fn()

        result = await fn()
        assert result == "ok"
        assert mock_fn.call_count == 1
        assert clock.sleeps == []

    async def test_retry_on_retryable_error(self):
        mock_fn = AsyncMock(side_effect=[ConnectionError("reset"), "ok"])
        clock = _FakeClock()

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), clock=clock, jitter=_no_jitter)
        async def fn():
            return await mock_fn()

        result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_retry_on_string_pattern(self):
        """Messages containing 429 retry."""
        mock_fn = AsyncMock(side_effect=[RuntimeError("Error code: 429"), "ok"])
        clock = _FakeClock()

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), clock=clock, jitter=_no_jitter)
        async def fn():
            return await mock_fn()

        result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_no_retry_on_non_retryable(self):
        mock_fn = AsyncMock(side_effect=ValueError("bad input"))
        clock = _FakeClock()

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), clock=clock, jitter=_no_jitter)
        async def fn():
            return await mock_fn()

        with pytest.raises(ValueError, match="bad input"):
            await fn()
        assert mock_fn.call_count == 1
        assert clock.sleeps == []

    async def test_no_retry_on_non_retryable_despite_colliding_pattern(self):
        """NonRetryableError containing "500" still invokes once, never retries."""

        class _Truncated(NonRetryableError):
            pass

        mock_fn = AsyncMock(side_effect=_Truncated("output_tokens=8500 处被截断"))
        clock = _FakeClock()

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), clock=clock, jitter=_no_jitter)
        async def fn():
            return await mock_fn()

        with pytest.raises(_Truncated):
            await fn()
        assert mock_fn.call_count == 1
        assert clock.sleeps == []

    async def test_exhausted_retries_raises_last_error(self):
        errors = [ConnectionError(f"attempt {i}") for i in range(3)]
        mock_fn = AsyncMock(side_effect=errors)
        clock = _FakeClock()

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), clock=clock, jitter=_no_jitter)
        async def fn():
            return await mock_fn()

        with pytest.raises(ConnectionError, match="attempt 2"):
            await fn()
        assert mock_fn.call_count == 3

    async def test_custom_retryable_errors(self):
        class CustomError(Exception):
            pass

        mock_fn = AsyncMock(side_effect=[CustomError("temp"), "ok"])
        clock = _FakeClock()

        @with_retry_async(
            max_attempts=3,
            backoff_seconds=(0, 0, 0),
            retryable_errors=(CustomError,),
            clock=clock,
            jitter=_no_jitter,
        )
        async def fn():
            return await mock_fn()

        result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_backoff_index_clamped(self):
        """Short backoff_seconds sequences reuse their final value."""
        errors = [ConnectionError(f"e{i}") for i in range(4)]
        mock_fn = AsyncMock(side_effect=[*errors, "ok"])
        clock = _FakeClock()

        @with_retry_async(max_attempts=5, backoff_seconds=(1, 2), clock=clock, jitter=_no_jitter)
        async def fn():
            return await mock_fn()

        await fn()

        # attempt 0→backoff[0]=1, attempt 1→backoff[1]=2, attempt 2→backoff[1]=2 (clamped), attempt 3→backoff[1]=2
        assert clock.sleeps == [1, 2, 2, 2]

    async def test_retry_if_true_triggers_retry(self):
        """retry_if True triggers retries."""
        mock_fn = AsyncMock(side_effect=[ValueError("transient"), "ok"])
        clock = _FakeClock()

        @with_retry_async(
            max_attempts=3,
            backoff_seconds=(0, 0, 0),
            retry_if=lambda e: isinstance(e, ValueError),
            clock=clock,
            jitter=_no_jitter,
        )
        async def fn():
            return await mock_fn()

        result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_retry_if_false_raises_immediately(self):
        """retry_if False raises immediately, even when should_retry says True."""
        mock_fn = AsyncMock(side_effect=ConnectionError("reset"))
        clock = _FakeClock()

        @with_retry_async(
            max_attempts=3,
            backoff_seconds=(0, 0, 0),
            retry_if=lambda e: False,  # Never retry.
            clock=clock,
            jitter=_no_jitter,
        )
        async def fn():
            return await mock_fn()

        with pytest.raises(ConnectionError, match="reset"):
            await fn()
        assert mock_fn.call_count == 1
        assert clock.sleeps == []

    async def test_retry_if_none_uses_default_should_retry(self):
        """Default retry_if=None preserves existing should_retry behavior."""
        mock_fn = AsyncMock(side_effect=[ConnectionError("reset"), "ok"])
        clock = _FakeClock()

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), retry_if=None, clock=clock, jitter=_no_jitter)
        async def fn():
            return await mock_fn()

        result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_custom_retry_if_cannot_override_non_retryable(self):
        """Even custom retry_if returning True for NonRetryableError must short-circuit without retry.

        Enforce centrally in decorators, never depending on predicates calling should_retry.
        New predicates forgetting NonRetryableError would otherwise violate module docstrings’
        promise that subclasses always bypass retry.
        """

        class _Truncated(NonRetryableError):
            pass

        mock_fn = AsyncMock(side_effect=_Truncated("boom"))
        clock = _FakeClock()

        @with_retry_async(
            max_attempts=3,
            backoff_seconds=(0, 0, 0),
            retry_if=lambda e: True,
            clock=clock,
            jitter=_no_jitter,
        )
        async def fn():
            return await mock_fn()

        with pytest.raises(_Truncated):
            await fn()
        assert mock_fn.call_count == 1
        assert clock.sleeps == []

    async def test_waits_backoff_plus_injected_jitter_on_injected_clock(self):
        mock_fn = AsyncMock(side_effect=[ConnectionError("reset"), "ok"])
        clock = _FakeClock()

        @with_retry_async(
            max_attempts=3,
            backoff_seconds=(5, 10),
            clock=clock,
            jitter=lambda _low, _high: 0.25,
        )
        async def fn():
            return await mock_fn()

        assert await fn() == "ok"
        assert clock.sleeps == [5.25]


class TestRetryAsync:
    async def test_waits_backoff_plus_injected_jitter_on_injected_clock(self):
        operation = AsyncMock(side_effect=[ConnectionError("reset"), "ok"])
        clock = _FakeClock()

        result = await retry_async(
            operation,
            max_attempts=3,
            backoff_seconds=(5, 10),
            clock=clock,
            jitter=lambda _low, _high: 0.25,
        )

        assert result == "ok"
        assert clock.sleeps == [5.25]


class TestBoundClock:
    async def test_retry_without_explicit_clock_waits_on_bound_clock(self):
        operation = AsyncMock(side_effect=[ConnectionError("reset"), "ok"])
        clock = _FakeClock()

        with bind_clock(clock):
            result = await retry_async(operation, backoff_seconds=(5,), jitter=_no_jitter)

        assert result == "ok"
        assert clock.sleeps == [5.0]

    async def test_explicit_clock_wins_over_bound_clock(self):
        bound, explicit = _FakeClock(), _FakeClock()

        with bind_clock(bound):
            resolved = resolve_clock(explicit)

        assert resolved is explicit

    async def test_binding_reaches_tasks_spawned_inside_and_is_restored_on_exit(self):
        clock = _FakeClock()

        async def current() -> object:
            return resolve_clock(None)

        with bind_clock(clock):
            spawned = await asyncio.create_task(current())

        assert spawned is clock
        assert isinstance(resolve_clock(None), SystemClock)

    async def test_binding_does_not_leak_into_concurrent_sibling_context(self):
        clock = _FakeClock()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def bound_holder() -> None:
            with bind_clock(clock):
                entered.set()
                await release.wait()

        holder = asyncio.create_task(bound_holder())
        await entered.wait()
        sibling_view = resolve_clock(None)
        release.set()
        await holder

        assert isinstance(sibling_view, SystemClock)
