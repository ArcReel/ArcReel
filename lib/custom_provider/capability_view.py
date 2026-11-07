"""Provider-model Sheet supported parameters: bases, overrides, and effective dimensions (docs/adr/0104, 0106).

Bases are runtime values without overrides: matching-entry facts or runtime fallbacks (name-inferred durations,
other unlisted dimensions ``None``); channel facts follow backend/endpoint declarations. Effective values compose bases/overrides,
matching execution.

Image reference limits jointly evaluate both sides before whole-dimension overrides (``lib.backends.image_capabilities.image_reference_limit``),
using effective image-to-image. ``None`` means unlisted; ``0`` rejects references via zero entry limits or unavailable effective I2I.
I2I bases share evaluation with bucket candidates/execution: model/channel support plus reference acceptance;
zero entry limits disable I2I. T2I likewise intersects model/channel facts.

Video input/count bases (text, first/last frames, references/audio) likewise jointly evaluate channel declarations/entries
(``lib.backends.video_capabilities``); ``None`` counts mean unlisted, ``0`` unsupported. ComfyUI ignores entries,
deriving inputs only from bindings. Images show readonly T2I/I2I/reference-max dimensions too.

Only user-owned models are editable: custom models and user-added built-in models. ComfyUI exposes video durations
only, and only when endpoints can drive duration (docs/adr/0082). Image T2I/I2I expose only implemented
channel dimensions: overrides cannot enable unimplemented capabilities.

Also owns write validation/load filtering for user-added built-in overrides; custom validation lives in
``server/routers/custom_providers.py``, sharing value-shape predicates :func:`model_fact_value_matches` /
:func:`capability_value_matches`.
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
from lib.backends.backend_assembly.specs import (
    declared_builtin_video_capabilities,
    two_sided_builtin_video_capabilities,
)
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_capabilities import (
    builtin_channel_capabilities,
    builtin_channel_reference_limit,
    image_reference_limit,
    image_reference_override,
)
from lib.catalog.query import CatalogModel
from lib.custom_provider.capabilities import (
    AUDIO_OVERRIDE_KEYS,
    CAPABILITY_OVERRIDE_FIELDS,
    CHANNEL_FACT_OVERRIDE_KEYS,
    capability_type_name,
    capability_value_matches,
    two_sided_video_capabilities,
)
from lib.custom_provider.endpoints import EndpointSpec, endpoint_reference_image_limit
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

#: UI order: model facts first, duration constraints immediately after durations, then channel facts.
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

#: Image override dimensions → required channel capabilities; unimplemented dimensions readonly. Reference maxima matter only for I2I.
_IMAGE_EDITABLE_REQUIRES: Mapping[str, ImageCapability] = {
    **_IMAGE_CAPABILITY_BY_KEY,
    "max_reference_images": ImageCapability.IMAGE_TO_IMAGE,
}


@dataclass(frozen=True)
class CapabilityView:
    """One model's provider-supported parameters.

    - ``editable``: editable dimensions in UI order; empty for official models.
    - ``base``: runtime dimensions without overrides; ``None`` means unlisted.
    - ``overrides``: sparse effective overrides.
    - ``effective``: bases plus overrides.
    - ``displayed``: UI dimensions, ``editable`` / ``base`` union in UI order.
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
    """All overridable dimensions per media: model facts, plus video channel facts."""
    keys = MODEL_FACT_OVERRIDE_KEYS.get(media_type, frozenset())
    return keys | CHANNEL_FACT_OVERRIDE_KEYS if media_type == "video" else keys


def override_value_check(media_type: str, key: str, value: object) -> tuple[bool, str]:
    """Whether values replace whole dimensions, plus expected shapes for rejected-write wording.

    Video channel facts validate through ``VideoCapabilities`` fields, others through model facts; image reference maxima share video names
    but use model-fact validation. Custom/built-in-user write validation shares this predicate.
    """
    if media_type == "video" and key in CHANNEL_FACT_OVERRIDE_KEYS:
        expected = CAPABILITY_OVERRIDE_FIELDS[key]
        return capability_value_matches(value, expected), capability_type_name(expected)
    return model_fact_value_matches(key, value), model_fact_type_name(key)


def _ordered(keys: frozenset[str] | set[str]) -> tuple[str, ...]:
    return tuple(key for key in OVERRIDE_KEY_ORDER if key in keys)


def _constraints_json(constraints: DurationConstraints) -> dict[str, object]:
    return {
        "by_resolution": {res: list(allowed) for res, allowed in constraints.by_resolution.items()},
        "with_reference_images": list(constraints.with_reference_images),
    }


def _channel_json(caps: VideoCapabilities, *, audio_signal: bool) -> dict[str, object]:
    """Channel bases. False ``audio_signal`` without custom audio overrides assumes audible/toggleable tracks."""
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
    """Echo effective override shapes: deduplicated/sorted durations, lowercase resolution keys in constraints."""
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
# Custom-provider models.
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
    """Custom parameter views; ``applied_overrides`` is write-rule-filtered values used for echoing."""
    media_type = spec.media_type
    if spec.kind == COMFYUI_KIND:
        if media_type == "image":
            # Bindings determine everything: no reference slots means unsupported, otherwise maxima equal slots, ignoring entries.
            return CapabilityView(
                media_type,
                base=_image_base(
                    implemented=spec.image_capabilities or frozenset(),
                    text_to_image=True,
                    image_to_image=True,
                    entry_limit=None,
                    channel_limit=endpoint_reference_image_limit(spec),
                ),
            )
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
            return _image_view(
                implemented=implemented,
                text_to_image=image.text_to_image is not False,
                image_to_image=image.image_to_image is not False,
                entry_limit=image.max_reference_images,
                channel_limit=endpoint_reference_image_limit(spec),
                editable=editable,
                overrides=applied_overrides or {},
            )
        case "video":
            video = video_model_facts(model_id, None, entry_binding=entry_binding)
            base = {
                "durations": list(video.durations),
                "duration_constraints": _constraints_json(video.duration_constraints),
                "resolutions": list(video.resolutions) if video.resolutions is not None else None,
            }
            try:
                caps = two_sided_video_capabilities(
                    endpoint=endpoint, model_id=model_id, endpoint_spec=spec, entry_binding=entry_binding
                )
            except ValueError:
                logger.warning("无法判定系统能力: endpoint=%r model_id=%r", endpoint, model_id)
                caps = VideoCapabilities()
            base |= _channel_json(caps, audio_signal=False)
        case _:
            editable = set()
    overrides = _normalized_overrides(applied_overrides or {})
    return CapabilityView(media_type, editable=_ordered(editable), base=base, overrides=overrides)


# ---------------------------------------------------------------------------
# Built-in-provider models.
# ---------------------------------------------------------------------------


def _catalog_model_facts(provider_id: str, model_id: str, model: CatalogModel) -> dict[str, object]:
    """Expand built-in catalog information by dimension, runtime bases without overrides. Image dimensions require joint channel evaluation,
    supplied by :func:`builtin_capability_view`."""
    caps = set(model.capabilities)
    match model.media_type:
        case "text":
            return {
                "image_input": "vision" in caps,
                "structured_output": "structured_output" in caps,
                "max_output_tokens": model.max_output_tokens,
            }
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
                declared = two_sided_builtin_video_capabilities(
                    declared_builtin_video_capabilities(provider_id, model_id), provider_id, model_id
                )
            except ValueError:
                logger.warning("无法读取内置视频能力: provider=%r model_id=%r", provider_id, model_id)
                declared = VideoCapabilities()
            return facts | _channel_json(declared, audio_signal=True)
        case _:
            return {}


def _image_base(
    *,
    implemented: frozenset[ImageCapability],
    text_to_image: bool,
    image_to_image: bool,
    entry_limit: int | None,
    channel_limit: int | None,
    reference_override: int | None = None,
) -> dict[str, object]:
    """Three image bases sharing bucket-candidate/execution ``image_reference_limit`` evaluation.

    ``text_to_image`` / ``image_to_image`` are model-side facts, allowing unmatched entries, intersected with channel
    ``implemented`` capabilities. I2I also requires reference acceptance, disabled by zero entry limits. Evaluate reference limits under effective I2I;
    ``reference_override`` replaces whole limits after joint evaluation.
    """

    reference_limit = image_reference_limit(
        capabilities=implemented if image_to_image else frozenset(),
        entry_limit=entry_limit,
        channel_limit=channel_limit,
        override=reference_override,
    )
    return {
        "text_to_image": text_to_image and ImageCapability.TEXT_TO_IMAGE in implemented,
        "image_to_image": reference_limit.accepts_input,
        "max_reference_images": reference_limit.to_json(),
    }


def _image_view(
    *,
    implemented: frozenset[ImageCapability],
    text_to_image: bool,
    image_to_image: bool,
    entry_limit: int | None,
    channel_limit: int | None,
    editable: set[str],
    overrides: Mapping[str, object],
) -> CapabilityView:
    editable = {key for key in editable if _IMAGE_EDITABLE_REQUIRES[key] in implemented}
    base = _image_base(
        implemented=implemented,
        text_to_image=text_to_image,
        image_to_image=image_to_image,
        entry_limit=entry_limit,
        channel_limit=channel_limit,
    )
    effective = _image_base(
        implemented=implemented,
        text_to_image=overrides.get("text_to_image", text_to_image) is True,
        image_to_image=overrides.get("image_to_image", image_to_image) is True,
        entry_limit=entry_limit,
        channel_limit=channel_limit,
        reference_override=image_reference_override(overrides),
    )
    return CapabilityView(
        "image", editable=_ordered(editable), base=base, overrides=dict(overrides), effective_values=effective
    )


def _image_channel(provider_id: str, model_id: str, endpoint: str | None = None) -> frozenset[ImageCapability]:
    try:
        return builtin_channel_capabilities(provider_id, model_id, endpoint=endpoint)
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
    """Built-in parameter views: official readonly, user additions editable across all media dimensions."""
    media_type = base_model.media_type
    base = _catalog_model_facts(provider_id, model_id, base_model)
    editable = set(overridable_keys(media_type)) if not official else set()
    if media_type == "image":
        implemented = _image_channel(provider_id, model_id)
        try:
            channel_limit = builtin_channel_reference_limit(provider_id, model_id)
        except ValueError:
            channel_limit = None
        return _image_view(
            implemented=implemented,
            text_to_image="text_to_image" in base_model.capabilities,
            image_to_image="image_to_image" in base_model.capabilities,
            entry_limit=base_model.max_reference_images,
            channel_limit=channel_limit,
            editable=editable,
            overrides={} if official else overrides,
        )
    if official:
        return CapabilityView(media_type, base=base)
    return CapabilityView(
        media_type, editable=_ordered(editable), base=base, overrides=_normalized_overrides(overrides)
    )


def user_added_runtime_overrides(media_type: str, raw: object | None, *, model_id: str = "") -> dict[str, object]:
    """Valid media-specific user-added overrides loaded into catalogs; discard/warn on all others."""
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
    """Rejected override writes. ``code`` is i18n keys, ``params`` wording parameters."""

    def __init__(self, code: str, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.params = params


def check_user_added_overrides(
    provider_id: str,
    model_id: str,
    media_type: str,
    overrides: Mapping[str, object] | None,
    *,
    endpoint: str | None = None,
) -> dict[str, object] | None:
    """Validate user-added built-in overrides, returning persisted values, ``None`` for empty overrides.

    Share custom-write rules: dimensions belong to media, values match shapes; enabling image T2I/I2I
    requires implementation; paired audio dimensions must be meaningful, taking missing dimensions from channels. Use attached built-in
    endpoints, or supplied ``endpoint`` when changing them in the same save.
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
        valid, expected_name = override_value_check(media_type, key, value)
        if not valid:
            raise OverrideRejected(
                "capability_override_invalid_value", model_id=model_id, capability=key, expected=expected_name
            )
        image_capability = _IMAGE_CAPABILITY_BY_KEY.get(key)
        if (
            image_capability is not None
            and value is True
            and image_capability not in _image_channel(provider_id, model_id, endpoint)
        ):
            raise OverrideRejected(
                "capability_override_image_unsupported", model_id=model_id, endpoint=provider_id, capability=key
            )
    if any(key in overrides for key in AUDIO_OVERRIDE_KEYS):
        try:
            base = two_sided_builtin_video_capabilities(
                declared_builtin_video_capabilities(provider_id, model_id, endpoint=endpoint), provider_id, model_id
            )
        except ValueError:
            base = VideoCapabilities()
        mode = overrides.get("reference_audio_mode", base.reference_audio_mode)
        count = overrides.get("max_reference_audio_count", base.max_reference_audio_count)
        if count is not None and not isinstance(count, int):
            count = 0
        if not audio_capability_pair_is_coherent(mode=mode, count=count):
            raise OverrideRejected("capability_override_audio_pair_incoherent", model_id=model_id)
    return dict(overrides)
