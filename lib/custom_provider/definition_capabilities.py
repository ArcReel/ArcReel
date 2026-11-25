"""把已通过市场校验的端点定义解码为协议能力。"""

from collections.abc import Mapping
from typing import Any

from arcreel_market_core.endpoint_definition import COMFYUI_KIND, definition_media_type, requires_image_input
from lib.capabilities.kernel import AudioProtocol, Endpoint
from lib.custom_provider.comfyui.capabilities import comfyui_description

_AUDIO = {
    "controllable": AudioProtocol.SWITCH,
    "always_on": AudioProtocol.NO_SWITCH,
    "always_off": AudioProtocol.SILENT,
}


def decode_definition(definition: Mapping[str, Any]) -> Endpoint:
    media = definition_media_type(definition)
    if definition["kind"] == COMFYUI_KIND:
        return comfyui_description(definition, media)
    declared = dict(definition.get("capabilities", {}))
    raw_count = declared.get("max_reference_images")
    count = 0 if raw_count is None else int(raw_count)
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
