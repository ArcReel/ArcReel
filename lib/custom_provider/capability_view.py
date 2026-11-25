"""供应商模型 Sheet 上「这个供应商支持的参数」：每个维度的原值、能力覆盖与生效值（docs/adr/0104、0106）。

原值是没有能力覆盖时运行时采用的值：模型事实取对应条目，对应不上条目时取运行时回落（可选时长取按名字推断的
预设，其余维度未收录，记为 ``None``）；通道事实取调用通道或调用端点的声明。生效值是原值叠加能力覆盖的结果，
与执行层读到的一致。

图片的参考图上限先两侧求值，再由能力覆盖整维替换（``lib.backends.image_capabilities.image_reference_limit``），
按生效的图生图求值：``None`` 为未收录，``0`` 为不接受参考图（条目记为 0 张，或图生图不在生效能力里）。图生图的原值
读同一个求值结果：模型事实与调用通道都允许、且参考图上限接受参考图，与任务类型桶的候选、执行时的校验一致；条目把
参考图上限记为 0 张时图生图为不支持。文生图的原值同样取模型事实与调用通道的交集。

视频的输入模式与数量上限（文生视频、首帧、尾帧、参考图、参考音频）的原值同样是调用通道声明与模型条目两侧求值的
结果（``lib.backends.video_capabilities``），数量上限 ``None`` 为未收录、``0`` 为不接受。ComfyUI 端点不读条目，
输入能力只由节点绑定决定；图片同样展示文生图、图生图与参考图上限三维，只读。

只有用户负责的供应商模型可以修改：自定义供应商的模型，以及内置供应商下用户添加的模型。ComfyUI 端点只开放视频
的可选时长，且端点驱动不了时长时这一维也不开放（docs/adr/0082）。图片的文生图、图生图只开放调用通道实现了的
那一维：通道没实现的能力覆盖为开启也打不开。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from arcreel_catalog_core import DurationConstraints, VideoFacts
from arcreel_market_core.endpoint_definition import COMFYUI_KIND
from arcreel_market_core.video_backend_contract import VideoCapabilities
from lib.backends.backend_assembly.specs import (
    builtin_video_snapshot,
    builtin_video_snapshot_from_view,
)
from lib.backends.image_capabilities import (
    builtin_image_snapshot,
    builtin_image_snapshot_from_view,
    resolve_image_model,
)
from lib.backends.video_capabilities import resolve_video_model, video_endpoint
from lib.capabilities.kernel import (
    TEXT_ENDPOINT,
    AudioProtocol,
    CapabilitySnapshot,
    Endpoint,
    FixedDuration,
    InvalidOverrides,
)
from lib.capabilities.override_schema import (
    LEGACY_AUDIO_KEYS,
    OVERRIDE_KEYS,
    expected_type_name,
    is_legacy_audio_mode,
    normalized_value,
    valid_value,
)
from lib.catalog.query import CatalogModel, ProviderModelView
from lib.custom_provider.capabilities import custom_image_snapshot, custom_video_snapshot
from lib.custom_provider.endpoints import EndpointSpec
from lib.custom_provider.model_facts import (
    builtin_text_snapshot,
    builtin_text_snapshot_from_view,
    model_fact_overrides,
    model_fact_type_name,
    model_fact_value_matches,
    text_capability_snapshot,
    video_model_facts,
)

logger = logging.getLogger(__name__)

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
    "audio_switch_controllable",
    "reference_route_audio_switch_controllable",
    "has_audio_track",
    "reference_route_has_audio_track",
)


@dataclass(frozen=True)
class CapabilityView:
    """一个供应商模型的「这个供应商支持的参数」。

    - ``editable``：可以修改的维度，按界面顺序；官方收录的模型为空。
    - ``base``：每个维度的原值（没有能力覆盖时运行时采用的值），``None`` 表示未收录。
    - ``overrides``：生效的能力覆盖（稀疏）。
    - ``effective``：原值叠加覆盖后的生效值。
    - ``displayed``：界面要显示的维度（``editable`` 与 ``base`` 的并集），按界面顺序。
    """

    media_type: str
    editable: tuple[str, ...] = ()
    base: dict[str, object] = field(default_factory=dict)
    overrides: dict[str, object] = field(default_factory=dict)
    effective_values: dict[str, object] | None = None

    @property
    def effective(self) -> dict[str, object]:
        return self.effective_values if self.effective_values is not None else {**self.base, **self.overrides}

    @property
    def displayed(self) -> tuple[str, ...]:
        return _ordered({*self.editable, *self.base})


def overridable_keys(media_type: str) -> frozenset[str]:
    """该媒体类型可以覆盖的全部维度：模型事实，视频另有通道事实。"""
    return OVERRIDE_KEYS.get(media_type, frozenset())


def _ordered(keys: frozenset[str] | set[str]) -> tuple[str, ...]:
    return tuple(key for key in OVERRIDE_KEY_ORDER if key in keys)


def _text_view(snapshot: CapabilitySnapshot) -> CapabilityView:
    def values(data: Mapping[str, object]) -> dict[str, object]:
        return {
            "image_input": data.get("image_input"),
            "structured_output": data.get("structured_output"),
            "max_output_tokens": data.get("max_output_tokens"),
        }

    return CapabilityView(
        "text",
        editable=_ordered(snapshot.editable),
        base=values(snapshot.base),
        effective_values=values(snapshot.effective),
        overrides=snapshot.override_dict(),
    )


def _video_view(snapshot: CapabilitySnapshot) -> CapabilityView:
    def values(data: Mapping) -> dict[str, object]:
        audio = {
            "audio_switch_controllable": snapshot.primary_protocol == AudioProtocol.SWITCH,
            "reference_route_audio_switch_controllable": snapshot.reference_protocol == AudioProtocol.SWITCH,
            "has_audio_track": data["has_audio_track"],
            "reference_route_has_audio_track": data["reference_route_has_audio_track"],
        }
        durations = list(data.get("durations", ()))
        if snapshot.endpoint.binding_owned:
            return {"durations": durations, "duration_fixed": isinstance(snapshot.duration, FixedDuration), **audio}
        constraints = data.get("duration_constraints", {})
        return {
            **{k: data.get(k) for k in OVERRIDE_KEYS["video"]},
            **audio,
            "durations": durations,
            "resolutions": list(data.get("resolutions") or ()) or None,
            "duration_constraints": {
                "by_resolution": {k: list(v) for k, v in constraints.get("by_resolution", {}).items()},
                "with_reference_images": list(constraints.get("with_reference_images", ())),
                **(
                    {"with_end_frame": list(constraints["with_end_frame"])} if constraints.get("with_end_frame") else {}
                ),
            },
        }

    return CapabilityView(
        "video",
        editable=_ordered(snapshot.editable),
        base=values(snapshot.base),
        effective_values=values(snapshot.effective),
        overrides=snapshot.override_dict(),
    )


def _image_view(snapshot: CapabilitySnapshot) -> CapabilityView:
    def values(data: Mapping[str, object]) -> dict[str, object]:
        return {
            "text_to_image": data["text_to_image"],
            "image_to_image": data["image_to_image"],
            "max_reference_images": data["max_reference_images"],
        }

    return CapabilityView(
        "image",
        editable=_ordered(snapshot.editable),
        base=values(snapshot.base),
        effective_values=values(snapshot.effective),
        overrides=snapshot.override_dict(),
    )


def _catalog_video_facts(base_model: CatalogModel) -> VideoFacts:
    return VideoFacts(
        durations=tuple(base_model.supported_durations),
        resolutions=tuple(base_model.resolutions),
        duration_constraints=DurationConstraints(
            by_resolution={k: tuple(v) for k, v in base_model.duration_resolution_constraints.items()},
            with_reference_images=tuple(base_model.reference_image_durations),
            with_end_frame=tuple(base_model.end_frame_durations),
        ),
        max_reference_images=base_model.max_reference_images,
    )


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
    if media_type == "video":
        raw = raw_overrides if isinstance(raw_overrides, Mapping) else {}
        try:
            snapshot = custom_video_snapshot(
                endpoint=endpoint, model_id=model_id, endpoint_spec=spec, entry_binding=entry_binding, overrides=raw
            )
        except ValueError:
            logger.warning("无法判定系统能力: endpoint=%r model_id=%r", endpoint, model_id)
            video = video_model_facts(model_id, None, entry_binding=entry_binding)
            snapshot = resolve_video_model(
                video_endpoint(VideoCapabilities()),
                VideoFacts(
                    durations=tuple(video.durations),
                    resolutions=tuple(video.resolutions or ()),
                    duration_constraints=video.duration_constraints,
                ),
                raw,
                model_id=model_id,
            )
        return _video_view(snapshot)
    if media_type == "image":
        raw = raw_overrides if isinstance(raw_overrides, Mapping) else {}
        try:
            snapshot = custom_image_snapshot(
                endpoint=endpoint, model_id=model_id, endpoint_spec=spec, entry_binding=entry_binding, overrides=raw
            )
        except ValueError:
            logger.warning("无法判定图片能力: endpoint=%r model_id=%r", endpoint, model_id)
            snapshot = resolve_image_model(Endpoint("image"), None, raw, model_id=model_id)
        return _image_view(snapshot)
    if spec.kind == COMFYUI_KIND:
        return CapabilityView(media_type)

    base: dict[str, object] = {}
    editable = set(overridable_keys(media_type))
    match media_type:
        case "text":
            snapshot = text_capability_snapshot(
                model_id,
                raw_overrides if raw_overrides is not None else applied_overrides,
                entry_binding=entry_binding,
            )
            return _text_view(snapshot)
        case _:
            editable = set()
    overrides = dict(applied_overrides or {})
    return CapabilityView(media_type, editable=_ordered(editable), base=base, overrides=overrides)


def _catalog_model_facts(model: CatalogModel) -> dict[str, object]:
    """内置供应商模型的目录信息（未叠加覆盖时即运行时采用的值）按维度展开；图片三维要与调用通道两侧求值，
    由 :func:`builtin_capability_view` 给出。"""
    caps = set(model.capabilities)
    match model.media_type:
        case "text":
            return {
                "image_input": "vision" in caps,
                "structured_output": "structured_output" in caps,
                "max_output_tokens": model.max_output_tokens,
            }
        case _:
            return {}


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
    if media_type == "video":
        try:
            snapshot = builtin_video_snapshot(
                provider_id, model_id, overrides=overrides, official=official, base_model=base_model
            )
        except ValueError:
            logger.warning("无法读取内置视频能力: provider=%r model_id=%r", provider_id, model_id)
            snapshot = resolve_video_model(
                video_endpoint(VideoCapabilities()),
                _catalog_video_facts(base_model),
                overrides,
                provider_id=provider_id,
                model_id=model_id,
                official=official,
            )
        return _video_view(snapshot)
    if media_type == "text":
        snapshot = builtin_text_snapshot(
            provider_id,
            model_id,
            base_model=base_model,
            official=official,
            overrides={} if official else overrides,
        )
        return _text_view(snapshot)
    if media_type == "image":
        try:
            snapshot = builtin_image_snapshot(
                provider_id, model_id, overrides=overrides, official=official, base_model=base_model
            )
        except ValueError:
            logger.warning("无法读取内置图片能力: provider=%r model_id=%r", provider_id, model_id)
            snapshot = resolve_image_model(
                Endpoint("image"),
                {
                    "text_to_image": "text_to_image" in base_model.capabilities,
                    "image_to_image": "image_to_image" in base_model.capabilities,
                    "max_reference_images": base_model.max_reference_images,
                    "resolutions": tuple(base_model.resolutions),
                },
                overrides,
                provider_id=provider_id,
                model_id=model_id,
                official=official,
            )
        return _image_view(snapshot)
    base = _catalog_model_facts(base_model)
    editable = set(overridable_keys(media_type)) if not official else set()
    if official:
        return CapabilityView(media_type, base=base)
    return CapabilityView(media_type, editable=_ordered(editable), base=base, overrides=dict(overrides))


def user_added_runtime_overrides(media_type: str, raw: object | None, *, model_id: str = "") -> dict[str, object]:
    """用户添加的模型装入目录查询时采用的能力覆盖：该媒体类型的维度里值合法的那些，其余丢弃并告警。"""
    if not isinstance(raw, Mapping):
        return {}
    if media_type in ("video", "image"):
        applied: dict[str, object] = {}
        allowed = OVERRIDE_KEYS[media_type]
        for key, value in raw.items():
            if (key in allowed and valid_value(key, value)) or (
                media_type == "video" and key in LEGACY_AUDIO_KEYS and is_legacy_audio_mode(value)
            ):
                applied[key] = normalized_value(key, value)
            else:
                logger.warning("忽略 %s 的能力覆盖 %s=%r：值不合法或维度不适用", model_id, key, value)
        return applied
    if media_type == "text":
        applied_text = TEXT_ENDPOINT.filter_overrides(raw, {})
        for key, value in raw.items():
            if key not in applied_text:
                logger.warning("忽略 %s 的能力覆盖 %s=%r：值不合法或维度不适用", model_id, key, value)
        return applied_text
    return model_fact_overrides(raw, media_type, model_id=model_id)


class OverrideRejected(Exception):
    """能力覆盖写入被拒绝。``code`` 是错误文案的 i18n key，``params`` 是文案参数。"""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


def validate_snapshot_overrides(
    snapshot: CapabilitySnapshot,
    overrides: Mapping[str, object],
    *,
    model_id: str,
    endpoint: str,
    carried: Mapping[str, object] | None = None,
    explicit: bool = True,
) -> dict[str, object] | None:
    """用纯能力快照校验模型能力覆盖，失败时抛出带具体本地化原因的 :class:`OverrideRejected`。"""
    effective = (
        overrides if (explicit or carried) else snapshot.endpoint.filter_overrides(overrides, snapshot.model_facts)
    )
    try:
        return snapshot.validate_overrides(effective, carried=carried) or None
    except InvalidOverrides as exc:
        raise _override_rejected_from_invalid(
            exc, model_id=model_id, endpoint=endpoint, media_type=snapshot.media
        ) from exc
    except ValueError as exc:
        raise OverrideRejected(
            "capability_override_not_applicable",
            model_id=model_id,
            endpoint=endpoint,
            media_type=snapshot.media,
            capability=str(exc),
        ) from exc


def validate_video_overrides(
    snapshot: CapabilitySnapshot,
    overrides: Mapping[str, object],
    *,
    model_id: str,
    endpoint: str,
    carried: Mapping[str, object] | None = None,
    explicit: bool = True,
) -> dict[str, object] | None:
    """用纯能力快照校验视频模型能力覆盖，失败时抛出带具体本地化原因的 :class:`OverrideRejected`。"""
    return validate_snapshot_overrides(
        snapshot, overrides, model_id=model_id, endpoint=endpoint, carried=carried, explicit=explicit
    )


def validate_text_overrides(
    snapshot: CapabilitySnapshot,
    overrides: Mapping[str, object],
    *,
    model_id: str,
    endpoint: str,
    carried: Mapping[str, object] | None = None,
    explicit: bool = True,
) -> dict[str, object] | None:
    """用纯能力快照校验文本模型能力覆盖，失败时抛出带具体本地化原因的 :class:`OverrideRejected`。"""
    return validate_snapshot_overrides(
        snapshot, overrides, model_id=model_id, endpoint=endpoint, carried=carried, explicit=explicit
    )


def _override_rejected_from_invalid(
    exc: InvalidOverrides, *, model_id: str, endpoint: str, media_type: str = "video"
) -> OverrideRejected:
    ordered = sorted(exc.keys)
    by_reason = {
        reason: next((k for k in ordered if exc.reasons.get(k) == reason), None) for reason in set(exc.reasons.values())
    }
    if "official" in by_reason:
        return OverrideRejected("builtin_model_official_settings", model=model_id)
    if "audio_pair_incoherent" in by_reason:
        return OverrideRejected("capability_override_audio_pair_incoherent", model_id=model_id)
    if (image_key := by_reason.get("image_unsupported")) is not None:
        return OverrideRejected(
            "capability_override_image_unsupported", model_id=model_id, endpoint=endpoint, capability=image_key
        )
    if "last_frame_unsupported" in by_reason:
        return OverrideRejected("capability_override_last_frame_unsupported", model_id=model_id, endpoint=endpoint)
    if "reference_audio_unsupported" in by_reason:
        return OverrideRejected("capability_override_reference_audio_unsupported", model_id=model_id, endpoint=endpoint)
    if (invalid_key := by_reason.get("invalid_value")) is not None:
        return OverrideRejected(
            "capability_override_invalid_value",
            model_id=model_id,
            capability=invalid_key,
            expected=expected_type_name(invalid_key),
        )
    key = ordered[0] if ordered else ""
    return OverrideRejected(
        "capability_override_not_applicable",
        model_id=model_id,
        endpoint=endpoint,
        media_type=media_type,
        capability=key,
    )


def check_user_added_overrides(
    provider_id: str,
    model_id: str,
    view: ProviderModelView,
    overrides: Mapping[str, object] | None,
    *,
    endpoint: str | None = None,
    carried: Mapping[str, object] | None = None,
    explicit: bool = True,
) -> dict[str, object] | None:
    """内置供应商下用户添加的模型的能力覆盖写入校验，通过时返回要存下的覆盖（空覆盖为 ``None``）。

    与自定义供应商的写入校验同一组规则：维度须属于该媒体类型，值的形状与维度一致；图片的文生图、图生图覆盖为
    开启时调用通道须实现了它；音频两维合起来须有意义（不足的一维取调用通道的声明）。调用通道取模型挂接的内置调用
    端点，``endpoint`` 给出时取它（同一次保存里换了端点）。
    """
    media_type = view.model.media_type
    if not overrides:
        return None
    if media_type in ("video", "image"):
        try:
            if media_type == "video":
                snapshot = builtin_video_snapshot_from_view(
                    provider_id, model_id, view, endpoint=endpoint, overrides={}, always_bill_audio=False
                )
            else:
                snapshot = builtin_image_snapshot_from_view(
                    provider_id, model_id, view, endpoint=endpoint, overrides={}
                )
        except ValueError as exc:
            raise OverrideRejected(
                "capability_override_not_applicable",
                model_id=model_id,
                endpoint=endpoint or provider_id,
                media_type=media_type,
                capability=str(exc),
            ) from exc
        return validate_snapshot_overrides(
            snapshot,
            overrides,
            model_id=model_id,
            endpoint=endpoint or provider_id,
            carried=carried,
            explicit=explicit,
        )
    if media_type == "text":
        snapshot = builtin_text_snapshot_from_view(provider_id, model_id, view, overrides={})
        return validate_text_overrides(
            snapshot,
            overrides,
            model_id=model_id,
            endpoint=endpoint or provider_id,
            carried=carried,
            explicit=explicit,
        )
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
        if not model_fact_value_matches(key, value):
            raise OverrideRejected(
                "capability_override_invalid_value",
                model_id=model_id,
                capability=key,
                expected=model_fact_type_name(key),
            )
    return dict(overrides)
