"""Generic retry decorator with exponential backoff and random jitter.

Does not depend on any specific vendor SDK; can be reused by all backends.
Each vendor can inject its own retryable exception types via the retryable_errors parameter.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random

logger = logging.getLogger(__name__)

# Base retryable errors (no SDK dependency)
BASE_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)

# String pattern matching: covers transient cases where the exception type is not in the list (case-insensitive)
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

# Default retry configuration, referenced directly by each backend to avoid magic numbers scattered across 9+ places
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS: tuple[int, ...] = (2, 4, 8)


def _should_retry(exc: Exception, retryable_errors: tuple[type[Exception], ...]) -> bool:
    """Determine whether an exception should be retried."""
    if isinstance(exc, retryable_errors):
        return True
    error_lower = str(exc).lower()
    return any(pattern in error_lower for pattern in RETRYABLE_STATUS_PATTERNS)


def with_retry_async(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
):
    """Async function retry decorator with exponential backoff and random jitter."""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    if not _should_retry(e, retryable_errors):
                        raise

                    if attempt < max_attempts - 1:
                        backoff_idx = min(attempt, len(backoff_seconds) - 1)
                        base_wait = backoff_seconds[backoff_idx]
                        jitter = random.uniform(0, 2)
                        wait_time = base_wait + jitter
                        logger.warning(
                            "API call error: %s - %s",
                            type(e).__name__,
                            str(e)[:200],
                        )
                        logger.warning(
                            "Retrying %d/%d, in %.1f seconds...",
                            attempt + 1,
                            max_attempts - 1,
                            wait_time,
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        raise

            raise RuntimeError(f"with_retry_async: max_attempts={max_attempts}, no attempts were executed")

        return wrapper

    return decorator
