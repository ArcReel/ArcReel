"""内置供应商调用端点与后端注册表的映射。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from lib.backends.builtin_endpoints import PROVIDER_ENDPOINTS, builtin_model_endpoint, select_provider_endpoint
from lib.backends.providers import video_audio_always_billed
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

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry
    from arcreel_market_core.video_backend_contract import (
        VideoCapabilities,
    )


@dataclass(frozen=True)
class ProviderSpec:
    """内置供应商经一个内置调用端点调用时的元数据规格：端点与注册表键。"""

    provider_id: str
    media_type: str
    registry_backend: str
    endpoint: str


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

_GEMINI_VIDEO_ENDPOINTS: Mapping[str, Mapping[str, str]] = {
    "veo": {"aistudio": "gemini-video", "vertex": "gemini-vertex-video"},
    "interactions": {"aistudio": "gemini-interactions-video", "vertex": "gemini-vertex-interactions-video"},
}


def _image_spec(provider_id: str, endpoint: str, registry_backend: str | None = None) -> ProviderSpec:
    return ProviderSpec(provider_id, "image", registry_backend or endpoint, endpoint)


def _video_spec(provider_id: str, endpoint: str, registry_backend: str | None = None) -> ProviderSpec:
    return ProviderSpec(provider_id, "video", registry_backend or endpoint, endpoint)


def _audio_spec(provider_id: str, endpoint: str, registry_backend: str | None = None) -> ProviderSpec:
    return ProviderSpec(provider_id, "audio", registry_backend or provider_id, endpoint)


def _text_spec(provider_id: str, endpoint: str, registry_backend: str | None = None) -> ProviderSpec:
    return ProviderSpec(provider_id, "text", registry_backend or provider_id, endpoint)


_SIMPLE_IMAGE_ENDPOINTS: Mapping[str, str] = {
    "grok": "grok-image",
    "openai": "openai-images",
    "vidu": "vidu-image",
    "minimax": "minimax-image",
}

_GEMINI_BACKEND_TYPES: dict[str, str] = {"gemini-aistudio": "aistudio", "gemini-vertex": "vertex"}

_MINIMAX_VIDEO_ENDPOINTS = ("minimax-hailuo-v1", "minimax-hailuo-v1-fast", "minimax-s2v-01", "minimax-h3")

_specs: list[ProviderSpec] = [
    _image_spec(provider_id, endpoint, provider_id) for provider_id, endpoint in _SIMPLE_IMAGE_ENDPOINTS.items()
]
_IMAGE_ENDPOINTS = {
    "ark": ("ark-seedream-3", "ark-seedream-4"),
    "ark-agent-plan": ("ark-seedream-3", "ark-seedream-4"),
    "dashscope": ("dashscope-qwen-image", "dashscope-wan-image", "dashscope-qwen-image-edit"),
    "agnes": ("agnes-image-pixels", "agnes-image-tier"),
}
_specs += [
    _image_spec(provider_id, endpoint) for provider_id, endpoints in _IMAGE_ENDPOINTS.items() for endpoint in endpoints
]
_DASHSCOPE_AUDIO_ENDPOINTS = ("dashscope-tts", "dashscope-speech-synthesizer")
_specs += [_audio_spec("dashscope", endpoint, endpoint) for endpoint in _DASHSCOPE_AUDIO_ENDPOINTS]
_specs += [
    _audio_spec("minimax", "minimax-tts"),
    _audio_spec("grok", "grok-tts"),
    _audio_spec("ark-agent-plan", "ark-agent-plan-tts"),
    *(_audio_spec(provider_id, "gemini-tts", _GEMINI_REGISTRY_BACKEND) for provider_id in _GEMINI_BACKEND_TYPES),
]
_specs += [_image_spec(provider_id, "gemini-image", _GEMINI_REGISTRY_BACKEND) for provider_id in _GEMINI_BACKEND_TYPES]
_specs.append(_video_spec("openai", "openai-video", "openai"))
_specs += [
    _video_spec(provider_id, endpoint, "gemini" if endpoint == "gemini-video" else endpoint)
    for provider_id, backend_type in _GEMINI_BACKEND_TYPES.items()
    for by_backend_type in _GEMINI_VIDEO_ENDPOINTS.values()
    for endpoint in (by_backend_type[backend_type],)
]
_specs += [_image_spec("kling", "kling-image", "kling"), _image_spec("kling", "kling-image-single")]
_specs.append(_image_spec("minimax", "minimax-h3-image"))
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
    _text_spec("ark", "ark-chat", "openai"),
    _text_spec("ark-agent-plan", "ark-chat", "openai"),
    _text_spec("grok", "grok-chat"),
    _text_spec("agnes", "agnes-chat", "openai"),
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
