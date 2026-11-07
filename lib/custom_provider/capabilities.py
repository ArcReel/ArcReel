"""Sole custom-video capability composition: jointly evaluate endpoint system declarations/model entries ⊕ user overrides.

System values are endpoint channel facts; jointly evaluate matching-entry model inputs
(:func:`lib.backends.video_capabilities.effective_video_capabilities`), then replace whole overridden dimensions.
ComfyUI derives capabilities from bindings alone, ignoring entries (docs/adr/0082, 0104).

All effective-capability consumers must use these semantics, never independently merge overrides, avoiding UI/execution
contradictions. Factories inject results into execution backends. System declarations never persist and follow registry
upgrades automatically; sparse user `CustomProviderModel.capability_overrides` keys absent means
follow system values.

The same dictionary contains model facts such as durations/T2I (:mod:`lib.custom_provider.model_facts`).
Video model-fact keys do not overlap channel keys and are skipped here; image reference maxima share key names.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import fields, replace
from enum import Enum
from types import UnionType
from typing import TYPE_CHECKING, get_args, get_type_hints

from arcreel_market_core.endpoint_definition import COMFYUI_KIND, requires_image_input
from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoCapabilities,
    VideoRoute,
    audio_capability_pair_is_coherent,
)
from lib.custom_provider.model_facts import ALL_MODEL_FACT_OVERRIDE_KEYS, MODEL_FACT_OVERRIDE_KEYS, model_entry

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from lib.custom_provider.endpoints import EndpointSpec


# Override keys → value types reflected directly from VideoCapabilities dataclasses; new dimensions enter schemas
# automatically without DB migrations or handwritten drift.
# get_type_hints, not field.type: base.py uses PEP563, leaving annotation strings there.
_CAPABILITY_TYPE_HINTS = get_type_hints(VideoCapabilities)
# Reference-image/audio maxima may be ``None`` when both sides undeclared, but user overrides specify concrete
# integer maxima; ``0`` rejects input. Remove overrides to return to unlisted values.
_INTEGER_ONLY_OVERRIDES = frozenset({"max_reference_images", "max_reference_audio_count"})
CAPABILITY_OVERRIDE_FIELDS: dict[str, object] = {
    f.name: int if f.name in _INTEGER_ONLY_OVERRIDES else _CAPABILITY_TYPE_HINTS[f.name]
    for f in fields(VideoCapabilities)
}

#: Exposed channel overrides (docs/adr/0104): input modes/reference-image/audio maxima/prompt limits/
#: audio forms. ``reference_audio_per_image`` / ``first_frame_ratio_adaptive_only`` define request shapes,
#: and changing them cannot change channel construction, so they remain unexposed.
CHANNEL_FACT_OVERRIDE_KEYS: frozenset[str] = frozenset(
    {
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
    }
)

#: Paired audio dimensions derive forms only with user overrides (:func:`overridden_audio_track`).
AUDIO_TRACK_OVERRIDE_KEYS: frozenset[str] = frozenset({"audio_track", "reference_route_audio_track"})


def overridden_audio_track(
    caps: VideoCapabilities, applied_overrides: Mapping[str, object], route: VideoRoute
) -> str | None:
    """Output-audio forms for this custom execution route; ``None`` without user audio-form overrides.

    Ignore name-based channel audio derivation: users supply unverified upstream models, so false always-silent classification
    would reject valid configurations before enqueueing. Without overrides, callers assume audible/toggleable. Return ``VideoAudioMode`` literals.
    """
    if not AUDIO_TRACK_OVERRIDE_KEYS & applied_overrides.keys():
        return None
    return caps.audio_track_for_route(route).value


if (
    CHANNEL_FACT_OVERRIDE_KEYS - CAPABILITY_OVERRIDE_FIELDS.keys()
    or CHANNEL_FACT_OVERRIDE_KEYS & (MODEL_FACT_OVERRIDE_KEYS["video"])
):
    raise RuntimeError("通道事实覆盖键须是 VideoCapabilities 字段，且不与视频模型事实覆盖键重名")


def _unwrap_optional(expected: object) -> tuple[object, bool]:
    """Unwrap ``T | None`` into ``(T, True)``; nonoptional annotations return ``(expected, False)`` unchanged.

    Override schemas reflect dataclasses. Optional fields such as
    ``max_reference_audio_total_seconds: float | None`` yield ``types.UnionType``,
    not ``type``, without ``__name__`` or suitability for concrete-type dispatch. All schema consumers
    pass here, avoiding per-site changes for new optional dimensions.
    """
    if not isinstance(expected, UnionType):
        return expected, False
    members = [arg for arg in get_args(expected) if arg is not type(None)]
    if len(members) != 1:
        return expected, True
    return members[0], True


def capability_type_name(expected: object) -> str:
    """Capability type display names for failed-validation logs/422 wording.

    ``types.UnionType`` lacks ``__name__``; direct lookup would raise
    ``AttributeError`` in invalid-value diagnostics, making fallback more fragile than validated inputs.
    """
    inner, optional = _unwrap_optional(expected)
    name = getattr(inner, "__name__", None) or str(inner)
    return f"{name} | None" if optional else name


def system_video_capabilities(
    *,
    endpoint: str,
    model_id: str,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> VideoCapabilities:
    """Read endpoint specs for system model-video declarations.

    Exactly one form, guaranteed by registry invariants:
    - ``video_caps_for_model``: backend pure per-model declarations for all four fields. Backends identify through
      matching entries (``entry_binding`` before current name matches, docs/adr/0104, 0105), while requests
      retain ``model_id``;
    - ``video_max_reference_images``: endpoint hard limits, other first/last/audio dimensions
      use ``VideoCapabilities`` defaults.

    No SDK clients/DB, callable without api_key.

    Raises:
        ValueError: Missing/nonvideo endpoints, missing both declarations, or negative maxima.
    """
    if endpoint_spec is None:
        from lib.custom_provider.endpoints import get_endpoint_spec

        endpoint_spec = get_endpoint_spec(endpoint)
    spec = endpoint_spec
    if spec.media_type != "video":
        raise ValueError(f"endpoint {endpoint!r} is {spec.media_type}, not video")

    caps_fn = spec.video_caps_for_model
    if caps_fn is not None:
        caps = caps_fn(model_id, model_entry(model_id, spec.media_type, entry_binding))
        if caps.max_reference_images is not None and caps.max_reference_images < 0:
            raise ValueError(
                f"invalid backend max_reference_images: endpoint={endpoint!r} model={model_id!r} "
                f"value={caps.max_reference_images!r}"
            )
        return caps

    endpoint_cap = spec.video_max_reference_images
    if endpoint_cap is None:
        raise ValueError(
            f"video endpoint {endpoint!r} declares neither video_max_reference_images nor video_caps_for_model"
        )
    if endpoint_cap < 0:
        raise ValueError(f"invalid video_max_reference_images on endpoint {endpoint!r}: {endpoint_cap!r}")
    return VideoCapabilities(max_reference_images=endpoint_cap)


def two_sided_video_capabilities(
    *,
    endpoint: str,
    model_id: str,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> VideoCapabilities:
    """Joint system/entry evaluation without overrides, used as settings bases.

    Matching entries use bindings before names (docs/adr/0105); ComfyUI ignores entries, yielding system declarations alone.

    Raises:
        ValueError: Unavailable system declarations (:func:`system_video_capabilities`).
    """
    from lib.backends.video_capabilities import effective_video_capabilities, entry_video_facts
    from lib.custom_provider.endpoints import get_endpoint_spec

    spec = endpoint_spec or get_endpoint_spec(endpoint)
    caps = system_video_capabilities(
        endpoint=endpoint, model_id=model_id, endpoint_spec=spec, entry_binding=entry_binding
    )
    if spec.kind == COMFYUI_KIND:
        return caps
    return effective_video_capabilities(caps, entry_video_facts(model_entry(model_id, spec.media_type, entry_binding)))


def video_capabilities_from_definition(definition: Mapping[str, object]) -> VideoCapabilities:
    """Mechanically derive declarative protocol capabilities; required image inputs determine text-only support."""
    raw_capabilities = definition.get("capabilities")
    capabilities = raw_capabilities if isinstance(raw_capabilities, Mapping) else {}
    applied: dict[str, object] = {}
    for key, value in capabilities.items():
        if key == "max_reference_images" and isinstance(value, float) and value.is_integer():
            value = int(value)
        expected = CAPABILITY_OVERRIDE_FIELDS.get(key)
        if expected is None or not capability_value_matches(value, expected):
            raise ValueError(f"invalid capability in endpoint definition: {key}={value!r}")
        applied[key] = value

    raw_inputs = definition.get("inputs")
    inputs = raw_inputs if isinstance(raw_inputs, Mapping) else None
    caps = merge_overrides(VideoCapabilities(first_frame=False), applied)
    return replace(caps, text_to_video=not requires_image_input(inputs))


def filter_valid_overrides(
    *, endpoint: str, model_id: str, overrides: object | None, endpoint_spec: EndpointSpec | None = None
) -> dict[str, object]:
    """Filter overrides through write-shared predicates, discarding values execution will ignore, returning effective subsets.

    Raw ``overrides`` from ``CustomProviderModel.capability_overrides`` may have any DB shape.
    Unknown keys/wrong types warn and skip: final execution composition must not let dirty configs
    disable whole generation paths. API allowlists validate writes.

    :func:`synthesize_video_capabilities` and API boundaries (``server/routers/custom_providers.py``
    ``_model_to_response``) share filtering of echoed ``capability_overrides``, preventing dirty values from appearing effective
    while execution ignores them. Responses additionally narrow to exposed allowlists, so echoed values
    are execution subsets: hidden keys can arise only from manual DB edits, without UI controls to show/delete them.
    Omit them to align GET/write key sets. No endpoint/media_type validation here,
    handled by callers (:func:`system_video_capabilities` / ``_system_capabilities_for``).
    """
    from lib.custom_provider.endpoints import get_endpoint_spec

    if overrides is None:
        return {}
    # ComfyUI derives all capabilities from bindings (``docs/adr/0082``); workflows alone change execution,
    # so overrides have no execution counterpart. Writes reject new values, filtering drops legacy ones;
    # shared execution/echo paths prevent effective-looking values ignored during execution.
    if endpoint_spec is not None and endpoint_spec.kind == COMFYUI_KIND:
        if overrides:
            logger.warning("忽略 %s/%s 的能力覆盖：ComfyUI 端点的能力只从节点绑定推导", endpoint, model_id)
        return {}
    if not isinstance(overrides, dict):
        logger.warning(
            "忽略 %s/%s 的能力覆盖：期望字典，实际 %s",
            endpoint,
            model_id,
            type(overrides).__name__,
        )
        return {}

    applied: dict[str, object] = {}
    for key, value in overrides.items():
        expected = CAPABILITY_OVERRIDE_FIELDS.get(key)
        if expected is None:
            if key not in ALL_MODEL_FACT_OVERRIDE_KEYS:
                logger.warning("忽略 %s/%s 的未知能力覆盖键 %r", endpoint, model_id, key)
            continue
        if not capability_value_matches(value, expected):
            logger.warning(
                "忽略 %s/%s 的能力覆盖 %s=%r：期望 %s，该维度回退系统判定",
                endpoint,
                model_id,
                key,
                value,
                capability_type_name(expected),
            )
            continue
        # Write-shared predicates, server/routers/custom_providers.py::_check_capability_overrides:
        # last_frame=True cannot work if endpoint delegates omit end_image; composition would claim support
        # while execution silently drops frames. Writes reject new configurations; filter legacy/non-API paths here.
        # Removed registry endpoints make get_endpoint_spec raise ValueError;
        # treat as unsupported last frames so dirty configs cannot break final response filtering.
        if key == "last_frame" and value is True:
            try:
                end_image_capable = (endpoint_spec or get_endpoint_spec(endpoint)).end_image_capable
            except ValueError:
                end_image_capable = False
            if not end_image_capable:
                logger.warning(
                    "忽略 %s/%s 的能力覆盖 last_frame=True：endpoint 不支持尾帧生成，该维度回退系统判定",
                    endpoint,
                    model_id,
                )
                continue
        # Likewise, delegates not composing reference_audio_files cannot support direct overrides:
        # composition would claim voice input while execution still generates random voices.
        if key == "reference_audio_mode" and value == ReferenceAudioMode.DIRECT.value:
            try:
                audio_capable = (endpoint_spec or get_endpoint_spec(endpoint)).reference_audio_capable
            except ValueError:
                audio_capable = False
            if not audio_capable:
                logger.warning(
                    "忽略 %s/%s 的能力覆盖 reference_audio_mode=direct：endpoint 不下传参考音频，该维度回退系统判定",
                    endpoint,
                    model_id,
                )
                continue
        applied[key] = value

    return applied


def synthesize_video_capabilities(
    *,
    endpoint: str,
    model_id: str,
    overrides: object | None,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> VideoCapabilities:
    """Joint system/entry evaluation ⊕ user overrides → effective capabilities.

    Raises:
        ValueError: Unavailable system declarations (:func:`system_video_capabilities`).
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
    """Like :func:`synthesize_video_capabilities`, also returning filtered sparse overrides.

    Factories separately pass these into request-tier queries
    (``CustomVideoBackend.video_capabilities_for_tier``), whose wrapped tier-aware bases
    must receive actual user overrides only, never whole composed objects from
    context-free system declarations, conservative for Kling/tier-sensitive backends. Otherwise queries short-circuit
    into behavior without tier awareness.

    Raises:
        ValueError: Unavailable system declarations (:func:`system_video_capabilities`).
    """
    caps = two_sided_video_capabilities(
        endpoint=endpoint, model_id=model_id, endpoint_spec=endpoint_spec, entry_binding=entry_binding
    )
    applied = filter_valid_overrides(
        endpoint=endpoint, model_id=model_id, overrides=overrides, endpoint_spec=endpoint_spec
    )
    merged = merge_overrides(caps, applied)
    return enforce_audio_capability_invariant(merged, endpoint=endpoint, model_id=model_id), applied


def merge_overrides(caps: VideoCapabilities, applied: dict[str, object]) -> VideoCapabilities:
    """Merge sparse overrides, restoring enum members/float dimensions from integer literals.

    JSON dictionaries retain enum strings; direct ``replace`` leaves enum-annotated
    fields as ``str``. Current ``StrEnum`` still permits ``==``, but
    ``is`` silently fails and ``.value`` raises ``AttributeError``, varying solely with caller syntax.
    Restoration is mechanical: :func:`capability_value_matches` already validates exact literals,
    without secondary judgment here.
    """
    if not applied:
        return caps
    coerced: dict[str, object] = {}
    for key, value in applied.items():
        inner, _optional = _unwrap_optional(CAPABILITY_OVERRIDE_FIELDS.get(key))
        if isinstance(inner, type) and issubclass(inner, Enum) and not isinstance(value, inner):
            coerced[key] = inner(value)
        elif inner is float and isinstance(value, int) and not isinstance(value, bool):
            coerced[key] = float(value)
        else:
            coerced[key] = value
    return replace(caps, **coerced)


AUDIO_OVERRIDE_KEYS = ("reference_audio_mode", "max_reference_audio_count")


def resolve_audio_pair(
    overrides: dict[str, object],
    *,
    endpoint: str,
    model_id: str,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> tuple[object, int | None]:
    """Complete sparse audio overrides into (mode, segment max), taking unoverridden dimensions from joint evaluation.

    Invariants require complete merged pairs, while dictionaries contain changed keys only. Writes/echoes share
    this completion rather than duplicated ``overrides.get(..., system_caps...)`` cascades.
    Unresolvable endpoint/model_id conservatively means no voice input.
    """
    try:
        system_caps = two_sided_video_capabilities(
            endpoint=endpoint, model_id=model_id, endpoint_spec=endpoint_spec, entry_binding=entry_binding
        )
    except ValueError:
        system_caps = None
    mode = overrides.get(
        "reference_audio_mode",
        system_caps.reference_audio_mode if system_caps is not None else ReferenceAudioMode.NONE,
    )
    count = overrides.get(
        "max_reference_audio_count",
        system_caps.max_reference_audio_count if system_caps is not None else 0,
    )
    if count is None:
        return mode, None
    return mode, int(count) if isinstance(count, int) else 0


def strip_incoherent_audio_overrides(
    overrides: dict[str, object],
    *,
    endpoint: str,
    model_id: str,
    endpoint_spec: EndpointSpec | None = None,
    entry_binding: str | None = None,
) -> dict[str, object]:
    """Remove override keys violating merged audio-pair invariants.

    :func:`enforce_audio_capability_invariant` downgrades execution to ``none``, leaving override values.
    Echoing legacy/manually edited ``direct`` ⊕ max0 would claim effective direct voices while
    execution omits them. Clients resubmitting unchanged audio during unrelated edits would also hit
    write invariants and reject whole saves as 422. Both mirror existing ``last_frame`` filtering.
    """
    if not any(key in overrides for key in AUDIO_OVERRIDE_KEYS):
        return overrides
    mode, count = resolve_audio_pair(
        overrides, endpoint=endpoint, model_id=model_id, endpoint_spec=endpoint_spec, entry_binding=entry_binding
    )
    if audio_capability_pair_is_coherent(mode=mode, count=count):
        return overrides
    return {key: value for key, value in overrides.items() if key not in AUDIO_OVERRIDE_KEYS}


def enforce_audio_capability_invariant(caps: VideoCapabilities, *, endpoint: str, model_id: str) -> VideoCapabilities:
    """Downgrade merged results violating :func:`audio_capability_pair_is_coherent` to ``none``.

    Writes already reject new invalid configurations; cover legacy/non-API paths here. Choose unsupported, never
    invented maxima, which are vendor facts systems cannot guess for users.
    """
    if audio_capability_pair_is_coherent(mode=caps.reference_audio_mode, count=caps.max_reference_audio_count):
        return caps
    logger.warning(
        "%s/%s 的合并能力 reference_audio_mode=%s 但 max_reference_audio_count=%d，按不支持参考音频处理",
        endpoint,
        model_id,
        caps.reference_audio_mode.value,
        caps.max_reference_audio_count,
    )
    return replace(caps, reference_audio_mode=ReferenceAudioMode.NONE)


def capability_value_matches(value: object, expected: object) -> bool:
    """Whether override values directly fit capability dimensions.

    Explicitly exclude bool/int in both directions: ``True`` must not become a one-image maximum,
    nor ``1`` boolean true. Such permissive truthiness guesses semantics; numeric dimensions also reject negatives.

    Optional ``T | None`` accepts ``None``, meaning undeclared backend constraints, matching field defaults;
    validate other values by inner types. Float dimensions accept integer literals: JSON ``15`` / ``15.0`` represent
    identical numbers, unlike semantically different bool/int confusion; rejecting integers creates needless config failures.

    Enums accept exact legal literals only, since JSON persistence cannot retain member objects.
    No case normalization/synonym mapping: unknown vocabulary fails back to system declarations,
    never guessed nearest members.

    API allowlist write validation and composition must share predicates; divergence would admit writes silently ignored
    by execution, recreating UI/execution contradictions. Public for API reuse.
    """
    inner, optional = _unwrap_optional(expected)
    if value is None:
        return optional
    if inner is bool:
        return isinstance(value, bool)
    if inner is int:
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if inner is float:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0
    if isinstance(inner, type) and issubclass(inner, Enum):
        return any(value == member.value for member in inner)
    return isinstance(value, inner) if isinstance(inner, type) else False
