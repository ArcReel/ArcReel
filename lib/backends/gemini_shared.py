"""Shared Gemini utilities.

Non-GeminiClient helpers extracted from gemini_client.py for image_backends / video_backends /
providers / media_generator reuse, avoiding dependency cycles.

Includes:
- VERTEX_SCOPES: Vertex AI OAuth scopes.
- RETRYABLE_ERRORS: Gemini-specific retry errors extending BASE_RETRYABLE_ERRORS.
- RateLimiter: multimodel sliding-window rate limiter.
- _rate_limiter_limits_from_env / get_shared_rate_limiter / refresh_shared_rate_limiter.
- with_retry_async: generic retry decorator reexported from lib.infra.retry.
- gemini_http_options: AI Studio http_options for base_url/transport injection.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

import httpx

from lib.backends.transport_layer import TransportLayer, layer_async_client, layer_sync_client
from lib.infra.retry import BASE_RETRYABLE_ERRORS, with_retry_async

if TYPE_CHECKING:
    from google.genai.types import HttpOptions, HttpOptionsDict

__all__ = [
    "BASE_RETRYABLE_ERRORS",
    "GEMINI_DEFAULT_IMAGE_MODEL",
    "GEMINI_DEFAULT_VIDEO_MODEL",
    "RETRYABLE_ERRORS",
    "VERTEX_SCOPES",
    "RateLimiter",
    "get_shared_rate_limiter",
    "refresh_shared_rate_limiter",
    "with_retry_async",
]

logger = logging.getLogger(__name__)

# Shared Vertex service-account OAuth scopes for gemini_client / video_backends / providers.
VERTEX_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/generative-language",
]

# Gemini-specific retryable errors extending the base set.
RETRYABLE_ERRORS: tuple[type[Exception], ...] = BASE_RETRYABLE_ERRORS

# Add google.genai errors, keeping the base set when packages are unavailable.
try:
    from google import genai

    RETRYABLE_ERRORS = (
        *RETRYABLE_ERRORS,
        genai.errors.ClientError,
        genai.errors.ServerError,
    )
except ImportError:
    logger.debug("google.genai 未安装，跳过对应可重试错误，沿用基础集合")


class RateLimiter:
    """Multimodel sliding-window rate limiter."""

    def __init__(self, limits_dict: dict[str, int] | None = None, *, request_gap: float = 3.1):
        """Args:
        limits_dict: {model_name: rpm}, for example {"gemini-3-pro-image": 20}.
        request_gap: Minimum request interval in seconds, default 3.1.
        """
        self.limits = limits_dict or {}
        self.request_gap = request_gap
        # Request timestamps: {model_name: deque([timestamp1, timestamp2, ...])}.
        self.request_logs: dict[str, deque] = {}
        self.lock = threading.Lock()

    def acquire(self, model_name: str):
        """Block until a token is acquired."""
        if model_name not in self.limits:
            return  # No rate limit configured for this model.

        limit = self.limits[model_name]
        if limit <= 0:
            return

        with self.lock:
            if model_name not in self.request_logs:
                self.request_logs[model_name] = deque()

            log = self.request_logs[model_name]

            while True:
                now = time.time()

                # Clear records older than 60 seconds.
                while log and now - log[0] > 60:
                    log.popleft()

                # Enforce additional request gaps, user requirement >3s.
                # Even with tokens, ensure at least 3s since the previous request.
                # Read latest request time, possibly just written by another thread.
                min_gap = self.request_gap
                if log:
                    last_request = log[-1]
                    gap = time.time() - last_request
                    if gap < min_gap:
                        time.sleep(min_gap - gap)
                        # Update time and recheck.
                        continue

                if len(log) < limit:
                    # Token acquired.
                    log.append(time.time())
                    return

                # At the limit, calculate waiting time.
                # Wait until the oldest record expires.
                wait_time = 60 - (now - log[0]) + 0.1  # Add 0.1s buffer.
                if wait_time > 0:
                    time.sleep(wait_time)

    async def acquire_async(self, model_name: str):
        """Wait asynchronously until a token is acquired."""
        if model_name not in self.limits:
            return  # No rate limit configured for this model.

        limit = self.limits[model_name]
        if limit <= 0:
            return

        while True:
            with self.lock:
                now = time.time()

                if model_name not in self.request_logs:
                    self.request_logs[model_name] = deque()

                log = self.request_logs[model_name]

                # Clear records older than 60 seconds.
                while log and now - log[0] > 60:
                    log.popleft()

                min_gap = self.request_gap
                wait_needed = 0
                if log:
                    last_request = log[-1]
                    gap = now - last_request
                    if gap < min_gap:
                        # Wait asynchronously after releasing locks.
                        wait_needed = min_gap - gap

                if len(log) >= limit:
                    # At the limit, calculate waiting time.
                    wait_needed = max(wait_needed, 60 - (now - log[0]) + 0.1)

                if wait_needed == 0 and len(log) < limit:
                    # Token acquired.
                    log.append(now)
                    return

            # Wait asynchronously outside locks.
            if wait_needed > 0:
                await asyncio.sleep(wait_needed)
            else:
                await asyncio.sleep(0.1)  # Briefly yield control.


# Shared limiter tracks RPM by model; omitted names count against these two defaults. Video backends also use its default when unnamed.
# Assembly always supplies catalog defaults or fails. The video name is a direct-construction fallback using active GA models.
GEMINI_DEFAULT_IMAGE_MODEL = "gemini-3.1-flash-image"
GEMINI_DEFAULT_VIDEO_MODEL = "veo-3.1-fast-generate-001"


def resolve_gemini_api_key(api_key: str | None = None) -> str:
    if api_key is None or not api_key.strip():
        raise ValueError("请到系统配置页填写 Gemini API Key")
    return api_key.strip()


def gemini_http_options(
    base_url: str | None, transport_layer: TransportLayer | None
) -> HttpOptions | HttpOptionsDict | None:
    """AI Studio http_options, containing only nonempty ``base_url`` without injection.

    Inject ``transport_layer`` over both sync/async httpx channels. Explicitly supply asynchronous
    ``httpx_async_client``: otherwise aiohttp-installed environments bypass injection through aiohttp.
    The SDK does not close externally supplied clients.
    """
    if transport_layer is None:
        return {"base_url": base_url} if base_url else None
    from google.genai.types import HttpOptions

    return HttpOptions(
        base_url=base_url or None,
        httpx_client=layer_sync_client(httpx.Client(follow_redirects=True), transport_layer),
        httpx_async_client=layer_async_client(httpx.AsyncClient(follow_redirects=True), transport_layer),
    )


_shared_rate_limiter: RateLimiter | None = None
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
        image_model = GEMINI_DEFAULT_IMAGE_MODEL
    if video_model is None:
        video_model = GEMINI_DEFAULT_VIDEO_MODEL

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
) -> RateLimiter:
    """Get the process-shared RateLimiter.

    First calls construct from parameters/environment; subsequent calls return the same instance.

    - image_rpm / video_rpm: requests per minute, read from environment when None.
    - request_gap: minimum interval, GEMINI_REQUEST_GAP environment when None, default 3.1.
    """
    global _shared_rate_limiter
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
) -> RateLimiter:
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
