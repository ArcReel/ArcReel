"""lib/grok_shared.py grok_should_retry 重试谓词测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import grpc
import grpc.aio
import pytest

from lib.grok_shared import grok_should_retry
from lib.retry import with_retry_async


def _make_aio_rpc_error(code: grpc.StatusCode, details: str = "") -> grpc.aio.AioRpcError:
    """构造一个 AioRpcError 用于测试。"""
    return grpc.aio.AioRpcError(
        code=code,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(),
        details=details,
        debug_error_string=f"test: {details}",
    )


class TestGrokShouldRetry:
    """grok_should_retry 谓词测试。"""

    def test_unavailable_is_retryable(self):
        exc = _make_aio_rpc_error(grpc.StatusCode.UNAVAILABLE, "Socket closed")
        assert grok_should_retry(exc) is True

    def test_deadline_exceeded_is_retryable(self):
        exc = _make_aio_rpc_error(grpc.StatusCode.DEADLINE_EXCEEDED, "timeout")
        assert grok_should_retry(exc) is True

    def test_resource_exhausted_is_retryable(self):
        exc = _make_aio_rpc_error(grpc.StatusCode.RESOURCE_EXHAUSTED, "quota exceeded")
        assert grok_should_retry(exc) is True

    def test_aborted_is_retryable(self):
        exc = _make_aio_rpc_error(grpc.StatusCode.ABORTED, "conflict")
        assert grok_should_retry(exc) is True

    def test_invalid_argument_not_retryable(self):
        exc = _make_aio_rpc_error(grpc.StatusCode.INVALID_ARGUMENT, "bad request")
        assert grok_should_retry(exc) is False

    def test_permission_denied_not_retryable(self):
        exc = _make_aio_rpc_error(grpc.StatusCode.PERMISSION_DENIED, "forbidden")
        assert grok_should_retry(exc) is False

    def test_unauthenticated_not_retryable(self):
        exc = _make_aio_rpc_error(grpc.StatusCode.UNAUTHENTICATED, "bad key")
        assert grok_should_retry(exc) is False

    def test_not_found_not_retryable(self):
        exc = _make_aio_rpc_error(grpc.StatusCode.NOT_FOUND, "no such resource")
        assert grok_should_retry(exc) is False

    def test_non_grpc_connection_error_retryable(self):
        """非 gRPC 异常回退到默认 _should_retry 逻辑。"""
        assert grok_should_retry(ConnectionError("reset")) is True

    def test_non_grpc_timeout_error_retryable(self):
        assert grok_should_retry(TimeoutError("deadline")) is True

    def test_non_grpc_unrelated_error_not_retryable(self):
        assert grok_should_retry(ValueError("bad input")) is False


class TestGrokRetryIntegration:
    """验证 grok_should_retry 与 with_retry_async 集成。"""

    async def test_grpc_unavailable_triggers_retry(self):
        """gRPC UNAVAILABLE 应触发重试并最终成功。"""
        exc = _make_aio_rpc_error(grpc.StatusCode.UNAVAILABLE, "Socket closed")
        mock_fn = AsyncMock(side_effect=[exc, "ok"])

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), retry_if=grok_should_retry)
        async def fn():
            return await mock_fn()

        with patch("lib.retry.asyncio.sleep", new_callable=AsyncMock):
            result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_grpc_invalid_argument_no_retry(self):
        """gRPC INVALID_ARGUMENT 不应重试，直接抛出。"""
        exc = _make_aio_rpc_error(grpc.StatusCode.INVALID_ARGUMENT, "bad prompt")
        mock_fn = AsyncMock(side_effect=exc)

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), retry_if=grok_should_retry)
        async def fn():
            return await mock_fn()

        with pytest.raises(grpc.aio.AioRpcError):
            await fn()
        assert mock_fn.call_count == 1
