"""ENDPOINT_REGISTRY — 自定义供应商可用 endpoint 单一真相源。

每条 endpoint 是一个 EndpointSpec，绑定 media_type、family、HTTP 调用形态与 build_backend 闭包。
factory.create_custom_backend 通过 endpoint 字符串查表派发；
server.routers.custom_providers 通过 GET /custom-providers/endpoints 把目录暴露给前端，
让前端的下拉选项、路径展示完全派生自此真相源。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from lib.audio_backends.comfyui import ComfyUIAudioBackend
from lib.config.url_utils import ensure_google_base_url, ensure_openai_base_url
from lib.custom_provider.backends import CustomAudioBackend, CustomImageBackend, CustomTextBackend, CustomVideoBackend
from lib.custom_provider.fal_backends import FalAudioBackend, FalImageBackend, FalVideoBackend
from lib.image_backends.base import ImageCapability
from lib.image_backends.gemini import GeminiImageBackend
from lib.image_backends.openai import OpenAIImageBackend
from lib.text_backends.gemini import GeminiTextBackend
from lib.text_backends.openai import OpenAITextBackend
from lib.video_backends.newapi import NewAPIVideoBackend
from lib.video_backends.openai import OpenAIVideoBackend

if TYPE_CHECKING:
    from lib.db.models.custom_provider import CustomProvider


# ── EndpointSpec 数据类型 ───────────────────────────────────────────


@dataclass(frozen=True)
class EndpointSpec:
    """单条 endpoint 的元数据 + backend 构造闭包。"""

    key: str  # "openai-chat"
    media_type: str  # "text" | "image" | "video"
    family: str  # "openai" | "google" | "newapi"
    display_name_key: str  # 前端 i18n key（dashboard ns）
    request_method: str  # "POST"
    request_path_template: str  # "/v1/chat/completions"，可含 {model} 等占位
    build_backend: Callable[[CustomProvider, str], CustomTextBackend | CustomImageBackend | CustomVideoBackend]
    image_capabilities: frozenset[ImageCapability] | None = None  # image 类才填，非 image 类省略


# ── 各 endpoint 的 build_backend 闭包 ──────────────────────────────


def _build_openai_chat(provider, model_id: str) -> CustomTextBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAITextBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomTextBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_gemini_generate(provider, model_id: str) -> CustomTextBackend:
    base_url = ensure_google_base_url(provider.base_url) or None
    delegate = GeminiTextBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomTextBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_images(provider, model_id: str) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIImageBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_images_generations(provider, model_id: str) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        mode="generations_only",
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_images_edits(provider, model_id: str) -> CustomImageBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIImageBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        mode="edits_only",
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_gemini_image(provider, model_id: str) -> CustomImageBackend:
    base_url = ensure_google_base_url(provider.base_url) or None
    delegate = GeminiImageBackend(api_key=provider.api_key, base_url=base_url, image_model=model_id)
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_openai_video(provider, model_id: str) -> CustomVideoBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    delegate = OpenAIVideoBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_newapi_video(provider, model_id: str) -> CustomVideoBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    if not base_url:
        raise ValueError("NewAPI 视频后端需要 base_url")
    delegate = NewAPIVideoBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_comfyui_image(provider, model_id: str) -> CustomImageBackend:
    from lib.image_backends.comfyui import ComfyUIImageBackend

    delegate = ComfyUIImageBackend(base_url=provider.base_url, model=model_id)
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_comfyui_video(provider, model_id: str) -> CustomVideoBackend:
    from lib.video_backends.comfyui import ComfyUIVideoBackend

    delegate = ComfyUIVideoBackend(base_url=provider.base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_comfyui_audio(provider, model_id: str) -> CustomAudioBackend:
    delegate = ComfyUIAudioBackend(base_url=provider.base_url, model=model_id)
    return CustomAudioBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_fal_image(provider, model_id: str) -> CustomImageBackend:
    delegate = FalImageBackend(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=model_id,
        provider_id=provider.provider_id,
    )
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_fal_video(provider, model_id: str) -> CustomVideoBackend:
    delegate = FalVideoBackend(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=model_id,
        provider_id=provider.provider_id,
    )
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_fal_audio(provider, model_id: str) -> CustomAudioBackend:
    delegate = FalAudioBackend(
        api_key=provider.api_key,
        base_url=provider.base_url,
        model=model_id,
        provider_id=provider.provider_id,
    )
    return CustomAudioBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


# ── ENDPOINT_REGISTRY 注册表 ───────────────────────────────────────


ENDPOINT_REGISTRY: dict[str, EndpointSpec] = {
    "openai-chat": EndpointSpec(
        key="openai-chat",
        media_type="text",
        family="openai",
        display_name_key="endpoint_openai_chat_display",
        request_method="POST",
        request_path_template="/v1/chat/completions",
        build_backend=_build_openai_chat,
    ),
    "gemini-generate": EndpointSpec(
        key="gemini-generate",
        media_type="text",
        family="google",
        display_name_key="endpoint_gemini_generate_display",
        request_method="POST",
        request_path_template="/v1beta/models/{model}:generateContent",
        build_backend=_build_gemini_generate,
    ),
    "openai-images": EndpointSpec(
        key="openai-images",
        media_type="image",
        family="openai",
        display_name_key="endpoint_openai_images_display",
        request_method="POST",
        # /generations 与 /edits 由是否传参考图自动派发，brace 表达两条路径
        request_path_template="/v1/images/{generations,edits}",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_openai_images,
    ),
    "openai-images-generations": EndpointSpec(
        key="openai-images-generations",
        media_type="image",
        family="openai",
        display_name_key="endpoint_openai_images_generations_display",
        request_method="POST",
        request_path_template="/v1/images/generations",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE}),
        build_backend=_build_openai_images_generations,
    ),
    "openai-images-edits": EndpointSpec(
        key="openai-images-edits",
        media_type="image",
        family="openai",
        display_name_key="endpoint_openai_images_edits_display",
        request_method="POST",
        request_path_template="/v1/images/edits",
        image_capabilities=frozenset({ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_openai_images_edits,
    ),
    "gemini-image": EndpointSpec(
        key="gemini-image",
        media_type="image",
        family="google",
        display_name_key="endpoint_gemini_image_display",
        request_method="POST",
        request_path_template="/v1beta/models/{model}:generateContent",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_gemini_image,
    ),
    "openai-video": EndpointSpec(
        key="openai-video",
        media_type="video",
        family="openai",
        display_name_key="endpoint_openai_video_display",
        request_method="POST",
        request_path_template="/v1/videos",
        build_backend=_build_openai_video,
    ),
    "newapi-video": EndpointSpec(
        key="newapi-video",
        media_type="video",
        family="newapi",
        display_name_key="endpoint_newapi_video_display",
        request_method="POST",
        request_path_template="/v1/video/generations",
        build_backend=_build_newapi_video,
    ),
    "comfyui-image": EndpointSpec(
        key="comfyui-image",
        media_type="image",
        family="comfyui",
        display_name_key="endpoint_comfyui_image_display",
        request_method="POST",
        request_path_template="/prompt",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_comfyui_image,
    ),
    "comfyui-video": EndpointSpec(
        key="comfyui-video",
        media_type="video",
        family="comfyui",
        display_name_key="endpoint_comfyui_video_display",
        request_method="POST",
        request_path_template="/prompt",
        build_backend=_build_comfyui_video,
    ),
    "comfyui-audio": EndpointSpec(
        key="comfyui-audio",
        media_type="audio",
        family="comfyui",
        display_name_key="endpoint_comfyui_audio_display",
        request_method="POST",
        request_path_template="/prompt",
        build_backend=_build_comfyui_audio,
    ),
    "fal-image": EndpointSpec(
        key="fal-image",
        media_type="image",
        family="fal",
        display_name_key="endpoint_fal_image_display",
        request_method="POST",
        request_path_template="/{model}",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE}),
        build_backend=_build_fal_image,
    ),
    "fal-video": EndpointSpec(
        key="fal-video",
        media_type="video",
        family="fal",
        display_name_key="endpoint_fal_video_display",
        request_method="POST",
        request_path_template="/{model}",
        build_backend=_build_fal_video,
    ),
    "fal-audio": EndpointSpec(
        key="fal-audio",
        media_type="audio",
        family="fal",
        display_name_key="endpoint_fal_audio_display",
        request_method="POST",
        request_path_template="/{model}",
        build_backend=_build_fal_audio,
    ),
}


ENDPOINT_KEYS_BY_MEDIA_TYPE: dict[str, tuple[str, ...]] = {
    media_type: tuple(k for k, s in ENDPOINT_REGISTRY.items() if s.media_type == media_type)
    for media_type in {s.media_type for s in ENDPOINT_REGISTRY.values()}
}


# ── 工具函数 ───────────────────────────────────────────────────────


def get_endpoint_spec(endpoint: str) -> EndpointSpec:
    spec = ENDPOINT_REGISTRY.get(endpoint)
    if spec is None:
        raise ValueError(f"unknown endpoint: {endpoint!r}")
    return spec


def endpoint_to_media_type(endpoint: str) -> str:
    return get_endpoint_spec(endpoint).media_type


def endpoint_to_image_capabilities(endpoint: str) -> frozenset[ImageCapability]:
    """返回 image 类 endpoint 的 capability 集合。非 image 类抛 ValueError。"""
    spec = get_endpoint_spec(endpoint)
    if spec.image_capabilities is None:
        raise ValueError(f"endpoint {endpoint!r} is not an image endpoint")
    return spec.image_capabilities


def list_endpoints_by_media_type(media_type: str) -> list[EndpointSpec]:
    return [ENDPOINT_REGISTRY[k] for k in ENDPOINT_KEYS_BY_MEDIA_TYPE.get(media_type, ())]


def endpoint_spec_to_dict(spec: EndpointSpec) -> dict:
    """把 EndpointSpec 转成可序列化的纯数据 dict（剥掉不可 JSON 化的 build_backend 闭包）。"""
    data = asdict(spec)
    data.pop("build_backend", None)
    if spec.image_capabilities is not None:
        data["image_capabilities"] = sorted(c.value for c in spec.image_capabilities)
    else:
        data["image_capabilities"] = None
    return data


# ── 启发式：从 model_id + discovery_format 推默认 endpoint ─────────


_IMAGE_PATTERN = re.compile(r"image|dall|img|imagen|flux|seedream|jimeng", re.IGNORECASE)
_VIDEO_PATTERN = re.compile(
    r"video|sora|kling|wan|seedance|cog|mochi|veo|pika|minimax|hailuo|jimeng-?video|runway",
    re.IGNORECASE,
)

# ComfyUI-specific patterns for model filename inference
_COMFYUI_VIDEO_PATTERN = re.compile(
    r"wan|cogvideo|hunyuan.?video|animate.?diff|svd|sv3d|cosmos|ltx|mochi|stable.?video",
    re.IGNORECASE,
)
_COMFYUI_AUDIO_PATTERN = re.compile(
    r"stable.?audio|ace.?step|bark|musicgen",
    re.IGNORECASE,
)

_FAL_AUDIO_PATTERN = re.compile(
    r"tts|speech|audio|bark|musicgen|ace.?step|chatterbox|elevenlabs",
    re.IGNORECASE,
)


def infer_endpoint(model_id: str, discovery_format: str) -> str:
    """根据模型 id 与 discovery_format 推默认 endpoint。

    1) 视频家族 → 一律 "openai-video"（OpenAI /v1/videos 协议为首选默认，
       newapi-video 仅在用户手动选择时使用）
    2) 图像家族 → discovery_format=google 走 "gemini-image" 否则 "openai-images"
    3) 文本（默认）→ discovery_format=google 走 "gemini-generate" 否则 "openai-chat"

    ComfyUI 格式:
    - 视频模型 → "comfyui-video"
    - 音频模型 → "comfyui-audio"
    - 默认 → "comfyui-image"
    """
    if discovery_format == "comfyui":
        if _COMFYUI_VIDEO_PATTERN.search(model_id):
            return "comfyui-video"
        if _COMFYUI_AUDIO_PATTERN.search(model_id):
            return "comfyui-audio"
        return "comfyui-image"
    if discovery_format == "fal":
        if _FAL_AUDIO_PATTERN.search(model_id):
            return "fal-audio"
        if _VIDEO_PATTERN.search(model_id):
            return "fal-video"
        return "fal-image"
    if _VIDEO_PATTERN.search(model_id):
        return "openai-video"
    if _IMAGE_PATTERN.search(model_id):
        if discovery_format == "google":
            return "gemini-image"
        return "openai-images"
    if discovery_format == "google":
        return "gemini-generate"
    return "openai-chat"
