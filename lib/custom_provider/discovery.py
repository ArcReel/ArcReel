"""Custom provider model discovery.

Provides model list queries and media_type inference.
"""

from __future__ import annotations

import asyncio
import logging
import re

from google import genai
from openai import OpenAI

logger = logging.getLogger(__name__)

_IMAGE_PATTERN = re.compile(r"image|dall|img", re.IGNORECASE)
_VIDEO_PATTERN = re.compile(r"video|sora|kling|wan|seedance|cog|mochi|veo|pika", re.IGNORECASE)

# Google generation method → media_type mapping
_GENERATION_METHOD_MAP: dict[str, str] = {
    "generateVideo": "video",
    "generateVideos": "video",
    "generateImages": "image",
    "generateImage": "image",
}


def infer_media_type(model_id: str) -> str:
    """Infer media_type from model ID keywords.

    Returns:
        "image" | "video" | "text"
    """
    if _IMAGE_PATTERN.search(model_id):
        return "image"
    if _VIDEO_PATTERN.search(model_id):
        return "video"
    return "text"


async def discover_models(api_format: str, base_url: str | None, api_key: str) -> list[dict]:
    """Query the list of available models for a provider.

    Args:
        api_format: API format ("openai" | "google")
        base_url: Provider API base URL
        api_key: API key

    Returns:
        List of models, each containing: model_id, display_name, media_type, is_default, is_enabled

    Raises:
        ValueError: if api_format is not supported
    """
    if api_format == "openai":
        return await _discover_openai(base_url, api_key)
    elif api_format == "google":
        return await _discover_google(base_url, api_key)
    else:
        raise ValueError(f"Unsupported api_format: {api_format!r}, supported: 'openai', 'google'")


async def _discover_openai(base_url: str | None, api_key: str) -> list[dict]:
    """Discover models via OpenAI-compatible API."""

    def _sync():
        from lib.config.url_utils import ensure_openai_base_url

        client = OpenAI(api_key=api_key, base_url=ensure_openai_base_url(base_url))
        raw_models = client.models.list()
        models = sorted(raw_models, key=lambda m: m.id)
        return _build_result_list([(m.id, infer_media_type(m.id)) for m in models])

    return await asyncio.to_thread(_sync)


async def _discover_google(base_url: str | None, api_key: str) -> list[dict]:
    """Discover models via Google genai SDK."""

    def _sync():
        from lib.config.url_utils import ensure_google_base_url

        kwargs: dict = {"api_key": api_key}
        effective_url = ensure_google_base_url(base_url) if base_url else None
        if effective_url:
            kwargs["http_options"] = {"base_url": effective_url}
        client = genai.Client(**kwargs)

        raw_models = client.models.list()

        entries: list[tuple[str, str]] = []
        for m in raw_models:
            model_id = m.name
            if model_id.startswith("models/"):
                model_id = model_id[len("models/") :]
            media_type = _infer_from_generation_methods(m) or infer_media_type(model_id)
            entries.append((model_id, media_type))

        entries.sort(key=lambda e: e[0])
        return _build_result_list(entries)

    return await asyncio.to_thread(_sync)


def _infer_from_generation_methods(model) -> str | None:
    """Infer media_type from a Google model's supported_generation_methods.

    Returns:
        Inferred media_type, or None if it cannot be inferred
    """
    methods = getattr(model, "supported_generation_methods", None)
    if not methods:
        return None

    for method in methods:
        if method in _GENERATION_METHOD_MAP:
            return _GENERATION_METHOD_MAP[method]

    return None


def _build_result_list(entries: list[tuple[str, str]]) -> list[dict]:
    """Convert a list of (model_id, media_type) entries into a list of result dicts, marking the first of each media_type as default."""
    seen_types: set[str] = set()
    result: list[dict] = []

    for model_id, media_type in entries:
        is_default = media_type not in seen_types
        seen_types.add(media_type)
        result.append(
            {
                "model_id": model_id,
                "display_name": model_id,
                "media_type": media_type,
                "is_default": is_default,
                "is_enabled": True,
            }
        )

    return result
