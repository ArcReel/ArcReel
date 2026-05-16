"""
Gemini 共享工具模块

从 gemini_client.py 提取的非 GeminiClient 工具，供 image_backends / video_backends /
providers / media_generator 等模块复用，避免循环依赖。

包含：
- VERTEX_SCOPES — Vertex AI OAuth scopes
- RETRYABLE_ERRORS — Gemini 专用可重试错误类型（扩展自 BASE_RETRYABLE_ERRORS）
- RateLimiter — 多模型滑动窗口限流器
- _rate_limiter_limits_from_env / get_shared_rate_limiter / refresh_shared_rate_limiter
- with_retry_async — 从 lib.retry re-export 的通用重试装饰器
"""

import asyncio
import logging
import threading
import time
from collections import deque
from typing import Optional

from .cost_calculator import cost_calculator
from .retry import BASE_RETRYABLE_ERRORS, with_retry_async

__all__ = [
    "BASE_RETRYABLE_ERRORS",
    "RETRYABLE_ERRORS",
    "VERTEX_SCOPES",
    "RateLimiter",
    "get_shared_rate_limiter",
    "refresh_shared_rate_limiter",
    "with_retry_async",
]

logger = logging.getLogger(__name__)

# Vertex AI 服务账号所需 OAuth scopes（共享常量，供 gemini_client / video_backends / providers 复用）
VERTEX_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language",
]

# Gemini 专用可重试错误类型（扩展基础集合）
RETRYABLE_ERRORS: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS

# 尝试导入 Google API 错误类型；google.api_core 与 google.genai 各自独立 try，
# 避免一边缺包就把另一边的可重试错误一起丢掉
try:
    from google.api_core import exceptions as google_exceptions  # pyright: ignore[reportMissingImports]

    RETRYABLE_ERRORS = RETRYABLE_ERRORS + (
        google_exceptions.ResourceExhausted,  # 429 Too Many Requests
        google_exceptions.ServiceUnavailable,  # 503
        google_exceptions.DeadlineExceeded,  # 超时
        google_exceptions.InternalServerError,  # 500
    )
except ImportError:
    pass

try:
    from google import genai

    RETRYABLE_ERRORS = RETRYABLE_ERRORS + (
        genai.errors.ClientError,  # pyright: ignore[reportAttributeAccessIssue]
        genai.errors.ServerError,  # pyright: ignore[reportAttributeAccessIssue]
    )
except ImportError:
    pass


class RateLimiter:
    """
    多模型滑动窗口限流器
    """

    def __init__(self, limits_dict: dict[str, int] | None = None, *, request_gap: float = 3.1):
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


def resolve_gemini_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 Gemini API Key")
    return api_key.strip()


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
