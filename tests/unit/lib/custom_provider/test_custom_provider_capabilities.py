"""Custom-provider video capability composition (system judgment ⊕ user overrides)."""

from __future__ import annotations

import logging

import pytest

from arcreel_market_core.video_backend_contract import ReferenceAudioMode, VideoAudioMode, VideoCapabilities
from lib.custom_provider.capabilities import (
    CAPABILITY_OVERRIDE_FIELDS,
    capability_type_name,
    capability_value_matches,
    filter_valid_overrides,
    synthesize_video_capabilities,
    synthesize_video_capabilities_with_overrides,
    system_video_capabilities,
    two_sided_video_capabilities,
    video_capabilities_from_definition,
)
from lib.custom_provider.endpoints import EndpointSpec, comfyui_endpoint_spec
from tests.factories import comfyui_endpoint_definition


class TestOverrideFieldSchema:
    def test_schema_covers_every_video_capabilities_field(self):
        """Override schemas generically support every VideoCapabilities field, with keys matching the dataclass exactly."""
        assert set(CAPABILITY_OVERRIDE_FIELDS) == {
            "text_to_video",
            "first_frame",
            "last_frame",
            "max_reference_images",
            "reference_audio_mode",
            "max_reference_audio_count",
            "max_reference_audio_total_seconds",
            "reference_audio_per_image",
            "max_prompt_chars",
            "first_frame_ratio_adaptive_only",
            "audio_track",
            "reference_route_audio_track",
        }
        assert CAPABILITY_OVERRIDE_FIELDS["last_frame"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["text_to_video"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["max_reference_images"] is int
        assert CAPABILITY_OVERRIDE_FIELDS["reference_audio_mode"] is ReferenceAudioMode
        assert CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_count"] is int
        assert CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_total_seconds"] == (float | None)
        assert CAPABILITY_OVERRIDE_FIELDS["max_prompt_chars"] == (int | None)
        assert CAPABILITY_OVERRIDE_FIELDS["reference_audio_per_image"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["first_frame_ratio_adaptive_only"] is bool
        assert CAPABILITY_OVERRIDE_FIELDS["audio_track"] is VideoAudioMode
        assert CAPABILITY_OVERRIDE_FIELDS["reference_route_audio_track"] == (VideoAudioMode | None)

    def test_open_dimensions_cover_both_kinds(self):
        """Overrides expose model facts and channel facts, including channel audio shape (``docs/adr/0104``)."""
        from server.routers.custom_providers import CAPABILITY_OVERRIDE_ALLOWLIST

        assert {
            # Model facts.
            "image_input",
            "structured_output",
            "max_output_tokens",
            "text_to_image",
            "image_to_image",
            "durations",
            "resolutions",
            "duration_constraints",
            # Channel facts.
            "text_to_video",
            "first_frame",
            "last_frame",
            "max_reference_images",
            "reference_audio_mode",
            "max_reference_audio_count",
            "max_reference_audio_total_seconds",
            "max_prompt_chars",
            "audio_track",
            "reference_route_audio_track",
        } == CAPABILITY_OVERRIDE_ALLOWLIST


class TestCapabilitiesFromDefinition:
    """Declarative protocol capabilities: accept explicit declarations, deriving ``text_to_video`` mechanically from required image inputs."""

    @staticmethod
    def _definition(*, source: str = "start_image", required: bool | None = None, **capabilities: object) -> dict:
        declaration: dict[str, object] = {"source": source, "encoding": "data_uri"}
        if required is not None:
            declaration["required"] = required
        return {"inputs": {"asset": declaration}, "capabilities": capabilities}

    def test_required_image_input_yields_no_text_to_video(self):
        caps = video_capabilities_from_definition(
            self._definition(required=True, first_frame=True, text_to_video=False)
        )
        assert caps.text_to_video is False
        assert caps.first_frame is True

    def test_optional_image_input_keeps_text_to_video(self):
        caps = video_capabilities_from_definition(
            self._definition(required=False, first_frame=True, text_to_video=True)
        )
        assert caps.text_to_video is True

    def test_undeclared_required_defaults_to_optional(self):
        """Absent ``required`` means optional; do not mistake missing keys for required inputs and disable text generation."""
        caps = video_capabilities_from_definition(self._definition(first_frame=True))
        assert caps.text_to_video is True

    def test_required_audio_input_does_not_close_text_to_video(self):
        """Only image inputs affect derivation; required reference audio does not change feasibility of pure text-to-video requests."""
        caps = video_capabilities_from_definition(
            self._definition(
                source="reference_audio_files",
                required=True,
                reference_audio_mode="direct",
                max_reference_audio_count=2,
            ),
        )
        assert caps.text_to_video is True

    def test_definition_without_first_frame_declaration_does_not_inherit_dataclass_default(self):
        """The ``VideoCapabilities`` first_frame=True default does not apply to declarations; undeclared means unsupported."""
        caps = video_capabilities_from_definition({"inputs": {}, "capabilities": {}})
        assert caps.first_frame is False
        assert caps.text_to_video is True

    @pytest.mark.parametrize("limit", [0, 0.0, 1, 1.0])
    def test_integral_reference_limits_become_integer_capabilities(self, limit: int | float):
        caps = video_capabilities_from_definition(
            self._definition(source="reference_images", max_reference_images=limit)
        )

        assert caps.max_reference_images == limit
        assert type(caps.max_reference_images) is int

    @pytest.mark.parametrize(
        "capabilities",
        [
            {"not_a_capability": True},
            {"max_reference_images": -1},
            {"max_reference_images": 1.5},
            {"max_reference_images": True},
            {"first_frame": "yes"},
        ],
        ids=["unknown-key", "negative-count", "fractional-count", "boolean-count", "wrong-type"],
    )
    def test_invalid_capability_declaration_raises(self, capabilities: dict[str, object]):
        """Definition capabilities do not use override-style discard-invalid-keys-and-continue: definitions are the sole source, so invalid declarations must fail."""
        with pytest.raises(ValueError, match="invalid capability"):
            video_capabilities_from_definition({"inputs": {}, "capabilities": capabilities})


class TestOptionalDimensionSchema:
    """Optional-dimension annotations (``T | None``) are not ``type`` objects; no schema consumer may assume otherwise."""

    def test_type_name_renders_optional_annotation_without_crashing(self):
        """Produce display names for types.UnionType too; validation-failure paths must not be more fragile than the validated value."""
        assert capability_type_name(CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_count"]) == "int"
        assert capability_type_name(CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_total_seconds"]) == "float | None"

    @pytest.mark.parametrize("value", [15.0, 15, 0, None])
    def test_optional_float_accepts_none_and_non_negative_numbers(self, value):
        """None means the backend declares no such constraint; JSON 15 and 15.0 are the same number, and both are accepted."""
        assert capability_value_matches(value, CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_total_seconds"])

    @pytest.mark.parametrize("value", [-1.0, True, "15", [15]])
    def test_optional_float_rejects_negative_bool_and_non_numeric(self, value):
        assert not capability_value_matches(value, CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_total_seconds"])

    def test_none_rejected_on_non_optional_dimension(self):
        assert not capability_value_matches(None, CAPABILITY_OVERRIDE_FIELDS["max_reference_audio_count"])

    def test_invalid_optional_float_override_is_dropped_with_warning(self, caplog):
        """Dirty existing rows / invalid non-API writes degrade with logging instead of raising AttributeError."""
        with caplog.at_level(logging.WARNING):
            applied = filter_valid_overrides(
                endpoint="vidu-video-q3-turbo",
                model_id="viduq3",
                overrides={"max_reference_audio_total_seconds": "15"},
            )
        assert applied == {}
        assert "float | None" in caplog.text

    def test_integer_override_is_coerced_to_float_on_merge(self):
        """After validating integer literals, merging restores float so the field retains no int."""
        caps = synthesize_video_capabilities(
            endpoint="vidu-video-q3-turbo",
            model_id="viduq3",
            overrides={"max_reference_audio_total_seconds": 12},
        )
        assert caps.max_reference_audio_total_seconds == 12.0
        assert isinstance(caps.max_reference_audio_total_seconds, float)


class TestSystemCapabilities:
    def test_endpoint_declaring_int_cap_rebuilds_capabilities(self):
        """Explicit endpoint hard limits pass unchanged to max_reference_images; other dimensions take defaults."""
        caps = system_video_capabilities(endpoint="openai-video", model_id="sora-2")
        assert caps == VideoCapabilities(first_frame=True, last_frame=False, max_reference_images=1)

    def test_endpoint_declaring_zero_cap_yields_no_reference_images(self):
        caps = system_video_capabilities(endpoint="newapi-video", model_id="whatever")
        assert caps.max_reference_images == 0

    def test_endpoint_with_caps_fn_delegates_to_backend_declaration(self):
        """Without endpoint hard limits, call the backend per-model pure function and take all four declared fields."""
        from lib.backends.video_backends.vidu import ViduQ3TurboVideoBackend

        caps = system_video_capabilities(endpoint="vidu-video-q3-turbo", model_id="viduq3")
        assert caps == ViduQ3TurboVideoBackend.video_capabilities_for_model("viduq3")

    def test_non_video_endpoint_raises(self):
        with pytest.raises(ValueError, match="not video"):
            system_video_capabilities(endpoint="openai-chat", model_id="gpt-4o")

    def test_unknown_endpoint_raises(self):
        with pytest.raises(ValueError, match=r"unknown endpoint"):
            system_video_capabilities(endpoint="no-such-endpoint", model_id="x")


class TestSynthesize:
    @pytest.mark.parametrize("overrides", [None, {}])
    def test_absent_overrides_follow_system(self, overrides: dict | None):
        """Column NULL and empty dict both mean follow system judgment for every dimension."""
        assert synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides=overrides
        ) == system_video_capabilities(endpoint="openai-video", model_id="sora-2")

    def test_missing_key_follows_system(self):
        """An existing dict missing a key follows system judgment for that dimension; other keys override normally."""
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance-2", model_id="doubao-seedance-1-0-pro-fast-251015", overrides={"first_frame": False}
        )
        assert caps.first_frame is False
        # Unmatched entry: conservative last-frame fallback is off; references follow only channel limits (Ark is unlimited; model unlisted).
        assert caps.last_frame is False
        assert caps.max_reference_images is None

    def test_force_on(self):
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance-2",
            model_id="doubao-seedance-1-0-pro-fast-251015",
            overrides={"last_frame": True, "max_reference_images": 4},
        )
        assert caps.last_frame is True
        assert caps.max_reference_images == 4

    def test_force_off(self):
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"first_frame": False}
        )
        assert caps.first_frame is False
        # Boolean overrides do not affect untouched numeric dimensions.
        assert caps.max_reference_images == 1

    def test_force_off_on_last_frame(self):
        """last_frame is among the first exposed overrides; separately verify system True forced off.

        Use viduq3-turbo, not viduq3: the latter entry rejects last frames (reference-to-video only), making two-sided evaluation already False
        and unable to demonstrate forced disabling.
        """
        assert two_sided_video_capabilities(endpoint="vidu-video-q3-turbo", model_id="viduq3-turbo").last_frame is True
        caps = synthesize_video_capabilities(
            endpoint="vidu-video-q3-turbo", model_id="viduq3-turbo", overrides={"last_frame": False}
        )
        assert caps.last_frame is False
        assert caps.first_frame is True

    def test_int_field_override(self):
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"max_reference_images": 3}
        )
        assert caps.max_reference_images == 3

    def test_system_capabilities_not_mutated_across_calls(self):
        """Composition returns a new instance without mutating system judgment (caps_fn may return shared objects)."""
        first = synthesize_video_capabilities(
            endpoint="ark-seedance-2", model_id="doubao-seedance-1-0-pro-fast-251015", overrides={"last_frame": True}
        )
        second = two_sided_video_capabilities(endpoint="ark-seedance-2", model_id="doubao-seedance-1-0-pro-fast-251015")
        assert first.last_frame is True
        assert second.last_frame is False


class TestTolerance:
    """Existing-data tolerance: composition is the execution layer's final boundary; invalid data degrades to system judgment instead of raising.

    Validation belongs on writes; existing rows, manual SQL, and narrowed field sets can expose unknown keys here.
    Raising would disable the whole generation chain because of one dirty configuration.
    """

    def test_unknown_key_ignored_with_warning(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="ark-seedance-2",
                model_id="doubao-seedance-1-0-pro-fast-251015",
                overrides={"last_frame": True, "audio_track": True},
            )
        assert caps.last_frame is True
        assert caps == VideoCapabilities(first_frame=True, last_frame=True, max_reference_images=None)
        assert "audio_track" in caplog.text

    def test_last_frame_override_ignored_when_endpoint_lacks_end_image_support(self, caplog: pytest.LogCaptureFixture):
        """The openai-video delegate does not send end_image; overrides would claim support while execution silently drops frames, so degrade to system judgment."""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"last_frame": True}
            )
        assert caps.last_frame is False
        assert "last_frame" in caplog.text

    @pytest.mark.parametrize("bad", ["true", 1, None, [], {"x": 1}])
    def test_non_bool_value_on_bool_field_ignored(self, bad: object, caplog: pytest.LogCaptureFixture):
        """Boolean dimensions accept only real bools; do not semantically map loose truthy values such as 1/"true"; always degrade to follow system judgment."""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"last_frame": bad}
            )
        assert caps.last_frame is False
        assert "last_frame" in caplog.text

    @pytest.mark.parametrize("bad", [True, -1, "2", 1.5, None])
    def test_bad_value_on_int_field_ignored(self, bad: object, caplog: pytest.LogCaptureFixture):
        """Numeric dimensions accept only nonnegative ints; explicitly exclude bool, an int subclass."""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"max_reference_images": bad}
            )
        assert caps.max_reference_images == 1
        assert "max_reference_images" in caplog.text

    @pytest.mark.parametrize("bad", ["last_frame", ["last_frame"], 42])
    def test_non_dict_overrides_ignored(self, bad: object, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(endpoint="openai-video", model_id="sora-2", overrides=bad)
        assert caps == system_video_capabilities(endpoint="openai-video", model_id="sora-2")
        assert caplog.records

    def test_last_frame_override_on_retired_endpoint_does_not_raise(self, caplog: pytest.LogCaptureFixture):
        """When an upgrade removes an endpoint from the registry, get_endpoint_spec raises ValueError. filter_valid_overrides
        is the final API-response filtering boundary: invalid existing configuration must not break it too; discard with a warning instead of raising."""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            applied = filter_valid_overrides(
                endpoint="no-such-retired-endpoint", model_id="whatever", overrides={"last_frame": True}
            )
        assert applied == {}
        assert "last_frame" in caplog.text


class TestComfyuiOverridesAreIgnored:
    """ComfyUI capabilities derive only from node bindings; discard all existing overrides on read.

    Ignore them in :func:`filter_valid_overrides`, the seam shared by API echo and runtime capability composition.
    Blocking only route writes would still let persisted overrides affect execution.
    """

    @staticmethod
    def _spec() -> EndpointSpec:
        return comfyui_endpoint_spec("ce-7", comfyui_endpoint_definition())

    def test_a_stored_override_is_dropped_with_a_warning(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            applied = filter_valid_overrides(
                endpoint="ce-7",
                model_id="wan-t2v",
                overrides={"last_frame": True},
                endpoint_spec=self._spec(),
            )
        assert applied == {}
        assert "ce-7" in caplog.text

    def test_the_runtime_synthesis_path_drops_it_too(self):
        """An override must not make execution-visible capabilities diverge from settings-page echo."""
        caps, applied = synthesize_video_capabilities_with_overrides(
            endpoint="ce-7",
            model_id="wan-t2v",
            overrides={"last_frame": True, "max_reference_images": 4},
            endpoint_spec=self._spec(),
        )
        assert applied == {}
        assert caps.last_frame is False
        assert caps.max_reference_images == 0


class TestReferenceAudioOverrideGuards:
    """Three audio guards: EndpointSpec transport declarations, post-merge invariants, and consistent enum judgments on both sides."""

    def test_enum_value_matches_accepts_declared_literal(self):
        assert capability_value_matches("direct", ReferenceAudioMode)
        assert capability_value_matches("none", ReferenceAudioMode)

    @pytest.mark.parametrize("value", ["DIRECT", "Direct", "native", "", True, 1, None])
    def test_enum_value_matches_rejects_everything_else(self, value):
        """Reject every value outside the vocabulary; neither normalize case nor guess synonyms."""
        assert not capability_value_matches(value, ReferenceAudioMode)

    def test_direct_override_applies_on_audio_capable_endpoint(self):
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance-2",
            model_id="doubao-seedance-1-0-pro-fast-251015",
            overrides={"reference_audio_mode": "direct", "max_reference_audio_count": 2},
        )
        assert caps.reference_audio_mode == ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 2

    def test_direct_override_ignored_when_endpoint_cannot_send_audio(self, caplog: pytest.LogCaptureFixture):
        """If endpoint delegates cannot send reference audio, overrides falsify declarations; degrade to system judgment."""
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="openai-video", model_id="sora-2", overrides={"reference_audio_mode": "direct"}
            )

        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert "reference_audio_mode=direct" in caplog.text

    def test_none_override_needs_no_endpoint_support(self):
        """Disabling voice capability needs no transport support; guards block only claims of support."""
        caps = synthesize_video_capabilities(
            endpoint="openai-video", model_id="sora-2", overrides={"reference_audio_mode": "none"}
        )
        assert caps.reference_audio_mode == ReferenceAudioMode.NONE

    def test_invalid_enum_value_falls_back_to_system_judgement(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="ark-seedance-2",
                model_id="doubao-seedance-2-0-260128",
                overrides={"reference_audio_mode": "DIRECT"},
            )

        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT  # System judgment is already direct.
        assert "reference_audio_mode" in caplog.text

    def test_direct_without_positive_count_falls_back_to_none(self, caplog: pytest.LogCaptureFixture):
        """Sparse overrides set only mode: merging yields direct ⊕ limit 0, each dimension valid but their combination meaningless.

        Without correction, the gate passes mode then hits limit 0, reporting at most 0 clips instead of no reference-audio support;
        users cannot pass even after reducing characters to zero clips.
        """
        with caplog.at_level(logging.WARNING, logger="lib.custom_provider.capabilities"):
            caps = synthesize_video_capabilities(
                endpoint="ark-seedance-2",
                model_id="doubao-seedance-1-0-pro-fast-251015",
                overrides={"reference_audio_mode": "direct"},
            )

        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0
        assert "max_reference_audio_count" in caplog.text

    def test_zero_count_override_disables_audio_on_capable_model(self):
        """The inverse combination also converges: system judgment direct plus user limit 0 downgrades mode to none."""
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance-2",
            model_id="doubao-seedance-2-0-260128",
            overrides={"max_reference_audio_count": 0},
        )
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE

    def test_none_mode_with_positive_count_is_not_a_violation(self):
        """The inverse combination is not a violation: with mode none, limits are irrelevant; treating it as a violation would reenable a capability the user disabled."""
        caps = synthesize_video_capabilities(
            endpoint="ark-seedance-2",
            model_id="doubao-seedance-2-0-260128",
            overrides={"reference_audio_mode": "none"},
        )
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 3  # Preserve system judgment unchanged; do not zero it as collateral.
