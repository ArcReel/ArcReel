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

from lib.audio_backends.openai import OpenAIAudioBackend
from lib.config.url_utils import ensure_google_base_url, ensure_openai_base_url
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.image_backends.base import ImageCapability
from lib.image_backends.dashscope import DashScopeImageBackend
from lib.image_backends.gemini import GeminiImageBackend
from lib.image_backends.kling import KlingImageBackend
from lib.image_backends.minimax import MiniMaxImageBackend
from lib.image_backends.openai import OpenAIImageBackend
from lib.text_backends.gemini import GeminiTextBackend
from lib.text_backends.openai import OpenAITextBackend
from lib.video_backends.ark import ArkVideoBackend
from lib.video_backends.base import VideoCapabilities
from lib.video_backends.dashscope import DashScopeVideoBackend
from lib.video_backends.kling import KlingVideoBackend
from lib.video_backends.minimax import MiniMaxVideoBackend
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
    media_type: str  # "text" | "image" | "video" | "audio"
    family: str  # "openai" | "google" | "newapi"
    display_name_key: str  # 前端 i18n key（dashboard ns）
    request_method: str  # "POST"
    request_path_template: str  # "/v1/chat/completions"，可含 {model} 等占位
    build_backend: Callable[
        [CustomProvider, str],
        CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend,
    ]
    image_capabilities: frozenset[ImageCapability] | None = None  # image 类才填，非 image 类省略
    # 参考生视频单镜头参考图上限；仅 video 类有意义。
    # 显式 int：原样下传作为硬约束（0 表示不接受参考图，executor 据此将 references 裁剪为 0 张）。
    # None：未声明 —— 一个 endpoint 多 model、容量不同时 endpoint 维度给不出准数，由 resolver
    # 调 video_caps_for_model 按 model_id 读取该 model 的真实上限。
    video_max_reference_images: int | None = None
    # 当 video_max_reference_images 为 None 时，resolver 用此纯函数按 model_id 读 backend 声明的
    # caps —— 不构造 SDK client、不查 provider 行。video_max_reference_images 为 int 时此字段应为
    # None（endpoint 维度已能给出硬上限）。二者对每个 video endpoint 恰填其一（见注册表末尾不变式）。
    video_caps_for_model: Callable[[str], VideoCapabilities] | None = None


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


def _build_openai_tts(provider, model_id: str) -> CustomAudioBackend:
    base_url = ensure_openai_base_url(provider.base_url)
    # provider_name 让 delegate 日志与 AudioSynthesisResult.provider 归因到真实 provider，
    # 与包装层 .name 的记账身份一致，而非内置 openai。
    delegate = OpenAIAudioBackend(
        api_key=provider.api_key,
        base_url=base_url,
        model=model_id,
        provider_name=provider.provider_id,
    )
    return CustomAudioBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


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
    """用户只填到 host 时补全协议已知挂载路径（ark /api/v3、vidu /ent/v2、kling /v1）；
    已带显式路径则原样信任，避免错误叠加。供 ark/vidu/kling 闭包复用。

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


def _build_dashscope_image(provider, model_id: str) -> CustomImageBackend:
    # backend 内部由 host 派生 /api/v1（容忍带/不带后缀），此处传原始 base_url 即可，不重复归一化
    delegate = DashScopeImageBackend(api_key=provider.api_key, base_url=provider.base_url, model=model_id)
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_dashscope_async_video(provider, model_id: str) -> CustomVideoBackend:
    delegate = DashScopeVideoBackend(api_key=provider.api_key, base_url=provider.base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_minimax_image(provider, model_id: str) -> CustomImageBackend:
    # backend 内部把 base_url 归一化为 {host}/v1（容忍 host 或带 /v1 后缀），此处传原始 base_url 即可
    delegate = MiniMaxImageBackend(api_key=provider.api_key, base_url=provider.base_url, model=model_id)
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_minimax_video(provider, model_id: str) -> CustomVideoBackend:
    # 两步取 URL（submit→轮询 file_id→retrieve download_url）由 MiniMaxVideoBackend 内部处理
    delegate = MiniMaxVideoBackend(api_key=provider.api_key, base_url=provider.base_url, model=model_id)
    return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_kling_image(provider, model_id: str) -> CustomImageBackend:
    # 中转站「原样代理可灵」：bearer 模式旁路 JWT 管理器，用静态 api_key 直发可灵原生异步图像端点。
    # 仅 host 时补全可灵协议挂载路径 /v1（含显式路径则原样信任）；原生 model_name 透传不解耦别名。
    base_url = _ensure_url_path_suffix(provider.base_url, "/v1")
    delegate = KlingImageBackend(auth_mode="bearer", api_key=provider.api_key, base_url=base_url, model=model_id)
    return CustomImageBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)


def _build_kling_video(provider, model_id: str) -> CustomVideoBackend:
    # 中转站「原样代理可灵」：bearer 模式旁路 JWT 管理器，用静态 api_key 直发可灵原生异步视频端点。
    base_url = _ensure_url_path_suffix(provider.base_url, "/v1")
    delegate = KlingVideoBackend(auth_mode="bearer", api_key=provider.api_key, base_url=base_url, model=model_id)
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
        # 多 model 共享端点、容量不同 → endpoint 维度不声明，按 model 读 backend caps（不构造 client）
        video_caps_for_model=V2VideoGenerationsBackend.video_capabilities_for_model,
    ),
    "ark-seedance": EndpointSpec(
        key="ark-seedance",
        media_type="video",
        family="ark",
        display_name_key="endpoint_ark_seedance_display",
        request_method="POST",
        request_path_template="/api/v3/contents/generations/tasks",
        build_backend=_build_ark_seedance,
        video_caps_for_model=ArkVideoBackend.video_capabilities_for_model,
    ),
    "vidu-video": EndpointSpec(
        key="vidu-video",
        media_type="video",
        family="vidu",
        display_name_key="endpoint_vidu_video_display",
        request_method="POST",
        request_path_template="/ent/v2/img2video",
        build_backend=_build_vidu_video,
        video_caps_for_model=ViduVideoBackend.video_capabilities_for_model,
    ),
    "dashscope-image": EndpointSpec(
        key="dashscope-image",
        media_type="image",
        family="dashscope",
        display_name_key="endpoint_dashscope_image_display",
        request_method="POST",
        request_path_template="/api/v1/services/aigc/multimodal-generation/generation",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_dashscope_image,
    ),
    "openai-tts": EndpointSpec(
        key="openai-tts",
        media_type="audio",
        family="openai",
        display_name_key="endpoint_openai_tts_display",
        request_method="POST",
        request_path_template="/v1/audio/speech",
        build_backend=_build_openai_tts,
    ),
    "dashscope-async-video": EndpointSpec(
        key="dashscope-async-video",
        media_type="video",
        family="dashscope",
        display_name_key="endpoint_dashscope_async_video_display",
        request_method="POST",
        request_path_template="/api/v1/services/aigc/video-generation/video-synthesis",
        build_backend=_build_dashscope_async_video,
        # 多 model（happyhorse-r2v=9 / wan2.7-r2v=5）容量不同 → endpoint 维度不声明 int cap，
        # 按 model 读 backend caps（不构造 client）。
        video_caps_for_model=DashScopeVideoBackend.video_capabilities_for_model,
    ),
    "minimax-image": EndpointSpec(
        key="minimax-image",
        media_type="image",
        family="minimax",
        display_name_key="endpoint_minimax_image_display",
        request_method="POST",
        request_path_template="/image_generation",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_minimax_image,
    ),
    "minimax-video": EndpointSpec(
        key="minimax-video",
        media_type="video",
        family="minimax",
        display_name_key="endpoint_minimax_video_display",
        request_method="POST",
        request_path_template="/video_generation",
        build_backend=_build_minimax_video,
        # 多 model 容量异质（S2V-01 单脸参考 max_ref=1 / 海螺系列走首帧 no-ref）→ endpoint 维度不
        # 声明 int cap，按 model 读 backend caps（不构造 client）。
        video_caps_for_model=MiniMaxVideoBackend.video_capabilities_for_model,
    ),
    "kling-image": EndpointSpec(
        key="kling-image",
        media_type="image",
        family="kling",
        display_name_key="endpoint_kling_image_display",
        request_method="POST",
        request_path_template="/v1/images/generations",
        image_capabilities=frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE}),
        build_backend=_build_kling_image,
    ),
    "kling-video": EndpointSpec(
        key="kling-video",
        media_type="video",
        family="kling",
        display_name_key="endpoint_kling_video_display",
        request_method="POST",
        # 无首帧走 text2video、有首帧走 image2video（含可选尾帧）、有多图主体走 multi-image2video（R2V）
        request_path_template="/v1/videos/{text2video,image2video,multi-image2video}",
        build_backend=_build_kling_video,
        # 参考图上限随 model 异质（v3-omni / video-o1 多图主体 R2V max=4，其余首尾帧无参考为 0）→ 不在
        # endpoint 维度声明 int cap，按 model 读 backend 纯 caps 函数（与 minimax-video 同构）。
        video_caps_for_model=KlingVideoBackend.video_capabilities_for_model,
    ),
}


ENDPOINT_KEYS_BY_MEDIA_TYPE: dict[str, tuple[str, ...]] = {
    media_type: tuple(k for k, s in ENDPOINT_REGISTRY.items() if s.media_type == media_type)
    for media_type in {s.media_type for s in ENDPOINT_REGISTRY.values()}
}


def _validate_video_caps_declarations() -> None:
    """import 期校验参考图上限来源：caps_fn 若声明必须可调用；每个 video endpoint 必须「int cap」
    XOR「caps_fn 非 None」恰一、且 int cap 非负；非 video endpoint 两者皆 None。misconfig（caps_fn
    填成非 callable、多 model 共享端点漏配 caps_fn、同时声明二者、或声明负数 cap）在 import 期
    fail-fast，而非等到 request 期 resolver 才抛。
    """
    for key, spec in ENDPOINT_REGISTRY.items():
        cap = spec.video_max_reference_images
        caps_fn = spec.video_caps_for_model
        has_int = cap is not None
        # resolver 会以 caps_fn(model_id) 执行它，故必须是 callable。误填字符串/整数等非空非 callable
        # 值要在 import 期就挡掉，而非放行到请求期才在 resolver 里炸——与本函数的 fail-fast 初衷一致。
        if caps_fn is not None and not callable(caps_fn):
            raise ValueError(f"endpoint {key!r} declares non-callable video_caps_for_model: {caps_fn!r}")
        has_fn = callable(caps_fn)
        if spec.media_type == "video":
            if has_int == has_fn:
                raise ValueError(
                    f"video endpoint {key!r} must declare exactly one of video_max_reference_images "
                    f"(int) or video_caps_for_model (callable), got "
                    f"video_max_reference_images={cap!r}, "
                    f"video_caps_for_model={caps_fn!r}"
                )
            if cap is not None and cap < 0:
                # int cap 是参考图张数硬上限；负数到了下游会被当负切片 references[:-1] 误丢最后一张
                # 而非裁成 0 张 → import 期挡掉，保证 resolver int 分支取到的恒为合法非负数。
                raise ValueError(f"video endpoint {key!r} declares negative video_max_reference_images: {cap}")
        elif has_int or has_fn:
            raise ValueError(
                f"non-video endpoint {key!r} must not declare video caps, got "
                f"video_max_reference_images={cap!r}, "
                f"video_caps_for_model={caps_fn!r}"
            )


_validate_video_caps_declarations()


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
    data.pop("video_caps_for_model", None)  # 同 build_backend：callable 不可 JSON 化，剥掉
    if spec.image_capabilities is not None:
        data["image_capabilities"] = sorted(c.value for c in spec.image_capabilities)
    else:
        data["image_capabilities"] = None
    return data


# ── 启发式：从 model_id + discovery_format 推默认 endpoint ─────────


_IMAGE_PATTERN = re.compile(r"image|dall|img|imagen|flux|seedream|jimeng|viduq[12](?:[-_].*)?", re.IGNORECASE)
_VIDEO_PATTERN = re.compile(
    r"video|sora|kling|wan|seedance|cog|mochi|veo|pika|runway|"
    r"vidu2(?:\.0)?(?:[-_].*)?|viduq3(?:[-_].*)?",
    re.IGNORECASE,
)
# TTS 模型 id 识别（tts-1 / gpt-4o-mini-tts / speech-1.5 / cosyvoice 等）。
# 刻意不含裸 "audio"：gpt-4o-audio-preview 等 chat 音频模态模型会被误归 TTS。
_AUDIO_PATTERN = re.compile(r"tts|speech|cosyvoice", re.IGNORECASE)
# 裸 "speech" 会撞上 ASR（语音转文字）家族 id，按内容排除，避免把识别模型默认归到 TTS 端点
_ASR_PATTERN = re.compile(r"transcribe|speech.?to.?text|recognition", re.IGNORECASE)


def infer_endpoint(model_id: str, discovery_format: str) -> str:
    """根据模型 id 与 discovery_format 推默认 endpoint（content-first）。

    model id 内容优先于 discovery_format：中转站普遍 discovery_format="openai"，但模型
    列表常夹带 gemini-*/imagen-* 原生 id，必须按内容纠偏到 Google 端点，否则被错推到
    openai-chat/openai-images，每次都要手动改回。

    1) 阿里百炼视频 → happyhorse / wan2.x（非 image）走 "dashscope-async-video"（原生异步端点）。
       happyhorse 不在 _VIDEO_PATTERN 须显式；wan2.x 视频抢在通用 is_video 前拦截。图像不自动推
       dashscope（中转可能是 OpenAI 兼容），qwen-image / wan2.x-image 落到既有图像家族推断。
    2) MiniMax 原生 token → 海螺 / S2V 走 "minimax-video"，image-01 走 "minimax-image"。先于通用
       is_video/is_image 拦截：s2v 不在 _VIDEO_PATTERN、image-01 含 "image" 否则会被推到通用图像家族。
    2.5) 可灵 kling token → 含 video 语义优先归 "kling-video"（kling-image2video 等 i2v 含 image
       语义但本质是视频）；其余含 image 语义走 "kling-image"，否则走 "kling-video"。kling 同时命中
       _VIDEO_PATTERN，须先于通用 is_video 拦截，否则视频会落到 openai-video；v3-omni 图像/视频同名
       默认归视频、图像手动选。
    3) imagen → "gemini-image"（图像，不论 discovery_format）
    4) gemini 原生模型（非 video）→ image 形态走 "gemini-image"，否则文本走 "gemini-generate"
    5) 视频家族 → seedance→"ark-seedance"、viduq3→"vidu-video"、否则 "openai-video"
    6) 图像家族 → discovery_format=google 走 "gemini-image" 否则 "openai-images"
    7) TTS 家族（tts/speech/cosyvoice）→ "openai-tts"（audio 仅 OpenAI 兼容一条端点，
       不分 discovery_format；precedence 在 text 默认之前）
    8) 默认（文本）→ discovery_format=google 走 "gemini-generate" 否则 "openai-chat"
    """
    lowered = model_id.lower()
    is_image = bool(_IMAGE_PATTERN.search(model_id))

    # 阿里百炼视频先于通用 is_video 拦截到原生异步端点
    if "happyhorse" in lowered:
        return "dashscope-async-video"
    if "wan2." in lowered and not is_image:
        return "dashscope-async-video"

    # MiniMax 原生 token 二级路由：海螺（含 minimax-hailuo）/ S2V → 两步 file_id 视频端点；
    # image-01 → 单步图像端点。先于通用 is_video/is_image：s2v 不被 _VIDEO_PATTERN 覆盖，
    # image-01 含 "image" 否则会被通用图像家族抢走。
    if "hailuo" in lowered or "s2v" in lowered:
        return "minimax-video"
    if "image-01" in lowered:
        return "minimax-image"

    # 可灵原生中转二级路由：kling 同时命中 _VIDEO_PATTERN（含 kling）与（含 image 语义时）
    # _IMAGE_PATTERN，须在通用 is_video/is_image 之前显式分流。video 语义优先于 image——
    # kling-image2video / kling-img2video 这类 image-to-video 含 image 语义但本质是视频模型，
    # 若直接看 is_image 会被误推到 kling-image，故先拦 video 关键字归 kling-video；其余含 image
    # 语义 → kling-image，否则 → kling-video。kling-v3-omni 图像/视频同名歧义无法纯靠 token 区分，
    # 默认归视频、图像手动选；不分 discovery_format（可灵端点各自唯一）。
    if "kling" in lowered:
        if "video" in lowered:
            return "kling-video"
        return "kling-image" if is_image else "kling-video"

    # wan2.x-image 含 "wan" 会被 _VIDEO_PATTERN 误判为视频；显式排除让它落到图像家族推断
    is_video = bool(_VIDEO_PATTERN.search(model_id)) and not ("wan2." in lowered and is_image)

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
    if _AUDIO_PATTERN.search(model_id) and not _ASR_PATTERN.search(model_id):
        return "openai-tts"
    return "gemini-generate" if discovery_format == "google" else "openai-chat"
