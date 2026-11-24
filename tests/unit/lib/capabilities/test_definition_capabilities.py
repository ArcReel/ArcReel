import json
from pathlib import Path

import pytest

from arcreel_market_core.endpoint_definition import validate_definition
from lib.capabilities.kernel import AudioProtocol, FixedDuration, bucket_fit, resolve
from lib.custom_provider.builtin_definitions import declarative_video_capabilities
from lib.custom_provider.definition_capabilities import decode_definition
from tests.factories import comfyui_endpoint_definition, image_endpoint_definition


@pytest.mark.parametrize(
    "path", sorted(Path("lib/custom_provider/builtin_endpoints").glob("*.json")), ids=lambda path: path.stem
)
def test_shipped_declarations_retain_wire_capabilities(path):
    definition = json.loads(path.read_text())
    assert validate_definition(definition).valid
    old = declarative_video_capabilities(definition)
    endpoint = decode_definition(definition)
    snapshot = resolve(endpoint, facts={"last_frame": True, "reference_audio": True})
    for key in (
        "text_to_video",
        "first_frame",
        "last_frame",
        "max_reference_images",
        "max_reference_audio_count",
        "max_prompt_chars",
        "reference_audio_per_image",
        "first_frame_ratio_adaptive_only",
        "reference_audio_mode",
    ):
        assert snapshot.effective[key] == getattr(old, key)
    for route in ("i2v", "r2v"):
        mode = old.audio_track_for_route(route)
        assert (
            endpoint.protocol(route)
            == {
                "controllable": AudioProtocol.SWITCH,
                "always_on": AudioProtocol.NO_SWITCH,
                "always_off": AudioProtocol.SILENT,
            }[mode]
        )


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
