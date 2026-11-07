"""lib/backends/grok_shared.py client factories/grok_should_retry predicates."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import grpc
import grpc.aio
import pytest
from xai_sdk.aio import chat, image, video

from lib.backends.grok_shared import create_grok_client, grok_should_retry
from lib.infra.retry import with_retry_async
from tests.fakes import bounded_poll_clock


def _make_aio_rpc_error(code: grpc.StatusCode, details: str = "") -> grpc.aio.AioRpcError:
    """Construct test AioRpcError."""
    return grpc.aio.AioRpcError(
        code=code,
        initial_metadata=grpc.aio.Metadata(),
        trailing_metadata=grpc.aio.Metadata(),
        details=details,
        debug_error_string=f"test: {details}",
    )


class TestCreateGrokClient:
    async def test_client_created_off_the_event_loop_works_on_it(self):
        """Construct in loopless workers, then obtain real SDK child clients back on event loops."""
        client = await asyncio.to_thread(create_grok_client, api_key="xai-key")

        assert isinstance(client.chat, chat.Client)
        assert isinstance(client.image, image.Client)
        assert isinstance(client.video, video.Client)

    @pytest.mark.parametrize("api_key", [None, "", "   "])
    def test_missing_api_key_fails_at_creation(self, api_key):
        with pytest.raises(ValueError, match="xAI API Key"):
            create_grok_client(api_key=api_key)


class TestGrokShouldRetry:
    """grok_should_retry predicates."""

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
        """NongRPC errors fall back to default should_retry."""
        assert grok_should_retry(ConnectionError("reset")) is True

    def test_non_grpc_timeout_error_retryable(self):
        assert grok_should_retry(TimeoutError("deadline")) is True

    def test_non_grpc_unrelated_error_not_retryable(self):
        assert grok_should_retry(ValueError("bad input")) is False


class TestGrokRetryIntegration:
    """Integrate grok_should_retry with with_retry_async."""

    async def test_grpc_unavailable_triggers_retry(self):
        """gRPC UNAVAILABLE retries and eventually succeeds."""
        exc = _make_aio_rpc_error(grpc.StatusCode.UNAVAILABLE, "Socket closed")
        mock_fn = AsyncMock(side_effect=[exc, "ok"])

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), retry_if=grok_should_retry)
        async def fn():
            return await mock_fn()

        with bounded_poll_clock():
            result = await fn()

        assert result == "ok"
        assert mock_fn.call_count == 2

    async def test_grpc_invalid_argument_no_retry(self):
        """gRPC INVALID_ARGUMENT raises without retry."""
        exc = _make_aio_rpc_error(grpc.StatusCode.INVALID_ARGUMENT, "bad prompt")
        mock_fn = AsyncMock(side_effect=exc)

        @with_retry_async(max_attempts=3, backoff_seconds=(0, 0, 0), retry_if=grok_should_retry)
        async def fn():
            return await mock_fn()

        with pytest.raises(grpc.aio.AioRpcError):
            await fn()
        assert mock_fn.call_count == 1
