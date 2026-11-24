import json
from dataclasses import asdict
from pathlib import Path

import pytest

from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
from lib.backends.generation_type_buckets import builtin_model_bucket_fit, custom_model_bucket_fit
from lib.catalog import query as catalog
from lib.custom_provider.capabilities import synthesize_video_capabilities
from lib.custom_provider.capability_view import builtin_capability_view, custom_capability_view
from lib.custom_provider.endpoints import comfyui_endpoint_spec, get_endpoint_spec

_CASES = json.loads(Path("tests/fixtures/capabilities-before-4003.json").read_text(encoding="utf-8"))["cases"]


@pytest.mark.parametrize(
    "case", [c for c in _CASES if c["entry"] in {"builtin_video", "custom_video"}], ids=lambda c: c["input"]["model_id"]
)
def test_video_endpoints_match_frozen_settings_and_bucket_decisions(case):
    inputs = case["input"]
    model = inputs["model_id"]
    if case["entry"] == "builtin_video":
        provider = inputs["provider_id"]
        info = catalog.get_model(provider, model)
        assert info
        caps = builtin_video_capabilities_for_model(provider, model)
        fit = builtin_model_bucket_fit(provider, model, info)
        view = builtin_capability_view(provider, model, base_model=info, official=True, overrides={})
        assert sorted(fit.buckets) == case["bucket_fit"]["buckets"]
        expected, unmet = case["output"], case["bucket_fit"]["unmet"]
    else:
        endpoint = inputs["endpoint"]
        definition = inputs["definition"]
        spec = comfyui_endpoint_spec(endpoint, definition) if definition else get_endpoint_spec(endpoint)
        caps = synthesize_video_capabilities(endpoint=endpoint, model_id=model, endpoint_spec=spec, overrides={})
        fit = custom_model_bucket_fit(endpoint=endpoint, model_id=model, endpoint_spec=spec)
        view = custom_capability_view(
            endpoint=endpoint, model_id=model, spec=spec, raw_overrides={}, applied_overrides={}, entry_binding=None
        )
        expected, unmet = case["output"]["capabilities"], case["output"]["unmet"]
    assert asdict(caps) == expected
    assert {k: list(v) for k, v in fit.unmet.items()} == unmet
    assert {"base": view.base, "effective": view.effective, "editable": list(view.editable)} == case["settings"]


def test_kling_multi_image_protocol_cannot_be_overridden_to_have_sound():
    caps = synthesize_video_capabilities(
        endpoint="kling-video-audio", model_id="kling-v3-omni", overrides={"reference_route_audio_track": "always_on"}
    )
    assert caps.audio_track_for_route("r2v") == "always_off"


@pytest.mark.parametrize(
    ("frames", "fps", "reason"), [(None, 16, "no_frames"), (65, None, "missing_fps"), (66, 16, "no_whole_seconds")]
)
def test_fixed_workflow_keeps_its_duration_reason_across_model_and_settings(frames, fps, reason):
    from lib.capabilities.kernel import FixedDuration
    from lib.custom_provider.capabilities import custom_video_snapshot
    from tests.factories import comfyui_endpoint_definition

    definition = comfyui_endpoint_definition()
    if frames is not None:
        definition["bindings"]["frames"] = [
            {"node": "5", "input": "frames", "class_type": "EmptyLatentImage", "step": 1}
        ]
        definition["workflow"]["5"]["inputs"]["frames"] = frames
    if fps is None:
        del definition["bindings"]["fps"]
    spec = comfyui_endpoint_spec("ce-1", definition)
    model = custom_video_snapshot(
        endpoint=spec.key, model_id="vidu2.0", endpoint_spec=spec, overrides={"durations": [8]}
    )
    view = custom_capability_view(
        endpoint=spec.key,
        model_id="vidu2.0",
        spec=spec,
        raw_overrides={"durations": [8]},
        applied_overrides={},
        entry_binding=None,
    )
    assert model.duration == FixedDuration(reason)
    assert model.effective["durations"] == ()
    assert view.effective["durations"] == []
    assert "durations" not in view.editable


def test_kling_keeps_tail_override_for_pro_and_applies_request_protocol():
    from lib.capabilities.kernel import RequestConditions
    from lib.custom_provider.capabilities import custom_video_snapshot

    snapshot = custom_video_snapshot(
        endpoint="kling-video-pro-audio",
        model_id="relay",
        overrides={"last_frame": True, "audio_track": "controllable"},
    )
    assert snapshot.overrides == {"last_frame": True, "has_audio_track": True}
    for resolution, tier, available in [
        ("1080P", "std", True),
        ("720p", "PRO", True),
        ("720p", "std", False),
        ("4K", "pro", False),
    ]:
        actual = snapshot.for_request(RequestConditions("i2v", resolution, tier))
        assert actual.effective["last_frame"] is available
        assert actual.audio("i2v", requested=True).output is available
    with pytest.raises(ValueError, match="incompatible input combination"):
        snapshot.for_request(RequestConditions("r2v", inputs=frozenset({"first_frame", "reference_images"})))


def test_builtin_and_custom_legacy_audio_overrides_share_the_protocol_and_model_facts():
    from lib.backends.backend_assembly.specs import builtin_video_snapshot
    from lib.custom_provider.capabilities import custom_video_snapshot

    overrides = {"audio_track": "controllable", "last_frame": True, "max_prompt_chars": 8000}
    endpoint = "dashscope-video"
    model = "relay"
    catalog.install_user_added_models(
        [catalog.UserAddedModel("dashscope", model, "video", endpoint=endpoint, capability_overrides=overrides)]
    )
    try:
        builtin = builtin_video_snapshot("dashscope", model)
        custom = custom_video_snapshot(endpoint=endpoint, model_id=model, overrides=overrides)
        for snapshot in (builtin, custom):
            assert snapshot.overrides == {"has_audio_track": True, "last_frame": True, "max_prompt_chars": 8000}
            assert snapshot.audio("i2v", requested=False).switch is None
            assert snapshot.audio_track("r2v") == "always_on"
            assert snapshot.effective["max_prompt_chars"] == 8000
    finally:
        catalog.install_user_added_models(())
