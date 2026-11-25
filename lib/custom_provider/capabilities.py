"""自定义供应商视频能力的唯一合成点：endpoint spec 系统判定与模型条目两侧求值 ⊕ 模型级用户覆盖。

系统判定是调用端点的声明（通道事实）；它与模型对应条目上的输入能力（模型事实）两侧求值
（:func:`lib.backends.video_capabilities.effective_video_capabilities`），能力覆盖在两侧求值之后整维替换；音轨覆盖只改变模型出声事实，不改变端点的开关事实。
ComfyUI 端点的能力只来自节点绑定，不读条目（docs/adr/0082、0104）。

合成语义只此一份：需要生效能力的调用方一律走这里，不得自行合并覆盖，否则「界面允许的
操作在执行期反悔」。执行层由工厂构建 backend 时注入。系统判定本身不落库，随注册表升级
自动变化；用户覆盖是稀疏字典（`CustomProviderModel.capability_overrides`），键缺席即该
维度跟随系统判定。

同一个覆盖字典里还有模型事实维度（可选时长、文生图等，见 :mod:`lib.custom_provider.model_facts`），
视频的模型事实键名与通道事实不重叠，这里跳过它们；图片的参考图上限共用同名键。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import replace
from typing import TYPE_CHECKING

from arcreel_catalog_core import ImageFacts
from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from arcreel_market_core.video_backend_contract import (
    VideoAudioFacts,
    VideoCapabilities,
    VideoRoute,
)
from lib.backends.video_capabilities import (
    channel_projection,
    entry_video_facts,
    projected_video_capabilities,
    resolve_video_model,
)
from lib.capabilities.kernel import CapabilitySnapshot, resolve
from lib.capabilities.override_schema import (
    LEGACY_AUDIO_KEYS,
    OVERRIDE_KEYS,
    VIDEO_CHANNEL_FLAG_KEYS,
    is_legacy_audio_mode,
    valid_value,
)
from lib.custom_provider.definition_capabilities import decode_definition
from lib.custom_provider.duration_presets import infer_supported_durations
from lib.custom_provider.model_facts import model_entry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from arcreel_catalog_core import CatalogEntry
    from lib.custom_provider.endpoints import EndpointSpec


def system_video_capabilities(
    *,
    endpoint: str,
    model_id: str,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> VideoCapabilities:
    """读取端点的通道声明；模型绑定用于选择对应的端点档案。"""
    from lib.custom_provider.endpoints import get_endpoint_spec

    spec = endpoint_spec or get_endpoint_spec(endpoint)
    entry = None if spec.kind == COMFYUI_KIND else model_entry(model_id, "video", entry_binding)
    return channel_projection(spec.video_description(model_id, entry))


def custom_image_snapshot(
    *,
    endpoint: str,
    model_id: str,
    overrides: object = None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
    pending: CatalogEntry | None = None,
) -> CapabilitySnapshot:
    from lib.custom_provider.endpoints import get_endpoint_spec

    spec = endpoint_spec or get_endpoint_spec(endpoint)
    entry = None if spec.kind == COMFYUI_KIND else model_entry(model_id, "image", entry_binding, pending=pending)
    return custom_image_snapshot_from_entry(spec, model_id, entry, overrides)


def custom_image_snapshot_from_entry(
    spec: EndpointSpec,
    model_id: str,
    entry: CatalogEntry | None,
    overrides: object,
    *,
    provider_id: str = "",
    default_resolution: str | None = None,
) -> CapabilitySnapshot:
    facts: dict[str, object] | None = None
    if entry is not None and isinstance(entry.facts, ImageFacts) and spec.kind != COMFYUI_KIND:
        facts = {
            "text_to_image": entry.facts.text_to_image,
            "image_to_image": entry.facts.image_to_image,
            "max_reference_images": entry.facts.max_reference_images,
            "resolutions": entry.facts.resolutions,
            "qualities": entry.facts.qualities,
        }
    return resolve(
        spec.image_description(),
        facts=facts,
        overrides=overrides,
        model_id=model_id,
        provider_id=provider_id,
        default_resolution=default_resolution,
    )


def custom_video_snapshot(
    *,
    endpoint: str,
    model_id: str,
    overrides: object = None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
    pending: CatalogEntry | None = None,
) -> CapabilitySnapshot:
    from lib.custom_provider.endpoints import get_endpoint_spec

    spec = endpoint_spec or get_endpoint_spec(endpoint)
    entry = None if spec.kind == COMFYUI_KIND else model_entry(model_id, "video", entry_binding, pending=pending)
    return custom_video_snapshot_from_entry(spec, model_id, entry, overrides)


def custom_video_snapshot_from_entry(
    spec: EndpointSpec,
    model_id: str,
    entry: CatalogEntry | None,
    overrides: object,
    *,
    provider_id: str = "",
    default_resolution: str | None = None,
) -> CapabilitySnapshot:
    facts = entry_video_facts(entry)
    if entry is None and spec.kind != COMFYUI_KIND:
        facts = replace(facts, durations=tuple(infer_supported_durations(model_id)))
    return resolve_video_model(
        spec.video_description(model_id, entry),
        facts,
        overrides,
        model_id=model_id,
        provider_id=provider_id,
        default_resolution=default_resolution,
    )


def two_sided_video_capabilities(
    *, endpoint: str, model_id: str, endpoint_spec: EndpointSpec | None = None, entry_binding: str | None = None
) -> VideoCapabilities:
    return projected_video_capabilities(
        custom_video_snapshot(
            endpoint=endpoint, model_id=model_id, endpoint_spec=endpoint_spec, entry_binding=entry_binding
        )
    )


def video_capabilities_from_definition(definition: Mapping[str, object]) -> VideoCapabilities:
    """从声明式定义机械合成协议能力；必需图输入决定是否支持纯文生请求。"""
    raw_capabilities = definition.get("capabilities")
    capabilities = raw_capabilities if isinstance(raw_capabilities, Mapping) else {}
    applied: dict[str, object] = {}
    for key, value in capabilities.items():
        if key == "max_reference_images" and isinstance(value, float) and value.is_integer():
            value = int(value)
        if key in LEGACY_AUDIO_KEYS:
            valid = is_legacy_audio_mode(value) or (value is None and key == "reference_route_audio_track")
        elif key in VIDEO_CHANNEL_FLAG_KEYS:
            valid = type(value) is bool
        else:
            valid = (
                key in VideoCapabilities.__dataclass_fields__
                and key in OVERRIDE_KEYS["video"]
                and valid_value(key, value)
            )
        if not valid:
            raise ValueError(f"invalid capability in endpoint definition: {key}={value!r}")
        applied[key] = value

    return channel_projection(decode_definition({**definition, "kind": "declarative", "capabilities": applied}))


def filter_valid_overrides(
    *,
    endpoint: str,
    model_id: str,
    overrides: object | None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> dict[str, object]:
    try:
        _, applied = synthesize_video_capabilities_with_overrides(
            endpoint=endpoint,
            model_id=model_id,
            overrides=overrides,
            endpoint_spec=endpoint_spec,
            entry_binding=entry_binding,
        )
    except ValueError:
        logger.warning("忽略 %s/%s 的能力覆盖 %s：无法解析端点", endpoint, model_id, overrides)
        return {}
    return applied


def video_audio_facts(channel: VideoCapabilities, effective: VideoCapabilities, route: VideoRoute) -> VideoAudioFacts:
    """开关只取端点声明，成片音轨取模型覆盖或端点无声信号；缺少无声信号时默认有声。"""
    return VideoAudioFacts(
        has_audio_track=effective.audio_track_for_route(route).has_audio_track,
        audio_switch_controllable=channel.audio_track_for_route(route).audio_switch_controllable,
    )


def synthesize_video_capabilities(
    *,
    endpoint: str,
    model_id: str,
    overrides: object | None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> VideoCapabilities:
    """系统判定与模型条目两侧求值 ⊕ 用户覆盖 → 生效能力。

    Raises:
        ValueError: 系统判定本身不可得（见 :func:`system_video_capabilities`）。
    """
    caps, _applied = synthesize_video_capabilities_with_overrides(
        endpoint=endpoint,
        model_id=model_id,
        overrides=overrides,
        endpoint_spec=endpoint_spec,
        entry_binding=entry_binding,
    )
    return caps


def synthesize_video_capabilities_with_overrides(
    *,
    endpoint: str,
    model_id: str,
    overrides: object | None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> tuple[VideoCapabilities, dict[str, object]]:
    snapshot = custom_video_snapshot(
        endpoint=endpoint,
        model_id=model_id,
        endpoint_spec=endpoint_spec,
        entry_binding=entry_binding,
        overrides=overrides,
    )
    if isinstance(overrides, Mapping):
        for key, value in overrides.items():
            if LEGACY_AUDIO_KEYS.get(key, key) not in snapshot.overrides:
                logger.warning("忽略 %s/%s 的能力覆盖 %s=%s", endpoint, model_id, key, value)
    elif overrides is not None:
        logger.warning("忽略 %s/%s 的非字典能力覆盖", endpoint, model_id)
    return projected_video_capabilities(snapshot), snapshot.override_dict()
