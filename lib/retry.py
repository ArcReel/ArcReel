"""通用重试装饰器，带指数退避和随机抖动。

不依赖任何特定供应商 SDK，可被所有后端复用。
各供应商可通过 retryable_errors 参数注入自己的可重试异常类型。
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
from pathlib import Path

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


def _should_retry(exc: Exception, retryable_errors: tuple[type[Exception], ...]) -> bool:
    """判断异常是否应当重试。"""
    if isinstance(exc, retryable_errors):
        return True
    error_lower = str(exc).lower()
    return any(pattern in error_lower for pattern in RETRYABLE_STATUS_PATTERNS)


def with_retry_async(
    max_attempts: int = 3,
    backoff_seconds: tuple[int, ...] = (2, 4, 8),
    retryable_errors: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS,
):
    """异步函数重试装饰器，带指数退避和随机抖动。"""

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            output_path = kwargs.get("output_path")
            context_str = f"[{Path(output_path).name}] " if output_path else ""

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
                            "%sAPI 调用异常: %s - %s",
                            context_str,
                            type(e).__name__,
                            str(e)[:200],
                        )
                        logger.warning(
                            "%s重试 %d/%d, %.1f 秒后...",
                            context_str,
                            attempt + 1,
                            max_attempts - 1,
                            wait_time,
                        )
                        await asyncio.sleep(wait_time)
                    else:
                        raise

            # max_attempts=0 的防御性兜底
            raise RuntimeError(f"with_retry_async: max_attempts={max_attempts}，未执行任何尝试")

        return wrapper

    return decorator
