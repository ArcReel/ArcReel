"""ENDPOINT_REGISTRY — 自定义供应商可用 endpoint 单一真相源。

每条 endpoint 是一个 EndpointSpec，绑定 media_type、family、HTTP 调用形态与 build_backend 闭包。
实现形态有两种：Python backend，或随版声明式定义（``builtin_endpoints/*.json``，import 期经
builtin_definitions 装入本注册表，用 EndpointSpec.definition 与前者区分）。两者共用同一键域。
factory.create_custom_backend 通过 endpoint 字符串查表派发；
server.routers.custom_providers 通过 GET /custom-providers/endpoints 把目录暴露给前端，
让前端的下拉选项、路径展示完全派生自此真相源。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlsplit

from lib.audio_backends.openai import OpenAIAudioBackend
from lib.config.url_utils import ensure_google_base_url, ensure_openai_base_url
from lib.custom_provider import is_custom_endpoint
from lib.custom_provider.backends import (
    CustomAudioBackend,
    CustomImageBackend,
    CustomTextBackend,
    CustomVideoBackend,
)
from lib.custom_provider.builtin_definitions import (
    DECLARATIVE_MEDIA_TYPE,
    BuiltinDefinitionError,
    declarative_display_name,
    declarative_family,
    declarative_request_path,
    declarative_video_capabilities,
    load_builtin_definitions,
)
from lib.custom_provider.declarative_backend import DeclarativeVideoBackend, request_urls
from lib.image_backends.base import ImageCapability
from lib.image_backends.dashscope import DashScopeImageBackend
from lib.image_backends.gemini import GeminiImageBackend
from lib.image_backends.kling import KlingImageBackend
from lib.image_backends.minimax import MiniMaxImageBackend
from lib.image_backends.openai import OpenAIImageBackend
from lib.text_backends.gemini import GeminiTextBackend
from lib.text_backends.openai import OpenAITextBackend
from lib.video_backends.ark import ArkVideoBackend
from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities
from lib.video_backends.dashscope import DashScopeVideoBackend, classify_wan_model
from lib.video_backends.kling import KlingVideoBackend
from lib.video_backends.openai import OpenAIVideoBackend
from lib.video_backends.vidu import ViduVideoBackend

if TYPE_CHECKING:
    from lib.db.models.custom_provider import CustomProvider


#: catalog 里用户自定义端点的家族标识。内置端点的 family 指向协议出处（openai / kling …），
#: 用户端点的协议由定义自身描述，没有可归属的外部家族。
CUSTOM_ENDPOINT_FAMILY = "custom"


# ── EndpointSpec 数据类型 ───────────────────────────────────────────


@dataclass(frozen=True)
class EndpointSpec:
    """单条 endpoint 的元数据 + backend 构造闭包。"""

    key: str  # "openai-chat"
    media_type: str  # "text" | "image" | "video" | "audio"
    family: str  # "openai" | "google" | "newapi"
    # 前端 i18n key（dashboard ns）。声明式端点（随版与用户自定义）的显示名写在定义的 meta.name
    # 里、不进 i18n 目录，此键置空串，取名走 display_name。
    display_name_key: str
    request_method: str  # "POST"
    request_path_template: str  # "/v1/chat/completions"，可含 {model} 等占位
    build_backend: Callable[
        [CustomProvider, str],
        CustomTextBackend | CustomImageBackend | CustomVideoBackend | CustomAudioBackend,
    ]
    # 端点来源：内置（随版发布，不可编辑删除）或用户自定义（落 custom_endpoint 表）。
    source: Literal["builtin", "custom"] = "builtin"
    image_capabilities: frozenset[ImageCapability] | None = None  # image 类才填，非 image 类省略
    # 单次参考生视频调用的参考图上限；仅 video 类有意义。
    # 显式 int：原样下传作为硬约束（0 表示不接受参考图，executor 据此将 references 裁剪为 0 张）。
    # None：未声明 —— 一个 endpoint 多 model、容量不同时 endpoint 维度给不出准数，由 resolver
    # 调 video_caps_for_model 按 model_id 读取该 model 的真实上限。
    video_max_reference_images: int | None = None
    # 当 video_max_reference_images 为 None 时，resolver 用此纯函数按 model_id 读 backend 声明的
    # caps —— 不构造 SDK client、不查 provider 行。video_max_reference_images 为 int 时此字段应为
    # None（endpoint 维度已能给出硬上限）。二者对每个 video endpoint 恰填其一（见注册表末尾不变式）。
    video_caps_for_model: Callable[[str], VideoCapabilities] | None = None
    # 该 endpoint 的 delegate.generate() 是否真的读取 VideoGenerationRequest.end_image 并下传
    # 尾帧约束。仅 video 类有意义；False 时即便系统判定或用户覆盖把 last_frame 置为 True，执行层
    # 也会静默丢弃尾帧、按无约束生成——写入侧 last_frame 覆盖据此收窄可开启的 endpoint 范围。
    end_image_capable: bool = False
    # 同构于 end_image_capable：该 endpoint 的 delegate.generate() 是否真的读取
    # VideoGenerationRequest.reference_audio_files 并组装进供应商请求。仅 video 类有意义；
    # False 时把 reference_audio_mode 覆盖为 direct 只会让能力声明失真，执行层照旧不带音色输入。
    reference_audio_capable: bool = False
    # 声明式定义的整份 JSON：随版定义读自 builtin_endpoints/，用户定义读自 custom_endpoint 表。
    # Python 实现的 endpoint 为 None。非 None 即该 endpoint 的调用形态由这份定义描述，上面各字段
    # 都是从它派生出来的镜像，取值一律以本字段为准。
    definition: Mapping[str, Any] | None = None

    @property
    def kind(self) -> str:
        """实现形态：``declarative``（声明式定义）或 ``python``（backend 代码）。"""
        return "declarative" if self.definition is not None else "python"

    @property
    def display_name(self) -> str | None:
        """声明式端点的显示名（取 ``meta.name``）；Python 内置为 None，按 display_name_key 取文案。"""
        return None if self.definition is None else declarative_display_name(self.definition)


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


def _ensure_url_path_suffix(base_url: str | None, suffix: str) -> str | None:
    """补全协议已知挂载路径（ark /api/v3、vidu /ent/v2、kling /v1）；
    已带对应协议路径则原样信任，避免重复叠加。供 ark/vidu/kling 闭包复用。

    纯域名（无 scheme，如 ``relay.example.com``）会被 urlsplit 整体当作 path，
    先补 ``https://`` 再判定，否则 host-only 配置既补不上协议也挂不上路径。
    """
    s = (base_url or "").strip().rstrip("/")
    if not s:
        return None
    normalized = s if "://" in s else f"https://{s}"
    if urlsplit(normalized).path.rstrip("/").endswith(suffix):
        return normalized
    return normalized + suffix


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
    "ark-seedance": EndpointSpec(
        key="ark-seedance",
        media_type="video",
        family="ark",
        display_name_key="endpoint_ark_seedance_display",
        request_method="POST",
        request_path_template="/api/v3/contents/generations/tasks",
        build_backend=_build_ark_seedance,
        video_caps_for_model=ArkVideoBackend.video_capabilities_for_model,
        end_image_capable=True,
        # _create_task 为 reference_audio_files 逐段组装 audio_url + role: reference_audio
        reference_audio_capable=True,
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
        end_image_capable=True,
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
        # _build_media 把 reference_audio_files 逐段挂到参考素材项的 reference_voice 上
        reference_audio_capable=True,
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
        # endpoint 维度声明 int cap，按 model 读 backend 纯 caps 函数。
        video_caps_for_model=KlingVideoBackend.video_capabilities_for_model,
        end_image_capable=True,
    ),
}


def validate_video_caps_declaration(spec: EndpointSpec) -> None:
    """校验单条 spec 的参考图上限来源：caps_fn 若声明必须可调用；video endpoint 必须「int cap」
    XOR「caps_fn 非 None」恰一、且 int cap 非负；非 video endpoint 两者皆 None。misconfig（caps_fn
    填成非 callable、多 model 共享端点漏配 caps_fn、同时声明二者、或声明负数 cap）在构造处
    fail-fast，而非等到 request 期 resolver 才抛。

    内置注册表在 import 期逐条过此校验；声明式端点在 declarative_endpoint_spec 里构造完即过这里
    ——各来源的 spec 走同一条不变式，能力判定层因此不必区分 spec 从哪来。
    """
    key = spec.key
    cap = spec.video_max_reference_images
    caps_fn = spec.video_caps_for_model
    has_int = cap is not None
    # resolver 会以 caps_fn(model_id) 执行它，故必须是 callable。误填字符串/整数等非空非 callable
    # 值要在 import 期就挡掉，而非放行到请求期才在 resolver 里炸——与本函数的 fail-fast 初衷一致。
    if caps_fn is not None and not callable(caps_fn):
        raise ValueError(f"endpoint {key!r} declares non-callable video_caps_for_model: {caps_fn!r}")
    has_fn = callable(caps_fn)
    if spec.media_type != "video" and spec.reference_audio_capable:
        raise ValueError(f"non-video endpoint {key!r} must not declare reference_audio_capable")
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
            # 而非裁成 0 张 → 构造期挡掉，保证 resolver int 分支取到的恒为合法非负数。
            raise ValueError(f"video endpoint {key!r} declares negative video_max_reference_images: {cap}")
    elif has_int or has_fn:
        raise ValueError(
            f"non-video endpoint {key!r} must not declare video caps, got "
            f"video_max_reference_images={cap!r}, "
            f"video_caps_for_model={caps_fn!r}"
        )


#: 只认占位符本身：写死的地址里恰好含 "base_url" 字样（如 https://api.test/base_url/video）
#: 不需要供应商填地址。
_BASE_URL_PLACEHOLDER = re.compile(r"\{\{\s*base_url\s*\}\}")


def declarative_requires_base_url(definition: Mapping[str, Any]) -> bool:
    """这份定义是否要求供应商填了 base_url。

    三节请求 URL 逐个查，不只看提交：提交写死绝对地址、轮询才引用 base_url 的定义是合法的，
    只查提交会让这类端点跑到付费提交成功之后，才在轮询渲染出一个没有协议的相对地址上失败。

    装配闭包与调用方（如测试连接路由的入参校验）共用这一条判定：装配层抛的是不带 i18n key
    的 ValueError，面向用户的那条路要在装配前按本判定给出可翻译的拒绝。
    """
    return any(_BASE_URL_PLACEHOLDER.search(url) for url in request_urls(definition))


def declarative_requires_api_key(definition: Mapping[str, Any]) -> bool:
    """这份定义是否要求调用方提供 api_key。

    校验器保证非空 ``auth`` 至少引用一次 api_key 占位符、且 api_key 只允许出现在 auth 节，
    故判 auth 节是否为空即可。与 :func:`declarative_requires_base_url` 同为凭证必需性的判定缝：
    两者皆否（auth 为空且三节 URL 全写死绝对地址）的定义不需要任何凭证即可调用。
    """
    return bool(definition.get("auth"))


def _build_declarative_video(
    definition: Mapping[str, Any],
) -> Callable[[CustomProvider, str], CustomVideoBackend]:
    """声明式端点的 backend 构造闭包。"""

    def build(provider: CustomProvider, model_id: str) -> CustomVideoBackend:
        if declarative_requires_base_url(definition) and not provider.base_url:
            raise ValueError("声明式调用端点需要 base_url")
        delegate = DeclarativeVideoBackend(
            api_key=provider.api_key,
            base_url=provider.base_url,
            model=model_id,
            definition=definition,
            provider=provider.provider_id,
        )
        return CustomVideoBackend(provider_id=provider.provider_id, delegate=delegate, model=model_id)

    return build


def declarative_endpoint_spec(
    key: str,
    definition: Mapping[str, Any],
    *,
    source: Literal["builtin", "custom"] = "builtin",
) -> EndpointSpec:
    """把一份声明式定义派生成 EndpointSpec。纯函数：不读库、不发请求。

    随版定义（``builtin_endpoints/*.json``）与用户定义（``custom_endpoint`` 表）走同一条投影：
    定义的表达力一样，两份实现只会在能力缺省这类地方悄悄分叉。差别只在 ``source`` 决定的家族归属
    ——随版端点的家族取键首段（协议出处），用户端点的协议由定义自身描述、没有可归属的外部家族。

    能力由定义显式全量声明，与 model 无关，故走 video_caps_for_model 这条「四字段全量声明」的
    通路（返回同一份常量），而不是只能表达参考图上限的 video_max_reference_images。
    """
    caps = declarative_video_capabilities(definition)
    spec = EndpointSpec(
        key=key,
        media_type=DECLARATIVE_MEDIA_TYPE,
        family=CUSTOM_ENDPOINT_FAMILY if source == "custom" else declarative_family(key),
        # 声明式端点的显示名取 meta.name，不进 i18n 目录（见 EndpointSpec.display_name）。
        display_name_key="",
        source=source,
        request_method=definition["submit"]["method"],
        request_path_template=declarative_request_path(definition),
        build_backend=_build_declarative_video(definition),
        video_caps_for_model=lambda _model_id: caps,
        end_image_capable=caps.last_frame,
        reference_audio_capable=caps.reference_audio_mode is not ReferenceAudioMode.NONE,
        definition=definition,
    )
    # 内置注册表在 import 期逐条过同一条不变式（见 _validate_registry）；用户定义现构造、没有
    # import 期可依托，故在构造处就过——两种来源的 spec 因此不必被能力判定层区分对待。
    validate_video_caps_declaration(spec)
    return spec


def merge_builtin_definitions(registry: dict[str, EndpointSpec], directory: Path | None = None) -> None:
    """把随版声明式定义并入注册表。键与 Python 内置同一命名空间，重复即拒。

    定义不合法、目录缺失、键冲突在 import 期一律抛错——装载失败让进程起不来，好过带着一个装不出
    backend 的 endpoint 跑到用户发起生成那一刻。
    """
    for key, definition in load_builtin_definitions(directory).items():
        if key in registry:
            raise BuiltinDefinitionError(f"随版定义 {key}.json 的内置键与已有 endpoint 重复")
        registry[key] = declarative_endpoint_spec(key, definition)


merge_builtin_definitions(ENDPOINT_REGISTRY)


ENDPOINT_KEYS_BY_MEDIA_TYPE: dict[str, tuple[str, ...]] = {
    media_type: tuple(k for k, s in ENDPOINT_REGISTRY.items() if s.media_type == media_type)
    for media_type in {s.media_type for s in ENDPOINT_REGISTRY.values()}
}


def _validate_registry() -> None:
    """import 期校验内置注册表的不变式。"""
    for key, spec in ENDPOINT_REGISTRY.items():
        # ce- 是自定义调用端点的键前缀，由 DB 自增 id 分配。内置键占用该前缀会让 resolve 的
        # 前缀分流失去唯一性——两个命名空间同域但永不重叠，正是靠这条不变式。
        if is_custom_endpoint(key):
            raise ValueError(f"builtin endpoint key {key!r} must not use the custom endpoint prefix")
        validate_video_caps_declaration(spec)


_validate_registry()


# ── 工具函数 ───────────────────────────────────────────────────────


def get_endpoint_spec(endpoint: str) -> EndpointSpec:
    spec = ENDPOINT_REGISTRY.get(endpoint)
    if spec is None:
        raise ValueError(f"unknown endpoint: {endpoint!r}")
    return spec


def endpoint_to_media_type(endpoint: str) -> str:
    return get_endpoint_spec(endpoint).media_type


def static_media_type(endpoint: str) -> str:
    """不读库判定端点媒体类型：自定义端点的键前缀已蕴含 video，其余走内置查表。

    模型行的 endpoint 列既可能是内置键，也可能是 ``ce-`` 键，凡是按端点分媒体类型的地方
    都要走这里；只查内置注册表会让带自定义端点的供应商整个判失败。

    Raises:
        ValueError: 既非自定义端点键，内置注册表里也没有该键。
    """
    # 延迟导入：builtin_definitions 消费本模块的注册表，模块级导入会成环。
    from lib.custom_provider import is_custom_endpoint
    from lib.custom_provider.builtin_definitions import DECLARATIVE_MEDIA_TYPE

    if is_custom_endpoint(endpoint):
        return DECLARATIVE_MEDIA_TYPE
    return endpoint_to_media_type(endpoint)


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
    # catalog 不内嵌定义：整份 JSON 由 GET /custom-providers/endpoints/{key}/definition 单独取。
    data.pop("definition", None)
    data["kind"] = spec.kind
    data["display_name"] = spec.display_name
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

#: MiniMax 输入受限端点的精确型号名（小写；剥离命名空间前缀后比较）。Fast 只接受图生视频、
#: S2V 必须带参考图——把一个别名建到这两键等于给它加上无从核实的输入要求，故只认表列的
#: 官方型号 id，与 alembic ``8c2b1e7d4a90`` 和 backend_assembly.specs._minimax_video_endpoint
#: 同源（三处各存一份字面量，改其一须同改另两处）。S2V 另收 "minimax-s2v-01"：聚合商把厂商名
#: 前缀直接粘进 id 的常见别名形态，语义仍是那一个精确型号。
_MINIMAX_FAST_MODELS = frozenset({"minimax-hailuo-2.3-fast"})
_MINIMAX_S2V_MODELS = frozenset({"s2v-01", "minimax-s2v-01"})


def infer_endpoint(model_id: str, discovery_format: str) -> str:
    """根据模型 id 与 discovery_format 推默认 endpoint（content-first）。

    model id 内容优先于 discovery_format：中转站普遍 discovery_format="openai"，但模型
    列表常夹带 gemini-*/imagen-* 原生 id，必须按内容纠偏到 Google 端点，否则被错推到
    openai-chat/openai-images，每次都要手动改回。

    1) 阿里百炼视频 → happyhorse / wan2.7 / wan3 家族（含 wan-2.7-xxx / wan-3-xxx 连字符形态、
       image-to-video 续接别名）走 "dashscope-async-video"（原生异步端点）。happyhorse 不在
       _VIDEO_PATTERN 须显式；万相视频抢在通用 is_video 前拦截。真正的图像变体不自动推 dashscope
       （中转可能是 OpenAI 兼容）：qwen-image / wan2.7-image / wan-2.7-image / wan3.0-video-image
       及带版本/日期后缀的同类 id 落到图像家族推断；wan-3-turbo-image-to-video /
       wan3-image2video 这类显式 image-to-video 续接语法仍归视频（同 2.5 节 kling-image2video
       的处理原则），按 classify_wan_model 的 is_image_to_video 精确挑出这一种形态，不对图像变体
       的命名形态（结尾 token 等）做任何假设。原生路由只认 2.7（含点号形态 "wan2.x"，见下方
       classify_wan_model 的说明）与 wan3；其余 2.x 连字符/下划线形态（wan-2.1、wan_2.2-s2v 等）
       落到下方 5) 的通用视频端点。2.7 家族内 videoedit 模态（wan2.7-videoedit）本后端未实现
       请求构造，同样排除出原生路由（见 classify_wan_model 的 is_videoedit 处的说明）。
    2) MiniMax 原生 token → 海螺（Fast 与非 Fast 各一键）/ S2V / H3 分别走各自声明式端点，
       image-01 走 "minimax-image"。先于通用
       is_video/is_image 拦截：s2v 不在 _VIDEO_PATTERN、image-01 含 "image" 否则会被推到通用图像家族。
       输入受限的 Fast / S2V 只认精确型号名（剥离中转命名空间前缀后比较），非精确海螺别名落
       通用海螺键，其余 s2v 形态落 5) 的通用视频端点。
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
    # 走百炼原生端点的万相/happyhorse 家族 id（视频与图像变体都命中），下面路由与 is_video 排除
    # 各用一次。家族归属、分隔符归一化、标识符边界、image-to-video 续接语法、videoedit 模态排除
    # 全部只在 classify_wan_model（lib.video_backends.dashscope）里判定一次，本函数与
    # DashScopeVideoBackend._profile_for_model、duration_presets.infer_supported_durations 三处
    # 只消费其结论，不再各自对 model_id 做正则匹配——避免三处宽度各自漂移，出现"路由到本后端却拿
    # 不到对应能力档"一类互斥组合。
    classification = classify_wan_model(model_id)
    is_wan_family = classification.family is not None and classification.family != "happyhorse"
    # wan 家族的 image-to-video 别名（如 wan-3-turbo-image-to-video / wan3-image2video）含 "image"
    # 子串但本质是视频模型，与下方 kling-image2video 同类陷阱：笼统 is_image 会把它们错判成图像
    # 变体。反过来"以 image 结尾才算图像变体"同样错——wan3.0-image-edit / wan-3-turbo-image-preview /
    # 带日期后缀的 wan3.0-video-image-20260801 这类真图像别名不以 "image" 结尾，会被误判成视频。
    # 故只把 image-to-video 续接语法（"image" 后紧跟 "to"/"2" 再接 "video"）当例外挑出来，其余
    # 含 image 语义一律按图像变体处理，不对图像变体的命名形态做任何假设。
    #
    # 该排除不能拿 is_wan_family（严格标识符边界）做门槛：_VIDEO_PATTERN 的 "wan" 分支本身无边界，
    # "swan2.7-image" / "vendorwan2.7-image" 这类不满足家族边界的 id 依然会命中 _VIDEO_PATTERN，
    # 若排除逻辑要求先通过严格家族判定，这类 id 就会被 is_video 误判为视频而不是落到图像家族推断。
    # 因此排除只看"是否含 wan 子串"（与 _VIDEO_PATTERN 的宽度一致），不要求满足家族标识符边界；
    # 家族边界只用于下面原生路由（dashscope-async-video）的资格判定。
    contains_wan_token = "wan" in lowered
    # wan2.7 家族已确认属于视频模态（t2v/i2v/r2v/s2v/v2v/videoedit 任一，即便部分未实现请求构造，
    # 见 classify_wan_model 的 is_known_video_modality 处的说明）时，该 profile 本身已确立视频
    # 语义——即便 id 别处（如代理命名空间前缀 "image-proxy/wan-2.7-s2v"）另含无关 "image" 子串，
    # 也不应被判成图像变体。未落入任一已知模态 token 的其余命名（如 "wan2.7-image"）保守按图像
    # 变体处理。不对 wan3 套用同一判定：wan3 只有单一 profile key，不区分 t2v/i2v/r2v/s2v 与
    # image-edit 等真图像别名，套用同一判定会反过来误伤 wan3.0-image-edit 一类真图像别名（见上方
    # 注释）。
    #
    # 该判定同样不能拿 is_wan_family 做门槛，理由与上面 wan_image_variant 的排除范围一致：
    # "wan-2.2-image-to-video" 一类不满足家族严格边界（连字符隔开 wan 与版本号）的 id，
    # classify_wan_model 仍会按标识符边界识别出其 image-to-video 续接语法（is_image_to_video），
    # 若在这里再要求先通过家族判定，这类显式 i2v 命名会被误判成图像变体。
    wan_video_continuation = contains_wan_token and (
        classification.is_image_to_video
        or (classification.family == "wan2.7" and classification.is_known_video_modality)
    )
    wan_image_variant = contains_wan_token and is_image and not wan_video_continuation
    # 未实现请求构造的模态（wan2.7-videoedit / wan2.7-s2v / wan2.7-v2v 等）即便命中家族正则也不
    # 走原生端点（见 classify_wan_model 的 has_known_modality 处的说明），落到下方 5) 的通用视频
    # 端点，避免本后端收到无法正确构造的请求。
    wan_unsupported_modality = is_wan_family and not classification.has_known_modality

    # 阿里百炼视频先于通用 is_video 拦截到原生异步端点
    if classification.family == "happyhorse":
        return "dashscope-async-video"
    if is_wan_family and not wan_image_variant and not wan_unsupported_modality:
        return "dashscope-async-video"

    # MiniMax 原生 token 二级路由：海螺（含 minimax-hailuo）/ S2V / H3 → 两步或单步取回的视频端点；
    # image-01 → 单步图像端点。先于通用 is_video/is_image：s2v 与 h3 均不被 _VIDEO_PATTERN 覆盖，
    # image-01 含 "image" 否则会被通用图像家族抢走。匹配 "minimax-h3" 而非裸 "h3"——后者过短，
    # 容易撞上其它厂商恰好含 h3 子串的型号 id。
    #
    # 输入受限的 Fast 与 S2V 只认精确型号名（见 _MINIMAX_FAST_MODELS / _MINIMAX_S2V_MODELS）：
    # "MiniMax-Hailuo-2.3-Fast" 与 "MiniMax-Hailuo-2.3" 前缀碰撞，宽松子串会把非 Fast 型号或
    # 无从核实上游的中转别名建到首帧必需的 Fast 定义。比较前剥离中转命名空间前缀——取末段，
    # 多层命名空间（"openrouter/minimax/MiniMax-Hailuo-2.3-Fast"）一并剥掉：命名空间只是
    # 转售包装，承担判定的是末段必须逐字等于官方型号 id。非精确的海螺别名落通用海螺键
    # （无输入要求），其余 s2v 形态（如
    # "wan2.7-s2v"）不再被误吞成 MiniMax S2V 协议，落下方 5) 的通用视频端点。
    canonical = re.split(r"[/:]", lowered)[-1]
    if "hailuo" in lowered:
        # Fast 只接受图生视频，端点级能力据此分居两键（见 backend_assembly.specs
        # ._minimax_video_endpoint 的同源口径），不能与 2.3 共用一个定义。
        return "minimax-hailuo-v1-fast" if canonical in _MINIMAX_FAST_MODELS else "minimax-hailuo-v1"
    if canonical in _MINIMAX_S2V_MODELS:
        return "minimax-s2v-01"
    if "minimax-h3" in lowered:
        return "minimax-h3"
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

    # wan2.x-image / wan3.0-video-image 含 "wan" 会被 _VIDEO_PATTERN 误判为视频；显式排除让它落到
    # 图像家族推断。复用上面的 wan_image_variant（排除 image-to-video 续接别名后的图像判定）。
    is_video = bool(_VIDEO_PATTERN.search(model_id)) and not wan_image_variant

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
