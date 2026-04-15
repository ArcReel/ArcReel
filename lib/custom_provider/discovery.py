"""自定义供应商模型发现。

提供模型列表查询与 media_type 推断功能。
"""

from __future__ import annotations

import asyncio
import logging
import re

from google import genai
from openai import OpenAI

logger = logging.getLogger(__name__)

_IMAGE_PATTERN = re.compile(r"image|dall|img", re.IGNORECASE)
_VIDEO_PATTERN = re.compile(
    r"video|sora|kling|wan|seedance|cog|mochi|veo|pika|minimax|hailuo|seedream|jimeng|runway",
    re.IGNORECASE,
)

# Google generation method → media_type 映射
_GENERATION_METHOD_MAP: dict[str, str] = {
    "generateVideo": "video",
    "generateVideos": "video",
    "generateImages": "image",
    "generateImage": "image",
}


def infer_media_type(model_id: str) -> str:
    """根据模型 ID 关键字推断 media_type。

    Returns:
        "image" | "video" | "text"
    """
    if _IMAGE_PATTERN.search(model_id):
        return "image"
    if _VIDEO_PATTERN.search(model_id):
        return "video"
    return "text"


async def discover_models(api_format: str, base_url: str | None, api_key: str) -> list[dict]:
    """查询供应商的可用模型列表。

    Args:
        api_format: API 格式 ("openai" | "google" | "newapi")
        base_url: 供应商 API 基础 URL
        api_key: API 密钥

    Returns:
        模型列表，每项包含: model_id, display_name, media_type, is_default, is_enabled

    Raises:
        ValueError: api_format 不支持
    """
    if api_format in {"openai", "newapi"}:
        return await _discover_openai(base_url, api_key)
    elif api_format == "google":
        return await _discover_google(base_url, api_key)
    else:
        raise ValueError(f"不支持的 api_format: {api_format!r}，支持: 'openai', 'google', 'newapi'")


async def _discover_openai(base_url: str | None, api_key: str) -> list[dict]:
    """通过 OpenAI 兼容 API 发现模型。"""

    def _sync():
        from lib.config.url_utils import ensure_openai_base_url

        client = OpenAI(api_key=api_key, base_url=ensure_openai_base_url(base_url))
        raw_models = client.models.list()
        models = sorted(raw_models, key=lambda m: m.id)
        return _build_result_list([(m.id, infer_media_type(m.id)) for m in models])

    return await asyncio.to_thread(_sync)


async def _discover_google(base_url: str | None, api_key: str) -> list[dict]:
    """通过 Google genai SDK 发现模型。"""

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
    """从 Google model 的 supported_generation_methods 推断 media_type。

    Returns:
        推断出的 media_type，无法推断时返回 None
    """
    methods = getattr(model, "supported_generation_methods", None)
    if not methods:
        return None

    for method in methods:
        if method in _GENERATION_METHOD_MAP:
            return _GENERATION_METHOD_MAP[method]

    return None


def _build_result_list(entries: list[tuple[str, str]]) -> list[dict]:
    """将 (model_id, media_type) 列表转为结果字典列表，标记每种 media_type 的第一个为 default。"""
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
