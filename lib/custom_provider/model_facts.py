"""Effective provider-model facts: overrides > matching entries > runtime fallback (docs/adr/0104, 0105).

Entries hold text vision/structured output/output-token maxima; image text/image-to-image/reference limits; video durations,
resolutions, and linked duration constraints. User-owned provider models may replace any whole dimension at highest priority;
unoverridden dimensions follow entries. Unmatched durations use name-inferred presets, falling back to
``[4, 8]`` without families; other facts remain unconstrained ``None``, leaving excess rejection upstream. Recompute fallbacks here, never persist.

Channel-fact overrides (video input modes/reference limits/prompt limits/audio forms) compose with declarations through
:mod:`lib.custom_provider.capabilities`, outside this module. Both dimension kinds share sparse
``CustomProviderModel.capability_overrides``; image/video reference limits share keys, validated by media.

ComfyUI capabilities derive only from node bindings (docs/adr/0082), bypassing this module via endpoint dispatch. The sole exception:
ComfyUI video-row durations are user preferences adding/removing workflow-native durations, stored as ``durations`` overrides through
:func:`comfyui_duration_override`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field

from arcreel_catalog_core import CatalogEntry, DurationConstraints, ImageFacts, TextFacts, VideoFacts
from lib.catalog import query as catalog
from lib.custom_provider.duration_presets import infer_supported_durations

logger = logging.getLogger(__name__)

#: Overridable model-fact dimensions by media; audio has none.
MODEL_FACT_OVERRIDE_KEYS: Mapping[str, frozenset[str]] = {
    "text": frozenset({"image_input", "structured_output", "max_output_tokens"}),
    "image": frozenset({"text_to_image", "image_to_image", "max_reference_images"}),
    "video": frozenset({"durations", "resolutions", "duration_constraints"}),
    "audio": frozenset(),
}

#: All model-fact keys for write allowlist merging.
ALL_MODEL_FACT_OVERRIDE_KEYS: frozenset[str] = frozenset().union(*MODEL_FACT_OVERRIDE_KEYS.values())

_BOOL_KEYS = frozenset({"image_input", "structured_output", "text_to_image", "image_to_image"})
_CONSTRAINT_KEYS = frozenset({"by_resolution", "with_reference_images"})


def _is_positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_duration_list(value: object) -> bool:
    return isinstance(value, list) and bool(value) and all(_is_positive_int(item) for item in value)


def _is_constraints(value: object) -> bool:
    if not isinstance(value, dict) or not value.keys() <= _CONSTRAINT_KEYS:
        return False
    by_resolution = value.get("by_resolution", {})
    if not isinstance(by_resolution, dict):
        return False
    if any(
        not isinstance(res, str) or not res.strip() or not _is_duration_list(allowed)
        for res, allowed in by_resolution.items()
    ):
        return False
    with_reference = value.get("with_reference_images", [])
    return isinstance(with_reference, list) and all(_is_positive_int(item) for item in with_reference)


def model_fact_value_matches(key: str, value: object) -> bool:
    """Whether override values replace whole model-fact dimensions; shared write validation/read filtering.

    Booleans require ``bool``; reference maxima are nonnegative integers; max output tokens positive integers; durations/resolutions nonempty lists;
    duration constraints accept only ``by_resolution`` (resolution → nonempty durations) and ``with_reference_images`` (duration lists).
    """
    if key in _BOOL_KEYS:
        return isinstance(value, bool)
    if key == "max_reference_images":
        return isinstance(value, int) and not isinstance(value, bool) and value >= 0
    if key == "max_output_tokens":
        return _is_positive_int(value)
    if key == "durations":
        return _is_duration_list(value)
    if key == "resolutions":
        return isinstance(value, list) and bool(value) and all(isinstance(r, str) and r.strip() for r in value)
    if key == "duration_constraints":
        return _is_constraints(value)
    return False


_TYPE_NAMES: Mapping[str, str] = {
    "max_output_tokens": "int > 0",
    "max_reference_images": "int >= 0",
    "durations": "list[int > 0]（非空）",
    "resolutions": "list[str]（非空）",
    "duration_constraints": "{by_resolution: {str: list[int > 0]}, with_reference_images: list[int > 0]}",
}


def model_fact_type_name(key: str) -> str:
    """Expected model-fact value shapes for 422 wording."""
    return "bool" if key in _BOOL_KEYS else _TYPE_NAMES.get(key, "unknown")


def model_fact_overrides(overrides: object | None, media_type: str, *, model_id: str = "") -> dict[str, object]:
    """Get media-specific model-fact overrides, discarding invalid values with warnings so dirty configs cannot break execution."""
    if not isinstance(overrides, Mapping):
        return {}
    keys = MODEL_FACT_OVERRIDE_KEYS.get(media_type, frozenset())
    applied: dict[str, object] = {}
    for key, value in overrides.items():
        if key not in keys:
            continue
        if not model_fact_value_matches(key, value):
            logger.warning("忽略 %s 的能力覆盖 %s=%r：值不合法，该维度回到条目或回落值", model_id, key, value)
            continue
        applied[key] = value
    return applied


@dataclass(frozen=True)
class TextModelFacts:
    """``None`` means unconstrained dimensions without matching entries/overrides."""

    image_input: bool | None = None
    structured_output: bool | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True)
class ImageModelFacts:
    """``None`` means unconstrained dimensions without matching entries/overrides.

    Entry ``max_reference_images`` is ``None`` when unlisted, ``0`` for explicit rejection; jointly take the smaller
    channel limit (:func:`lib.backends.image_capabilities.image_reference_limit`).
    """

    text_to_image: bool | None = None
    image_to_image: bool | None = None
    max_reference_images: int | None = None


@dataclass(frozen=True)
class VideoModelFacts:
    """Durations always exist, inferred by name without entry matches; ``None`` resolutions mean unconstrained."""

    durations: tuple[int, ...] = ()
    resolutions: tuple[str, ...] | None = None
    duration_constraints: DurationConstraints = field(default_factory=DurationConstraints)


def model_entry(model_id: str, media_type: str, entry_binding: str | None = None) -> CatalogEntry | None:
    """Matching entries, bindings first then current names (docs/adr/0105); media from endpoints."""
    return catalog.find_entry(model_id, media_type=media_type, binding=catalog.binding_key(entry_binding, media_type))


def text_model_facts(model_id: str, overrides: object | None, *, entry_binding: str | None = None) -> TextModelFacts:
    """Effective text facts: overrides > entries, otherwise unconstrained."""
    applied = model_fact_overrides(overrides, "text", model_id=model_id)
    entry = model_entry(model_id, "text", entry_binding)
    facts = entry.facts if entry is not None and isinstance(entry.facts, TextFacts) else None
    max_output_tokens = applied.get("max_output_tokens")
    if max_output_tokens is None and facts is not None:
        max_output_tokens = facts.max_output_tokens
    return TextModelFacts(
        image_input=_bool_fact(applied, "image_input", None if facts is None else facts.image_input),
        structured_output=_bool_fact(applied, "structured_output", None if facts is None else facts.structured_output),
        max_output_tokens=max_output_tokens if isinstance(max_output_tokens, int) else None,
    )


def image_model_facts(model_id: str, overrides: object | None, *, entry_binding: str | None = None) -> ImageModelFacts:
    """Effective image facts before channel intersection (:mod:`lib.backends.image_capabilities`)."""
    applied = model_fact_overrides(overrides, "image", model_id=model_id)
    entry = model_entry(model_id, "image", entry_binding)
    facts = entry.facts if entry is not None and isinstance(entry.facts, ImageFacts) else None
    return ImageModelFacts(
        text_to_image=_bool_fact(applied, "text_to_image", None if facts is None else facts.text_to_image),
        image_to_image=_bool_fact(applied, "image_to_image", None if facts is None else facts.image_to_image),
        max_reference_images=None if facts is None else facts.max_reference_images,
    )


def video_model_facts(model_id: str, overrides: object | None, *, entry_binding: str | None = None) -> VideoModelFacts:
    """Effective video facts.

    Durations: overrides > entries > name-inferred presets without entry matches.
    """
    applied = model_fact_overrides(overrides, "video", model_id=model_id)
    entry = model_entry(model_id, "video", entry_binding)
    facts = entry.facts if entry is not None and isinstance(entry.facts, VideoFacts) else None

    overridden = applied.get("durations")
    if isinstance(overridden, list):
        durations = tuple(sorted({int(d) for d in overridden}))
    elif facts is not None:
        durations = tuple(facts.durations)
    else:
        durations = tuple(infer_supported_durations(model_id))

    raw_resolutions = applied.get("resolutions")
    if isinstance(raw_resolutions, list):
        resolutions: tuple[str, ...] | None = tuple(str(r).strip() for r in raw_resolutions)
    else:
        resolutions = tuple(facts.resolutions) if facts is not None else None

    raw_constraints = applied.get("duration_constraints")
    if isinstance(raw_constraints, dict):
        constraints = _constraints_from_override(raw_constraints)
    else:
        constraints = facts.duration_constraints if facts is not None else DurationConstraints()
    return VideoModelFacts(durations=durations, resolutions=resolutions, duration_constraints=constraints)


#: Sole ComfyUI-video override: durations. Capabilities derive only from bindings (docs/adr/0082),
#: while durations add/remove native workflow choices as user preferences, not capability declarations (docs/adr/0104).
COMFYUI_VIDEO_OVERRIDE_KEYS: frozenset[str] = frozenset({"durations"})


def comfyui_duration_override(overrides: object | None, *, model_id: str = "") -> list[int] | None:
    """User ComfyUI-video duration choices; ``None`` for absent/invalid values, using endpoint node-derived tiers."""
    if not isinstance(overrides, Mapping):
        return None
    value = overrides.get("durations")
    if value is None:
        return None
    if not isinstance(value, list) or not model_fact_value_matches("durations", value):
        logger.warning("忽略 %s 的可选时长覆盖 %r：值不合法，回到端点推导的档位", model_id, value)
        return None
    return sorted({int(d) for d in value})


def _bool_fact(applied: Mapping[str, object], key: str, entry_value: bool | None) -> bool | None:
    value = applied.get(key)
    return value if isinstance(value, bool) else entry_value


def _constraints_from_override(raw: Mapping[str, object]) -> DurationConstraints:
    """Convert override duration constraints into entry shapes; compare resolution keys lowercase like entries."""
    by_resolution_raw = raw.get("by_resolution")
    by_resolution: dict[str, tuple[int, ...]] = {}
    if isinstance(by_resolution_raw, Mapping):
        for res, allowed in by_resolution_raw.items():
            if isinstance(allowed, list):
                by_resolution[str(res).strip().lower()] = tuple(int(d) for d in allowed)
    with_reference_raw = raw.get("with_reference_images")
    with_reference = tuple(int(d) for d in with_reference_raw) if isinstance(with_reference_raw, list) else ()
    return DurationConstraints(by_resolution=by_resolution, with_reference_images=with_reference)


def matched_entry_summary(model_id: str, media_type: str, entry_binding: str | None = None) -> dict[str, object] | None:
    """Matching entry summaries for settings matched-model/unpriced display; ``None`` if unmatched.

    ``priced`` means price tables understood by current code; false still records model calls unpriced.
    """
    entry = model_entry(model_id, media_type, entry_binding)
    return entry_summary(entry) if entry is not None else None


def entry_summary(entry: CatalogEntry) -> dict[str, object]:
    """Entry summaries: maker, official name, media, display name, and priced status (:func:`matched_entry_summary`)."""
    return {
        "maker": entry.maker,
        "name": entry.name,
        "media_type": entry.media_type,
        "display_name": entry.display_name,
        "priced": entry.price is not None and entry.price.is_priceable,
    }
