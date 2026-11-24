"""内置 provider 的 (provider_id, media_type) → ProviderSpec 声明式表。

镜像自定义侧 ENDPOINT_REGISTRY（lib/custom_provider/endpoints.py）：每条 spec 是 frozen
dataclass，挂一个 build 闭包；闭包读 LoadedConfig 信封 + model_id 拼 backend，不查 DB、不 await。
登记简单族（媒体侧只需 api_key + model + base_url 的内置 provider）的 image/video/audio，
共享一个 _build_simple 闭包；外加 gemini（backend_type 双模式，image/video/audio 对等透传 base_url）与
kling（JWT 双 secret + api_model_name 解耦）两个特例族，各自挂专属 build 闭包。文本（media_type=text）
四条 media_type 同经本表：简单文本（ark/ark-agent-plan/grok/agnes，agnes 走 OpenAI 兼容
chat/completions 但鉴权 / base_url 收口在专属 AgnesTextBackend，故归简单族而非 OpenAI-compat 别名）、gemini 文本（aistudio/vertex）、
OpenAI-compat 文本（openai/dashscope/minimax，dashscope/minimax 经 helper 派生 base_url + 透传
provider_name 计费归因）各挂专属闭包；文本侧别名映射（dashscope/minimax → openai registry backend）
并入 spec 的 registry_backend 字段，根除原文本工厂第二份 PROVIDER_ID_TO_BACKEND。表在 import 期校验
不变式（registry 名已注册除外，见模块末尾说明），misconfig fail-fast。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Any

from lib.backends.kling_shared import kling_auth_mode
from lib.backends.providers import PROVIDER_KLING
from lib.catalog import query as catalog

if TYPE_CHECKING:
    from arcreel_market_core.video_backend_contract import (
        VideoCapabilities,
        VideoGenerationRequest,
        VideoGenerationResult,
    )
    from lib.backends.backend_assembly.loaded_config import LoadedConfig
    from lib.billing.metering import Usage


@dataclass(frozen=True)
class ProviderSpec:
    """内置供应商经一个内置调用端点调用时的 backend 构造规格：供应商给凭证，端点决定请求。"""

    provider_id: str
    media_type: str
    registry_backend: str
    build_backend: Callable[[LoadedConfig, str | None], Any]
    endpoint: str


@dataclass(frozen=True)
class ProviderEndpoints:
    """内置供应商在一个媒体类型下可挂接的内置调用端点（按声明顺序）与默认端点。

    默认端点是官方收录的模型没有另行指定时挂接的端点，也是用户添加模型的推断落空时的预填值。
    """

    provider_id: str
    media_type: str
    endpoints: tuple[str, ...]
    default: str


def _media_create_backend(media_type: str) -> Callable[..., Any]:
    """按 media_type 取对应 registry 的 create_backend（运行时取，便于测试 patch 模块属性）。

    text 与 image/video/audio 四条 media_type 共用同一构造缝，文本 backend 注册在
    独立的 lib.backends.text_backends.registry，故在此分流。
    """
    if media_type == "image":
        from lib.backends.image_backends.registry import create_backend
    elif media_type == "video":
        from lib.backends.video_backends.registry import create_backend
    elif media_type == "audio":
        from lib.backends.audio_backends.registry import create_backend
    elif media_type == "text":
        from lib.backends.text_backends.registry import create_backend
    else:
        raise ValueError(f"unknown media_type: {media_type!r}")
    return create_backend


def _resolve_base_url(config: LoadedConfig) -> str | None:
    """base_url 优先级：用户在 db_config 显式填写 > ProviderMeta.default_base_url > None。

    简单族与 kling 共用此兜底语义；调用方各自决定 None 时是否省略 base_url 参数。
    """
    default = config.provider_meta.default_base_url if config.provider_meta else None
    return config.credentials.get("base_url") or default


def _build_simple(
    config: LoadedConfig,
    model_id: str | None,
    *,
    media_type: str,
    registry_backend: str,
    extra_keys: tuple[str, ...] = (),
    extra_kwargs: dict[str, Any] | None = None,
) -> Any:
    """简单族通用构造：api_key + model + base_url，外加 extra_keys 声明的 db_config 直传键。

    api_key 与 base_url 同遵「仅非空才写入 kwargs」：显式传 None 可能覆盖底层 SDK 的环境变量兜底
    （如 OpenAI SDK 读 OPENAI_API_KEY），缺省由 backend 各自处理（要么读环境变量、要么 fail-loud）。
    base_url 优先级见 _resolve_base_url —— grok 等无 default 且用户未配的 provider 不接受 base_url
    参数，传 None 会触发 TypeError，故仅非空才写入。extra_keys 同规则：键名即 backend 构造参数名，
    供个别 provider 的专属 endpoint 配置（如 dashscope 的 wan3_base_url）复用同一条构造缝。
    """
    kwargs: dict[str, Any] = {"model": model_id}
    api_key = config.credentials.get("api_key")
    if api_key:
        kwargs["api_key"] = api_key
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    for key in extra_keys:
        value = config.credentials.get(key)
        if value:
            kwargs[key] = value
    kwargs.update(extra_kwargs or {})
    return _media_create_backend(media_type)(registry_backend, **kwargs)


def _build_ark_video(config: LoadedConfig, model_id: str | None, *, endpoint: str) -> Any:
    """方舟视频：简单族构造之外报出实际使用的推理凭证，供素材库按绑定选用供应商素材。

    凭证引用只在 api_key 来自一条凭证记录时给出：素材库只能绑定凭证记录，按供应商配置直填的密钥没有可绑定的对象。
    参考图槽位是否接受素材引用由端点声明（``SeedanceEndpoint.reference_image_asset``），与自定义供应商同一份。
    """
    from lib.backends.provider_assets import InferenceCredentialRef

    api_key = config.credentials.get("api_key")
    extra: dict[str, Any] = {}
    if config.credential_id is not None and api_key:
        extra["inference_credential"] = InferenceCredentialRef(
            credential_type="builtin",
            credential_id=config.credential_id,
            api_key=api_key,
            base_url=config.credentials.get("base_url"),
            default_base_url=config.provider_meta.default_base_url if config.provider_meta else None,
        )
    return _build_simple(config, model_id, media_type="video", registry_backend=endpoint, extra_kwargs=extra)


def _build_ark_agent_plan_tts(config: LoadedConfig, model_id: str | None) -> Any:
    """方舟 Agent Plan 语音合成：只取 api_key 与 model。

    语音合成的上游主机与 ``/api/plan/v3`` 不同，地址是后端常量；简单族会把这个默认地址当作 base_url 传入，所以单列构造。
    """
    kwargs: dict[str, Any] = {"model": model_id}
    api_key = config.credentials.get("api_key")
    if api_key:
        kwargs["api_key"] = api_key
    return _media_create_backend("audio")("ark-agent-plan", **kwargs)


def _simple_spec(provider_id: str, media_type: str, endpoint: str, *, extra_keys: tuple[str, ...] = ()) -> ProviderSpec:
    """登记一条简单族 spec：registry_backend 即 provider_id 自身（媒体侧无别名映射）。"""
    return ProviderSpec(
        provider_id=provider_id,
        media_type=media_type,
        registry_backend=provider_id,
        build_backend=partial(
            _build_simple, media_type=media_type, registry_backend=provider_id, extra_keys=extra_keys
        ),
        endpoint=endpoint,
    )


_SEEDANCE_ENDPOINTS = ("ark-seedance-1", "ark-seedance-2", "ark-seedance-2-5")


def seedance_endpoint_for_name(name: str) -> str | None:
    """名字里写明的 Seedance 代际对应的端点；名字不带 2.0 / 2.5 时为 ``None``。

    用 ``in`` 子串兼容多套前缀命名（doubao- 火山国内站 / dreamina- BytePlus 国际站），版本号收窄到已登记的代际：同代的
    未知后缀（fast、mini、日期快照）落同一端点。只用于添加模型时的预填
    （``lib.custom_provider.endpoints.infer_endpoint``）。
    alembic ``e5b1c9a3d7f2`` 冻结了一份同样的规则回填已有模型。
    """
    lowered = name.lower()
    if "seedance-2-5" in lowered or "seedance-2.5" in lowered:
        return "ark-seedance-2-5"
    if "seedance-2-0" in lowered or "seedance-2.0" in lowered:
        return "ark-seedance-2"
    return None


_VIDU_PROFILES = {
    "viduq3-pro": "q3-pro",
    "viduq3-turbo": "q3-turbo",
    "viduq3-pro-fast": "q3-fast",
    "viduq3": "q3-reference",
    "viduq3-drama": "q3-drama",
    "viduq3-mix": "q3-mix",
    "viduq3-ad": "q3-ad",
    "viduq2": "q2",
    "viduq2-pro": "q2-pro",
    "viduq2-pro-fast": "q2-fast",
    "viduq2-turbo": "q2-fast",
    "viduq1": "q1",
    "viduq1-classic": "q1-classic",
    "vidu2.0": "2",
    "viduq4-preview": "q4",
}
_KLING_PROFILES = {
    "kling-v2-5-turbo": "pro-silent",
    "kling-v2-6": "pro-audio",
    "kling-v3": "audio",
    "kling-v3-omni": "audio",
    "kling-video-o1": "silent",
    "kling-4.0": "model-path",
    "kling-4.0-flash": "model-path",
    "kling-3.0-turbo": "model-path",
}
_GROK_ENDPOINTS = ("grok-video-classic", "grok-video-mixed")
_VIDU_ENDPOINTS = (
    "vidu-video-generic",
    "vidu-video-q3-pro",
    "vidu-video-q3-turbo",
    "vidu-video-q3-fast",
    "vidu-video-q3-reference",
    "vidu-video-q3-drama",
    "vidu-video-q3-mix",
    "vidu-video-q3-ad",
    "vidu-video-q2",
    "vidu-video-q2-pro",
    "vidu-video-q2-fast",
    "vidu-video-q1",
    "vidu-video-q1-classic",
    "vidu-video-2",
    "vidu-video-q4",
)
_KLING_ENDPOINTS = (
    "kling-video-pro-silent",
    "kling-video-pro-audio",
    "kling-video-audio",
    "kling-video-silent",
    "kling-video-model-path",
)


def request_profile_endpoint_for_name(provider_id: str, name: str) -> str | None:
    """按名字识别 Grok、Vidu、可灵视频的请求端点，只用于添加时预填；迁移冻结了同一规则。"""
    if provider_id == "grok":
        return "grok-video-classic" if name == "grok-imagine-video" else None
    if provider_id == "vidu":
        profile = _VIDU_PROFILES.get(name)
        return f"vidu-video-{profile}" if profile else None
    if provider_id == "kling":
        profile = _KLING_PROFILES.get(name.replace(":", "/").rsplit("/", 1)[-1].strip().lower())
        return f"kling-video-{profile}" if profile else None
    return None


def builtin_declarative_video_diagnostics(
    provider_id: str, model_id: str, config: Mapping[str, str]
) -> tuple[Mapping[str, Any], str, str] | None:
    """内置模型行走声明式装配时，测试连接诊断用的 ``(definition, base_url, api_key)``。

    该 (provider, model) 由 Python backend 实现时返回 None。端点判定（:func:`builtin_model_spec`）与凭证回落与
    :func:`_build_minimax_declarative_video` 同一份——诊断里渲出的请求必须与真发的一致。
    调用方须先按各自的凭证闸确认 ``api_key`` 非空（空值在此处会 fail-loud）。
    """
    if provider_id != "minimax":
        return None
    from lib.backends.minimax_shared import MINIMAX_BASE_URL, resolve_minimax_api_key

    definition = _declarative_definition(builtin_model_spec(provider_id, "video", model_id))
    if definition is None:
        return None
    return definition, config.get("base_url") or MINIMAX_BASE_URL, resolve_minimax_api_key(config.get("api_key"))


def _declarative_definition(spec: ProviderSpec) -> Mapping[str, Any] | None:
    """经随版声明式定义调用的端点的定义；Python backend 实现的端点为 ``None``。"""
    if spec.registry_backend != "declarative":
        return None
    from lib.custom_provider.endpoints import get_endpoint_spec

    return get_endpoint_spec(spec.endpoint).definition


def _build_minimax_declarative_video(config: LoadedConfig, model_id: str | None, *, endpoint: str) -> Any:
    from lib.backends.minimax_shared import MINIMAX_BASE_URL, resolve_minimax_api_key
    from lib.custom_provider.declarative_backend import DeclarativeVideoBackend
    from lib.custom_provider.endpoints import get_endpoint_spec

    model = model_id or "MiniMax-H3"
    definition = get_endpoint_spec(endpoint).definition
    if definition is None:
        raise ValueError(f"builtin endpoint {endpoint!r} is not declarative")
    return DeclarativeVideoBackend(
        api_key=resolve_minimax_api_key(config.credentials.get("api_key")),
        base_url=_resolve_base_url(config) or MINIMAX_BASE_URL,
        model=model,
        definition=definition,
        provider="minimax",
    )


_GEMINI_REGISTRY_BACKEND = "gemini"


def _build_gemini_image(config: LoadedConfig, model_id: str | None, *, backend_type: str) -> Any:
    return _media_create_backend("image")(
        _GEMINI_REGISTRY_BACKEND,
        backend_type=backend_type,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
        rate_limiter=config.rate_limiter,
        image_model=model_id,
        **({"credentials_path": config.credentials.get("credentials_path")} if backend_type == "vertex" else {}),
    )


def _build_gemini_video(config: LoadedConfig, model_id: str | None, *, backend_type: str, registry_backend: str) -> Any:
    return _media_create_backend("video")(
        registry_backend,
        backend_type=backend_type,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
        rate_limiter=config.rate_limiter,
        video_model=model_id,
        **({"credentials_path": config.credentials.get("credentials_path")} if backend_type == "vertex" else {}),
    )


def _build_gemini_audio(config: LoadedConfig, model_id: str | None, *, backend_type: str) -> Any:
    return _media_create_backend("audio")(
        _GEMINI_REGISTRY_BACKEND,
        backend_type=backend_type,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
        model=model_id,
        **({"credentials_path": config.credentials.get("credentials_path")} if backend_type == "vertex" else {}),
    )


_GEMINI_BUILDS = {"image": _build_gemini_image, "audio": _build_gemini_audio}


_GEMINI_ENDPOINTS = {"image": "gemini-image", "audio": "gemini-tts"}


_GEMINI_VIDEO_ENDPOINTS: Mapping[str, Mapping[str, str]] = {
    "veo": {"aistudio": "gemini-video", "vertex": "gemini-vertex-video"},
    "interactions": {"aistudio": "gemini-interactions-video", "vertex": "gemini-vertex-interactions-video"},
}


def _gemini_spec(provider_id: str, media_type: str, *, backend_type: str) -> ProviderSpec:
    build = _GEMINI_BUILDS[media_type]
    return ProviderSpec(
        provider_id=provider_id,
        media_type=media_type,
        registry_backend=_GEMINI_REGISTRY_BACKEND,
        build_backend=partial(build, backend_type=backend_type),
        endpoint=_GEMINI_ENDPOINTS[media_type],
    )


def _gemini_video_spec(provider_id: str, endpoint: str, *, backend_type: str) -> ProviderSpec:
    registry_backend = _GEMINI_REGISTRY_BACKEND if endpoint == "gemini-video" else endpoint
    return ProviderSpec(
        provider_id=provider_id,
        media_type="video",
        registry_backend=registry_backend,
        build_backend=partial(_build_gemini_video, backend_type=backend_type, registry_backend=registry_backend),
        endpoint=endpoint,
    )


_KLING_REGISTRY_BACKEND = "kling"


def _build_kling(config: LoadedConfig, model_id: str | None, *, media_type: str, endpoint: str | None = None) -> Any:
    """kling 通用构造：按凭证形态分派 auth_mode（api_key 优先于双 secret）；image 侧叠加 api_model_name 注入。"""
    if kling_auth_mode(config.credentials) == "bearer":
        kwargs: dict[str, Any] = {
            "auth_mode": "bearer",
            "api_key": config.credentials.get("api_key"),
            "model": model_id,
        }
    else:
        kwargs = {
            "auth_mode": "jwt",
            "access_key": config.credentials.get("access_key"),
            "secret_key": config.credentials.get("secret_key"),
            "model": model_id,
        }
    if media_type == "image":
        model_info = catalog.get_model(PROVIDER_KLING, model_id) if model_id else None
        if model_info is not None and model_info.api_model_name:
            kwargs["api_model_name"] = model_info.api_model_name
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    return _media_create_backend(media_type)(endpoint or _KLING_REGISTRY_BACKEND, **kwargs)


def _kling_spec(media_type: str, *, endpoint: str | None = None) -> ProviderSpec:
    return ProviderSpec(
        provider_id=_KLING_REGISTRY_BACKEND,
        media_type=media_type,
        registry_backend=endpoint or _KLING_REGISTRY_BACKEND,
        build_backend=partial(_build_kling, media_type=media_type, endpoint=endpoint),
        endpoint=endpoint or f"kling-{media_type}",
    )


def _build_text_simple(config: LoadedConfig, model_id: str | None, *, registry_backend: str) -> Any:
    """简单文本族：model + api_key（无条件透传）+ base_url（user > registry default，仅非空才传）。

    api_key 无条件透传（含 None）保留迁移前命令式分支语义（db_config.get("api_key") 直写 kwargs）；
    base_url 仅非空才写入，对称媒体简单族 —— ark/ark-agent-plan 有 registry default 回落，grok 无 default
    且用户未配时省略该参数。
    """
    kwargs: dict[str, Any] = {"model": model_id, "api_key": config.credentials.get("api_key")}
    base_url = _resolve_base_url(config)
    if base_url:
        kwargs["base_url"] = base_url
    return _media_create_backend("text")(registry_backend, **kwargs)


def _text_simple_spec(provider_id: str, endpoint: str) -> ProviderSpec:
    """登记一条简单文本 spec：registry_backend 即 provider_id 自身（文本侧无别名映射）。"""
    return ProviderSpec(
        provider_id=provider_id,
        media_type="text",
        registry_backend=provider_id,
        build_backend=partial(_build_text_simple, registry_backend=provider_id),
        endpoint=endpoint,
    )


_TEXT_GEMINI_REGISTRY_BACKEND = "gemini"


def _build_text_gemini_aistudio(config: LoadedConfig, model_id: str | None) -> Any:
    return _media_create_backend("text")(
        _TEXT_GEMINI_REGISTRY_BACKEND,
        model=model_id,
        api_key=config.credentials.get("api_key"),
        base_url=config.credentials.get("base_url"),
    )


def _build_text_gemini_vertex(config: LoadedConfig, model_id: str | None) -> Any:
    return _media_create_backend("text")(
        _TEXT_GEMINI_REGISTRY_BACKEND,
        model=model_id,
        backend="vertex",
        gcs_bucket=config.credentials.get("gcs_bucket"),
        credentials_path=config.credentials.get("credentials_path"),
    )


def _text_gemini_spec(provider_id: str, *, build: Callable[[LoadedConfig, str | None], Any]) -> ProviderSpec:
    return ProviderSpec(
        provider_id=provider_id,
        media_type="text",
        registry_backend=_TEXT_GEMINI_REGISTRY_BACKEND,
        build_backend=build,
        endpoint="gemini-generate",
    )


_TEXT_OPENAI_COMPAT_REGISTRY_BACKEND = "openai"
_TEXT_OPENAI_RESPONSES_REGISTRY_BACKEND = "openai-responses"


def _dashscope_text_base_url(configured: str | None) -> str:
    from lib.backends.dashscope_shared import dashscope_text_base_url

    return dashscope_text_base_url(configured)


def _minimax_text_base_url(configured: str | None) -> str:
    from lib.backends.minimax_shared import minimax_text_base_url

    return minimax_text_base_url(configured)


def _build_text_openai_compat(
    config: LoadedConfig,
    model_id: str | None,
    *,
    derive_base_url: Callable[[str | None], str] | None,
    provider_name: str | None,
    registry_backend: str = _TEXT_OPENAI_COMPAT_REGISTRY_BACKEND,
) -> Any:
    """OpenAI 协议文本构造：dashscope / minimax 走 Chat Completions（OpenAITextBackend），
    openai 走 Responses（OpenAIResponsesTextBackend），二者构造参数同形，由 registry_backend 区分。

    derive_base_url=None（openai）：直传用户 base_url（无条件，含 None，由 backend 决定 endpoint）。
    derive_base_url 非空（dashscope/minimax）：由 helper 从用户 host 派生 OpenAI 兼容 base，并透传
    provider_name 给 backend，确保 usage 记账与计费查表命中自身 CNY 费率而非 OpenAI USD。
    """
    user_base_url = config.credentials.get("base_url")
    kwargs: dict[str, Any] = {
        "model": model_id,
        "api_key": config.credentials.get("api_key"),
        "base_url": derive_base_url(user_base_url) if derive_base_url else user_base_url,
    }
    if provider_name:
        kwargs["provider_name"] = provider_name
    return _media_create_backend("text")(registry_backend, **kwargs)


def _text_openai_compat_spec(
    provider_id: str,
    *,
    derive_base_url: Callable[[str | None], str] | None,
    provider_name: str | None,
    registry_backend: str = _TEXT_OPENAI_COMPAT_REGISTRY_BACKEND,
) -> ProviderSpec:
    return ProviderSpec(
        provider_id=provider_id,
        media_type="text",
        registry_backend=registry_backend,
        build_backend=partial(
            _build_text_openai_compat,
            derive_base_url=derive_base_url,
            provider_name=provider_name,
            registry_backend=registry_backend,
        ),
        endpoint="openai-responses" if registry_backend == _TEXT_OPENAI_RESPONSES_REGISTRY_BACKEND else "openai-chat",
    )


PROVIDER_ONLY_ENDPOINTS: Mapping[str, str] = {
    "ark-chat": "text",
    "grok-chat": "text",
    "agnes-chat": "text",
    "grok-image": "image",
    "vidu-image": "image",
    "agnes-image-pixels": "image",
    "agnes-image-tier": "image",
    **dict.fromkeys(_GROK_ENDPOINTS, "video"),
    "agnes-video-2": "video",
    "agnes-video-2-5": "video",
    "gemini-vertex-video": "video",
    "gemini-vertex-interactions-video": "video",
    "grok-tts": "audio",
    "ark-agent-plan-tts": "audio",
}

_SIMPLE_MEDIA_ENDPOINTS: Mapping[tuple[str, str], str] = {
    ("grok", "image"): "grok-image",
    ("openai", "image"): "openai-images",
    ("vidu", "image"): "vidu-image",
    ("minimax", "image"): "minimax-image",
    ("openai", "video"): "openai-video",
    ("minimax", "audio"): "minimax-tts",
    ("grok", "audio"): "grok-tts",
}

_GEMINI_BACKEND_TYPES: dict[str, str] = {"gemini-aistudio": "aistudio", "gemini-vertex": "vertex"}

_MINIMAX_VIDEO_ENDPOINTS = ("minimax-hailuo-v1", "minimax-hailuo-v1-fast", "minimax-s2v-01", "minimax-h3")

_specs: list[ProviderSpec] = [
    _simple_spec(provider_id, media_type, endpoint)
    for (provider_id, media_type), endpoint in _SIMPLE_MEDIA_ENDPOINTS.items()
]
_IMAGE_ENDPOINTS = {
    "ark": ("ark-seedream-3", "ark-seedream-4"),
    "ark-agent-plan": ("ark-seedream-3", "ark-seedream-4"),
    "dashscope": ("dashscope-qwen-image", "dashscope-wan-image", "dashscope-qwen-image-edit"),
    "agnes": ("agnes-image-pixels", "agnes-image-tier"),
}
_specs += [
    ProviderSpec(
        provider_id=provider_id,
        media_type="image",
        registry_backend=endpoint,
        build_backend=partial(_build_simple, media_type="image", registry_backend=endpoint),
        endpoint=endpoint,
    )
    for provider_id, endpoints in _IMAGE_ENDPOINTS.items()
    for endpoint in endpoints
]
_DASHSCOPE_AUDIO_ENDPOINTS = ("dashscope-tts", "dashscope-speech-synthesizer")
_specs += [
    ProviderSpec(
        provider_id="dashscope",
        media_type="audio",
        registry_backend=endpoint,
        build_backend=partial(_build_simple, media_type="audio", registry_backend=endpoint),
        endpoint=endpoint,
    )
    for endpoint in _DASHSCOPE_AUDIO_ENDPOINTS
]
_specs += [
    _gemini_spec(provider_id, media_type, backend_type=backend_type)
    for provider_id, backend_type in _GEMINI_BACKEND_TYPES.items()
    for media_type in ("image", "audio")
]
_specs += [
    _gemini_video_spec(provider_id, by_backend_type[backend_type], backend_type=backend_type)
    for provider_id, backend_type in _GEMINI_BACKEND_TYPES.items()
    for by_backend_type in _GEMINI_VIDEO_ENDPOINTS.values()
]
_specs += [_kling_spec("image"), _kling_spec("image", endpoint="kling-image-single")]
_specs.append(
    ProviderSpec(
        provider_id="minimax",
        media_type="image",
        registry_backend="minimax-h3-image",
        build_backend=partial(_build_simple, media_type="image", registry_backend="minimax-h3-image"),
        endpoint="minimax-h3-image",
    )
)
_specs += [
    ProviderSpec(
        provider_id=provider,
        media_type="video",
        registry_backend=endpoint,
        build_backend=(
            partial(_build_kling, media_type="video", endpoint=endpoint)
            if provider == "kling"
            else partial(_build_simple, media_type="video", registry_backend=endpoint)
        ),
        endpoint=endpoint,
    )
    for provider, endpoints in (("grok", _GROK_ENDPOINTS), ("vidu", _VIDU_ENDPOINTS), ("kling", _KLING_ENDPOINTS))
    for endpoint in endpoints
]
_specs.append(
    ProviderSpec(
        provider_id="ark-agent-plan",
        media_type="audio",
        registry_backend="ark-agent-plan",
        build_backend=_build_ark_agent_plan_tts,
        endpoint="ark-agent-plan-tts",
    )
)
_specs += [
    ProviderSpec(
        provider_id=ark_provider,
        media_type="video",
        registry_backend=endpoint,
        build_backend=partial(_build_ark_video, endpoint=endpoint),
        endpoint=endpoint,
    )
    for ark_provider in ("ark", "ark-agent-plan")
    for endpoint in _SEEDANCE_ENDPOINTS
]
for _provider, _endpoints in (
    (
        "dashscope",
        (
            "dashscope-video",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-wan3-video",
        ),
    ),
    ("agnes", ("agnes-video-2", "agnes-video-2-5")),
):
    for _endpoint in _endpoints:
        _specs.append(
            ProviderSpec(
                provider_id=_provider,
                media_type="video",
                registry_backend=_endpoint,
                endpoint=_endpoint,
                build_backend=partial(
                    _build_simple,
                    media_type="video",
                    registry_backend=_endpoint,
                    extra_keys=("wan3_base_url",) if _provider == "dashscope" else (),
                ),
            )
        )
_specs += [
    ProviderSpec(
        provider_id="minimax",
        media_type="video",
        registry_backend="declarative",
        build_backend=partial(_build_minimax_declarative_video, endpoint=endpoint),
        endpoint=endpoint,
    )
    for endpoint in _MINIMAX_VIDEO_ENDPOINTS
]
_specs += [
    _text_simple_spec("ark", "ark-chat"),
    _text_simple_spec("ark-agent-plan", "ark-chat"),
    _text_simple_spec("grok", "grok-chat"),
    _text_simple_spec("agnes", "agnes-chat"),
    _text_gemini_spec("gemini-aistudio", build=_build_text_gemini_aistudio),
    _text_gemini_spec("gemini-vertex", build=_build_text_gemini_vertex),
    _text_openai_compat_spec(
        "openai", derive_base_url=None, provider_name=None, registry_backend=_TEXT_OPENAI_RESPONSES_REGISTRY_BACKEND
    ),
    _text_openai_compat_spec("dashscope", derive_base_url=_dashscope_text_base_url, provider_name="dashscope"),
    _text_openai_compat_spec("minimax", derive_base_url=_minimax_text_base_url, provider_name="minimax"),
]

PROVIDER_ENDPOINT_SPECS: dict[tuple[str, str], ProviderSpec] = {
    (spec.provider_id, spec.endpoint): spec for spec in _specs
}


def _declare_endpoints(provider_id: str, media_type: str, default: str) -> ProviderEndpoints:
    endpoints = tuple(
        spec.endpoint
        for spec in PROVIDER_ENDPOINT_SPECS.values()
        if spec.provider_id == provider_id and spec.media_type == media_type
    )
    return ProviderEndpoints(provider_id=provider_id, media_type=media_type, endpoints=endpoints, default=default)


_MULTI_ENDPOINT_DEFAULTS: Mapping[tuple[str, str], str] = {
    ("ark", "image"): "ark-seedream-4",
    ("ark-agent-plan", "image"): "ark-seedream-4",
    ("dashscope", "image"): "dashscope-qwen-image",
    ("gemini-aistudio", "video"): "gemini-video",
    ("gemini-vertex", "video"): "gemini-vertex-video",
    ("agnes", "image"): "agnes-image-tier",
    ("minimax", "image"): "minimax-image",
    ("minimax", "video"): "minimax-hailuo-v1",
    ("grok", "video"): "grok-video-classic",
    ("vidu", "video"): "vidu-video-q3-turbo",
    ("kling", "image"): "kling-image",
    ("kling", "video"): "kling-video-pro-silent",
    ("dashscope", "video"): "dashscope-always-on-video",
    ("dashscope", "audio"): "dashscope-tts",
    ("agnes", "video"): "agnes-video-2-5",
    ("ark", "video"): "ark-seedance-2",
    ("ark-agent-plan", "video"): "ark-seedance-2",
}

PROVIDER_ENDPOINTS: dict[tuple[str, str], ProviderEndpoints] = {
    (spec.provider_id, spec.media_type): _declare_endpoints(
        spec.provider_id,
        spec.media_type,
        _MULTI_ENDPOINT_DEFAULTS.get((spec.provider_id, spec.media_type), spec.endpoint),
    )
    for spec in PROVIDER_ENDPOINT_SPECS.values()
}

PROVIDER_SPEC_REGISTRY: dict[tuple[str, str], ProviderSpec] = {
    key: PROVIDER_ENDPOINT_SPECS[(declared.provider_id, declared.default)]
    for key, declared in PROVIDER_ENDPOINTS.items()
}


_VALID_MEDIA_TYPES = frozenset({"image", "video", "audio", "text"})


def endpoint_media_type(endpoint: str) -> str | None:
    """内置调用端点的媒体类型；不是内置调用端点时为 ``None``。"""
    if endpoint in PROVIDER_ONLY_ENDPOINTS:
        return PROVIDER_ONLY_ENDPOINTS[endpoint]
    from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

    spec = ENDPOINT_REGISTRY.get(endpoint)
    return spec.media_type if spec is not None and spec.source == "builtin" else None


def _validate_provider_specs() -> None:
    """import 期校验内置表自身不变式，misconfig fail-fast（镜像 endpoints._validate_video_caps_declarations）。

    只做不需 import 后端的内表自洽检查：build 可调用、字典键与 spec 字段一致、media_type 合法，以及每个
    「供应商 + 媒体类型」的默认端点在可选端点里、可选端点都登记了同媒体类型的构造。端点名与端点注册表的媒体类型
    一致（:func:`validate_endpoint_declarations`）要 import 端点注册表，改在端点注册表 import 时校验；「registry 名
    都在对应后端 registry 里」归入单测。两者都免得轻量场景（CLI / 迁移）因 import 本缝而被动拉起全部后端。
    """
    for key, spec in PROVIDER_SPEC_REGISTRY.items():
        if not callable(spec.build_backend):
            raise ValueError(f"ProviderSpec {key!r} declares non-callable build_backend: {spec.build_backend!r}")
        if (spec.provider_id, spec.media_type) != key:
            raise ValueError(
                f"PROVIDER_SPEC_REGISTRY key {key!r} does not match spec fields "
                f"(provider_id={spec.provider_id!r}, media_type={spec.media_type!r})"
            )
        if spec.media_type not in _VALID_MEDIA_TYPES:
            raise ValueError(f"ProviderSpec {key!r} declares unknown media_type: {spec.media_type!r}")
    for key, declared in PROVIDER_ENDPOINTS.items():
        if declared.default not in declared.endpoints:
            raise ValueError(f"default endpoint {declared.default!r} of {key!r} is not among {declared.endpoints!r}")
        for endpoint in declared.endpoints:
            spec = PROVIDER_ENDPOINT_SPECS.get((declared.provider_id, endpoint))
            if spec is None or spec.media_type != declared.media_type:
                raise ValueError(f"endpoint {endpoint!r} of {key!r} has no {declared.media_type} build")


def validate_endpoint_declarations() -> None:
    """每个内置供应商声明的端点都是内置调用端点，且媒体类型与声明它的「供应商 + 媒体类型」一致。"""
    for key, declared in PROVIDER_ENDPOINTS.items():
        for endpoint in declared.endpoints:
            media_type = endpoint_media_type(endpoint)
            if media_type != declared.media_type:
                raise ValueError(
                    f"endpoint {endpoint!r} of {key!r} is a {media_type or 'unknown'} endpoint, "
                    f"expected {declared.media_type}"
                )


_validate_provider_specs()


def get_provider_endpoints(provider_id: str, media_type: str) -> ProviderEndpoints:
    """内置供应商在这个媒体类型下可选的内置调用端点与默认端点；没有这个媒体类型的调用通道时抛 ``ValueError``。"""
    declared = PROVIDER_ENDPOINTS.get((provider_id, media_type))
    if declared is None:
        raise ValueError(f"no builtin ProviderSpec for provider={provider_id!r} media={media_type!r}")
    return declared


def get_provider_spec(provider_id: str, media_type: str, endpoint: str | None = None) -> ProviderSpec:
    """内置供应商经 ``endpoint`` 调用时的构造规格；不给端点时取默认端点。端点不在可选端点里时抛 ``ValueError``。"""
    declared = get_provider_endpoints(provider_id, media_type)
    name = declared.default if endpoint is None else endpoint
    if name not in declared.endpoints:
        raise ValueError(f"endpoint {name!r} is not available for provider={provider_id!r} media={media_type!r}")
    return PROVIDER_ENDPOINT_SPECS[(provider_id, name)]


def image_endpoint_for_name(provider_id: str, model_id: str) -> str:
    """图片端点的名字规则，仅用于新增模型预填。"""
    lowered = model_id.lower()
    if provider_id in ("ark", "ark-agent-plan"):
        return "ark-seedream-3" if "seedream-3" in lowered else "ark-seedream-4"
    if provider_id == "dashscope":
        if lowered.startswith("wan"):
            return "dashscope-wan-image"
        return "dashscope-qwen-image-edit" if "qwen-image-edit" in lowered else "dashscope-qwen-image"
    return "agnes-image-tier" if model_id.startswith("agnes-image-2.5") else "agnes-image-pixels"


def dashscope_video_endpoint_for_name(name: str) -> str:
    """按名字预填百炼请求契约；执行时只读取已经选定的端点。"""
    import re

    normalized = name.strip().lower()
    if re.search(r"(?<![a-z0-9])wan[-_]?3(?![a-z0-9])", normalized):
        return "dashscope-wan3-video"
    wan27 = re.search(r"(?<![a-z0-9])wan[-_]?2\.7(?![a-z0-9])", normalized)
    if wan27:
        suffix = normalized[wan27.start() :]
        if re.search(r"(?<![a-z0-9])(?:video[-_]?edit|[sv]2v)(?![a-z0-9])", suffix):
            return "dashscope-video"
        suffix = re.sub(r"wan[-_]?2\.7[_-]", "wan2.7-", suffix)
        suffix = re.sub(r"(?<![a-z0-9])image[-_]?(?:to|2)[-_]?video(?![a-z0-9])", "i2v", suffix)
        normalized = normalized[: wan27.start()] + suffix
    if re.search(r"(?<![a-z0-9])wan2\.7-r2v(?![a-z0-9])", normalized):
        return "dashscope-reference-voice-video"
    if re.search(r"(?<![a-z0-9])happyhorse-1\.[01]-r2v(?![a-z0-9])", normalized):
        return "dashscope-reference-video"
    if re.search(r"(?<![a-z0-9])(?:wan2\.7|happyhorse-1\.[01])-(?:t2v|i2v)(?![a-z0-9])", normalized):
        return "dashscope-always-on-video"
    return "dashscope-video"


def agnes_video_endpoint_for_name(name: str) -> str | None:
    """按已知代际预填 Agnes 契约；未识别时由供应商默认端点决定。"""
    if name in {"agnes-video-2.5", "agnes-video-2.5-flash"}:
        return "agnes-video-2-5"
    if name == "agnes-video-v2.0":
        return "agnes-video-2"
    return None


def gemini_video_endpoint_for_name(provider_id: str, name: str) -> str | None:
    """按名字预填 Gemini 视频的请求协议：Gemini Omni 走 Interactions API，其余由供应商默认端点决定。"""
    if "gemini-omni" not in name.strip().lower():
        return None
    return _GEMINI_VIDEO_ENDPOINTS["interactions"][_GEMINI_BACKEND_TYPES[provider_id]]


def builtin_model_endpoint(provider_id: str, media_type: str, model_id: str | None) -> str:
    """官方模型读取目录声明，用户添加的模型读取保存的选择；未声明时取供应商默认端点。

    声明的端点不在该供应商这一媒体类型的可选端点里时抛 ``ValueError``，避免用其他请求结构调用。
    """
    chosen = catalog.provider_model_endpoint(provider_id, model_id, media_type) if model_id else None
    return get_provider_spec(provider_id, media_type, chosen).endpoint


def builtin_model_spec(provider_id: str, media_type: str, model_id: str | None) -> ProviderSpec:
    """内置供应商的供应商模型的构造规格：按 :func:`builtin_model_endpoint` 定下的端点。"""
    return PROVIDER_ENDPOINT_SPECS[(provider_id, builtin_model_endpoint(provider_id, media_type, model_id))]


def infer_builtin_endpoint(provider_id: str, media_type: str, model_id: str) -> str:
    """内置供应商下添加模型时预填的端点：按名字推断（与自定义供应商同一套规则），不在可选端点里时取默认端点。"""
    if media_type == "image" and provider_id in _IMAGE_ENDPOINTS:
        return image_endpoint_for_name(provider_id, model_id)
    from lib.custom_provider.endpoints import infer_endpoint

    declared = get_provider_endpoints(provider_id, media_type)
    if media_type == "video" and provider_id == "grok":
        entry = catalog.find_entry(model_id, media_type="video")
        names = (entry.name, *entry.aliases) if entry is not None else (model_id,)
        for name in names:
            endpoint = request_profile_endpoint_for_name(provider_id, name)
            if endpoint is not None:
                return endpoint
        return declared.default
    if media_type == "video" and provider_id in _GEMINI_BACKEND_TYPES:
        return gemini_video_endpoint_for_name(provider_id, model_id) or declared.default
    discovery_format = "google" if provider_id in _GEMINI_BACKEND_TYPES else "openai"
    inferred = (
        agnes_video_endpoint_for_name(model_id) if provider_id == "agnes" and media_type == "video" else None
    ) or infer_endpoint(model_id, discovery_format, media_type)
    return inferred if inferred in declared.endpoints else declared.default


def builtin_video_capabilities_for_model(
    provider_id: str, model_id: str, *, endpoint: str | None = None
) -> VideoCapabilities:
    """读取内置视频的生效能力：调用通道的声明与模型条目两侧求值，用户添加的模型再叠加能力覆盖（docs/adr/0104）。

    覆盖整维替换、优先级最高；音频两维合起来无意义时按不支持参考音频处理，与自定义供应商同一口径。
    ``endpoint`` 给出时按它求值，使执行侧能力与构造后端时选定的端点一致。
    """
    return effective_builtin_video_capabilities(
        declared_builtin_video_capabilities(provider_id, model_id, endpoint=endpoint), provider_id, model_id
    )


def two_sided_builtin_video_capabilities(
    channel: VideoCapabilities, provider_id: str, model_id: str
) -> VideoCapabilities:
    """调用通道给出的能力与模型条目两侧求值，不含能力覆盖；设置页的原值就是它。

    条目取供应商模型对应的那个：官方收录的取清单所引的条目，用户添加的取条目绑定或按名字现查的结果。
    """
    from lib.backends.video_capabilities import effective_video_capabilities, entry_video_facts

    entry = catalog.provider_model_entry(provider_id, model_id, "video")
    return effective_video_capabilities(channel, entry_video_facts(entry))


def effective_builtin_video_capabilities(
    channel: VideoCapabilities, provider_id: str, model_id: str
) -> VideoCapabilities:
    """内置视频的生效能力：``channel`` 与模型条目两侧求值，再叠加用户添加的模型的能力覆盖（docs/adr/0104）。

    基底由调用方给定：规划层传无请求上下文的声明，执行闸门传按档位解析的能力，两边求值口径相同。
    """
    return apply_user_added_video_overrides(
        two_sided_builtin_video_capabilities(channel, provider_id, model_id), provider_id, model_id
    )


def apply_user_added_video_overrides(caps: VideoCapabilities, provider_id: str, model_id: str) -> VideoCapabilities:
    """在两侧求值的结果上叠加内置供应商下用户添加的模型的能力覆盖，整维替换（docs/adr/0104）。"""
    overrides = catalog.user_added_capability_overrides(provider_id, model_id)
    if not overrides:
        return caps
    from lib.custom_provider.capabilities import (
        CHANNEL_FACT_OVERRIDE_KEYS,
        enforce_audio_capability_invariant,
        merge_overrides,
    )

    channel = {key: value for key, value in overrides.items() if key in CHANNEL_FACT_OVERRIDE_KEYS}
    return enforce_audio_capability_invariant(merge_overrides(caps, channel), endpoint=provider_id, model_id=model_id)


def _builtin_video_spec(provider_id: str, model_id: str, endpoint: str | None) -> ProviderSpec:
    """给出的端点或模型当前挂接的端点所对应的内置视频调用通道。"""
    if endpoint is not None:
        return get_provider_spec(provider_id, "video", endpoint)
    return builtin_model_spec(provider_id, "video", model_id)


def declared_builtin_video_capabilities(
    provider_id: str, model_id: str, *, endpoint: str | None = None
) -> VideoCapabilities:
    """内置视频调用通道的声明（通道事实），不含模型条目与用户的能力覆盖；声明式端点从随版定义读，其余沿用 backend registry。

    通道取模型挂接的端点（:func:`builtin_model_endpoint`）；``endpoint`` 给出时按它求值，供保存前核对换端点后的能力覆盖。
    """
    spec = _builtin_video_spec(provider_id, model_id, endpoint)
    if spec.registry_backend == "declarative":
        from lib.custom_provider.builtin_definitions import declarative_video_capabilities

        definition = _declarative_definition(spec)
        if definition is None:
            raise ValueError(f"builtin declarative definition missing for {provider_id}/{model_id}")
        return declarative_video_capabilities(definition)
    from lib.backends.video_backends.registry import video_capabilities_for_model

    return video_capabilities_for_model(
        spec.registry_backend, model_id, catalog.provider_model_entry(provider_id, model_id, "video")
    )


def builtin_video_metered_usage(
    provider_id: str,
    model_id: str,
    request: VideoGenerationRequest,
    result: VideoGenerationResult | None = None,
) -> Usage:
    """内置供应商的视频调用通道对一个请求上报的计量，不构造实例；声明式供应商从随版定义读。

    ``result`` 为 ``None`` 时是提交前预估；给出时与结算时调用通道实例上报的计量相同。请求没带能力时补上
    叠加用户覆盖后的能力，与执行时请求携带的那份同源，放宽的维度按实际发出的请求计量。
    """
    spec = builtin_model_spec(provider_id, "video", model_id)
    if spec.registry_backend == "declarative":
        from lib.custom_provider.declarative_backend import declarative_video_usage

        definition = _declarative_definition(spec)
        if definition is None:
            raise ValueError(f"builtin declarative definition missing for {provider_id}/{model_id}")
        return declarative_video_usage(definition, request, result)
    from lib.backends.video_backends.registry import metered_usage_for_model

    if request.video_capabilities is None:
        request = replace(request, video_capabilities=builtin_video_capabilities_for_model(provider_id, model_id))
    return metered_usage_for_model(
        spec.registry_backend,
        model_id,
        request,
        result,
        entry=catalog.provider_model_entry(provider_id, model_id, "video"),
    )


def builtin_effective_generate_audio_for_model(provider_id: str, model_id: str, *, endpoint: str | None = None) -> bool:
    spec = _builtin_video_spec(provider_id, model_id, endpoint)
    if spec.registry_backend == "declarative":
        from arcreel_market_core.video_backend_contract import VideoAudioMode

        return (
            builtin_video_capabilities_for_model(provider_id, model_id, endpoint=endpoint).audio_track
            is not VideoAudioMode.ALWAYS_OFF
        )
    from lib.backends.video_backends.registry import effective_generate_audio_for_model

    return effective_generate_audio_for_model(
        spec.registry_backend, model_id, catalog.provider_model_entry(provider_id, model_id, "video")
    )
