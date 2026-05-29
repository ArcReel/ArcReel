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
from urllib.parse import urlsplit

from lib.config.url_utils import ensure_google_base_url, ensure_openai_base_url
from lib.custom_provider.backends import CustomImageBackend, CustomTextBackend, CustomVideoBackend
from lib.image_backends.base import ImageCapability
from lib.image_backends.gemini import GeminiImageBackend
from lib.image_backends.openai import OpenAIImageBackend
from lib.text_backends.gemini import GeminiTextBackend
from lib.text_backends.openai import OpenAITextBackend
from lib.video_backends.ark import ArkVideoBackend
from lib.video_backends.newapi import NewAPIVideoBackend
from lib.video_backends.openai import OpenAIVideoBackend
from lib.video_backends.v2_video_generations import V2VideoGenerationsBackend
from lib.video_backends.vidu import ViduVideoBackend

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
    # 参考生视频单镜头参考图上限；仅 video 类有意义。
    # 显式 int：原样下传作为硬约束（0 表示不接受参考图，executor 据此将 references 裁剪为 0 张）。
    # None：未声明 —— 一个 endpoint 多 model、容量不同时 endpoint 维度给不出准数，由 resolver
    # fallthrough 到 backend video_capabilities 读取该 model 的真实上限。
    video_max_reference_images: int | None = None


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


def _ensure_url_path_suffix(base_url: str | None, suffix: str) -> str | None:
    """用户只填到 host 时补全协议已知挂载路径（ark /api/v3、vidu /ent/v2）；
    已带显式路径则原样信任，避免错误叠加。供 ark/vidu 闭包复用。

    纯域名（无 scheme，如 ``relay.example.com``）会被 urlsplit 整体当作 path，
    先补 ``https://`` 再判定，否则 host-only 配置既补不上协议也挂不上路径。
    """
    s = (base_url or "").strip().rstrip("/")
    if not s:
        return None
    normalized = s if "://" in s else f"https://{s}"
    if urlsplit(normalized).path in ("", "/"):
        return normalized + suffix
    return normalized


def _build_v2_video_generations(provider, model_id: str) -> CustomVideoBackend:
    if not provider.base_url:
        raise ValueError("v2-video-generations 端点需要 base_url")
    # base_url 归一化（去版本段 + 拼 /v2/video/generations）由 V2VideoGenerationsBackend 内部处理
    delegate = V2VideoGenerationsBackend(api_key=provider.api_key, base_url=provider.base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_ark_seedance(provider, model_id: str) -> CustomVideoBackend:
    base_url = _ensure_url_path_suffix(provider.base_url, "/api/v3")
    delegate = ArkVideoBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_vidu_video(provider, model_id: str) -> CustomVideoBackend:
    base_url = _ensure_url_path_suffix(provider.base_url, "/ent/v2")
    delegate = ViduVideoBackend(api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


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
        # OpenAI Sora input_reference 为单张首帧图。
        video_max_reference_images=1,
    ),
    "newapi-video": EndpointSpec(
        key="newapi-video",
        media_type="video",
        family="newapi",
        display_name_key="endpoint_newapi_video_display",
        request_method="POST",
        request_path_template="/v1/video/generations",
        build_backend=_build_newapi_video,
        video_max_reference_images=0,
    ),
    "v2-video-generations": EndpointSpec(
        key="v2-video-generations",
        media_type="video",
        family="v2",
        display_name_key="endpoint_v2_video_generations_display",
        request_method="POST",
        request_path_template="/v2/video/generations",
        build_backend=_build_v2_video_generations,
        # 多 model 共享端点、容量不同 → 未声明，fallthrough 到 backend caps
    ),
    "ark-seedance": EndpointSpec(
        key="ark-seedance",
        media_type="video",
        family="ark",
        display_name_key="endpoint_ark_seedance_display",
        request_method="POST",
        request_path_template="/api/v3/contents/generations/tasks",
        build_backend=_build_ark_seedance,
    ),
    "vidu-video": EndpointSpec(
        key="vidu-video",
        media_type="video",
        family="vidu",
        display_name_key="endpoint_vidu_video_display",
        request_method="POST",
        request_path_template="/ent/v2/img2video",
        build_backend=_build_vidu_video,
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


_IMAGE_PATTERN = re.compile(r"image|dall|img|imagen|flux|seedream|jimeng|viduq[12](?:[-_].*)?", re.IGNORECASE)
_VIDEO_PATTERN = re.compile(
    r"video|sora|kling|wan|seedance|cog|mochi|veo|pika|minimax|hailuo|jimeng-?video|runway|"
    r"vidu2(?:\.0)?(?:[-_].*)?|viduq3(?:[-_].*)?",
    re.IGNORECASE,
)


def infer_endpoint(model_id: str, discovery_format: str) -> str:
    """根据模型 id 与 discovery_format 推默认 endpoint（content-first）。

    model id 内容优先于 discovery_format：中转站普遍 discovery_format="openai"，但模型
    列表常夹带 gemini-*/imagen-* 原生 id，必须按内容纠偏到 Google 端点，否则被错推到
    openai-chat/openai-images，每次都要手动改回。

    1) imagen → "gemini-image"（图像，不论 discovery_format）
    2) gemini 原生模型（非 video）→ image 形态走 "gemini-image"，否则文本走 "gemini-generate"
       （均不论 discovery_format：gemini-* 一律按 Google 端点路由，gemini-*-image 也不例外）
    3) 视频家族 → seedance→"ark-seedance"、viduq3→"vidu-video"、否则 "openai-video"
       （v2-video-generations 命名碎片化无法可靠识别，不自动推断，留用户手选）
    4) 图像家族 → discovery_format=google 走 "gemini-image" 否则 "openai-images"
    5) 默认（文本）→ discovery_format=google 走 "gemini-generate" 否则 "openai-chat"
    """
    lowered = model_id.lower()
    is_video = bool(_VIDEO_PATTERN.search(model_id))
    is_image = bool(_IMAGE_PATTERN.search(model_id))

    if "imagen" in lowered:
        return "gemini-image"
    if "gemini" in lowered and not is_video:
        return "gemini-image" if is_image else "gemini-generate"
    if is_video:
        if "seedance" in lowered:
            return "ark-seedance"
        if "viduq3" in lowered:
            return "vidu-video"
        return "openai-video"
    if is_image:
        return "gemini-image" if discovery_format == "google" else "openai-images"
    return "gemini-generate" if discovery_format == "google" else "openai-chat"
