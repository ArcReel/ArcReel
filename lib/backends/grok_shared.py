"""Shared Grok (xAI) utilities.

Reused by text_backends / image_backends / video_backends.

Includes:
- create_grok_client: lazy xAI AsyncClient factory, see LazyGrokClient.
- grok_should_retry: gRPC-aware retry predicate.
- grpc_rejection: deterministic gRPC refusals mapped to HTTP statuses and rejection summaries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.infra.retry import BASE_RETRYABLE_ERRORS, should_retry

# Transient gRPC statuses equivalent to HTTP 429/500/502/503/504.
_GRPC_RETRYABLE_CODES: set | None = None
# Deterministic gRPC refusals → HTTP equivalents by official mappings; retries cannot help before input/credential changes.
_GRPC_REJECTION_STATUS: dict | None = None

try:
    import grpc

    _GRPC_RETRYABLE_CODES = {
        grpc.StatusCode.UNAVAILABLE,  # 503: disconnected / Socket closed
        grpc.StatusCode.DEADLINE_EXCEEDED,  # 504: timeout
        grpc.StatusCode.RESOURCE_EXHAUSTED,  # 429: throttled
        grpc.StatusCode.ABORTED,  # Retry conflicts.
    }
    _GRPC_REJECTION_STATUS = {
        grpc.StatusCode.INVALID_ARGUMENT: 400,
        grpc.StatusCode.FAILED_PRECONDITION: 400,
        grpc.StatusCode.OUT_OF_RANGE: 400,
        grpc.StatusCode.UNAUTHENTICATED: 401,
        grpc.StatusCode.PERMISSION_DENIED: 403,
        grpc.StatusCode.NOT_FOUND: 404,
    }
except ImportError:
    pass  # grpc is optional; fall back to pattern-based retry below

if TYPE_CHECKING:
    import xai_sdk
    from xai_sdk.aio import chat, image, video


def grok_should_retry(exc: Exception) -> bool:
    """Grok retry predicate: exact transient gRPC statuses, otherwise default pattern matching."""
    if _GRPC_RETRYABLE_CODES is not None:
        try:
            from grpc.aio import AioRpcError

            if isinstance(exc, AioRpcError):
                return exc.code() in _GRPC_RETRYABLE_CODES
        except ImportError:
            pass  # grpc available at module level but aio submodule missing; fall through
    return should_retry(exc, BASE_RETRYABLE_ERRORS)


def grpc_rejection(exc: BaseException) -> tuple[int, str | None] | None:
    """Equivalent HTTP status and redacted summary for deterministic gRPC refusals, otherwise ``None``.

    ``AioRpcError`` ``str()`` contains the entire debug representation (``<AioRpcError of RPC that terminated with …>``),
    unreadable to users as task failures. Persistence encodes converted results as ``provider_rejected``.
    Authentication details describe credentials themselves; never forward them, matching HTTP 401 policy.
    """
    if _GRPC_REJECTION_STATUS is None:
        return None
    try:
        from grpc.aio import AioRpcError
    except ImportError:
        return None
    if not isinstance(exc, AioRpcError):
        return None
    status = _GRPC_REJECTION_STATUS.get(exc.code())
    if status is None:
        return None
    if status == 401:
        return status, None
    from lib.backends.backend_runtime import summarize_provider_reason

    return status, summarize_provider_reason(exc.details())


def resolve_grok_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 xAI API Key")
    return api_key.strip()


class LazyGrokClient:
    """Lazy xAI AsyncClient wrapper, creating SDK clients only on first chat / image / video access.

    ``xai_sdk.AsyncClient`` immediately creates grpc.aio channels requiring a current-thread event loop.
    Built-in backends are constructed in ``asyncio.to_thread`` workers
    (``lib.backends.backend_assembly.assembler``), but access these subclients only from async generation
    methods on event-loop threads. Channels therefore bind to their actual operating loop.
    Validate API keys at construction, still failing immediately if missing.
    """

    def __init__(self, *, api_key: str | None) -> None:
        self._api_key = resolve_grok_api_key(api_key)
        self._sdk_client: xai_sdk.AsyncClient | None = None

    def _sdk(self) -> xai_sdk.AsyncClient:
        if self._sdk_client is None:
            import xai_sdk

            self._sdk_client = xai_sdk.AsyncClient(api_key=self._api_key)
        return self._sdk_client

    @property
    def chat(self) -> chat.Client:
        return self._sdk().chat

    @property
    def image(self) -> image.Client:
        return self._sdk().image

    @property
    def video(self) -> video.Client:
        return self._sdk().video


def create_grok_client(*, api_key: str | None = None) -> LazyGrokClient:
    """Validate/create xAI clients consistently; defer SDK creation until first use so backends can be constructed on any thread."""
    return LazyGrokClient(api_key=api_key)
