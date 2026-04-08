"""
OpenAI shared utilities module

Shared by text_backends / image_backends / video_backends / providers.

Contains:
- OPENAI_RETRYABLE_ERRORS — retryable error types
- create_openai_client — AsyncOpenAI client factory
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

OPENAI_RETRYABLE_ERRORS: tuple[type[Exception], ...] = ()

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
    pass  # openai is a required dependency; this branch is purely defensive — fall back to empty tuple


def create_openai_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    max_retries: int | None = None,
) -> AsyncOpenAI:
    """Create an AsyncOpenAI client, handling api_key and base_url uniformly."""
    kwargs: dict = {}
    if api_key:
        kwargs["api_key"] = api_key
    if base_url:
        kwargs["base_url"] = base_url
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    return AsyncOpenAI(**kwargs)
