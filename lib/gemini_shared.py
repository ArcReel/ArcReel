"""
Gemini 共享工具模块

从 gemini_client.py 提取的非 GeminiClient 工具，供 image_backends / video_backends /
providers / media_generator 等模块复用，避免循环依赖。

包含：
- VERTEX_SCOPES — Vertex AI OAuth scopes
- RETRYABLE_ERRORS — 可重试错误类型
- RateLimiter — 多模型滑动窗口限流器
- _rate_limiter_limits_from_env / get_shared_rate_limiter / refresh_shared_rate_limiter
- with_retry / with_retry_async — 带指数退避的重试装饰器
"""

import asyncio
import functools
import logging
import random
import threading
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .cost_calculator import cost_calculator

logger = logging.getLogger(__name__)

# Vertex AI 服务账号所需 OAuth scopes（共享常量，供 gemini_client / video_backends / providers 复用）
VERTEX_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language",
]

# 可重试的错误类型
RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
)

# 尝试导入 Google API 错误类型
try:
    from google import genai  # Import genai to access its errors
    from google.api_core import exceptions as google_exceptions

    RETRYABLE_ERRORS = RETRYABLE_ERRORS + (
        google_exceptions.ResourceExhausted,  # 429 Too Many Requests
        google_exceptions.ServiceUnavailable,  # 503
        google_exceptions.DeadlineExceeded,  # 超时
        google_exceptions.InternalServerError,  # 500
        genai.errors.ClientError,  # 4xx errors from new SDK
        genai.errors.ServerError,  # 5xx errors from new SDK
    )
except ImportError:
    pass


class RateLimiter:
    """
    多模型滑动窗口限流器
    """

    def __init__(self, limits_dict: dict[str, int] = None, *, request_gap: float = 3.1):
        """
        Args:
            limits_dict: {model_name: rpm} 字典。例如 {"gemini-3-pro-image-preview": 20}
            request_gap: 最小请求间隔（秒），默认 3.1
        """
        self.limits = limits_dict or {}
        self.request_gap = request_gap
        # 存储请求时间戳：{model_name: deque([timestamp1, timestamp2, ...])}
        self.request_logs: dict[str, deque] = {}
        self.lock = threading.Lock()

    def acquire(self, model_name: str):
        """
        阻塞直到获得令牌
        """
        if model_name not in self.limits:
            return  # 该模型无限流配置

        limit = self.limits[model_name]
        if limit <= 0:
            return

        with self.lock:
            if model_name not in self.request_logs:
                self.request_logs[model_name] = deque()

            log = self.request_logs[model_name]

            while True:
                now = time.time()

                # 清理超过 60 秒的旧记录
                while log and now - log[0] > 60:
                    log.popleft()

                # 强制增加请求间隔（用户要求 > 3s）
                # 即使获得了令牌，也要确保距离上一次请求至少 3s
                # 获取最新的请求时间（可能是其他线程刚刚写入的）
                min_gap = self.request_gap
                if log:
                    last_request = log[-1]
                    gap = time.time() - last_request
                    if gap < min_gap:
                        time.sleep(min_gap - gap)
                        # 更新时间，重新检查
                        continue

                if len(log) < limit:
                    # 获取令牌成功
                    log.append(time.time())
                    return

                # 达到限制，计算等待时间
                # 等待直到最早的记录过期
                wait_time = 60 - (now - log[0]) + 0.1  # 多加 0.1s 缓冲
                if wait_time > 0:
                    time.sleep(wait_time)

    async def acquire_async(self, model_name: str):
        """
        异步阻塞直到获得令牌
        """
        if model_name not in self.limits:
            return  # 该模型无限流配置

        limit = self.limits[model_name]
        if limit <= 0:
            return

        while True:
            with self.lock:
                now = time.time()

                if model_name not in self.request_logs:
                    self.request_logs[model_name] = deque()

                log = self.request_logs[model_name]

                # 清理超过 60 秒的旧记录
                while log and now - log[0] > 60:
                    log.popleft()

                min_gap = self.request_gap
                wait_needed = 0
                if log:
                    last_request = log[-1]
                    gap = now - last_request
                    if gap < min_gap:
                        # 释放锁后异步等待
                        wait_needed = min_gap - gap

                if len(log) >= limit:
                    # 达到限制，计算等待时间
                    wait_needed = max(wait_needed, 60 - (now - log[0]) + 0.1)

                if wait_needed == 0 and len(log) < limit:
                    # 获取令牌成功
                    log.append(now)
                    return

            # 在锁外异步等待
            if wait_needed > 0:
                await asyncio.sleep(wait_needed)
            else:
                await asyncio.sleep(0.1)  # 短暂让出控制权


_SHARED_IMAGE_MODEL_NAME = cost_calculator.DEFAULT_IMAGE_MODEL
_SHARED_VIDEO_MODEL_NAME = cost_calculator.DEFAULT_VIDEO_MODEL

_shared_rate_limiter: Optional["RateLimiter"] = None
_shared_rate_limiter_lock = threading.Lock()


def _rate_limiter_limits_from_env(
    *,
    image_rpm: int | None = None,
    video_rpm: int | None = None,
    image_model: str | None = None,
    video_model: str | None = None,
) -> dict[str, int]:
    if image_rpm is None:
        image_rpm = 15
    if video_rpm is None:
        video_rpm = 10
    if image_model is None:
        image_model = _SHARED_IMAGE_MODEL_NAME
    if video_model is None:
        video_model = _SHARED_VIDEO_MODEL_NAME

    limits: dict[str, int] = {}
    if image_rpm > 0:
        limits[image_model] = image_rpm
    if video_rpm > 0:
        limits[video_model] = video_rpm
    return limits


def get_shared_rate_limiter(
    *,
    image_rpm: int | None = None,
    video_rpm: int | None = None,
    image_model: str | None = None,
    video_model: str | None = None,
    request_gap: float | None = None,
) -> "RateLimiter":
    """
    获取进程内共享的 RateLimiter

    首次调用时根据参数或环境变量创建实例，后续调用返回同一实例。

    - image_rpm / video_rpm：每分钟请求数限制（None 时从环境变量读取）
    - request_gap：最小请求间隔（None 时从环境变量 GEMINI_REQUEST_GAP 读取，默认 3.1）
    """
    global _shared_rate_limiter
    if _shared_rate_limiter is not None:
        return _shared_rate_limiter

    with _shared_rate_limiter_lock:
        if _shared_rate_limiter is not None:
            return _shared_rate_limiter

        limits = _rate_limiter_limits_from_env(
            image_rpm=image_rpm,
            video_rpm=video_rpm,
            image_model=image_model,
            video_model=video_model,
        )
        if request_gap is None:
            request_gap = 3.1
        _shared_rate_limiter = RateLimiter(limits, request_gap=request_gap)
        return _shared_rate_limiter


def refresh_shared_rate_limiter(
    *,
    image_rpm: int | None = None,
    video_rpm: int | None = None,
    image_model: str | None = None,
    video_model: str | None = None,
    request_gap: float | None = None,
) -> "RateLimiter":
    """
    Refresh the process-wide shared RateLimiter in-place.

    Updates model keys and request_gap. Parameters default to env vars when None.
    """
    limiter = get_shared_rate_limiter()
    new_limits = _rate_limiter_limits_from_env(
        image_rpm=image_rpm,
        video_rpm=video_rpm,
        image_model=image_model,
        video_model=video_model,
    )

    with limiter.lock:
        limiter.limits = new_limits
        if request_gap is not None:
            limiter.request_gap = request_gap

    return limiter


def with_retry(
    max_attempts: int = 5,
    backoff_seconds: tuple[int, ...] = (2, 4, 8, 16, 32),
    retryable_errors: tuple[type[Exception], ...] = RETRYABLE_ERRORS,
):
    """
    带指数退避的重试装饰器
    """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 尝试提取 output_path 以便在日志中显示上下文
            output_path = kwargs.get("output_path")
            # 如果是位置参数，generate_image 的 output_path 是第 5 个参数 (self, prompt, ref, ar, output_path)
            if not output_path and len(args) > 4:
                output_path = args[4]

            context_str = ""
            if output_path:
                context_str = f"[{Path(output_path).name}] "

            last_error = None
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    # Catch ALL exceptions and check if they look like a retryable error
                    last_error = e
                    should_retry = False

                    # Check if it's in our explicit list
                    if isinstance(e, retryable_errors):
                        should_retry = True

                    # Check by string analysis (catch-all for 429/500/503)
                    error_str = str(e)
                    if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                        should_retry = True
                    elif "500" in error_str or "InternalServerError" in error_str:
                        should_retry = True
                    elif "503" in error_str or "ServiceUnavailable" in error_str:
                        should_retry = True

                    if not should_retry:
                        raise e

                    if attempt < max_attempts - 1:
                        # 确保不超过 backoff 数组长度
                        backoff_idx = min(attempt, len(backoff_seconds) - 1)
                        base_wait = backoff_seconds[backoff_idx]
                        jitter = random.uniform(0, 2)  # 0-2秒随机抖动
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
                        time.sleep(wait_time)
            raise last_error

        return wrapper

    return decorator


def with_retry_async(
    max_attempts: int = 5,
    backoff_seconds: tuple[int, ...] = (2, 4, 8, 16, 32),
    retryable_errors: tuple[type[Exception], ...] = RETRYABLE_ERRORS,
):
    """
    异步函数重试装饰器，带指数退避和随机抖动
    """

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # 尝试提取 output_path 以便在日志中显示上下文
            output_path = kwargs.get("output_path")
            if not output_path and len(args) > 4:
                output_path = args[4]

            context_str = ""
            if output_path:
                context_str = f"[{Path(output_path).name}] "

            last_error = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e
                    should_retry = False

                    if isinstance(e, retryable_errors):
                        should_retry = True

                    error_str = str(e)
                    if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                        should_retry = True
                    elif "500" in error_str or "InternalServerError" in error_str:
                        should_retry = True
                    elif "503" in error_str or "ServiceUnavailable" in error_str:
                        should_retry = True

                    if not should_retry:
                        raise e

                    if attempt < max_attempts - 1:
                        backoff_idx = min(attempt, len(backoff_seconds) - 1)
                        base_wait = backoff_seconds[backoff_idx]
                        jitter = random.uniform(0, 2)  # 0-2秒随机抖动
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
            raise last_error

        return wrapper

    return decorator
