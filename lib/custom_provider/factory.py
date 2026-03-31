"""自定义供应商 Backend 工厂。

根据 CustomProvider 配置创建包装后的 Backend 实例。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.config.url_utils import normalize_base_url
from lib.custom_provider.backends import CustomImageBackend, CustomTextBackend, CustomVideoBackend
from lib.image_backends.gemini import GeminiImageBackend
from lib.image_backends.openai import OpenAIImageBackend
from lib.text_backends.gemini import GeminiTextBackend
from lib.text_backends.openai import OpenAITextBackend
from lib.video_backends.gemini import GeminiVideoBackend
from lib.video_backends.openai import OpenAIVideoBackend

if TYPE_CHECKING:
    from lib.db.models.custom_provider import CustomProvider

_VALID_MEDIA_TYPES = {"text", "image", "video"}
_VALID_API_FORMATS = {"openai", "google"}


def create_custom_backend(
    *,
    provider: CustomProvider,
    model_id: str,
    media_type: str,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend:
    """根据自定义供应商配置创建包装后的 Backend 实例。

    Args:
        provider: 自定义供应商 ORM 对象
        model_id: 要使用的模型 ID
        media_type: 媒体类型 ("text" | "image" | "video")

    Returns:
        包装后的 Custom*Backend 实例

    Raises:
        ValueError: api_format 或 media_type 不合法
    """
    api_format = provider.api_format
    if api_format not in _VALID_API_FORMATS:
        raise ValueError(f"不支持的 api_format: {api_format!r}，支持: {_VALID_API_FORMATS}")
    if media_type not in _VALID_MEDIA_TYPES:
        raise ValueError(f"不支持的 media_type: {media_type!r}，支持: {_VALID_MEDIA_TYPES}")

    provider_id = provider.provider_id

    if api_format == "openai":
        return _create_openai_backend(
            provider=provider, model_id=model_id, media_type=media_type, provider_id=provider_id
        )
    else:  # google
        return _create_google_backend(
            provider=provider, model_id=model_id, media_type=media_type, provider_id=provider_id
        )


def _create_openai_backend(
    *,
    provider: CustomProvider,
    model_id: str,
    media_type: str,
    provider_id: str,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend:
    """创建 OpenAI 格式的后端。"""
    if media_type == "text":
        delegate = OpenAITextBackend(api_key=provider.api_key, base_url=provider.base_url, model=model_id)
        return CustomTextBackend(provider_id=provider_id, delegate=delegate, model=model_id)
    elif media_type == "image":
        delegate = OpenAIImageBackend(api_key=provider.api_key, base_url=provider.base_url, model=model_id)
        return CustomImageBackend(provider_id=provider_id, delegate=delegate, model=model_id)
    else:  # video
        delegate = OpenAIVideoBackend(api_key=provider.api_key, base_url=provider.base_url, model=model_id)
        return CustomVideoBackend(provider_id=provider_id, delegate=delegate, model=model_id)


def _create_google_backend(
    *,
    provider: CustomProvider,
    model_id: str,
    media_type: str,
    provider_id: str,
) -> CustomTextBackend | CustomImageBackend | CustomVideoBackend:
    """创建 Google 格式的后端。"""
    base_url = normalize_base_url(provider.base_url) or None

    if media_type == "text":
        delegate = GeminiTextBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
        return CustomTextBackend(provider_id=provider_id, delegate=delegate, model=model_id)
    elif media_type == "image":
        delegate = GeminiImageBackend(api_key=provider.api_key, base_url=base_url, image_model=model_id)
        return CustomImageBackend(provider_id=provider_id, delegate=delegate, model=model_id)
    else:  # video
        delegate = GeminiVideoBackend(api_key=provider.api_key, base_url=base_url, video_model=model_id)
        return CustomVideoBackend(provider_id=provider_id, delegate=delegate, model=model_id)
