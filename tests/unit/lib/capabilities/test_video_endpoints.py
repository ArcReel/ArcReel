import pytest

from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
from lib.backends.generation_type_buckets import builtin_model_bucket_fit, custom_model_bucket_fit
from lib.catalog import query as catalog
from lib.custom_provider.capabilities import synthesize_video_capabilities
from lib.custom_provider.capability_view import builtin_capability_view, custom_capability_view
from lib.custom_provider.endpoints import comfyui_endpoint_spec, get_endpoint_spec
from tests.factories import comfyui_endpoint_definition


def test_builtin_and_custom_video_settings_views_reflect_protocol_bounds():
    kling_info = catalog.get_model("kling", "kling-v3-omni")
    assert kling_info is not None
    kling_view = builtin_capability_view("kling", "kling-v3-omni", base_model=kling_info, official=True, overrides={})
    assert kling_view.effective["has_audio_track"] is True
    assert kling_view.effective["audio_switch_controllable"] is True
    assert kling_view.effective["reference_route_has_audio_track"] is False
    assert kling_view.effective["reference_route_audio_switch_controllable"] is False
    assert "reference_route_has_audio_track" not in kling_view.editable
    assert sorted(builtin_model_bucket_fit("kling", "kling-v3-omni", kling_info).buckets) == ["i2v", "r2v"]

    custom_spec = get_endpoint_spec("kling-video-audio")
    custom_view = custom_capability_view(
        endpoint="kling-video-audio",
        model_id="kling-v3-omni",
        spec=custom_spec,
        raw_overrides={},
        applied_overrides={},
        entry_binding=None,
    )
    assert "has_audio_track" in custom_view.editable
    assert "reference_route_has_audio_track" not in custom_view.editable
    assert sorted(
        custom_model_bucket_fit(
            endpoint="kling-video-audio", model_id="kling-v3-omni", endpoint_spec=custom_spec
        ).buckets
    ) == [
        "i2v",
        "r2v",
    ]

    comfy_spec = comfyui_endpoint_spec("comfyui-fixed", comfyui_endpoint_definition(media_type="video"))
    comfy_view = custom_capability_view(
        endpoint="comfyui-fixed",
        model_id="workflow",
        spec=comfy_spec,
        raw_overrides={},
        applied_overrides={},
        entry_binding=None,
    )
    assert comfy_view.effective["duration_fixed"] is True
    assert "durations" not in comfy_view.editable
    assert builtin_video_capabilities_for_model("kling", "kling-v3-omni").first_frame is True


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
    from lib.custom_provider.comfyui.request_builder import build_workflow
    from lib.generation.video_request_facts import planning_durations, project_video_capabilities_with_facts
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
    payload, facts = project_video_capabilities_with_facts(model, {}, generation_type="i2v", requested_audio=True)
    assert payload["supported_durations"] == payload["duration_constraints"]["allowed"] == []
    assert payload["duration_endpoint_fixed"] is True
    assert payload["duration_endpoint_fixed_reason"] == "endpoint"
    assert planning_durations(facts) == [4, 8]
    built = build_workflow(definition, prompt="p", aspect_ratio="16:9", duration_seconds=8)
    assert built.frames is None
    assert built.workflow["5"]["inputs"].get("frames") == frames


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
        assert view.overrides == {"has_audio_track": False}
        assert view.effective["last_frame"] is False
        assert view.effective["max_reference_images"] == 1
        assert view.effective["has_audio_track"] is False
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


def test_read_side_request_facts_reflect_snapshot_and_project_preferences():
    from lib.backends.backend_assembly.specs import builtin_video_snapshot
    from lib.generation.video_request_facts import snapshot_video_request_facts

    snapshot = builtin_video_snapshot("kling", "kling-v3-omni")
    facts = snapshot_video_request_facts(
        snapshot,
        {"generation_mode": "reference_video", "video_provider_r2v": "kling/kling-v3-omni"},
        route="reference_video",
        generation_type="r2v",
        requested_audio=True,
    )
    assert facts.provider_id == "kling"
    assert facts.model_id == "kling-v3-omni"
    assert facts.has_audio_track is False
    assert facts.generate_audio is False
    assert facts.voice_consistency == "none"


def test_kling_r2v_read_side_projection_and_facts_match_silent_protocol():
    from lib.backends.backend_assembly.specs import builtin_video_snapshot
    from lib.generation.video_request_facts import project_video_capabilities, snapshot_video_request_facts

    snapshot = builtin_video_snapshot("kling", "kling-v3-omni")
    project = {"generation_mode": "reference_video", "video_provider_r2v": "kling/kling-v3-omni"}
    facts = snapshot_video_request_facts(
        snapshot, project, route="reference_video", generation_type="r2v", requested_audio=True
    )
    payload = project_video_capabilities(snapshot, project, generation_type="r2v", requested_audio=True)

    assert facts.generate_audio is False
    assert facts.has_audio_track is False
    assert facts.audio_switch_controllable is False
    assert facts.voice_consistency == "none"
    assert payload["generate_audio"] is False
    assert payload["voice_consistency"] == "none"
    assert payload["last_frame"] is False


def test_grok_1080p_read_side_projection_matches_execution_restrictions():
    from lib.backends.backend_assembly.specs import builtin_video_snapshot
    from lib.generation.video_request_facts import ResolutionOverride, project_video_capabilities

    snapshot = builtin_video_snapshot(
        "grok",
        "grok-imagine-video",
        overrides={"last_frame": True},
        official=False,
    )
    project = {"generation_mode": "storyboard"}

    at_720p_i2v = project_video_capabilities(
        snapshot, project, generation_type="i2v", requested_audio=True, resolution_override=ResolutionOverride("720p")
    )
    at_1080p_i2v = project_video_capabilities(
        snapshot, project, generation_type="i2v", requested_audio=True, resolution_override=ResolutionOverride("1080p")
    )
    at_720p_r2v = project_video_capabilities(
        snapshot, project, generation_type="r2v", requested_audio=True, resolution_override=ResolutionOverride("720p")
    )
    at_1080p_r2v = project_video_capabilities(
        snapshot, project, generation_type="r2v", requested_audio=True, resolution_override=ResolutionOverride("1080p")
    )

    assert at_720p_i2v["last_frame"] is True
    assert at_1080p_i2v["last_frame"] is False
    assert at_720p_r2v["max_reference_images"] == 7
    assert at_1080p_r2v["max_reference_images"] == 0


def test_three_state_resolution_override_is_pure_sync_with_model_default_on_snapshot():
    from dataclasses import replace

    from lib.custom_provider.capabilities import custom_video_snapshot
    from lib.generation.video_request_facts import ResolutionOverride, snapshot_video_request_facts

    snapshot = custom_video_snapshot(
        endpoint="newapi-video",
        model_id="veo-3.1-generate-001",
        overrides={"durations": [4, 6, 8], "duration_constraints": {"by_resolution": {"1080p": [8]}}},
    )
    snapshot = replace(snapshot, default_resolution="720p")
    project = {"model_settings": {f"{snapshot.provider_id}/{snapshot.model_id}": {"resolution": "1080p"}}}

    saved = snapshot_video_request_facts(
        snapshot, project, route="storyboard", generation_type="i2v", requested_audio=True
    )
    auto = snapshot_video_request_facts(
        snapshot,
        project,
        route="storyboard",
        generation_type="i2v",
        requested_audio=True,
        resolution_override=ResolutionOverride(None),
    )
    explicit = snapshot_video_request_facts(
        snapshot,
        project,
        route="storyboard",
        generation_type="i2v",
        requested_audio=True,
        resolution_override=ResolutionOverride("1080p"),
    )

    assert saved.resolution == "1080p"
    assert saved.allowed_durations == (8,)
    assert auto.resolution == "720p"
    assert auto.allowed_durations == (4, 6, 8)
    assert explicit.resolution == "1080p"
    assert explicit.allowed_durations == (8,)


def test_custom_video_backend_tier_capabilities_respect_bucket_and_inputs():
    from unittest.mock import MagicMock

    from arcreel_market_core.video_backend_contract import VideoBackend
    from lib.custom_provider.backends import CustomVideoBackend
    from lib.custom_provider.capabilities import custom_video_snapshot

    kling_snapshot = custom_video_snapshot(endpoint="kling-video-audio", model_id="kling-v3-omni")
    kling_backend = CustomVideoBackend(
        provider_id="custom-1", delegate=MagicMock(spec=VideoBackend), model="kling-v3-omni", snapshot=kling_snapshot
    )
    i2v_caps = kling_backend.video_capabilities_for_tier("default", "720p", bucket="i2v")
    r2v_caps = kling_backend.video_capabilities_for_tier("default", "720p", bucket="r2v")
    assert i2v_caps.audio_track_for_route("i2v") == "controllable"
    assert r2v_caps.audio_track_for_route("r2v") == "always_off"

    from lib.backends.backend_assembly.specs import builtin_video_snapshot

    grok_snapshot = builtin_video_snapshot("grok", "grok-imagine-video", overrides={"last_frame": True}, official=False)
    grok_backend = CustomVideoBackend(
        provider_id="custom-1",
        delegate=MagicMock(spec=VideoBackend),
        model="grok-imagine-video",
        snapshot=grok_snapshot,
    )
    grok_720p = grok_backend.video_capabilities_for_tier(
        "default", "720p", bucket="i2v", inputs=frozenset({"last_frame"})
    )
    grok_1080p = grok_backend.video_capabilities_for_tier(
        "default", "1080p", bucket="i2v", inputs=frozenset({"last_frame"})
    )
    assert grok_720p.last_frame is True
    assert grok_1080p.last_frame is False


def test_comfyui_fixed_duration_consistent_across_projection_and_facts():
    from lib.custom_provider.capabilities import custom_video_snapshot
    from lib.generation.video_request_facts import (
        ENDPOINT_FIXED_PLANNING_DURATIONS,
        project_video_capabilities,
        snapshot_video_request_facts,
    )
    from tests.factories import comfyui_endpoint_definition

    spec = comfyui_endpoint_spec("ce-fixed", comfyui_endpoint_definition())
    snapshot = custom_video_snapshot(endpoint=spec.key, model_id="wan-fixed", endpoint_spec=spec)
    project = {"generation_mode": "storyboard"}

    facts = snapshot_video_request_facts(
        snapshot, project, route="storyboard", generation_type="i2v", requested_audio=True
    )
    payload = project_video_capabilities(
        snapshot, project, generation_type="i2v", requested_audio=True, include_planning=True
    )

    assert facts.duration_endpoint_fixed is True
    assert facts.supported_durations == ()
    assert facts.allowed_durations == ()
    assert payload["supported_durations"] == []
    assert payload["max_duration"] == 0
    assert payload["duration_endpoint_fixed"] is True
    assert payload["duration_endpoint_fixed_reason"] == "endpoint"
    assert payload["duration_constraints"]["allowed"] == []
    assert payload["duration_constraints"]["planning"] == ENDPOINT_FIXED_PLANNING_DURATIONS
