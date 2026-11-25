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
构造表与供应商端点清单的一致性。后端 registry 与自定义端点注册表的一致性由 CI 校验。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Any

from lib.backends.builtin_endpoints import PROVIDER_ENDPOINTS, builtin_model_endpoint, select_provider_endpoint
from lib.backends.kling_shared import kling_auth_mode
from lib.backends.providers import PROVIDER_KLING, video_audio_always_billed
from lib.backends.video_capabilities import (
    VideoEndpointCapabilities,
    channel_projection,
    effective_video_capabilities,
    entry_video_facts,
    projected_video_capabilities,
    resolve_video_model,
    video_endpoint,
)
from lib.backends.video_frame_slots import VideoCapabilityProbe, resolve_video_capabilities
from lib.capabilities.kernel import CapabilitySnapshot, Endpoint, RequestConditions
from lib.catalog import query as catalog
from lib.video_runtime.builtin import build_legacy_audio, build_legacy_text, build_legacy_video

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry
    from arcreel_market_core.video_backend_contract import (
        VideoCapabilities,
    )
    from lib.backends.backend_assembly.loaded_config import LoadedConfig


@dataclass(frozen=True)
class ProviderSpec:
    """内置供应商经一个内置调用端点调用时的 backend 构造规格：供应商给凭证，端点决定请求。"""

    provider_id: str
    media_type: str
    registry_backend: str
    build_backend: Callable[[LoadedConfig, str | None], Any]
    endpoint: str


def _media_create_backend(media_type: str) -> Callable[..., Any]:
    """按 media_type 取对应 registry 的 create_backend（运行时取，便于测试 patch 模块属性）。"""
    if media_type == "image":
        from lib.backends.image_backends.registry import create_backend
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


_GEMINI_BUILDS = {"image": _build_gemini_image}


_GEMINI_ENDPOINTS = {"image": "gemini-image"}


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


def _video_spec(provider_id: str, endpoint: str, registry_backend: str | None = None) -> ProviderSpec:
    registry = registry_backend or endpoint
    return ProviderSpec(
        provider_id,
        "video",
        registry,
        partial(build_legacy_video, provider_id=provider_id, registry=registry, endpoint=endpoint),
        endpoint,
    )


def _audio_spec(provider_id: str, endpoint: str, registry_backend: str | None = None) -> ProviderSpec:
    registry = registry_backend or provider_id
    return ProviderSpec(
        provider_id,
        "audio",
        registry,
        partial(build_legacy_audio, provider_id=provider_id, registry=registry),
        endpoint,
    )


def _text_spec(provider_id: str, endpoint: str, registry_backend: str | None = None) -> ProviderSpec:
    registry = registry_backend or provider_id
    return ProviderSpec(
        provider_id,
        "text",
        registry,
        partial(build_legacy_text, provider_id=provider_id, registry=registry),
        endpoint,
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


_SIMPLE_MEDIA_ENDPOINTS: Mapping[tuple[str, str], str] = {
    ("grok", "image"): "grok-image",
    ("openai", "image"): "openai-images",
    ("vidu", "image"): "vidu-image",
    ("minimax", "image"): "minimax-image",
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
_specs += [_audio_spec("dashscope", endpoint, endpoint) for endpoint in _DASHSCOPE_AUDIO_ENDPOINTS]
_specs += [
    _audio_spec("minimax", "minimax-tts"),
    _audio_spec("grok", "grok-tts"),
    _audio_spec("ark-agent-plan", "ark-agent-plan-tts"),
    *(_audio_spec(provider_id, "gemini-tts", _GEMINI_REGISTRY_BACKEND) for provider_id in _GEMINI_BACKEND_TYPES),
]
_specs += [
    _gemini_spec(provider_id, "image", backend_type=backend_type)
    for provider_id, backend_type in _GEMINI_BACKEND_TYPES.items()
]
_specs.append(_video_spec("openai", "openai-video", "openai"))
_specs += [
    _video_spec(provider_id, endpoint, "gemini" if endpoint == "gemini-video" else endpoint)
    for provider_id, backend_type in _GEMINI_BACKEND_TYPES.items()
    for by_backend_type in _GEMINI_VIDEO_ENDPOINTS.values()
    for endpoint in (by_backend_type[backend_type],)
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
    _video_spec(provider, endpoint)
    for provider, endpoints in (("grok", _GROK_ENDPOINTS), ("vidu", _VIDU_ENDPOINTS), ("kling", _KLING_ENDPOINTS))
    for endpoint in endpoints
]
_specs += [
    _video_spec(ark_provider, endpoint)
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
        _specs.append(_video_spec(_provider, _endpoint))
_specs += [_video_spec("minimax", endpoint, "declarative") for endpoint in _MINIMAX_VIDEO_ENDPOINTS]
_specs += [
    _text_spec("ark", "ark-chat"),
    _text_spec("ark-agent-plan", "ark-chat"),
    _text_spec("grok", "grok-chat"),
    _text_spec("agnes", "agnes-chat"),
    _text_spec("gemini-aistudio", "gemini-generate", _GEMINI_REGISTRY_BACKEND),
    _text_spec("gemini-vertex", "gemini-generate", _GEMINI_REGISTRY_BACKEND),
    _text_spec("openai", "openai-responses", "openai-responses"),
    _text_spec("dashscope", "openai-chat", "openai"),
    _text_spec("minimax", "openai-chat", "openai"),
]

PROVIDER_ENDPOINT_SPECS: dict[tuple[str, str], ProviderSpec] = {
    (spec.provider_id, spec.endpoint): spec for spec in _specs
}


def _validate_provider_specs() -> None:
    for key, declared in PROVIDER_ENDPOINTS.items():
        if declared.default not in declared.endpoints:
            raise ValueError(f"default endpoint {declared.default!r} of {key!r} is not among {declared.endpoints!r}")
        for endpoint in declared.endpoints:
            spec = PROVIDER_ENDPOINT_SPECS.get((declared.provider_id, endpoint))
            if spec is None or spec.media_type != declared.media_type:
                raise ValueError(f"endpoint {endpoint!r} of {key!r} has no {declared.media_type} build")
    for key, spec in PROVIDER_ENDPOINT_SPECS.items():
        if key != (spec.provider_id, spec.endpoint):
            raise ValueError(f"ProviderSpec key {key!r} does not match spec fields")
        if not callable(spec.build_backend):
            raise ValueError(f"ProviderSpec {key!r} declares non-callable build_backend: {spec.build_backend!r}")
        declared = PROVIDER_ENDPOINTS.get((spec.provider_id, spec.media_type))
        if declared is None or spec.endpoint not in declared.endpoints:
            raise ValueError(f"ProviderSpec {key!r} has no {spec.media_type} endpoint declaration")


_validate_provider_specs()

PROVIDER_SPEC_REGISTRY: dict[tuple[str, str], ProviderSpec] = {
    key: PROVIDER_ENDPOINT_SPECS[(declared.provider_id, declared.default)]
    for key, declared in PROVIDER_ENDPOINTS.items()
}


def get_provider_spec(provider_id: str, media_type: str, endpoint: str | None = None) -> ProviderSpec:
    """内置供应商经 ``endpoint`` 调用时的构造规格；不给端点时取默认端点。端点不在可选端点里时抛 ``ValueError``。"""
    name = select_provider_endpoint(provider_id, media_type, endpoint)
    return PROVIDER_ENDPOINT_SPECS[(provider_id, name)]


def builtin_model_spec(provider_id: str, media_type: str, model_id: str | None) -> ProviderSpec:
    """内置供应商的供应商模型的构造规格：按 :func:`builtin_model_endpoint` 定下的端点。"""
    return PROVIDER_ENDPOINT_SPECS[(provider_id, builtin_model_endpoint(provider_id, media_type, model_id))]


def builtin_video_capabilities_for_model(
    provider_id: str, model_id: str, *, endpoint: str | None = None
) -> VideoCapabilities:
    """读取内置视频的生效能力：调用通道的声明与模型条目两侧求值，用户添加的模型再叠加能力覆盖（docs/adr/0104）。

    覆盖整维替换、优先级最高；音频两维合起来无意义时按不支持参考音频处理，与自定义供应商同一口径。
    ``endpoint`` 给出时按它求值，使执行侧能力与构造后端时选定的端点一致。
    """
    return projected_video_capabilities(builtin_video_snapshot(provider_id, model_id, endpoint=endpoint))


def builtin_video_snapshot(
    provider_id: str,
    model_id: str,
    *,
    endpoint: str | None = None,
    overrides: object = None,
    official: bool | None = None,
    base_model: catalog.CatalogModel | None = None,
) -> CapabilitySnapshot:
    view = catalog.provider_model_view(provider_id, model_id, media_type="video")
    return builtin_video_snapshot_from_view(
        provider_id,
        model_id,
        view,
        endpoint=endpoint,
        overrides=overrides,
        official=official,
        base_model=base_model,
        always_bill_audio=video_audio_always_billed(provider_id),
    )


def builtin_video_snapshot_from_view(
    provider_id: str,
    model_id: str,
    view: catalog.ProviderModelView | None,
    *,
    endpoint: str | None,
    always_bill_audio: bool,
    description: Endpoint | None = None,
    overrides: object = None,
    official: bool | None = None,
    base_model: catalog.CatalogModel | None = None,
) -> CapabilitySnapshot:
    from arcreel_catalog_core import DurationConstraints, VideoFacts

    entry = view.entry if view else None
    description = description or _builtin_video_description(provider_id, model_id, endpoint, entry)
    facts = entry_video_facts(entry)
    if view and entry is None:
        facts = replace(facts, durations=tuple((view.base_model or view.model).supported_durations))
    elif view is None and base_model is not None:
        facts = VideoFacts(
            durations=tuple(base_model.supported_durations),
            resolutions=tuple(base_model.resolutions),
            duration_constraints=DurationConstraints(
                by_resolution={k: tuple(v) for k, v in base_model.duration_resolution_constraints.items()},
                with_reference_images=tuple(base_model.reference_image_durations),
                with_end_frame=tuple(base_model.end_frame_durations),
            ),
            max_reference_images=base_model.max_reference_images,
        )
    return resolve_video_model(
        description,
        facts,
        view.capability_overrides if view and overrides is None else overrides,
        provider_id=provider_id,
        model_id=model_id,
        always_bill_audio=always_bill_audio,
        official=view.official if official is None and view else bool(official),
    )


def two_sided_builtin_video_capabilities(
    channel: VideoCapabilities, provider_id: str, model_id: str
) -> VideoCapabilities:
    """调用通道给出的能力与模型条目两侧求值，不含能力覆盖；设置页的原值就是它。

    条目取供应商模型对应的那个：官方收录的取清单所引的条目，用户添加的取条目绑定或按名字现查的结果。
    """
    entry = catalog.provider_model_entry(provider_id, model_id, "video")
    return effective_video_capabilities(channel, entry_video_facts(entry))


def effective_builtin_video_capabilities(
    channel: VideoCapabilities, provider_id: str, model_id: str
) -> VideoCapabilities:
    """内置视频的生效能力：``channel`` 与模型条目两侧求值，再叠加用户添加的模型的能力覆盖（docs/adr/0104）。

    基底由调用方给定：规划层传无请求上下文的声明，执行闸门传按档位解析的能力，两边求值口径相同。
    """
    return effective_video_capabilities(
        channel,
        entry_video_facts(catalog.provider_model_entry(provider_id, model_id, "video")),
        catalog.user_added_capability_overrides(provider_id, model_id),
    )


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
    entry = catalog.provider_model_entry(provider_id, model_id, "video")
    return channel_projection(_builtin_video_description(provider_id, model_id, endpoint, entry))


def _builtin_video_description(
    provider_id: str, model_id: str, endpoint: str | None, entry: CatalogEntry | None
) -> Endpoint:
    from lib.backends.video_backends.registry import describe_video_endpoint
    from lib.custom_provider.builtin_definitions import builtin_video_description

    spec = _builtin_video_spec(provider_id, model_id, endpoint)
    if spec.registry_backend == "declarative":
        return builtin_video_description(spec.endpoint)
    return describe_video_endpoint(spec.registry_backend, model_id, entry)


def builtin_request_video_capabilities(
    backend: VideoCapabilityProbe, provider_id: str, model_id: str, conditions: RequestConditions
) -> VideoCapabilities:
    entry = catalog.provider_model_entry(provider_id, model_id, "video")
    if isinstance(backend, VideoEndpointCapabilities):
        description = backend.describe_video_endpoint(model_id, entry)
    else:
        channel = resolve_video_capabilities(
            backend, service_tier=conditions.service_tier, resolution=conditions.resolution
        )
        description = video_endpoint(channel)
    snapshot = resolve_video_model(
        description,
        entry_video_facts(entry),
        catalog.user_added_capability_overrides(provider_id, model_id),
        provider_id=provider_id,
        request=conditions,
    )
    return projected_video_capabilities(snapshot)


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
