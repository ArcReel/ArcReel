"""
OpenAI 共享工具模块

供 text_backends / image_backends / video_backends / providers 复用。

包含：
- OPENAI_RETRYABLE_ERRORS — 可重试错误类型
- create_openai_client — AsyncOpenAI 客户端工厂
- OPENAI_IMAGE_SIZE_MAP / OPENAI_IMAGE_QUALITY_MAP — (image_size, aspect_ratio) → "WxH" 与 image_size → quality 的映射，
  供 image_backends.openai 与 cost_calculator 共同消费（cost_calculator 兜底路径需在 SDK 不返回 usage 时反查 size）。
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

OPENAI_RETRYABLE_ERRORS: tuple[type[Exception], ...] = ()

OPENAI_IMAGE_SIZE_MAP: dict[tuple[str, str], str] = {
    ("512px", "1:1"): "512x512",
    ("512px", "9:16"): "512x896",
    ("512px", "16:9"): "896x512",
    ("1K", "1:1"): "1024x1024",
    ("1K", "9:16"): "1024x1792",
    ("1K", "16:9"): "1792x1024",
    ("1K", "3:4"): "1024x1792",
    ("1K", "4:3"): "1792x1024",
    ("2K", "1:1"): "2048x2048",
    ("2K", "9:16"): "2048x3584",
    ("2K", "16:9"): "3584x2048",
}

OPENAI_IMAGE_QUALITY_MAP: dict[str, str] = {
    "512px": "low",
    "1K": "medium",
    "2K": "high",
    "4K": "high",
}

try:
    from openai import (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )

    OPENAI_RETRYABLE_ERRORS = (
        APIConnectionError,
        APITimeoutError,
        InternalServerError,
        RateLimitError,
    )
except ImportError:
    pass  # openai 是必装依赖，此分支仅作防御性保护；回退到空 tuple


def create_openai_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    max_retries: int | None = None,
) -> AsyncOpenAI:
    """创建 AsyncOpenAI 客户端，统一处理 api_key 和 base_url。"""
    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return AsyncOpenAI(**kwargs)
