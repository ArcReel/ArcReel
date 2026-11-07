"""Script editing core pure-function tests.

Assert external behavior only: supply plain dicts plus operations and check returned arrays/IDs/assets, or
`ScriptEditError` for invalid operations. Cover patch/insert/remove/split and dispatch in all three modes, without patching
private methods. Fixtures match `tests/test_script_structure_validator.py`.
"""

from __future__ import annotations

import pytest

from lib.script.script_editor import (
    ScriptEditError,
    insert_segment,
    patch_field,
    remove_segment,
    resolve_items,
    split_segment,
)


def _segment(segment_id: str = "E1S01", duration: int = 4) -> dict:
    return {
        "segment_id": segment_id,
        "duration_seconds": duration,
        "novel_text": "原文",
        "characters_in_segment": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "generated_assets": {"storyboard_image": "scripts/x.png", "status": "completed"},
    }


def _narration(segments: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "content_mode": "narration",
        "episode": 1,
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": segments if segments is not None else [_segment("E1S01"), _segment("E1S02")],
    }


def _scene(scene_id: str = "E1S01", duration: int = 8) -> dict:
    return {
        "scene_id": scene_id,
        "duration_seconds": duration,
        "scene_type": "剧情",
        "characters_in_scene": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "generated_assets": {"storyboard_image": "scripts/y.png"},
    }


def _drama(scenes: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "content_mode": "drama",
        "episode": 1,
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": scenes if scenes is not None else [_scene("E1S01"), _scene("E1S02")],
    }


def _unit(unit_id: str = "E1U1", shots: list[dict] | None = None) -> dict:
    shots = shots if shots is not None else [{"text": "镜头1"}, {"text": "镜头2"}]
    return {
        "unit_id": unit_id,
        "shots": shots,
        "references": [],
        "duration_seconds": 8,
        "generated_assets": {"video_clip": "scripts/z.mp4"},
    }


def _reference(units: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "episode": 1,
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": units if units is not None else [_unit("E1U1"), _unit("E1U2")],
    }


class TestResolveItems:
    def test_narration(self):
        items, id_field, kind = resolve_items(_narration())
        assert id_field == "segment_id"
        assert kind == "segments"
        assert len(items) == 2

    def test_returned_list_is_live_reference(self):
        script = _narration()
        items, _id, _kind = resolve_items(script)
        items.append(_segment("E1S03"))
        assert len(script["segments"]) == 3

    def test_missing_key_is_empty_list(self):
        # Missing content-array key → empty list, a valid empty draft, without error.
        items, _id, kind = resolve_items({"content_mode": "narration"})
        assert kind == "segments"
        assert items == []

    def test_non_list_items_fail_loud(self):
        # Existing nonlist key means corruption: fail loudly instead of silently returning [].
        with pytest.raises(ScriptEditError):
            resolve_items({"content_mode": "narration", "segments": "oops"})

    def test_present_but_null_fails_loud(self):
        # Existing null key means corruption: fail loudly, distinct from absent-key empty drafts.
        with pytest.raises(ScriptEditError):
            resolve_items({"content_mode": "narration", "segments": None})


class TestPatchField:
    def test_patch_top_level_field(self):
        script = patch_field(_narration(), "E1S02", "duration_seconds", 9)
        assert script["segments"][1]["duration_seconds"] == 9

    def test_patch_nested_field(self):
        script = patch_field(_narration(), "E1S01", "image_prompt.scene", "新场景")
        assert script["segments"][0]["image_prompt"]["scene"] == "新场景"

    def test_patch_drama_by_scene_id(self):
        script = patch_field(_drama(), "E1S02", "scene_type", "空镜")
        assert script["scenes"][1]["scene_type"] == "空镜"

    def test_patch_reference_unit_field(self):
        script = patch_field(_reference(), "E1U2", "note", "备注")
        assert script["video_units"][1]["note"] == "备注"

    def test_patch_unknown_leaf_field_succeeds_at_set_nested_layer(self):
        # _set_nested permits leaf writes at unit level; dict operations do not check schemas.
        # But video_prompt.note is nonexistent in VideoPrompt,
        # a hallucinated field written into the dict by _set_nested, then rejected
        # by Pydantic extra="forbid" through _write_script_unlocked / _guard_no_worse.
        # Real tools cannot persist it; see test_patch_unknown_leaf_blocked_at_write_throat.
        script = patch_field(_narration(), "E1S01", "video_prompt.note", "新增备注")
        assert script["segments"][0]["video_prompt"]["note"] == "新增备注"

    def test_patch_optional_leaf_present_in_schema_succeeds(self):
        # Valid backfill of an LLM-omitted optional field: NarrationSegment.note is a schema
        # SkipJsonSchema[str | None] field, patchable through both _set_nested writes
        # and _guard_no_worse validation because the schema field is legitimate.
        # However, _narration() defaults note=None; strip it first to test backfill,
        # or, as a fallback, test writing a valid schema field itself.
        script = _narration()
        # Delete segment.note to simulate LLM omission.
        for seg in script["segments"]:
            seg.pop("note", None)
        script = patch_field(script, "E1S01", "note", "补全的备注")
        assert script["segments"][0]["note"] == "补全的备注"

    def test_patch_unknown_id_raises(self):
        with pytest.raises(ScriptEditError):
            patch_field(_narration(), "E9S99", "duration_seconds", 9)

    def test_patch_generated_assets_rejected(self):
        with pytest.raises(ScriptEditError):
            patch_field(_narration(), "E1S01", "generated_assets.status", "completed")

    @pytest.mark.parametrize("id_field", ["segment_id", "scene_id", "unit_id"])
    def test_patch_id_field_rejected(self, id_field):
        # All segment/scene/unit ID fields reject direct edits; insert/split derive IDs. Agent changes
        # would misdirect other ID-based writeback helpers or create duplicate-ID ambiguity.
        with pytest.raises(ScriptEditError, match="不可改分镜 id"):
            patch_field(_narration(), "E1S01", id_field, "X")

    def test_patch_end_frame_image_rejected(self):
        # Only last-frame set/clear endpoints write last-frame fields; allowing patch would permit arbitrary strings,
        # bypassing snapshot copying and reintroducing dangling references/out-of-bounds paths.
        with pytest.raises(ScriptEditError, match="不可改 end_frame_image"):
            patch_field(_narration(), "E1S01", "end_frame_image", "../../../etc/passwd")

    def test_patch_pending_authoring_rejected(self):
        # Only adding items sets pending-authoring, and prompt authoring clears it; patch must not let Agents edit this statement.
        with pytest.raises(ScriptEditError, match="不可改 pending_authoring"):
            patch_field(_narration(), "E1S01", "pending_authoring", False)

    @pytest.mark.parametrize("field", ["image_prompt", "video_prompt"])
    def test_patch_cannot_clear_a_prompt_to_none(self, field):
        # None means pending prompt generation, written only by mechanical script-plan conversion; after script models allow None,
        # patch becomes the only entry for clearing existing prompts and must reject that here.
        with pytest.raises(ScriptEditError, match="清成 null"):
            patch_field(_narration(), "E1S01", field, None)
        with pytest.raises(ScriptEditError, match="清成 null"):
            patch_field(_drama(), "E1S01", field, None)

    def test_patch_does_not_touch_generated_assets(self):
        script = patch_field(_narration(), "E1S01", "duration_seconds", 7)
        assert script["segments"][0]["generated_assets"]["status"] == "completed"

    def test_patch_missing_parent_path_raises(self):
        with pytest.raises(ScriptEditError):
            patch_field(_narration(), "E1S01", "no_such.deep", 1)


class TestInsertSegment:
    def test_insert_after_takes_the_next_main_number_at_right_position(self):
        script = insert_segment(_narration(), "E1S01", _segment("IGNORED"))
        ids = [s["segment_id"] for s in script["segments"]]
        assert ids == ["E1S01", "E1S03", "E1S02"]

    def test_insert_without_anchor_goes_first(self):
        script = insert_segment(_narration([]), None, _segment("X"))
        assert [s["segment_id"] for s in script["segments"]] == ["E1S01"]

    def test_insert_clears_generated_assets(self):
        script = insert_segment(_narration(), "E1S01", _segment("X"))
        assert script["segments"][1]["generated_assets"] == {}

    def test_insert_drops_end_frame_image(self):
        # New IDs have no snapshots; Agent-supplied values would reference anchor snapshots or nonexistent paths.
        new_item = _segment("X") | {"end_frame_image": "end_frames/scene_E1S01.png"}
        script = insert_segment(_narration(), "E1S01", new_item)
        assert script["segments"][1].get("end_frame_image") is None

    def test_insert_number_counts_suffixed_ids(self):
        script = insert_segment(_narration([_segment("E1S01"), _segment("E1S04_1")]), "E1S01", _segment("X"))
        ids = [s["segment_id"] for s in script["segments"]]
        assert ids == ["E1S01", "E1S05", "E1S04_1"]

    def test_insert_unknown_anchor_raises(self):
        with pytest.raises(ScriptEditError):
            insert_segment(_narration(), "E9S99", _segment("X"))

    def test_insert_reference_unit(self):
        script = insert_segment(_reference(), "E1U1", _unit("X"))
        ids = [u["unit_id"] for u in script["video_units"]]
        assert ids == ["E1U1", "E1U3", "E1U2"]


class TestRemoveSegment:
    def test_remove_by_id(self):
        script = remove_segment(_narration(), "E1S01")
        assert [s["segment_id"] for s in script["segments"]] == ["E1S02"]

    def test_remove_does_not_renumber_others(self):
        script = remove_segment(_narration([_segment("E1S01"), _segment("E1S02"), _segment("E1S03")]), "E1S02")
        assert [s["segment_id"] for s in script["segments"]] == ["E1S01", "E1S03"]

    def test_remove_unknown_id_raises(self):
        with pytest.raises(ScriptEditError):
            remove_segment(_narration(), "E9S99")


class TestSplitSegment:
    def test_split_keeps_first_id_and_issues_new_numbers_for_the_rest(self):
        parts = [_segment("a"), _segment("b"), _segment("c")]
        script = split_segment(_narration(), "E1S01", parts)
        ids = [s["segment_id"] for s in script["segments"]]
        assert ids == ["E1S01", "E1S03", "E1S04", "E1S02"]

    def test_split_issues_numbers_after_the_floor(self):
        # floor is the episode maximum ever used, including deleted IDs; new split shots number after it.
        script = split_segment(_narration(), "E1S01", [_segment("a"), _segment("b")], floor=7)
        assert [s["segment_id"] for s in script["segments"]] == ["E1S01", "E1S08", "E1S02"]

    def test_split_keeps_anchor_assets_clears_new_parts(self):
        # Keep anchor parts[0] generated_assets under its original ID, matching insert_segment anchor preservation;
        # clear generated_assets on other newly derived parts, returning them to pending regeneration.
        # Even if Agents provide generated_assets in parts[0], retain actual original-shot values rather than letting Agents
        # invent asset paths.
        anchor_assets = _narration()["segments"][0]["generated_assets"]
        script = split_segment(_narration(), "E1S01", [_segment("a"), _segment("b")])
        assert script["segments"][0]["generated_assets"] == anchor_assets
        assert script["segments"][1]["generated_assets"] == {}

    def test_split_keeps_anchor_end_frame_clears_new_parts(self):
        # Last-frame snapshots key by shot ID; anchors retain IDs and snapshots, while newly derived IDs have none.
        # Retain actual original-shot values instead of letting Agents invent snapshot paths in parts[0].
        script_in = _narration()
        script_in["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"
        parts = [_segment("a") | {"end_frame_image": "end_frames/forged.png"}, _segment("b")]
        script = split_segment(script_in, "E1S01", parts)
        assert script["segments"][0]["end_frame_image"] == "end_frames/scene_E1S01.png"
        assert script["segments"][1].get("end_frame_image") is None

    def test_split_anchor_without_end_frame_drops_agent_supplied_value(self):
        parts = [_segment("a") | {"end_frame_image": "end_frames/forged.png"}, _segment("b")]
        script = split_segment(_narration(), "E1S01", parts)
        assert script["segments"][0].get("end_frame_image") is None

    def test_split_requires_at_least_two_parts(self):
        with pytest.raises(ScriptEditError):
            split_segment(_narration(), "E1S01", [_segment("a")])

    def test_split_unknown_id_raises(self):
        with pytest.raises(ScriptEditError):
            split_segment(_narration(), "E9S99", [_segment("a"), _segment("b")])

    def test_split_reference_units_act_on_video_units(self):
        anchor_assets = _reference()["video_units"][0]["generated_assets"]
        script = split_segment(_reference(), "E1U1", [_unit("a"), _unit("b")])
        ids = [u["unit_id"] for u in script["video_units"]]
        assert ids == ["E1U1", "E1U3", "E1U2"]
        # Anchor video_unit retains generated_assets; newly derived units clear them.
        assert script["video_units"][0]["generated_assets"] == anchor_assets
        assert script["video_units"][1]["generated_assets"] == {}

    def test_split_warns_when_anchor_assets_dirty_non_dict(self, caplog):
        """Malformed nondict anchor generated_assets degrades to an empty dict,
        but must warn under ADR-0003's no-zero-signal-success addendum. anchor_assets=None
        is a valid initial state without warning; warn only for genuinely invalid list / str / int values."""
        import logging

        # Corrupt generated_assets is list, not dict.
        script_dirty = _narration([_segment("E1S01"), _segment("E1S02")])
        script_dirty["segments"][0]["generated_assets"] = ["unexpected_list_form"]

        with caplog.at_level(logging.WARNING, logger="lib.script.script_editor"):
            split_segment(script_dirty, "E1S01", [_segment("a"), _segment("b")])

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("E1S01" in m and "list" in m for m in warnings), warnings

    def test_split_no_warn_when_anchor_assets_none(self, caplog):
        """anchor generated_assets=None / absent is a valid ungenerated initial state and must not warn."""
        import logging

        script_clean = _narration([_segment("E1S01"), _segment("E1S02")])
        script_clean["segments"][0].pop("generated_assets", None)  # Simulate an absent initial state.

        with caplog.at_level(logging.WARNING, logger="lib.script.script_editor"):
            split_segment(script_clean, "E1S01", [_segment("a"), _segment("b")])

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert not warnings, f"unexpected warnings: {warnings}"
