from collections.abc import Mapping
from math import isfinite
from typing import Any

LEGACY_AUDIO_KEYS = {"audio_track": "has_audio_track", "reference_route_audio_track": "reference_route_has_audio_track"}
LEGACY_AUDIO_MODES = frozenset({"always_on", "always_off", "controllable"})


def is_legacy_audio_mode(value: object) -> bool:
    return isinstance(value, str) and value in LEGACY_AUDIO_MODES


VIDEO_CHANNEL_FLAG_KEYS = frozenset({"first_frame_ratio_adaptive_only", "reference_audio_per_image"})
_EXPECTED_TYPE_NAMES: Mapping[str, str] = {
    "durations": "list[int > 0]（非空）",
    "resolutions": "list[str]（非空）",
    "duration_constraints": "{by_resolution: {str: list[int > 0]}, with_reference_images: list[int > 0], with_end_frame: list[int > 0]}",
    "reference_audio_mode": "none | direct",
    "max_reference_audio_total_seconds": "float | None",
    "max_prompt_chars": "int | None",
    "max_output_tokens": "int > 0",
    "max_reference_images": "int",
    "max_reference_audio_count": "int",
}


def expected_type_name(key: str) -> str:
    if key in _EXPECTED_TYPE_NAMES:
        return _EXPECTED_TYPE_NAMES[key]
    if key in LEGACY_AUDIO_KEYS:
        return "controllable | always_on | always_off"
    return "bool"


OVERRIDE_KEYS = {
    "text": frozenset({"image_input", "structured_output", "max_output_tokens"}),
    "image": frozenset({"text_to_image", "image_to_image", "max_reference_images"}),
    "video": frozenset(
        {
            "text_to_video",
            "first_frame",
            "last_frame",
            "has_audio_track",
            "reference_route_has_audio_track",
            "reference_audio_mode",
            "max_reference_images",
            "max_reference_audio_count",
            "max_reference_audio_total_seconds",
            "max_prompt_chars",
            "durations",
            "resolutions",
            "duration_constraints",
        }
    ),
}
INPUT_SLOT = {
    "max_reference_images": "reference_images",
    "reference_audio_mode": "reference_audio",
    "max_reference_audio_count": "reference_audio",
    "max_reference_audio_total_seconds": "reference_audio",
}


def _durations(value: object) -> bool:
    return isinstance(value, (tuple, list)) and all(type(n) is int and n > 0 for n in value)


def valid_value(key: str, value: Any) -> bool:
    if key == "duration_constraints":
        if not isinstance(value, Mapping) or value.keys() - {
            "by_resolution",
            "with_end_frame",
            "with_reference_images",
        }:
            return False
        by_resolution = value.get("by_resolution", {})
        return (
            isinstance(by_resolution, Mapping)
            and all(isinstance(k, str) and k.strip() and v and _durations(v) for k, v in by_resolution.items())
            and all(_durations(value.get(k, [])) for k in ("with_end_frame", "with_reference_images"))
        )
    if key == "durations":
        return _durations(value) and bool(value)
    if key == "resolutions":
        return isinstance(value, (tuple, list)) and bool(value) and all(isinstance(r, str) and r.strip() for r in value)
    if key == "reference_audio_mode":
        return value in ("none", "direct")
    if key == "max_reference_audio_total_seconds":
        return (
            value is None
            or (type(value) is int and value >= 0)
            or (type(value) is float and isfinite(value) and value >= 0)
        )
    if key == "max_prompt_chars" and value is None:
        return True
    if key.startswith("max_"):
        return type(value) is int and value >= (1 if key in ("max_prompt_chars", "max_output_tokens") else 0)
    return type(value) is bool


def normalized_value(key: str, value: Any) -> Any:
    if key == "resolutions":
        return [resolution.strip() for resolution in value]
    if key == "durations":
        return sorted(set(value))
    if key == "duration_constraints":
        return {
            **value,
            "by_resolution": {r.strip().lower(): list(ns) for r, ns in value.get("by_resolution", {}).items()},
        }
    return value


def same_override_value(key: str, stored: object, submitted: object) -> bool:
    if type(stored) is not type(submitted):
        return False
    if stored == submitted:
        return True
    return (
        valid_value(key, stored)
        and valid_value(key, submitted)
        and normalized_value(key, stored) == normalized_value(key, submitted)
    )
