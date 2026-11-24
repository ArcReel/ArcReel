"""把已通过市场校验的端点定义解码为协议能力。"""

from collections.abc import Mapping
from typing import Any

from arcreel_market_core.comfyui.bindings import targets_of
from arcreel_market_core.endpoint_definition import COMFYUI_KIND, definition_media_type, requires_image_input
from lib.capabilities.kernel import AudioProtocol, DurationMode, Endpoint, EndpointDurations, FixedDuration
from lib.custom_provider.comfyui.capabilities import (
    default_supported_durations,
    derive_video_capabilities,
    duration_is_fixed,
    frame_rate_is_missing,
)

_AUDIO = {
    "controllable": AudioProtocol.SWITCH,
    "always_on": AudioProtocol.NO_SWITCH,
    "always_off": AudioProtocol.SILENT,
}


def decode_definition(definition: Mapping[str, Any]) -> Endpoint:
    media = definition_media_type(definition)
    if definition["kind"] == COMFYUI_KIND:
        return _comfyui(definition, media)
    declared = dict(definition.get("capabilities", {}))
    count = int(declared.get("max_reference_images", 0))
    if media == "image":
        slots = {key for key in ("text_to_image", "image_to_image") if declared.get(key)}
        if "image_to_image" in slots:
            slots.add("reference_images")
        return Endpoint("image", frozenset(slots), defaults={"max_reference_images": count or None})
    if media != "video":
        raise ValueError(media)
    declared["text_to_video"] = not requires_image_input(definition.get("inputs"))
    slots = {key for key in ("text_to_video", "first_frame", "last_frame") if declared.pop(key, False)}
    if count:
        slots.add("reference_images")
    if declared.get("reference_audio_mode") == "direct":
        slots.add("reference_audio")
    primary = _AUDIO[declared.pop("audio_track", "controllable")]
    reference = declared.pop("reference_route_audio_track", None)
    declared["max_reference_images"] = count
    declared.setdefault("max_reference_audio_count", 0)
    declared.setdefault("reference_audio_per_image", False)
    declared.setdefault("first_frame_ratio_adaptive_only", False)
    return Endpoint(
        "video",
        frozenset(slots),
        defaults=declared,
        audio=primary,
        reference_audio=_AUDIO[reference] if reference else None,
    )


def _comfyui(definition: Mapping[str, Any], media: str) -> Endpoint:
    bindings = definition["bindings"]
    if media == "image":
        count = len(targets_of(bindings.get("reference_images")))
        slots = {"image_to_image", "reference_images"} if count else {"text_to_image"}
        return Endpoint("image", frozenset(slots), limits={"max_reference_images": count}, binding_owned=True)
    if media != "video":
        raise ValueError(media)
    caps = derive_video_capabilities(definition)
    slots = {key for key in ("text_to_video", "first_frame", "last_frame") if getattr(caps, key)}
    if caps.max_reference_images:
        slots.add("reference_images")
    if caps.max_reference_audio_count:
        slots.add("reference_audio")
    seconds = default_supported_durations(definition)
    mode: DurationMode
    if seconds:
        mode = EndpointDurations(tuple(seconds))
    elif duration_is_fixed(bindings):
        mode = FixedDuration("no_frames")
    elif frame_rate_is_missing(definition):
        mode = FixedDuration("missing_fps")
    else:
        mode = FixedDuration("no_whole_seconds")
    return Endpoint(
        "video",
        frozenset(slots),
        limits={
            "max_reference_images": caps.max_reference_images,
            "max_reference_audio_count": caps.max_reference_audio_count,
        },
        defaults={"reference_audio_mode": "direct" if caps.max_reference_audio_count else "none"},
        audio=_AUDIO[caps.audio_track],
        duration=mode,
        binding_owned=True,
    )
