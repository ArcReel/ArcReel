"""通用重试装饰器，带指数退避和随机抖动。

不依赖任何特定供应商 SDK，可被所有后端复用。
各供应商可通过 retryable_errors 参数注入自己的可重试异常类型。
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random

logger = logging.getLogger(__name__)

# 基础可重试错误（不依赖任何 SDK）
BASE_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)

# 字符串模式匹配：覆盖异常类型不在列表中但属于瞬态的情况（大小写不敏感）
RETRYABLE_STATUS_PATTERNS = (
    "429",
    "resource_exhausted",
    "500",
    "502",
    "503",
    "504",
    "internalservererror",
    "serviceunavailable",
    "bad gateway",
    "gateway timeout",
)

# 默认重试配置，供各后端直接引用，避免魔法数字分散在 9+ 处
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS: tuple[int, ...] = (2, 4, 8)


def _should_retry(exc: Exception, retryable_errors: tuple[type[Exception], ...]) -> bool:
    """判断异常是否应当重试。"""
    if isinstance(exc, retryable_errors):
        return True
    error_lower = str(exc).lower()
    return any(pattern in error_lower for pattern in RETRYABLE_STATUS_PATTERNS)


def with_retry_async(
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    backoff_seconds: tuple[int, ...] = DEFAULT_BACKOFF_SECONDS,
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
):
    """异步函数重试装饰器，带指数退避和随机抖动。"""

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
                            "API 调用异常: %s - %s",
                            type(e).__name__,
                            str(e)[:200],
                        )
                        logger.warning(
                            "重试 %d/%d, %.1f 秒后...",
                            attempt + 1,
                            max_attempts - 1,
                            wait_time,
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        raise

            raise RuntimeError(f"with_retry_async: max_attempts={max_attempts}，未执行任何尝试")

        return wrapper

    return decorator
