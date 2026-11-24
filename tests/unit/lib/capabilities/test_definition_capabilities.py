import json
from pathlib import Path

import pytest

from arcreel_market_core.endpoint_definition import requires_image_input, validate_definition
from lib.capabilities.kernel import AudioProtocol, FixedDuration, bucket_fit, resolve
from lib.custom_provider.definition_capabilities import decode_definition
from tests.factories import comfyui_endpoint_definition, image_endpoint_definition


@pytest.mark.parametrize(
    "path", sorted(Path("lib/custom_provider/builtin_endpoints").glob("*.json")), ids=lambda path: path.stem
)
def test_shipped_declarations_retain_wire_capabilities(path):
    definition = json.loads(path.read_text(encoding="utf-8"))
    assert validate_definition(definition).valid
    declared = definition.get("capabilities", {})
    endpoint = decode_definition(definition)
    snapshot = resolve(endpoint, facts={"last_frame": True, "reference_audio": True})
    expected = {
        "text_to_video": not requires_image_input(definition.get("inputs")),
        "first_frame": bool(declared.get("first_frame", False)),
        "last_frame": bool(declared.get("last_frame", False)),
        "max_reference_images": int(declared.get("max_reference_images", 0)),
        "max_reference_audio_count": int(declared.get("max_reference_audio_count", 0)),
        "max_reference_audio_total_seconds": declared.get("max_reference_audio_total_seconds"),
        "max_prompt_chars": declared.get("max_prompt_chars"),
        "reference_audio_per_image": bool(declared.get("reference_audio_per_image", False)),
        "first_frame_ratio_adaptive_only": bool(declared.get("first_frame_ratio_adaptive_only", False)),
        "reference_audio_mode": declared.get("reference_audio_mode", "none"),
    }
    for key, value in expected.items():
        assert snapshot.effective[key] == value
    audio_map = {
        "controllable": AudioProtocol.SWITCH,
        "always_on": AudioProtocol.NO_SWITCH,
        "always_off": AudioProtocol.SILENT,
    }
    primary = declared.get("audio_track", "controllable")
    reference = declared.get("reference_route_audio_track") or primary
    assert endpoint.protocol("i2v") == audio_map[primary]
    assert endpoint.protocol("r2v") == audio_map[reference]


def test_decode_definition_handles_null_max_reference_images():
    endpoint = decode_definition({"kind": "declarative", "capabilities": {"max_reference_images": None}})
    assert resolve(endpoint).effective["max_reference_images"] == 0
    assert "reference_images" not in endpoint.slots


def test_market_image_and_binding_owned_fixed_duration_use_the_same_kernel():
    for definition in (image_endpoint_definition(), comfyui_endpoint_definition(media_type="image")):
        assert validate_definition(definition).valid
        view = resolve(decode_definition(definition))
        assert bucket_fit("image", view) == {"i2i": ("image_to_image",)}
    definition = comfyui_endpoint_definition()
    assert validate_definition(definition).valid
    endpoint = decode_definition(definition)
    with pytest.raises(ValueError, match="durations"):
        endpoint.validate({"durations": [8]})
    snapshot = resolve(endpoint, facts={"durations": [4]}, overrides={"durations": [8]})
    assert snapshot.duration == FixedDuration("no_frames")
    assert snapshot.effective["durations"] == ()
    assert not snapshot.editable


@pytest.mark.parametrize(
    ("frames", "fps", "reason"), [(65, None, "missing_fps"), (66, 16, "no_whole_seconds"), (65, 16, None)]
)
def test_binding_duration_mode_controls_edits(frames, fps, reason):
    definition = comfyui_endpoint_definition()
    definition["bindings"]["frames"] = [{"node": "5", "input": "frames", "class_type": "EmptyLatentImage", "step": 1}]
    definition["workflow"]["5"]["inputs"]["frames"] = frames
    if fps is None:
        del definition["bindings"]["fps"]
    endpoint = decode_definition(definition)
    if reason:
        assert isinstance(endpoint.duration, FixedDuration)
        assert endpoint.duration.reason == reason
        assert not endpoint.editable
    else:
        assert resolve(endpoint).effective["durations"] == (4,)
        raw = endpoint.validate({"durations": [8, 4, 8]})
        assert resolve(endpoint, overrides=raw).effective["durations"] == (4, 8)
