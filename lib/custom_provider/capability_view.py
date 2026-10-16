"""供应商模型 Sheet 上「这个供应商支持的参数」：每个维度的原值、能力覆盖与生效值（docs/adr/0104、0106）。

原值是没有能力覆盖时运行时采用的值：模型事实取对应条目，对应不上条目时取运行时回落（可选时长取按名字推断的
预设，其余维度不加约束，记为 ``None``）；通道事实取调用通道或调用端点的声明。生效值是原值叠加能力覆盖的结果，
与执行层读到的一致。

只有用户负责的供应商模型可以修改：自定义供应商的模型，以及内置供应商下用户添加的模型。ComfyUI 端点只开放视频
的可选时长，且端点驱动不了时长时这一维也不开放（docs/adr/0082）。图片的文生图、图生图只开放调用通道实现了的
那一维：通道没实现的能力覆盖为开启也打不开。

本模块还承担内置供应商下用户添加的模型的能力覆盖写入校验与装入过滤；自定义供应商模型的写入校验在
``server/routers/custom_providers.py``，两边的值形状判定共用 :func:`model_fact_value_matches` 与
:func:`capability_value_matches`。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field

from arcreel_catalog_core import DurationConstraints
from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoAudioMode,
    VideoCapabilities,
    audio_capability_pair_is_coherent,
)
from lib.backends.backend_assembly.specs import declared_builtin_video_capabilities
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_capabilities import builtin_channel_capabilities
from lib.catalog.query import CatalogModel
from lib.custom_provider.capabilities import (
    AUDIO_OVERRIDE_KEYS,
    CAPABILITY_OVERRIDE_FIELDS,
    CHANNEL_FACT_OVERRIDE_KEYS,
    capability_type_name,
    capability_value_matches,
    system_video_capabilities,
)
from lib.custom_provider.endpoints import EndpointSpec
from lib.custom_provider.model_facts import (
    MODEL_FACT_OVERRIDE_KEYS,
    comfyui_duration_override,
    image_model_facts,
    model_fact_overrides,
    model_fact_type_name,
    model_fact_value_matches,
    text_model_facts,
    video_model_facts,
)

logger = logging.getLogger(__name__)

#: 界面上的维度顺序：模型事实在前（时长联动约束紧跟可选时长），通道事实随后。
OVERRIDE_KEY_ORDER: tuple[str, ...] = (
    "image_input",
    "structured_output",
    "max_output_tokens",
    "text_to_image",
    "image_to_image",
    "durations",
    "duration_constraints",
    "resolutions",
    "text_to_video",
    "first_frame",
    "last_frame",
    "max_reference_images",
    "reference_audio_mode",
    "max_reference_audio_count",
    "max_reference_audio_total_seconds",
    "max_prompt_chars",
    "audio_track",
    "reference_route_audio_track",
)

_IMAGE_CAPABILITY_BY_KEY: Mapping[str, ImageCapability] = {
    "text_to_image": ImageCapability.TEXT_TO_IMAGE,
    "image_to_image": ImageCapability.IMAGE_TO_IMAGE,
}


@dataclass(frozen=True)
class CapabilityView:
    """一个供应商模型的「这个供应商支持的参数」。

    - ``editable``：可以修改的维度，按界面顺序；官方收录的模型为空。
    - ``base``：每个维度的原值（没有能力覆盖时运行时采用的值），``None`` 表示不加约束。
    - ``overrides``：生效的能力覆盖（稀疏）。
    - ``effective``：原值叠加覆盖后的生效值。
    """

    media_type: str
    editable: tuple[str, ...] = ()
    base: dict[str, object] = field(default_factory=dict)
    overrides: dict[str, object] = field(default_factory=dict)

    @property
    def effective(self) -> dict[str, object]:
        return {**self.base, **self.overrides}


def overridable_keys(media_type: str) -> frozenset[str]:
    """该媒体类型可以覆盖的全部维度：模型事实，视频另有通道事实。"""
    keys = MODEL_FACT_OVERRIDE_KEYS.get(media_type, frozenset())
    return keys | CHANNEL_FACT_OVERRIDE_KEYS if media_type == "video" else keys


def _ordered(keys: frozenset[str] | set[str]) -> tuple[str, ...]:
    return tuple(key for key in OVERRIDE_KEY_ORDER if key in keys)


def _constraints_json(constraints: DurationConstraints) -> dict[str, object]:
    return {
        "by_resolution": {res: list(allowed) for res, allowed in constraints.by_resolution.items()},
        "with_reference_images": list(constraints.with_reference_images),
    }


def _channel_json(caps: VideoCapabilities, *, audio_signal: bool) -> dict[str, object]:
    """通道事实维度的原值。``audio_signal`` 为假时（自定义供应商没有音轨覆盖）音轨形态按「假定有声、开关可控」。"""
    raw = asdict(caps)
    values: dict[str, object] = {}
    for key in CHANNEL_FACT_OVERRIDE_KEYS:
        value = raw[key]
        values[key] = value.value if isinstance(value, ReferenceAudioMode | VideoAudioMode) else value
    if audio_signal:
        values["reference_route_audio_track"] = caps.audio_track_for_route("r2v").value
    else:
        values["audio_track"] = VideoAudioMode.CONTROLLABLE.value
        values["reference_route_audio_track"] = VideoAudioMode.CONTROLLABLE.value
    return values


def _normalized_overrides(overrides: Mapping[str, object]) -> dict[str, object]:
    """覆盖值按生效形状回显：可选时长去重排序，时长联动约束的分辨率键小写。"""
    result = dict(overrides)
    durations = result.get("durations")
    if isinstance(durations, list):
        result["durations"] = sorted({int(d) for d in durations})
    constraints = result.get("duration_constraints")
    if isinstance(constraints, Mapping):
        by_resolution = constraints.get("by_resolution")
        with_reference = constraints.get("with_reference_images")
        result["duration_constraints"] = {
            "by_resolution": (
                {str(res).strip().lower(): list(allowed) for res, allowed in by_resolution.items()}
                if isinstance(by_resolution, Mapping)
                else {}
            ),
            "with_reference_images": list(with_reference) if isinstance(with_reference, list) else [],
        }
    return result


# ---------------------------------------------------------------------------
# 自定义供应商的模型
# ---------------------------------------------------------------------------


def custom_capability_view(
    *,
    model_id: str,
    endpoint: str,
    spec: EndpointSpec,
    raw_overrides: object | None,
    applied_overrides: Mapping[str, object] | None,
    entry_binding: str | None,
) -> CapabilityView:
    """自定义供应商模型的参数视图。``applied_overrides`` 是按写入侧同一判定过滤后的覆盖（回显用的那一份）。"""
    media_type = spec.media_type
    if spec.kind == COMFYUI_KIND:
        if media_type != "video":
            return CapabilityView(media_type)
        endpoint_durations = list(spec.endpoint_durations or [])
        override = comfyui_duration_override(raw_overrides, model_id=model_id)
        return CapabilityView(
            media_type,
            editable=("durations",) if endpoint_durations else (),
            base={"durations": endpoint_durations},
            overrides={"durations": override} if override and endpoint_durations else {},
        )

    base: dict[str, object] = {}
    editable = set(overridable_keys(media_type))
    match media_type:
        case "text":
            text = text_model_facts(model_id, None, entry_binding=entry_binding)
            base = {
                "image_input": text.image_input,
                "structured_output": text.structured_output,
                "max_output_tokens": text.max_output_tokens,
            }
        case "image":
            image = image_model_facts(model_id, None, entry_binding=entry_binding)
            implemented = spec.image_capabilities or frozenset()
            base = {
                "text_to_image": image.text_to_image is not False and ImageCapability.TEXT_TO_IMAGE in implemented,
                "image_to_image": image.image_to_image is not False and ImageCapability.IMAGE_TO_IMAGE in implemented,
            }
            editable = {key for key in editable if _IMAGE_CAPABILITY_BY_KEY[key] in implemented}
        case "video":
            video = video_model_facts(model_id, None, entry_binding=entry_binding)
            base = {
                "durations": list(video.durations),
                "duration_constraints": _constraints_json(video.duration_constraints),
                "resolutions": list(video.resolutions) if video.resolutions is not None else None,
            }
            try:
                caps = system_video_capabilities(endpoint=endpoint, model_id=model_id, endpoint_spec=spec)
            except ValueError:
                logger.warning("无法判定系统能力: endpoint=%r model_id=%r", endpoint, model_id)
                caps = VideoCapabilities()
            base |= _channel_json(caps, audio_signal=False)
        case _:
            editable = set()
    overrides = _normalized_overrides(applied_overrides or {})
    return CapabilityView(media_type, editable=_ordered(editable), base=base, overrides=overrides)


# ---------------------------------------------------------------------------
# 内置供应商的模型
# ---------------------------------------------------------------------------


def _catalog_model_facts(provider_id: str, model_id: str, model: CatalogModel) -> dict[str, object]:
    """内置供应商模型的目录信息（未叠加覆盖时即运行时采用的值）按维度展开。"""
    caps = set(model.capabilities)
    match model.media_type:
        case "text":
            return {
                "image_input": "vision" in caps,
                "structured_output": "structured_output" in caps,
                "max_output_tokens": model.max_output_tokens,
            }
        case "image":
            return {"text_to_image": "text_to_image" in caps, "image_to_image": "image_to_image" in caps}
        case "video":
            facts: dict[str, object] = {
                "durations": list(model.supported_durations),
                "duration_constraints": {
                    "by_resolution": {
                        res: list(allowed) for res, allowed in model.duration_resolution_constraints.items()
                    },
                    "with_reference_images": list(model.reference_image_durations),
                },
                "resolutions": list(model.resolutions) or None,
            }
            try:
                declared = declared_builtin_video_capabilities(provider_id, model_id)
            except ValueError:
                logger.warning("无法读取内置视频能力: provider=%r model_id=%r", provider_id, model_id)
                declared = VideoCapabilities()
            return facts | _channel_json(declared, audio_signal=True)
        case _:
            return {}


def _image_channel(provider_id: str) -> frozenset[ImageCapability]:
    try:
        return builtin_channel_capabilities(provider_id)
    except ValueError:
        return frozenset()


def builtin_capability_view(
    provider_id: str,
    model_id: str,
    *,
    base_model: CatalogModel,
    official: bool,
    overrides: Mapping[str, object],
) -> CapabilityView:
    """内置供应商模型的参数视图。官方收录的模型只读；用户添加的可以修改该媒体类型的全部维度。"""
    media_type = base_model.media_type
    base = _catalog_model_facts(provider_id, model_id, base_model)
    if official:
        return CapabilityView(media_type, base=base)
    editable = set(overridable_keys(media_type))
    if media_type == "image":
        implemented = _image_channel(provider_id)
        editable = {key for key in editable if _IMAGE_CAPABILITY_BY_KEY[key] in implemented}
    return CapabilityView(
        media_type, editable=_ordered(editable), base=base, overrides=_normalized_overrides(overrides)
    )


def user_added_runtime_overrides(media_type: str, raw: object | None, *, model_id: str = "") -> dict[str, object]:
    """用户添加的模型装入目录查询时采用的能力覆盖：该媒体类型的维度里值合法的那些，其余丢弃并告警。"""
    if not isinstance(raw, Mapping):
        return {}
    applied = model_fact_overrides(raw, media_type, model_id=model_id)
    if media_type == "video":
        for key, value in raw.items():
            if key not in CHANNEL_FACT_OVERRIDE_KEYS:
                continue
            if capability_value_matches(value, CAPABILITY_OVERRIDE_FIELDS[key]):
                applied[key] = value
            else:
                logger.warning("忽略 %s 的能力覆盖 %s=%r：值不合法，该维度回到调用通道的声明", model_id, key, value)
    return applied


class OverrideRejected(Exception):
    """能力覆盖写入被拒绝。``code`` 是错误文案的 i18n key，``params`` 是文案参数。"""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


def check_user_added_overrides(
    provider_id: str, model_id: str, media_type: str, overrides: Mapping[str, object] | None
) -> dict[str, object] | None:
    """内置供应商下用户添加的模型的能力覆盖写入校验，通过时返回要存下的覆盖（空覆盖为 ``None``）。

    与自定义供应商的写入校验同一组规则：维度须属于该媒体类型，值的形状与维度一致；图片的文生图、图生图覆盖为
    开启时调用通道须实现了它；音频两维合起来须有意义（不足的一维取调用通道的声明）。
    """
    if not overrides:
        return None
    applicable = overridable_keys(media_type)
    for key, value in overrides.items():
        if key not in applicable:
            raise OverrideRejected(
                "capability_override_not_applicable",
                model_id=model_id,
                endpoint=provider_id,
                media_type=media_type,
                capability=key,
            )
        if key in CHANNEL_FACT_OVERRIDE_KEYS:
            expected = CAPABILITY_OVERRIDE_FIELDS[key]
            valid, expected_name = capability_value_matches(value, expected), capability_type_name(expected)
        else:
            valid, expected_name = model_fact_value_matches(key, value), model_fact_type_name(key)
        if not valid:
            raise OverrideRejected(
                "capability_override_invalid_value", model_id=model_id, capability=key, expected=expected_name
            )
        image_capability = _IMAGE_CAPABILITY_BY_KEY.get(key)
        if image_capability is not None and value is True and image_capability not in _image_channel(provider_id):
            raise OverrideRejected(
                "capability_override_image_unsupported", model_id=model_id, endpoint=provider_id, capability=key
            )
    if any(key in overrides for key in AUDIO_OVERRIDE_KEYS):
        try:
            declared = declared_builtin_video_capabilities(provider_id, model_id)
        except ValueError:
            declared = VideoCapabilities()
        mode = overrides.get("reference_audio_mode", declared.reference_audio_mode)
        count = overrides.get("max_reference_audio_count", declared.max_reference_audio_count)
        if not audio_capability_pair_is_coherent(mode=mode, count=int(count) if isinstance(count, int) else 0):
            raise OverrideRejected("capability_override_audio_pair_incoherent", model_id=model_id)
    return dict(overrides)
