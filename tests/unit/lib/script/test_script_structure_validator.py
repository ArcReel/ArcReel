"""Script structural validator pure-function tests.

Assert external behavior only: supply plain dicts and inspect ValidationResult. Cover valid/invalid judgments
and classification for all three modes without patching private methods.
"""

from __future__ import annotations

from lib.script.script_structure_validator import validate_script_structure


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
    }


def _narration(segments: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "content_mode": "narration",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": segments if segments is not None else [_segment()],
    }


def _scene(scene_id: str = "E1S01", duration: int = 8) -> dict:
    return {
        "scene_id": scene_id,
        "duration_seconds": duration,
        "characters_in_scene": ["角色A"],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _drama(scenes: list[dict] | None = None) -> dict:
    return {
        "title": "标题",
        "content_mode": "drama",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": scenes if scenes is not None else [_scene()],
    }


def _unit(unit_id: str = "E1U1", text: str | None = None, duration: int | None = None, **extra) -> dict:
    unit = {
        "unit_id": unit_id,
        "text": text if text is not None else "中景，@[张三] 推门。\n近景，他抬眼。",
        "duration_seconds": duration if duration is not None else 8,
    }
    unit.update(extra)
    return unit


def _reference(units: list[dict] | None = None, content_mode: str = "narration") -> dict:
    return {
        "title": "标题",
        "content_mode": content_mode,
        "generation_mode": "reference_video",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": units if units is not None else [_unit()],
    }


class TestValidScripts:
    def test_valid_narration(self):
        assert validate_script_structure(_narration()).valid

    def test_valid_drama(self):
        assert validate_script_structure(_drama()).valid

    def test_valid_reference_video(self):
        assert validate_script_structure(_reference()).valid


class TestModeDetection:
    def test_video_units_only_picks_reference_model(self):
        """When only video_units exists without segments/scenes, use ReferenceVideoScript regardless of
        content_mode, routing by data shape as introduced in action 5.

        Reference scripts have video_units but no segments; misclassification as NarrationEpisodeScript would reject
        missing segments. A valid result proves ReferenceVideoScript selection.
        """
        script = _reference(content_mode="narration")
        assert script.get("content_mode") == "narration"
        assert validate_script_structure(script).valid

    def test_partial_migration_segments_picks_narration_model(self):
        """Partial migration: generation_mode='reference_video' but data remains in segments.
        Follow shape into NarrationEpisodeScript rather than letting generation_mode unconditionally win;
        forcing ReferenceVideoScript would reject missing video_units.
        """
        # Use _narration() segments with generation_mode='reference_video' as a partial migration.
        script = _narration()
        script["generation_mode"] = "reference_video"
        assert validate_script_structure(script).valid

    def test_stray_video_units_do_not_hijack_storyboard_script(self):
        """Legacy corruption inserts stray video_units into narration scripts. With both arrays but no explicit
        reference mode, do not select ReferenceVideoScript and reject real segments for absent valid units;
        follow content_mode into Narration, with extra=ignore discarding surplus video_units.
        """
        script = _narration()
        script["video_units"] = [{"unit_id": "E1U1", "generated_assets": {"status": "pending"}}]
        assert validate_script_structure(script).valid

    def test_empty_scenes_drama_detected_by_content_mode(self):
        """Structurally valid empty drama scenes=[] follows content_mode into Drama, not list truthiness into Narration.

        scenes has no min_length; empty lists support creating empty drama before gradually adding scenes.
        Testing script.get("scenes") truthiness would misclassify falsy [] as NarrationEpisodeScript and reject writing.
        """
        script = _drama(scenes=[])
        assert validate_script_structure(script).valid

    def test_drama_detected_by_scenes_key_when_content_mode_absent(self):
        # Without content_mode, present scenes (even empty) implies Drama by key presence.
        script = _drama(scenes=[])
        del script["content_mode"]
        assert validate_script_structure(script).valid

    def test_narration_is_default_fallback(self):
        # Without content_mode or scenes/video_units, fall back to NarrationEpisodeScript.
        script = _narration()
        del script["content_mode"]
        assert validate_script_structure(script).valid


class TestInvalidNarration:
    def test_missing_required_top_field(self):
        script = _narration()
        del script["title"]
        result = validate_script_structure(script)
        assert not result.valid
        assert any("title" in e for e in result.errors)

    def test_missing_segment_required_field(self):
        seg = _segment()
        del seg["novel_text"]
        result = validate_script_structure(_narration([seg]))
        assert not result.valid
        assert any("novel_text" in e for e in result.errors)

    def test_duration_below_range(self):
        result = validate_script_structure(_narration([_segment(duration=0)]))
        assert not result.valid

    def test_duration_above_range(self):
        result = validate_script_structure(_narration([_segment(duration=61)]))
        assert not result.valid

    def test_video_prompt_wrong_shape(self):
        seg = _segment()
        seg["video_prompt"] = 123
        result = validate_script_structure(_narration([seg]))
        assert not result.valid
        assert any("video_prompt" in e for e in result.errors)

    def test_video_prompt_blank_text_rejected(self):
        seg = _segment()
        seg["video_prompt"] = "   "
        result = validate_script_structure(_narration([seg]))
        assert not result.valid
        assert any("video_prompt" in e for e in result.errors)

    def test_image_prompt_missing_composition(self):
        seg = _segment()
        seg["image_prompt"] = {"scene": "只有 scene"}
        result = validate_script_structure(_narration([seg]))
        assert not result.valid
        assert any("composition" in e for e in result.errors)


class TestInvalidReferenceVideo:
    def test_duration_out_of_structural_range_rejected(self):
        # Unit duration is authoritative, independent of body text and limited only by structural plausibility bounds.
        result = validate_script_structure(_reference([_unit(duration=9999)]))
        assert not result.valid
        assert any("duration" in e.lower() for e in result.errors)

    def test_duration_independent_of_text_length(self):
        assert validate_script_structure(_reference([_unit(duration=12)])).valid
        assert validate_script_structure(_reference([_unit(text="一行", duration=12)])).valid

    def test_empty_text_rejected_unless_replan_shell(self):
        assert not validate_script_structure(_reference([_unit(text="", duration=8)])).valid
        assert validate_script_structure(_reference([_unit(text="", duration=0, needs_replan=True)])).valid

    def test_legacy_shot_fields_rejected(self):
        unit = _unit(shots=[{"text": "镜头1"}])
        assert not validate_script_structure(_reference([unit])).valid


def _ad_shot(shot_id: str = "E1S01", duration: int = 3) -> dict:
    return {
        "shot_id": shot_id,
        "section": "hook",
        "duration_seconds": duration,
        "voiceover_text": "口播文案",
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
    }


def _ad(shots: list[dict] | None = None) -> dict:
    return {
        "title": "短片",
        "content_mode": "ad",
        "novel": {"title": "", "chapter": ""},
        "shots": shots if shots is not None else [_ad_shot()],
    }


class TestAdScripts:
    def test_valid_ad(self):
        assert validate_script_structure(_ad()).valid

    def test_ad_detected_by_content_mode(self):
        """Validate ad scripts with AdEpisodeScript, not narration/drama models."""
        bad = _ad()
        del bad["shots"][0]["voiceover_text"]
        result = validate_script_structure(bad)
        assert not result.valid
        assert any("voiceover_text" in e for e in result.errors)

    def test_ad_detected_by_shots_key_when_content_mode_absent(self):
        script = _ad()
        del script["content_mode"]
        assert validate_script_structure(script).valid

    def test_resolve_kind_and_items_for_ad(self):
        from lib.script.script_editor import resolve_items
        from lib.script.script_skeleton import resolve_script_kind

        script = _ad()
        assert resolve_script_kind(script) == "shots"
        items, id_field, kind = resolve_items(script)
        assert (id_field, kind) == ("shot_id", "shots")
        assert items[0]["shot_id"] == "E1S01"
