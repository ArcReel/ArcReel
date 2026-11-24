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


def test_gemini_interactions_declares_frame_and_reference_mutual_exclusion():
    from lib.backends.video_backends.gemini_interactions import GeminiInteractionsVideoBackend
    from lib.capabilities.kernel import RequestConditions, resolve

    endpoint = GeminiInteractionsVideoBackend.describe_video_endpoint("gemini-omni-1.2-flash")
    for inputs in (frozenset({"first_frame", "reference_images"}), frozenset({"last_frame", "reference_images"})):
        with pytest.raises(ValueError, match="incompatible input combination"):
            resolve(endpoint, request=RequestConditions("r2v", inputs=inputs))


def test_vidu_prompt_limit_is_hard_ceiling():
    from lib.custom_provider.capabilities import custom_video_snapshot

    endpoint = get_endpoint_spec("vidu-video-q3-turbo").video_description("viduq3-turbo")
    assert endpoint.validate({"max_prompt_chars": 3000}) == {"max_prompt_chars": 3000}
    for invalid in (5001, None):
        with pytest.raises(ValueError, match="max_prompt_chars"):
            endpoint.validate({"max_prompt_chars": invalid})
    snapshot = custom_video_snapshot(
        endpoint="vidu-video-q3-turbo", model_id="viduq3-turbo", overrides={"max_prompt_chars": 8000}
    )
    assert snapshot.effective["max_prompt_chars"] == 5000
    assert "max_prompt_chars" not in snapshot.overrides


def test_builtin_capability_view_degrades_on_unavailable_endpoint_and_drops_rejected_overrides():
    fallback = catalog.CatalogModel(
        display_name="Missing Endpoint Video",
        media_type="video",
        capabilities=["first_frame"],
        supported_durations=[4, 8],
        resolutions=["720p"],
    )
    degraded = builtin_capability_view("nonexistent-provider", "m1", base_model=fallback, official=False, overrides={})
    assert degraded.base["durations"] == [4, 8]
    assert degraded.base["resolutions"] == ["720p"]

    model = "relay-openai"
    catalog.install_user_added_models(
        [
            catalog.UserAddedModel(
                "openai",
                model,
                "video",
                endpoint="openai-video",
                capability_overrides={"last_frame": True, "max_reference_images": 5, "audio_track": "always_off"},
            )
        ]
    )
    try:
        view = builtin_capability_view(
            "openai",
            model,
            base_model=fallback,
            official=False,
            overrides={"last_frame": True, "max_reference_images": 5, "audio_track": "always_off"},
        )
        assert view.overrides == {"audio_track": "always_off"}
        assert view.effective["last_frame"] is False
        assert view.effective["max_reference_images"] == 1
        assert view.effective["audio_track"] == "always_off"
    finally:
        catalog.install_user_added_models(())


@pytest.mark.parametrize(
    ("route", "slot", "field", "accepted_value", "blocked_value"),
    [
        ("i2v", "last_frame", "last_frame", True, False),
        ("r2v", "reference_images", "max_reference_images", None, 0),
    ],
)
def test_grok_endpoint_describes_reference_route_resolution_limit(route, slot, field, accepted_value, blocked_value):
    from lib.backends.video_backends.grok import GrokVideoBackend
    from lib.backends.video_capabilities import channel_projection
    from lib.capabilities.kernel import RequestConditions

    endpoint = GrokVideoBackend.describe_video_endpoint("grok-imagine-video")
    at_720p = channel_projection(endpoint, RequestConditions(route, "720p", inputs=frozenset({slot})))
    at_1080p = channel_projection(endpoint, RequestConditions(route, "1080p", inputs=frozenset({slot})))
    assert getattr(at_720p, field) == accepted_value
    assert getattr(at_1080p, field) == blocked_value
