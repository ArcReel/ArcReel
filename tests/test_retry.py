"""lib/retry.py 通用重试装饰器单元测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from lib.retry import (
    BASE_RETRYABLE_ERRORS,
    RETRYABLE_STATUS_PATTERNS,
    _should_retry,
    with_retry_async,
)


class TestShouldRetry:
    """_should_retry 判断逻辑测试。"""

    def test_retryable_error_type(self):
        assert _should_retry(ConnectionError("reset"), BASE_RETRYABLE_ERRORS) is True
        assert _should_retry(TimeoutError("deadline"), BASE_RETRYABLE_ERRORS) is True

    def test_non_retryable_error_type(self):
        assert _should_retry(ValueError("bad"), BASE_RETRYABLE_ERRORS) is False
        assert _should_retry(RuntimeError("crash"), BASE_RETRYABLE_ERRORS) is False

    def test_string_pattern_429(self):
        exc = RuntimeError("Error code: 429 - rate limited")
        assert _should_retry(exc, ()) is True

    def test_string_pattern_500(self):
        exc = RuntimeError("HTTP 500 Internal Server Error")
        assert _should_retry(exc, ()) is True

    def test_string_pattern_503(self):
        exc = RuntimeError("503 Service Unavailable")
        assert _should_retry(exc, ()) is True

    def test_string_pattern_resource_exhausted(self):
        exc = RuntimeError("RESOURCE_EXHAUSTED: quota exceeded")
        assert _should_retry(exc, ()) is True

    def test_all_patterns_covered(self):
        for pattern in RETRYABLE_STATUS_PATTERNS:
            exc = RuntimeError(f"Error: {pattern}")
            assert _should_retry(exc, ()) is True, f"Pattern '{pattern}' not detected"

    def test_string_pattern_502(self):
        exc = RuntimeError("502 Bad Gateway")
        assert _should_retry(exc, ()) is True

    def test_string_pattern_504(self):
        exc = RuntimeError("504 Gateway Timeout")
        assert _should_retry(exc, ()) is True

    def test_case_insensitive_matching(self):
        assert _should_retry(RuntimeError("RESOURCE_EXHAUSTED: quota"), ()) is True
        assert _should_retry(RuntimeError("Internal Server Error"), ()) is True
        assert _should_retry(RuntimeError("InternalServerError"), ()) is True
        assert _should_retry(RuntimeError("Service Unavailable"), ()) is True

    def test_timeout_patterns(self):
        assert _should_retry(RuntimeError("Connection timed out"), ()) is True
        assert _should_retry(RuntimeError("read timeout"), ()) is True
        assert _should_retry(RuntimeError("httpx.ReadTimeout"), ()) is True

    def test_unrelated_error_message(self):
        exc = RuntimeError("Invalid API key")
        assert _should_retry(exc, ()) is False

    def test_custom_retryable_errors(self):
        class MyError(Exception):
            pass

        assert _should_retry(MyError("oops"), (MyError,)) is True
        assert _should_retry(MyError("oops"), ()) is False


class TestWithRetryAsync:
    """with_retry_async 装饰器测试。"""

    async def test_success_no_retry(self):
        mock_fn = AsyncMock(return_value="ok")

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0))
        async def fn():
            return await mock_fn()

        result = await fn()
        assert result == "ok"
        assert mock_fn.call_count == 1

    async def test_retry_on_retryable_error(self):
        mock_fn = AsyncMock(side_effect=[ConnectionError("reset"), "ok"])

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0))
        async def fn():
            return await mock_fn()

        with patch("lib.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_retry_on_string_pattern(self):
        """错误信息中包含 429 时应重试。"""
        mock_fn = AsyncMock(side_effect=[RuntimeError("Error code: 429"), "ok"])

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0))
        async def fn():
            return await mock_fn()

        with patch("lib.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_no_retry_on_non_retryable(self):
        mock_fn = AsyncMock(side_effect=ValueError("bad input"))

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0))
        async def fn():
            return await mock_fn()

        with pytest.raises(ValueError, match="bad input"):
            await fn()
        assert mock_fn.call_count == 1

    async def test_exhausted_retries_raises_last_error(self):
        errors = [ConnectionError(f"attempt {i}") for i in range(3)]
        mock_fn = AsyncMock(side_effect=errors)

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0))
        async def fn():
            return await mock_fn()

        with patch("lib.retry.asyncio.sleep", new_callable=AsyncMock):
            with pytest.raises(ConnectionError, match="attempt 2"):
                await fn()
        assert mock_fn.call_count == 3

    async def test_custom_retryable_errors(self):
        class CustomError(Exception):
            pass

        mock_fn = AsyncMock(side_effect=[CustomError("temp"), "ok"])

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), retryable_errors=(CustomError,))
        async def fn():
            return await mock_fn()

        with patch("lib.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_backoff_sleep_called(self):
        mock_fn = AsyncMock(side_effect=[ConnectionError("err"), "ok"])

        @with_retry_async(max_attempts=3, backoff_seconds=(5, 10, 20))
        async def fn():
            return await mock_fn()

        with patch("lib.retry.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            with patch("lib.retry.random.uniform", return_value=0.5):
                await fn()

        mock_sleep.assert_called_once()
        # base_wait=5 + jitter=0.5 = 5.5
        assert mock_sleep.call_args[0][0] == 5.5

    async def test_backoff_index_clamped(self):
        """backoff_seconds 长度不足时，使用最后一个值。"""
        errors = [ConnectionError(f"e{i}") for i in range(4)]
        mock_fn = AsyncMock(side_effect=[*errors, "ok"])

        @with_retry_async(max_attempts=5, backoff_seconds=(1, 2))
        async def fn():
            return await mock_fn()

        sleep_values = []

        async def capture_sleep(t):
            sleep_values.append(t)

        with patch("lib.retry.asyncio.sleep", side_effect=capture_sleep):
            with patch("lib.retry.random.uniform", return_value=0):
                await fn()

        # attempt 0→backoff[0]=1, attempt 1→backoff[1]=2, attempt 2→backoff[1]=2 (clamped), attempt 3→backoff[1]=2
        assert sleep_values == [1, 2, 2, 2]
