"""通用重试装饰器，带指数退避和随机抖动。

不依赖任何特定供应商 SDK，可被所有后端复用。
各供应商可通过 retryable_errors 参数注入自己的可重试异常类型，
或通过 retry_if 谓词实现精细化的条件重试。
继承 NonRetryableError 的异常类型始终不重试，不受消息文本模式匹配影响。
"""

from __future__ import annotations

import asyncio
import functools
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Protocol

logger = logging.getLogger(__name__)

# 基础可重试错误（不依赖任何 SDK）
BASE_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)


class NonRetryableError(RuntimeError):
    """标记基类：命中此类型的异常始终不重试。

    _should_retry 的字符串模式匹配（RETRYABLE_STATUS_PATTERNS）按子串比对，若异常消息
    恰好携带一个数值型细节（如 token 数、行号）而其十进制文本包含 "429"/"500" 等子串，
    会被误判为瞬态错误进而重试——重发同一份必然复现同一错误的请求没有意义。需要绝对不
    可重试语义的异常类型应继承本类，在模式匹配之前短路。
    """


# 字符串模式匹配：覆盖异常类型不在列表中但属于瞬态的情况（大小写不敏感）
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

# 默认重试配置，供各后端直接引用，避免魔法数字分散在 9+ 处
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS: tuple[int, ...] = (2, 4, 8)

# 下载阶段重试配置（比生成阶段更宽容，因为下载失败不会浪费生成额度）
DOWNLOAD_MAX_ATTEMPTS = 5
DOWNLOAD_BACKOFF_SECONDS: tuple[int, ...] = (5, 10, 20, 40)


class AsyncClock(Protocol):
    """异步等待与单调计时的显式 seam。"""

    def monotonic(self) -> float: ...

    async def sleep(self, delay: float) -> None: ...


class SystemClock:
    """生产默认时钟。"""

    def monotonic(self) -> float:
        return time.monotonic()

    async def sleep(self, delay: float) -> None:
        await asyncio.sleep(delay)


def _should_retry(exc: Exception, retryable_errors: tuple[type[Exception], ...]) -> bool:
    """判断异常是否应当重试。"""
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
    """异步函数重试装饰器兼容壳，等待逻辑委托给 ``retry_async``。

    等价的显式入口是 ``retry_async(operation, clock=..., jitter=...)``：装饰器在定义期就绑死
    clock 与 jitter，调用方无法按调用现场替换，显式入口没有这一限制。

    当指定 retry_if 时，用该谓词替代默认的 _should_retry 进行重试判定，
    允许调用方精确控制哪些异常应当重试（如仅重试特定 HTTP 状态码）。
    """

    predicate = retry_if if retry_if is not None else lambda e: _should_retry(e, retryable_errors)

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
    """执行可重试异步操作，允许显式注入时钟与随机抖动。"""
    active_clock = clock if clock is not None else SystemClock()
    predicate = retry_if if retry_if is not None else lambda e: _should_retry(e, retryable_errors)

    for attempt in range(max_attempts):
        try:
            return await operation()
        except Exception as exc:
            is_last = attempt >= max_attempts - 1
            if is_last or isinstance(exc, NonRetryableError) or not predicate(exc):
                raise
            # 未注入 jitter 时按两个位置参调用：仍有测试把 `_compute_wait` 整体替换成
            # 只接 (attempt, backoff) 的确定化替身，多传 jitter= 会让它们 TypeError。
            wait_time = (
                _compute_wait(attempt, backoff_seconds)
                if jitter is None
                else _compute_wait(attempt, backoff_seconds, jitter=jitter)
            )
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
    """计算第 attempt 次重试的等待时间（含随机抖动）。"""
    backoff_idx = min(attempt, len(backoff_seconds) - 1)
    uniform = jitter if jitter is not None else random.uniform
    return backoff_seconds[backoff_idx] + uniform(0, 2)
