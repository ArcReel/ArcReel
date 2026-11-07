"""General retry decorator with exponential backoff and random jitter.

Independent of any specific provider SDK and reusable by all backends.
Providers can inject their retryable exception types through retryable_errors,
or use the retry_if predicate for fine-grained conditional retries.
Exceptions inheriting NonRetryableError are never retried, regardless of message-pattern matching.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from collections.abc import Awaitable, Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Protocol

logger = logging.getLogger(__name__)

# Base retryable errors (independent of any SDK)
BASE_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)


class NonRetryableError(RuntimeError):
    """Marker base class: exceptions of this type are never retried.

    should_retry uses substring matching (RETRYABLE_STATUS_PATTERNS). An exception
    message containing numeric details (such as token counts or line numbers) whose
    decimal representation includes "429"/"500" can be misclassified as transient.
    Resending a request that inevitably reproduces the same error is pointless.
    Exception types requiring unconditional non-retry semantics should inherit this
    class to short-circuit before pattern matching.
    """


# String-pattern matching covers transient errors whose types are not listed (case-insensitive)
RETRYABLE_STATUS_PATTERNS = (
    "429",
    "resource_exhausted",
    "500",
    "502",
    "503",
    "504",
    "internalservererror",
    "internal server error",
    "serviceunavailable",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "timed out",
    "timeout",
)

# Default retry configuration shared by backends, avoiding magic numbers scattered across 9+ locations
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS: tuple[int, ...] = (2, 4, 8)

# Downloads have no separate retry configuration: artifact retrieval and polling are both idempotent
# retrieval after provider job creation, sharing lib.backends.backend_runtime.with_artifact_retry failure budgets and backoff.


class AsyncClock(Protocol):
    """Explicit seam for asynchronous waiting and monotonic time."""

    def monotonic(self) -> float: ...

    async def sleep(self, delay: float) -> None: ...


class SystemClock:
    """Default production clock."""

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)


_SYSTEM_CLOCK = SystemClock()

# Clock used when ``clock`` is not explicit. Following contextvars semantics, it applies only to the
# bound context and tasks or ``to_thread`` threads derived from it; other contexts in the process retain the system clock.
_bound_clock: ContextVar[AsyncClock | None] = ContextVar("lib_infra_retry_bound_clock", default=None)


def resolve_clock(clock: AsyncClock | None) -> AsyncClock:
    """Clock used by waiters: explicit argument first, then the current context binding, then the system clock."""
    if clock is not None:
        return clock
    bound = _bound_clock.get()
    return bound if bound is not None else _SYSTEM_CLOCK


@contextmanager
def bind_clock(clock: AsyncClock) -> Generator[None]:
    """Use ``clock`` for retry and polling waits without an explicit ``clock`` in the current context.

    Useful when waiters are deep in the call chain and passing ``clock`` through every layer
    is impractical. The binding affects only the current context and tasks derived while
    bound; restore the previous value on exit without changing module state in ``time`` / ``asyncio``.
    """
    token = _bound_clock.set(clock)
    try:
        yield
    finally:
        _bound_clock.reset(token)


def should_retry(exc: Exception, retryable_errors: tuple[type[Exception], ...]) -> bool:
    """Determine whether an exception should be retried."""
    if isinstance(exc, NonRetryableError):
        return False
    if isinstance(exc, retryable_errors):
        return True
    error_lower = str(exc).lower()
    return any(pattern in error_lower for pattern in RETRYABLE_STATUS_PATTERNS)


def with_retry_async(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    retry_if: Callable[[Exception], bool] | None = None,
    clock: AsyncClock | None = None,
    jitter: Callable[[float, float], float] | None = None,
):
    """Compatibility wrapper for the async retry decorator; delegate waiting to ``retry_async``.

    The equivalent explicit entry point is ``retry_async(operation, clock=..., jitter=...)``.
    The decorator binds clock and jitter at definition time, preventing per-call replacement;
    the explicit entry point has no such limitation.

    When supplied, retry_if replaces the default should_retry predicate, allowing callers
    to control precisely which exceptions to retry (for example, only specific HTTP statuses).
    """

    predicate = retry_if if retry_if is not None else lambda e: should_retry(e, retryable_errors)

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if max_attempts <= 0:
                raise RuntimeError(f"with_retry_async: max_attempts={max_attempts}，未执行任何尝试")
            return await retry_async(
                lambda: func(*args, **kwargs),
                max_attempts=max_attempts,
                backoff_seconds=backoff_seconds,
                retry_if=predicate,
                clock=clock,
                jitter=jitter,
            )

        return wrapper

    return decorator


async def retry_async[T](
    operation: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
    retry_if: Callable[[Exception], bool] | None = None,
    clock: AsyncClock | None = None,
    jitter: Callable[[float, float], float] | None = None,
) -> T:
    """Run a retryable async operation, allowing explicit clock and jitter injection.

    Without ``clock``, use ``resolve_clock`` to obtain the current context binding, defaulting to the system clock.
    """
    active_clock = resolve_clock(clock)
    predicate = retry_if if retry_if is not None else lambda e: should_retry(e, retryable_errors)

    for attempt in range(max_attempts):
        try:
            return await operation()
        except Exception as exc:
            is_last = attempt >= max_attempts - 1
            if is_last or isinstance(exc, NonRetryableError) or not predicate(exc):
                raise
            wait_time = _compute_wait(attempt, backoff_seconds, jitter=jitter)
            logger.warning("API 调用异常: %s - %s", type(exc).__name__, str(exc)[:200])
            logger.warning("重试 %d/%d, %.1f 秒后...", attempt + 1, max_attempts - 1, wait_time)
            await active_clock.sleep(wait_time)

    raise RuntimeError(f"retry_async: max_attempts={max_attempts}，未执行任何尝试")


def _compute_wait(
    attempt: int,
    backoff_seconds: tuple[int, ...],
    *,
    jitter: Callable[[float, float], float] | None = None,
) -> float:
    """Compute the wait for retry attempt (including random jitter)."""
    backoff_idx = min(attempt, len(backoff_seconds) - 1)
    uniform = jitter if jitter is not None else random.uniform
    return backoff_seconds[backoff_idx] + uniform(0, 2)
