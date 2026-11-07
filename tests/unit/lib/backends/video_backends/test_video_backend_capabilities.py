from pathlib import Path

import pytest

from arcreel_market_core.video_backend_contract import (
    ReferenceAudioMode,
    VideoAudioMode,
    VideoCapabilities,
    VideoGenerationRequest,
)


class TestVideoCapabilities:
    def test_defaults(self):
        caps = VideoCapabilities()
        assert caps.text_to_video is True
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.max_reference_images == 0
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0
        assert caps.first_frame_ratio_adaptive_only is False

    def test_first_frame_ratio_adaptive_only_declared(self):
        caps = VideoCapabilities(first_frame_ratio_adaptive_only=True)
        assert caps.first_frame_ratio_adaptive_only is True

    def test_first_last(self):
        caps = VideoCapabilities(last_frame=True)
        assert caps.last_frame is True

    def test_custom_values(self):
        caps = VideoCapabilities(
            last_frame=True,
            max_reference_images=9,
            reference_audio_mode=ReferenceAudioMode.DIRECT,
            max_reference_audio_count=3,
        )
        assert caps.last_frame is True
        assert caps.max_reference_images == 9
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 3


class TestVideoGenerationRequestNewFields:
    def test_end_image_default_none(self):
        req = VideoGenerationRequest(prompt="t", output_path=Path("/tmp/o.mp4"))
        assert req.end_image is None
        assert req.reference_images is None

    def test_end_image_set(self):
        req = VideoGenerationRequest(
            prompt="t",
            output_path=Path("/tmp/o.mp4"),
            start_image=Path("/tmp/f.png"),
            end_image=Path("/tmp/l.png"),
        )
        assert req.end_image == Path("/tmp/l.png")

    def test_reference_images(self):
        req = VideoGenerationRequest(
            prompt="t",
            output_path=Path("/tmp/o.mp4"),
            reference_images=[Path("/tmp/r1.png"), Path("/tmp/r2.png")],
        )
        assert len(req.reference_images) == 2

    def test_existing_fields_unchanged(self):
        """Ensure existing fields still work as before."""
        req = VideoGenerationRequest(
            prompt="test prompt",
            output_path=Path("/tmp/out.mp4"),
            aspect_ratio="16:9",
            duration_seconds=5,
            resolution="720p",
            start_image=Path("/tmp/start.png"),
            generate_audio=False,
            project_name="my_project",
            service_tier="flex",
            seed=42,
        )
        assert req.prompt == "test prompt"
        assert req.start_image == Path("/tmp/start.png")
        assert req.generate_audio is False
        assert req.seed == 42


class TestGrokVideoCapabilities:
    def test_no_start_frame_overlay_field(self):
        """Grok sends both image_url and reference_image_urls, but the fields are consolidated; no separate combined capability is declared."""
        from unittest.mock import patch

        from lib.backends.video_backends.grok import GrokVideoBackend

        with patch("lib.backends.video_backends.grok.create_grok_client"):
            caps = GrokVideoBackend(api_key="test-key").video_capabilities
        # The channel supports reference images without a limit; model entries supply their limits.
        assert caps.max_reference_images is None
        assert not hasattr(caps, "reference_images_with_start_frame")


class TestVideoCapabilitiesForModel:
    """Each backend's client-free static caps method declares channel capabilities without instances / api_key; effective capabilities then evaluate both channel and entry.

    The resolver uses this pure-function path for reference-image limits, so it must not construct SDK clients or validate api_key."""

    def test_ark_seedance_2_returns_nine(self):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.backends.video_backends.ark import Seedance2VideoBackend

        # Obtain the channel declaration (unlimited reference images) without an instance, Ark SDK client, or api_key.
        assert (
            Seedance2VideoBackend.video_capabilities_for_model("doubao-seedance-2-0-260128").max_reference_images
            is None
        )
        # Effective limits come from entries.
        assert builtin_video_capabilities_for_model("ark", "doubao-seedance-2-0-260128").max_reference_images == 9

    def test_vidu_returns_seven(self):
        from lib.backends.video_backends.vidu import ViduVideoBackend

        assert ViduVideoBackend.video_capabilities_for_model("viduq3-turbo").max_reference_images == 7

    def test_minimax_h3_declares_multimodal_limits(self):
        from arcreel_market_core.video_backend_contract import ReferenceAudioMode
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        caps = builtin_video_capabilities_for_model("minimax", "MiniMax-H3")
        assert caps.max_reference_images == 9
        assert caps.last_frame is True
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 3
        assert caps.max_prompt_chars == 7000
        assert caps.first_frame_ratio_adaptive_only is True

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("MiniMax-Hailuo-2.3", True),
            # Fast supports only image-to-video: the required first-frame input in minimax-hailuo-v1-fast supplies this narrowing.
            ("MiniMax-Hailuo-2.3-Fast", False),
            ("MiniMax-H3", True),
        ],
    )
    def test_minimax_declares_text_to_video(self, model: str, expected: bool):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        assert builtin_video_capabilities_for_model("minimax", model).text_to_video is expected

    @pytest.mark.parametrize(
        ("model", "expected"),
        [("kling-v3", True), ("kling-video-o1", False)],
    )
    def test_kling_declares_text_to_video(self, model: str, expected: bool):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        assert builtin_video_capabilities_for_model("kling", model).text_to_video is expected

    @pytest.mark.parametrize(
        ("model", "expected"),
        [("viduq3-pro", True), ("viduq3", False), ("vidu2.0", False)],
    )
    def test_vidu_declares_text_to_video(self, model: str, expected: bool):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        assert builtin_video_capabilities_for_model("vidu", model).text_to_video is expected

    def test_v2_returns_four(self):
        from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

        caps = ENDPOINT_REGISTRY["v2-video-generations"].video_caps_for_model
        assert caps is not None
        assert caps("whatever", None).max_reference_images == 4

    def test_instance_property_delegates_to_static(self):
        """Instance video_capabilities delegates to the static method, keeping the backend the single source of truth.

        Patch create_ark_client: test only property-to-static-method delegation, without actually constructing
        an Ark SDK client in __init__ (caps does not depend on the client)."""
        from unittest.mock import patch

        from lib.backends.video_backends.ark import Seedance25VideoBackend

        with patch("lib.backends.video_backends.ark.create_ark_client"):
            backend = Seedance25VideoBackend(api_key="k", model="doubao-seedance-2-0")
        assert backend.video_capabilities == Seedance25VideoBackend.video_capabilities_for_model("doubao-seedance-2-0")


#: Pin audio stance for every built-in video model, separately by execution path: ``controllable`` = request carries an audio switch;
#: ``always_on`` = audio always present, switch uncontrollable; ``always_off`` = this path produces no audio. Values are ``(i2v, r2v)``.
#:
#: Register every new video model here, forcing an explicit audio stance. A model missing its backend declaration reaches
#: this table with the ``VideoCapabilities`` default (controllable); disagreement with the registered intent then fails CI.
#: Keep the table here, not in registry tests: backend VideoCapabilities is the audio-shape source of truth; guard it there.
_VIDEO_AUDIO_STANCES: dict[tuple[str, str], tuple[str, str]] = {
    ("agnes", "agnes-video-2.5"): ("always_on", "always_on"),
    ("agnes", "agnes-video-2.5-flash"): ("always_on", "always_on"),
    ("agnes", "agnes-video-v2.0"): ("always_off", "always_off"),
    ("ark", "doubao-seedance-2-0-260128"): ("controllable", "controllable"),
    ("ark", "doubao-seedance-2-0-fast-260128"): ("controllable", "controllable"),
    ("ark", "doubao-seedance-2-0-mini-260615"): ("controllable", "controllable"),
    ("ark", "doubao-seedance-2-5-260628"): ("controllable", "controllable"),
    ("ark-agent-plan", "doubao-seedance-2.0"): ("controllable", "controllable"),
    ("ark-agent-plan", "doubao-seedance-2.0-fast"): ("controllable", "controllable"),
    ("ark-agent-plan", "doubao-seedance-2.0-mini"): ("controllable", "controllable"),
    ("ark-agent-plan", "doubao-seedance-2.5"): ("controllable", "controllable"),
    ("dashscope", "happyhorse-1.0-i2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.0-r2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.0-t2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.1-i2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.1-r2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.1-t2v"): ("always_on", "always_on"),
    ("dashscope", "wan2.7-i2v"): ("always_on", "always_on"),
    ("dashscope", "wan2.7-r2v"): ("always_on", "always_on"),
    ("dashscope", "wan2.7-t2v"): ("always_on", "always_on"),
    ("dashscope", "wan3.0-video"): ("controllable", "controllable"),
    ("dashscope", "wan3.0-video-prime"): ("controllable", "controllable"),
    ("gemini-aistudio", "veo-3.1-fast-generate-preview"): ("always_on", "always_on"),
    ("gemini-aistudio", "veo-3.1-generate-preview"): ("always_on", "always_on"),
    ("gemini-aistudio", "veo-3.1-lite-generate-preview"): ("always_on", "always_on"),
    ("gemini-vertex", "veo-3.1-fast-generate-001"): ("controllable", "controllable"),
    ("gemini-vertex", "veo-3.1-generate-001"): ("controllable", "controllable"),
    ("grok", "grok-imagine-video"): ("controllable", "controllable"),
    ("grok", "grok-imagine-video-1.5"): ("controllable", "controllable"),
    ("grok", "grok-imagine-video-1.5-lite"): ("controllable", "controllable"),
    ("kling", "kling-v2-5-turbo"): ("always_off", "always_off"),
    # The three audio-capable Kling tiers have a sound switch on image/text-to-video paths; the native multi-image subject (R2V) schema
    # omits that field, so its final video is always silent.
    ("kling", "kling-v2-6"): ("controllable", "always_off"),
    ("kling", "kling-v3"): ("controllable", "always_off"),
    ("kling", "kling-v3-omni"): ("controllable", "always_off"),
    ("kling", "kling-video-o1"): ("always_off", "always_off"),
    ("minimax", "MiniMax-H3"): ("always_on", "always_on"),
    ("minimax", "MiniMax-H3-Highspeed"): ("always_on", "always_on"),
    ("minimax", "MiniMax-H3-Lite"): ("always_on", "always_on"),
    ("minimax", "MiniMax-H3-Max"): ("always_on", "always_on"),
    ("minimax", "MiniMax-Hailuo-2.3"): ("always_off", "always_off"),
    ("minimax", "MiniMax-Hailuo-2.3-Fast"): ("always_off", "always_off"),
    ("vidu", "vidu2.0"): ("always_off", "always_off"),
    ("vidu", "viduq3"): ("controllable", "controllable"),
    ("vidu", "viduq3-pro"): ("controllable", "controllable"),
    ("vidu", "viduq3-pro-fast"): ("controllable", "controllable"),
    ("vidu", "viduq3-turbo"): ("controllable", "controllable"),
}


class TestVideoAudioTrack:
    """Audio shape: per-path declarations, per-path lookup, and a registry-wide stance guard."""

    def test_default_is_controllable_and_shared_by_both_routes(self):
        """Undeclared means no signal and no narrowing: treat both paths as switch-controllable."""
        caps = VideoCapabilities()
        assert caps.audio_track is VideoAudioMode.CONTROLLABLE
        assert caps.reference_route_audio_track is None
        assert caps.audio_track_for_route("i2v") is VideoAudioMode.CONTROLLABLE
        assert caps.audio_track_for_route("r2v") is VideoAudioMode.CONTROLLABLE

    def test_reference_route_declaration_narrows_only_that_route(self):
        caps = VideoCapabilities(
            audio_track=VideoAudioMode.CONTROLLABLE,
            reference_route_audio_track=VideoAudioMode.ALWAYS_OFF,
        )
        assert caps.audio_track_for_route("i2v") is VideoAudioMode.CONTROLLABLE
        assert caps.audio_track_for_route("r2v") is VideoAudioMode.ALWAYS_OFF

    def test_video_route_vocabulary_matches_generation_type_buckets(self):
        """VideoRoute and lib.config.resolver.VideoGenerationType share the same bucket vocabulary.

        Layering forbids the backend from importing config, so each declares it separately. If values drift,
        ``audio_track_for_route`` silently uses i2v values for r2v.
        """
        from typing import get_args

        from arcreel_market_core.video_backend_contract import VideoRoute
        from lib.config.resolver import VideoGenerationType

        assert get_args(VideoRoute) == get_args(VideoGenerationType)

    def test_every_video_model_matches_declared_stance(self):
        """Backend per-path values for all built-in video models equal the whole table above, not merely a subset."""
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.catalog import query as catalog
        from lib.config.registry import PROVIDER_REGISTRY

        actual: dict[tuple[str, str], tuple[str, str]] = {}
        for provider_id in PROVIDER_REGISTRY:
            for model_id, info in catalog.list_models(provider_id).items():
                if info.media_type != "video":
                    continue
                caps = builtin_video_capabilities_for_model(provider_id, model_id)
                actual[(provider_id, model_id)] = (
                    caps.audio_track_for_route("i2v").value,
                    caps.audio_track_for_route("r2v").value,
                )
        assert actual == _VIDEO_AUDIO_STANCES

    def test_registry_lookup_matches_backend_declaration(self):
        """Derived lib.config queries equal backend declarations; presentation and enqueue prechecks read this same source."""
        from lib.config.resolver import builtin_video_audio_track

        for (provider_id, model_id), (i2v, r2v) in _VIDEO_AUDIO_STANCES.items():
            assert builtin_video_audio_track(provider_id, model_id, generation_type="i2v") == i2v
            assert builtin_video_audio_track(provider_id, model_id, generation_type="r2v") == r2v

    def test_lookup_returns_none_without_signal(self):
        """Nonvideo models / unknown providers have no per-model declaration; return None for callers to apply no-signal/no-narrowing semantics."""
        from lib.config.resolver import builtin_video_audio_track

        assert builtin_video_audio_track("dashscope", "wan2.7-image", generation_type="i2v") is None
        assert builtin_video_audio_track("custom-1", "whatever", generation_type="i2v") is None
        assert builtin_video_audio_track("kling", "not-a-registered-model", generation_type="i2v") is None


class TestVideoCapabilitySingleSourceOfTruth:
    """Scan the whole catalog: only the backend hand-declares built-in video input modes, reference-image limits, and audio shape.

    Catalog model information `CatalogModel` does not describe these dimensions: a second declaration has no comparison counterpart, allowing
    undetected drift and directing reviewers to the copy unused by resolution. These cases guard the single-source structure.
    """

    def test_registry_declares_no_video_capability_bits(self):
        """Video model capabilities must not contain input-mode or audio tokens; VideoCapabilities is the source of truth."""
        from lib.catalog import query as catalog
        from lib.config.registry import PROVIDER_REGISTRY

        banned = {"text_to_video", "image_to_video", "reference_to_video", "generate_audio"}
        offenders = [
            f"{provider_id}/{model_id}: {sorted(banned & set(info.capabilities))}"
            for provider_id in PROVIDER_REGISTRY
            for model_id, info in catalog.list_models(provider_id).items()
            if info.media_type == "video" and banned & set(info.capabilities)
        ]
        assert offenders == []

    def test_model_info_has_no_audio_cap_field(self):
        """Likewise, CatalogModel must not acquire audio fields.

        One per-model declaration cannot express branching by execution subpath within a model. Adding it would create
        a split where the UI allows disabling audio but execution silently discards the setting.
        """
        from dataclasses import fields

        from lib.catalog.query import CatalogModel

        names = {f.name for f in fields(CatalogModel)}
        assert not {n for n in names if "audio" in n}

    def test_every_registry_video_model_resolves_backend_capabilities(self):
        """Every built-in video model obtains a backend capability declaration; the single source must cover all built-in providers."""
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.catalog import query as catalog
        from lib.config.registry import PROVIDER_REGISTRY

        unresolved: list[str] = []
        for provider_id in PROVIDER_REGISTRY:
            for model_id, info in catalog.list_models(provider_id).items():
                if info.media_type != "video":
                    continue
                try:
                    builtin_video_capabilities_for_model(provider_id, model_id)
                except ValueError as exc:
                    unresolved.append(f"{provider_id}/{model_id}: {exc}")
        assert unresolved == []
