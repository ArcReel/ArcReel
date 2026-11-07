"""Direct gate_video_request capability/plan_frame_slots assembly tests."""

from pathlib import Path

import pytest

from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoCapabilities, VideoCapabilityError
from lib.backends.video_frame_slots import (
    FIRST_FRAME_ADAPTIVE_RATIO,
    gate_video_request,
    plan_frame_slots,
    resolve_first_frame_aspect_ratio,
    resolve_video_capabilities,
)
from lib.references.reference_compression import RefRole

CAPS_WITH_LAST_FRAME = VideoCapabilities(first_frame=True, last_frame=True, max_reference_images=4)
CAPS_NO_LAST_FRAME = VideoCapabilities(first_frame=True, last_frame=False, max_reference_images=4)
CAPS_WITH_AUDIO = VideoCapabilities(
    first_frame=True,
    max_reference_images=4,
    reference_audio_mode=ReferenceAudioMode.DIRECT,
    max_reference_audio_count=3,
)
CAPS_WITH_AUDIO_DURATION_LIMIT = VideoCapabilities(
    first_frame=True,
    max_reference_images=4,
    reference_audio_mode=ReferenceAudioMode.DIRECT,
    max_reference_audio_count=3,
    max_reference_audio_total_seconds=15.0,
)
CAPS_WITH_PROMPT_LIMIT = VideoCapabilities(first_frame=True, max_prompt_chars=100)


def _gate(caps: VideoCapabilities | None, **kwargs):
    """Run request validation and expose returns.

    Admission means no raise and ``is None``, explicitly asserted rather than implied by no
    test crash; otherwise emptied test bodies would silently pass.
    """
    return gate_video_request(caps=caps, provider="acme", model="acme-v1", **kwargs)


def _plan(caps: VideoCapabilities | None, **kwargs):
    """Follow production validate-then-assemble order; assembly is pure and never judges capabilities itself."""
    gate_kwargs = {k: v for k, v in kwargs.items() if k != "start_image"}
    _gate(caps, **gate_kwargs)
    return plan_frame_slots(**kwargs)


class TestFirstFrameGating:
    def test_first_frame_on_caps_without_first_frame_raises(self):
        """first_frame=False with actual first frames fails hard, matching last-frame rules."""
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(VideoCapabilities(first_frame=False), has_image=True, has_first_frame=True)

        assert exc.value.code == "video_first_frame_unsupported"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1"}

    def test_no_first_frame_on_caps_without_first_frame_passes(self):
        assert _gate(VideoCapabilities(first_frame=False, max_reference_images=2), has_image=True) is None

    def test_first_frame_without_caps_raises(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(None, has_image=True, has_first_frame=True)

        assert exc.value.code == "video_first_frame_unsupported"


class TestLastFrameGating:
    def test_unsupported_last_frame_with_end_image_raises(self):
        """Unsupported supplied last frames fail hard, never silently downgrade."""
        with pytest.raises(VideoCapabilityError) as exc:
            _plan(CAPS_NO_LAST_FRAME, start_image=Path("start.png"), end_image=Path("end.png"))

        assert exc.value.code == "video_last_frame_unsupported"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1"}

    def test_unsupported_last_frame_without_end_image_passes(self):
        """Unsupported but absent last frames admit normally, without slots."""
        plan = _plan(CAPS_NO_LAST_FRAME, start_image=Path("start.png"))

        assert plan.end_index is None
        assert [s.source for s in plan.specs] == [Path("start.png")]

    def test_supported_last_frame_with_end_image_passes(self):
        """Supported supplied last frames enter first_last slots."""
        plan = _plan(CAPS_WITH_LAST_FRAME, start_image=Path("start.png"), end_image=Path("end.png"))

        assert plan.start_index == 0
        assert plan.end_index == 1
        assert [s.role for s in plan.specs] == [RefRole.FRAME, RefRole.FRAME]

    def test_supported_last_frame_without_end_image_passes(self):
        plan = _plan(CAPS_WITH_LAST_FRAME, start_image=Path("start.png"))

        assert plan.end_index is None
        assert plan.start_index == 0

    def test_end_image_without_start_image_still_gated(self):
        """Last frames alone still gate; missing first frames never bypass validation."""
        with pytest.raises(VideoCapabilityError):
            _plan(CAPS_NO_LAST_FRAME, end_image=Path("end.png"))

    def test_end_image_only_takes_first_slot_when_supported(self):
        plan = _plan(CAPS_WITH_LAST_FRAME, end_image=Path("end.png"))

        assert plan.start_index is None
        assert plan.end_index == 0

    def test_uncharted_caps_without_end_image_passes(self):
        """caps=None with no optional paths admits without capability declarations."""
        assert _gate(None) is None

    def test_uncharted_caps_with_end_image_raises(self):
        """caps=None with last frames rejects unverified support, never assumes availability."""
        with pytest.raises(VideoCapabilityError) as exc:
            _plan(None, start_image=Path("start.png"), end_image=Path("end.png"))

        assert exc.value.code == "video_last_frame_unsupported"


class TestSlotAssembly:
    def test_no_inputs_yields_empty_plan(self):
        plan = _plan(CAPS_WITH_LAST_FRAME)

        assert plan.specs == []
        assert (plan.start_index, plan.end_index, plan.reference_start_index) == (None, None, None)

    def test_reference_images_follow_frames(self):
        """Array references always follow first/last frames; callers recover them by starting indices."""
        plan = _plan(
            CAPS_WITH_LAST_FRAME,
            start_image=Path("start.png"),
            end_image=Path("end.png"),
            reference_images=[Path("r1.png"), Path("r2.png")],
        )

        assert (plan.start_index, plan.end_index, plan.reference_start_index) == (0, 1, 2)
        assert [s.role for s in plan.specs] == [RefRole.FRAME, RefRole.FRAME, RefRole.ARRAY, RefRole.ARRAY]
        assert [s.source for s in plan.specs[plan.reference_start_index :]] == [Path("r1.png"), Path("r2.png")]

    def test_empty_reference_list_yields_no_reference_index(self):
        """Empty lists equal None for indexing: omit start indices and preserve original []/None through caller fallback."""
        plan = _plan(CAPS_WITH_LAST_FRAME, start_image=Path("start.png"), reference_images=[])

        assert plan.reference_start_index is None
        assert len(plan.specs) == 1

    def test_str_start_image_normalized_to_path(self):
        plan = _plan(CAPS_WITH_LAST_FRAME, start_image="start.png")

        assert plan.specs[0].source == Path("start.png")

    def test_pil_start_image_skips_compression(self):
        """PIL.Image first frames bypass compression, preserving request.start_image=None behavior."""
        from PIL import Image

        plan = _plan(CAPS_WITH_LAST_FRAME, start_image=Image.new("RGB", (2, 2)), end_image=Path("end.png"))

        assert plan.start_index is None
        assert plan.end_index == 0


class TestTextToVideoGating:
    def test_text_only_request_on_image_only_model_raises(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(VideoCapabilities(text_to_video=False))

        assert exc.value.code == "video_capability_missing_t2v"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1"}

    def test_image_request_on_image_only_model_passes(self):
        assert _gate(VideoCapabilities(text_to_video=False), has_image=True) is None

    def test_text_only_request_on_default_model_passes(self):
        assert _gate(VideoCapabilities()) is None


class TestResolveVideoCapabilities:
    def test_prefers_tier_aware_query(self):
        """Tier-aware video_capabilities_for_tier uses actual tiers, not conservative properties."""
        seen: dict[str, object] = {}

        class TierAwareBackend:
            video_capabilities = CAPS_NO_LAST_FRAME

            def video_capabilities_for_tier(self, service_tier: str, resolution: str | None = None):
                seen["service_tier"] = service_tier
                seen["resolution"] = resolution
                return CAPS_WITH_LAST_FRAME

        caps = resolve_video_capabilities(TierAwareBackend(), service_tier="pro", resolution="1080p")

        assert caps.last_frame is True
        assert seen == {"service_tier": "pro", "resolution": "1080p"}

    def test_falls_back_to_static_property(self):
        class PlainBackend:
            video_capabilities = CAPS_NO_LAST_FRAME

        assert resolve_video_capabilities(PlainBackend()).last_frame is False


class TestReferenceImageGating:
    def test_reference_images_beyond_limit_raise(self):
        """Over-limit requests fail hard; silent truncation would mislead users that all references applied."""
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(CAPS_WITH_LAST_FRAME, reference_images=[Path(f"r{i}.png") for i in range(5)])

        assert exc.value.code == "video_reference_images_exceeded"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1", "limit": 4, "count": 5}

    def test_reference_images_at_limit_pass(self):
        assert _gate(CAPS_WITH_LAST_FRAME, reference_images=[Path(f"r{i}.png") for i in range(4)]) is None

    def test_reference_images_on_zero_capacity_model_raise(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(VideoCapabilities(), reference_images=[Path("r.png")])

        assert exc.value.code == "video_reference_images_unsupported"

    def test_uncharted_caps_with_reference_images_raise(self):
        """caps=None references reject unverified support like last frames."""
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(None, reference_images=[Path("r.png")])

        assert exc.value.code == "video_reference_images_unsupported"


class TestReferenceAudioGating:
    def test_audio_on_model_without_capability_raises(self):
        """Audio without voice-input support fails hard, never drops audio yet bills random-voice output."""
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(CAPS_WITH_LAST_FRAME, reference_audio_files=[Path("a.mp3")])

        assert exc.value.code == "video_reference_audio_unsupported"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1"}

    def test_audio_within_limit_passes(self):
        assert _gate(CAPS_WITH_AUDIO, reference_audio_files=[Path("a.mp3"), Path("b.wav")]) is None

    def test_audio_at_limit_passes(self):
        assert _gate(CAPS_WITH_AUDIO, reference_audio_files=[Path(f"a{i}.mp3") for i in range(3)]) is None

    def test_audio_beyond_limit_raises(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(CAPS_WITH_AUDIO, reference_audio_files=[Path(f"a{i}.mp3") for i in range(4)])

        assert exc.value.code == "video_reference_audio_exceeded"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1", "limit": 3, "count": 4}

    def test_uncharted_caps_with_audio_raises(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(None, reference_audio_files=[Path("a.mp3")])

        assert exc.value.code == "video_reference_audio_unsupported"

    def test_empty_audio_list_passes_on_incapable_model(self):
        """Empty lists equal None; absent audio demands never hit audio capability gates."""
        assert _gate(CAPS_WITH_LAST_FRAME, reference_audio_files=[]) is None


class TestReferenceAudioDurationGating:
    def test_total_within_limit_passes(self):
        assert (
            _gate(
                CAPS_WITH_AUDIO_DURATION_LIMIT,
                reference_audio_files=[Path("a.mp3"), Path("b.wav")],
                reference_audio_total_seconds=14.9,
            )
            is None
        )

    def test_total_at_limit_passes(self):
        assert (
            _gate(
                CAPS_WITH_AUDIO_DURATION_LIMIT,
                reference_audio_files=[Path("a.mp3"), Path("b.wav")],
                reference_audio_total_seconds=15.0,
            )
            is None
        )

    def test_total_beyond_limit_raises(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(
                CAPS_WITH_AUDIO_DURATION_LIMIT,
                reference_audio_files=[Path("a.mp3"), Path("b.wav")],
                reference_audio_total_seconds=15.1,
            )

        assert exc.value.code == "video_reference_audio_duration_exceeded"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1", "limit": 15.0, "total": 15.1}

    def test_unknown_total_skips_check_even_when_limit_declared(self):
        """Failed duration probes total=None skip checks per existing fallback, never reject as overflow."""
        assert (
            _gate(
                CAPS_WITH_AUDIO_DURATION_LIMIT,
                reference_audio_files=[Path("a.mp3"), Path("b.wav")],
                reference_audio_total_seconds=None,
            )
            is None
        )

    def test_no_declared_limit_skips_check_regardless_of_total(self):
        """Undeclared total-duration caps None admit even huge totals; the dimension is inapplicable."""
        assert (
            _gate(
                CAPS_WITH_AUDIO,
                reference_audio_files=[Path("a.mp3"), Path("b.wav")],
                reference_audio_total_seconds=1000.0,
            )
            is None
        )


class TestPromptLengthGating:
    def test_prompt_within_limit_passes(self):
        assert _gate(CAPS_WITH_PROMPT_LIMIT, prompt="x" * 99) is None

    def test_prompt_at_limit_passes(self):
        assert _gate(CAPS_WITH_PROMPT_LIMIT, prompt="x" * 100) is None

    def test_prompt_beyond_limit_raises(self):
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(CAPS_WITH_PROMPT_LIMIT, prompt="x" * 101)

        assert exc.value.code == "video_prompt_too_long"
        assert exc.value.params == {"provider": "acme", "model": "acme-v1", "limit": 100, "count": 101}

    def test_limit_counts_characters_not_bytes(self):
        """Character counts weight Chinese/English equally; byte counts would falsely reject Chinese prompts."""
        assert _gate(CAPS_WITH_PROMPT_LIMIT, prompt="中" * 100) is None

    def test_no_declared_limit_skips_check(self):
        """Undeclared caps admit all lengths, never imply zero."""
        assert _gate(CAPS_WITH_LAST_FRAME, prompt="x" * 100_000) is None

    def test_none_caps_skips_check(self):
        """Unqueried caps=None never rejects prompts with unknown limits, avoiding false rejection."""
        assert _gate(None, prompt="x" * 100_000) is None

    def test_prompt_checked_before_optional_paths(self):
        """Prompt violations precede last-frame/optional-path errors; show first matches only."""
        with pytest.raises(VideoCapabilityError) as exc:
            _gate(CAPS_WITH_PROMPT_LIMIT, prompt="x" * 101, end_image=Path("end.png"))

        assert exc.value.code == "video_prompt_too_long"


class TestGateAndAssemblySeparation:
    def test_plan_frame_slots_does_not_gate(self):
        """Pure assembly still creates unsupported last-frame slots; gates alone reject them."""
        plan = plan_frame_slots(start_image=Path("start.png"), end_image=Path("end.png"))

        assert (plan.start_index, plan.end_index) == (0, 1)


class TestResolveFirstFrameAspectRatio:
    CAPS_ADAPTIVE_ONLY = VideoCapabilities(first_frame_ratio_adaptive_only=True)

    def test_declared_constraint_with_first_frame_forces_adaptive(self):
        ratio = resolve_first_frame_aspect_ratio(
            caps=self.CAPS_ADAPTIVE_ONLY, aspect_ratio="9:16", has_first_frame=True
        )

        assert ratio == FIRST_FRAME_ADAPTIVE_RATIO

    def test_declared_constraint_without_first_frame_passes_through(self):
        """No first frame, text-only/reference-only paths, bypasses this constraint and forwards user ratios unchanged."""
        ratio = resolve_first_frame_aspect_ratio(
            caps=self.CAPS_ADAPTIVE_ONLY, aspect_ratio="9:16", has_first_frame=False
        )

        assert ratio == "9:16"

    def test_default_caps_unaffected(self):
        """Default False preserves existing model payloads."""
        ratio = resolve_first_frame_aspect_ratio(caps=CAPS_WITH_LAST_FRAME, aspect_ratio="16:9", has_first_frame=True)

        assert ratio == "16:9"

    def test_none_caps_passes_through(self):
        ratio = resolve_first_frame_aspect_ratio(caps=None, aspect_ratio="1:1", has_first_frame=True)

        assert ratio == "1:1"
