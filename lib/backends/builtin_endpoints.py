"""内置供应商的调用端点清单与默认值查询。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from arcreel_catalog_core import BUILTIN_ENDPOINTS, BUILTIN_PROVIDER_ENDPOINTS
from lib.catalog import query as catalog


@dataclass(frozen=True)
class ProviderEndpoints:
    """内置供应商在一个媒体类型下可挂接的内置调用端点（按声明顺序）与默认端点。

    默认端点是官方收录的模型没有另行指定时挂接的端点，也是用户添加模型的推断落空时的预填值。
    """

    provider_id: str
    media_type: str
    endpoints: tuple[str, ...]
    default: str


PROVIDER_ONLY_ENDPOINTS: Mapping[str, str] = {
    endpoint: BUILTIN_ENDPOINTS[endpoint].media_type
    for endpoint in (
        "ark-chat",
        "grok-chat",
        "agnes-chat",
        "grok-image",
        "vidu-image",
        "agnes-image-pixels",
        "agnes-image-tier",
        "grok-video-classic",
        "grok-video-mixed",
        "agnes-video-2",
        "agnes-video-2-5",
        "gemini-vertex-video",
        "gemini-vertex-interactions-video",
        "grok-tts",
        "ark-agent-plan-tts",
    )
}


PROVIDER_ENDPOINTS: dict[tuple[str, str], ProviderEndpoints] = {
    (provider_id, media_type): ProviderEndpoints(provider_id, media_type, options.endpoints, options.default)
    for provider_id, by_media in BUILTIN_PROVIDER_ENDPOINTS.items()
    for media_type, options in by_media.items()
}


def get_provider_endpoints(provider_id: str, media_type: str) -> ProviderEndpoints:
    """内置供应商在这个媒体类型下可选的内置调用端点与默认端点；没有这个媒体类型的调用通道时抛 ``ValueError``。"""
    declared = PROVIDER_ENDPOINTS.get((provider_id, media_type))
    if declared is None:
        raise ValueError(f"no builtin ProviderSpec for provider={provider_id!r} media={media_type!r}")
    return declared


def select_provider_endpoint(provider_id: str, media_type: str, endpoint: str | None) -> str:
    declared = get_provider_endpoints(provider_id, media_type)
    name = declared.default if endpoint is None else endpoint
    if name not in declared.endpoints:
        raise ValueError(f"endpoint {name!r} is not available for provider={provider_id!r} media={media_type!r}")
    return name


def builtin_model_endpoint(provider_id: str, media_type: str, model_id: str | None) -> str:
    """官方模型读取目录声明，用户添加的模型读取保存的选择；未声明时取供应商默认端点。

    声明的端点不在该供应商这一媒体类型的可选端点里时抛 ``ValueError``，避免用其他请求结构调用。
    """
    chosen = catalog.provider_model_endpoint(provider_id, model_id, media_type) if model_id else None
    return select_provider_endpoint(provider_id, media_type, chosen)
