"""Central matrix tests for skeleton registries and canonical/evidence-based resolvers.

Five areas:
- Table consistency: all four rows, valid fields, and import validation.
- All canonical combinations: 3 content_mode × storyboard/grid/reference_video.
- Every evidence-resolution ladder step.
- Fail loudly on unknown/absent content_mode with ValueError, reversing the old unknown-to-drama fallback.
- Generation-mode gates reject cross-family mismatches and allow within-family differences.
"""

from __future__ import annotations

import pytest

from lib.script import script_skeleton
from lib.script.script_skeleton import (
    SKELETONS,
    Skeleton,
    SkeletonRouteMismatchError,
    ensure_route_skeleton,
    resolve_declared_kind,
    resolve_kind_items,
    resolve_script_kind,
)


class TestRegistrySelfConsistency:
    def test_four_kinds_complete(self):
        # Keys are item-array keys; all four skeletons are present.
        assert set(SKELETONS) == {"segments", "scenes", "shots", "video_units"}

    def test_id_fields(self):
        assert SKELETONS["segments"].id_field == "segment_id"
        assert SKELETONS["scenes"].id_field == "scene_id"
        assert SKELETONS["shots"].id_field == "shot_id"
        assert SKELETONS["video_units"].id_field == "unit_id"

    def test_chars_fields_declared_or_explicitly_absent(self):
        assert SKELETONS["segments"].chars_field == "characters_in_segment"
        assert SKELETONS["scenes"].chars_field == "characters_in_scene"
        assert SKELETONS["shots"].chars_field == "characters_in_shot"
        # video_units has no per-item character list; characters appear as character entries in references.
        # The table declares absence truthfully as None, without fabricated field names.
        assert SKELETONS["video_units"].chars_field is None

    def test_real_registry_passes_validation(self):
        """The actual registry is consistent; validator acceptance means no exception and no return value."""
        assert script_skeleton._validate_registry() is None

    def test_missing_kind_fails_fast(self, monkeypatch):
        broken = {k: v for k, v in SKELETONS.items() if k != "video_units"}
        monkeypatch.setattr(script_skeleton, "SKELETONS", broken)
        with pytest.raises(RuntimeError):
            script_skeleton._validate_registry()

    def test_empty_id_field_fails_fast(self, monkeypatch):
        broken = dict(SKELETONS)
        broken["segments"] = Skeleton("", "characters_in_segment")
        monkeypatch.setattr(script_skeleton, "SKELETONS", broken)
        with pytest.raises(RuntimeError):
            script_skeleton._validate_registry()

    def test_empty_chars_field_fails_fast(self, monkeypatch):
        # None is valid explicit absence; empty strings are invalid.
        broken = dict(SKELETONS)
        broken["scenes"] = Skeleton("scene_id", "")
        monkeypatch.setattr(script_skeleton, "SKELETONS", broken)
        with pytest.raises(RuntimeError):
            script_skeleton._validate_registry()


class TestDeclaredResolver:
    """All canonical combinations: (content_mode, generation_mode) → kind."""

    @pytest.mark.parametrize("generation_mode", [None, "storyboard", "grid_4", "grid_6", "grid_9"])
    def test_storyboard_and_grid_paths(self, generation_mode):
        # Nonreference generation: content_mode selects the content skeleton, independent of storyboard/grid.
        assert resolve_declared_kind("narration", generation_mode) == "segments"
        assert resolve_declared_kind("drama", generation_mode) == "scenes"
        assert resolve_declared_kind("ad", generation_mode) == "shots"

    def test_reference_video_routes_all_content_modes_to_video_units(self):
        assert resolve_declared_kind("narration", "reference_video") == "video_units"
        assert resolve_declared_kind("drama", "reference_video") == "video_units"
        assert resolve_declared_kind("ad", "reference_video") == "video_units"

    @pytest.mark.parametrize("content_mode", [None, "", "reference_video", "unknown"])
    @pytest.mark.parametrize("generation_mode", [None, "reference_video"])
    def test_unknown_or_missing_content_mode_raises(self, content_mode, generation_mode):
        # Fail loudly, without silent drama/narration fallback.
        with pytest.raises(ValueError, match=r"未知或缺失 content_mode"):
            resolve_declared_kind(content_mode, generation_mode)


class TestScriptResolver:
    """Exercise every evidence-resolution ladder step (classification order 1→4, then final fallback)."""

    def test_script_plan_video_units_alone(self):
        # video_units present without segments/scenes/shots → reference.
        assert resolve_script_kind({"video_units": []}) == "video_units"

    def test_script_plan_floating_video_units_does_not_hijack_storyboard(self):
        # Stray video_units must not take classification from storyboard scripts.
        assert resolve_script_kind({"video_units": [], "segments": [], "content_mode": "narration"}) == "segments"

    def test_script_plan_floating_video_units_hijack_guard_without_content_mode(self):
        # Stray video_units plus segments without content_mode: script_plan guards still prevent video_units taking precedence;
        # absent content_mode follows key-presence steps (step 4/final fallback) to segments. Cover legacy corrupted storyboard
        # scripts containing stray video_units without content_mode stamps, avoiding false reference classification.
        assert resolve_script_kind({"video_units": [], "segments": []}) == "segments"

    def test_prompt_authoring_content_mode_authority(self):
        assert resolve_script_kind({"content_mode": "ad"}) == "shots"
        assert resolve_script_kind({"content_mode": "drama"}) == "scenes"
        assert resolve_script_kind({"content_mode": "narration", "segments": []}) == "segments"

    def test_step3_narration_falls_back_to_scenes_key(self):
        # content_mode=narration with scenes and no segments falls back to scenes.
        assert resolve_script_kind({"content_mode": "narration", "scenes": []}) == "scenes"

    def test_residual_generation_mode_field_is_ignored(self):
        # Legacy generation-mode markers are unknown fields; evidence resolution reads shapes, returning segments and preserving editability.
        script = {"content_mode": "narration", "generation_mode": "reference_video", "segments": []}
        assert resolve_script_kind(script) == "segments"

    def test_step4_key_existence_inference_when_content_mode_absent(self):
        assert resolve_script_kind({"scenes": []}) == "scenes"
        assert resolve_script_kind({"shots": []}) == "shots"

    def test_final_fallback_segments(self):
        assert resolve_script_kind({}) == "segments"


class TestRouteSkeletonGate:
    """Generation-mode gates reject script/project cross-family mismatches while allowing within-family differences."""

    def test_matched_reference_route_passes(self):
        script = {"content_mode": "narration", "video_units": []}
        assert ensure_route_skeleton(script, "narration", "reference_video") == "video_units"

    def test_matched_storyboard_route_passes(self):
        script = {"content_mode": "drama", "scenes": []}
        assert ensure_route_skeleton(script, "drama", "storyboard") == "scenes"

    def test_ad_reference_route_expects_video_units(self):
        script = {"content_mode": "ad", "video_units": []}
        assert ensure_route_skeleton(script, "ad", "reference_video") == "video_units"

    def test_ad_reference_route_rejects_legacy_shots_skeleton(self):
        script = {"content_mode": "ad", "shots": []}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "ad", "reference_video")
        assert (exc.value.expected, exc.value.actual) == ("video_units", "shots")

    def test_narration_data_in_scenes_key_is_not_a_mismatch(self):
        # Return within-family legacy narration-under-scenes shapes truthfully, without mismatch rejection.
        script = {"content_mode": "narration", "scenes": []}
        assert ensure_route_skeleton(script, "narration", "storyboard") == "scenes"

    def test_reference_route_passes_with_residual_storyboard_array(self):
        # Reference-to-video scripts with leftover storyboard arrays resolve segments by shape, but generation reads video_units;
        # leftover arrays do not vote, so gates allow them, matching unit-based cost estimation.
        script = {
            "content_mode": "narration",
            "video_units": [{"unit_id": "E1U1"}],
            "segments": [{"segment_id": "E1S1"}],
        }
        assert ensure_route_skeleton(script, "narration", "reference_video") == "video_units"

    def test_storyboard_route_passes_with_residual_unit_array(self):
        # Inverse: storyboard-image-to-video scripts with stray video_units pass when storyboard arrays exist.
        script = {"content_mode": "narration", "segments": [{"segment_id": "E1S1"}], "video_units": []}
        assert ensure_route_skeleton(script, "narration", "storyboard") == "segments"

    def test_unit_script_on_storyboard_route_is_rejected(self):
        script = {"content_mode": "narration", "video_units": []}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "narration", "storyboard")
        assert exc.value.expected == "segments"
        assert exc.value.actual == "video_units"
        # Structural error plus resplitting guidance; explain that viewing/editing/export remain available.
        assert "骨架" in str(exc.value)
        assert "重新拆分" in str(exc.value)
        assert "查看" in str(exc.value)

    def test_storyboard_script_on_reference_route_is_rejected(self):
        script = {"content_mode": "narration", "segments": []}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "narration", "reference_video")
        assert exc.value.expected == "video_units"
        assert exc.value.actual == "segments"
        assert "generate_script_plan" in str(exc.value)

    def test_storyboard_route_rejects_script_without_any_skeleton_array(self):
        # Without all three storyboard keys, resolve_script_kind synthesizes segments from content_mode; allowing that would make
        # storyboard enqueue falsely report all storyboard images already generated. Key presence is the criterion, so reject.
        script = {"content_mode": "narration", "title": "第一集"}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "narration", "storyboard")
        assert exc.value.expected == "segments"
        assert exc.value.actual is None
        # Distinguish absent skeleton arrays from other-family arrays; avoid saying required segments, current segments.
        assert "没有任何骨架数组" in str(exc.value)
        assert "当前剧本是" not in str(exc.value)

    def test_reference_route_rejects_script_without_any_skeleton_array(self):
        script = {"content_mode": "narration"}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "narration", "reference_video")
        assert exc.value.expected == "video_units"
        assert exc.value.actual is None
        assert "没有任何骨架数组" in str(exc.value)

    def test_ad_route_rejects_script_without_any_skeleton_array(self):
        script = {"content_mode": "ad"}
        with pytest.raises(SkeletonRouteMismatchError) as exc:
            ensure_route_skeleton(script, "ad", "reference_video")
        assert exc.value.expected == "video_units"
        assert exc.value.actual is None

    def test_empty_skeleton_array_is_present_and_passes(self):
        # Existing keys pass: empty arrays mean split with no items, distinct from never split; this gate does not reject them.
        assert ensure_route_skeleton({"content_mode": "narration", "segments": []}, "narration", "storyboard") == (
            "segments"
        )

    def test_unknown_content_mode_still_fails_loud(self):
        with pytest.raises(ValueError, match=r"未知或缺失 content_mode"):
            ensure_route_skeleton({"segments": []}, None, "storyboard")


class TestKindItemsAccessor:
    """Sole item-access entry: both kind-resolution sources × unchanged item values."""

    def test_forensic_kind_when_not_given(self):
        # Missing kind uses evidence resolution, matching resolve_script_kind.
        script = {"content_mode": "drama", "scenes": [{"scene_id": "E1S01"}]}
        items, id_field, kind = resolve_kind_items(script)
        assert kind == "scenes"
        assert id_field == SKELETONS["scenes"].id_field
        assert items == [{"scene_id": "E1S01"}]

    def test_explicit_kind_skips_forensics(self):
        # Explicit kind overrides shape: evidence resolution would choose segments, while explicit video_units selects that array.
        script = {"content_mode": "narration", "segments": [], "video_units": [{"unit_id": "E1U01"}]}
        items, id_field, kind = resolve_kind_items(script, kind="video_units")
        assert (kind, id_field) == ("video_units", SKELETONS["video_units"].id_field)
        assert items == [{"unit_id": "E1U01"}]

    def test_id_field_per_kind(self):
        # Do not generalize away each skeleton's item identity.
        for kind, expected in (
            ("segments", "segment_id"),
            ("scenes", "scene_id"),
            ("shots", "shot_id"),
            ("video_units", "unit_id"),
        ):
            assert resolve_kind_items({}, kind=kind)[1] == expected

    def test_items_returned_by_reference(self):
        # Return the actual script reference so in-place edits affect callers.
        items_in_script: list[dict[str, object]] = []
        script = {"content_mode": "narration", "segments": items_in_script}
        items, _id_field, _kind = resolve_kind_items(script)
        assert items is items_in_script

    def test_missing_key_yields_none_not_empty_list(self):
        # Do not substitute empty arrays for absence; callers decide separately for missing versus present-empty keys.
        items, id_field, kind = resolve_kind_items({"content_mode": "narration"})
        assert (items, kind) == (None, "segments")
        assert id_field == "segment_id"

    def test_non_list_value_returned_as_is(self):
        # Return malformed values unchanged, without failing loudly or silently degrading here.
        assert resolve_kind_items({"content_mode": "narration", "segments": None})[0] is None
        assert resolve_kind_items({"content_mode": "drama", "scenes": "E1S01"})[0] == "E1S01"
