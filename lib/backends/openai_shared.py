"""Shared OpenAI utilities.

Reused by text_backends / image_backends / video_backends / providers.

Includes:
- OPENAI_RETRYABLE_ERRORS: retryable exception types.
- create_openai_client: AsyncOpenAI client factory.
- OPENAI_IMAGE_QUALITY_MAP: image_size tier → quality mapping for image_backends.openai.
  Sizes no longer use static (image_size, aspect_ratio) → "WxH" tables; arcreel_market_core.aspect_size
  computes exact proportional sizes, prioritizing ratio over resolution (docs/adr/0011).
"""

from __future__ import annotations

import logging

from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from lib.backends.transport_layer import TransportLayer, layer_async_client
from lib.config.url_utils import OFFICIAL_OPENAI_BASE_URL

logger = logging.getLogger(__name__)

OPENAI_RETRYABLE_ERRORS: tuple[type[Exception], ...] = ()

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
    pass  # openai is required; this branch is defensive only, falling back to an empty tuple.


def create_openai_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    max_retries: int | None = None,
    transport_layer: TransportLayer | None = None,
) -> AsyncOpenAI:
    """Create AsyncOpenAI clients with consistent api_key/base_url handling.

    Explicitly supply official endpoints for None/blank base_url: otherwise AsyncOpenAI reads
    OPENAI_BASE_URL environment variables, silently overriding DB configuration with residual values.
    DB is the sole base_url source; explicit values close that fallback. ``transport_layer`` layers
    over the SDK's default client.
    """
    kwargs: dict = {"base_url": (base_url or "").strip() or OFFICIAL_OPENAI_BASE_URL}
    if api_key:
        kwargs["api_key"] = api_key
    if max_retries is not None:
        kwargs["max_retries"] = max_retries
    if transport_layer is not None:
        kwargs["http_client"] = layer_async_client(DefaultAsyncHttpxClient(), transport_layer)
    return AsyncOpenAI(**kwargs)
