"""No-worse structural validation guards at the unified persistence entry.

Assert only external behavior: construct before/after scripts and check whether writes raise ScriptStructureValidationError,
asset-writeback exemptions, and validate defaults. Do not patch private methods.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lib.project.project_manager import ProjectManager
from lib.script.script_editor import ScriptEditError
from lib.script.script_structure_validator import ScriptStructureValidationError


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


def _valid_script(segments: list[dict] | None = None) -> dict:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "segments": segments if segments is not None else [_segment()],
    }


def _invalid_script() -> dict:
    # Missing summary/novel and malformed image_prompt/video_prompt: structurally invalid.
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "segments": [{"segment_id": "E1S01", "duration_seconds": 4, "image_prompt": "x", "video_prompt": "y"}],
    }


def _pm(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    return pm


class TestNoWorseSemantics:
    def test_valid_to_invalid_is_rejected(self, tmp_path: Path):
        """Valid before and invalid after means reject: this edit introduced new structural errors."""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")

        with pytest.raises(ScriptStructureValidationError), pm.locked_script("demo", "episode_1.json") as script:
            # Change a valid segment duration to an out-of-range value.
            script["segments"][0]["duration_seconds"] = 999

    def test_invalid_to_invalid_is_allowed(self, tmp_path: Path):
        """Invalid before means allow without penalizing historical errors, even when still invalid afterward."""
        pm = _pm(tmp_path)
        pm.save_script("demo", _invalid_script(), "episode_1.json", validate=False)

        # A valid title edit on an already-invalid legacy script must not be blocked.
        with pm.locked_script("demo", "episode_1.json") as script:
            script["title"] = "新标题"

        assert pm.load_script("demo", "episode_1.json")["title"] == "新标题"

    def test_valid_to_valid_is_allowed(self, tmp_path: Path):
        """Valid before and after means allow."""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")

        with pm.locked_script("demo", "episode_1.json") as script:
            script["segments"][0]["duration_seconds"] = 10

        assert pm.load_script("demo", "episode_1.json")["segments"][0]["duration_seconds"] == 10

    def test_fresh_save_invalid_is_rejected(self, tmp_path: Path):
        """New saves with no prior version reject invalid structure strictly."""
        pm = _pm(tmp_path)
        with pytest.raises(ScriptStructureValidationError):
            pm.save_script("demo", _invalid_script(), "episode_1.json")

        # Rejection must leave no file on disk.
        scripts_dir = pm.get_project_path("demo") / "scripts"
        assert not (scripts_dir / "episode_1.json").exists()

    def test_fresh_save_valid_is_allowed(self, tmp_path: Path):
        """New saves with no prior version allow valid structure."""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")
        assert pm.load_script("demo", "episode_1.json")["title"] == "标题"


class TestValidateDefaultsOn:
    def test_locked_script_validates_by_default(self, tmp_path: Path):
        """Omitting validate enables validation by default (fail-safe)."""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")

        # Do not pass validate to locked_script.
        with pytest.raises(ScriptStructureValidationError), pm.locked_script("demo", "episode_1.json") as script:
            script["segments"][0]["video_prompt"] = 123

    def test_validate_false_bypasses_guard(self, tmp_path: Path):
        """Explicit validate=False permits even newly introduced invalid structure."""
        pm = _pm(tmp_path)
        pm.save_script("demo", _valid_script(), "episode_1.json")

        with pm.locked_script("demo", "episode_1.json", validate=False) as script:
            script["segments"][0]["video_prompt"] = 123

        assert pm.load_script("demo", "episode_1.json")["segments"][0]["video_prompt"] == 123


def _drama_scene(scene_id: str = "E1S01") -> dict:
    return {
        "scene_id": scene_id,
        "duration_seconds": 8,
        "characters_in_scene": ["角色A"],
        "scenes": [],
        "props": [],
        "image_prompt": {
            "scene": "场景描述",
            "composition": {"shot_type": "Medium Shot", "lighting": "暖光", "ambiance": "薄雾"},
        },
        "video_prompt": {"action": "转身", "camera_motion": "Static", "ambiance_audio": "风声"},
        "utterances": [{"kind": "dialogue", "speaker": "角色A", "text": "你来了。"}],
    }


def _valid_drama_script(scenes: list[dict] | None = None) -> dict:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "drama",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": scenes if scenes is not None else [_drama_scene()],
    }


def _pm_drama(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "drama")
    return pm


class TestUtterancesEditGuard:
    """Storyboard-detail drama utterance edits use locked_script and the no-worse persistence guard."""

    def test_valid_utterances_edit_persists(self, tmp_path: Path):
        """Persist valid utterance edits (dialogue with speaker, voiceover without speaker)."""
        pm = _pm_drama(tmp_path)
        pm.save_script("demo", _valid_drama_script(), "episode_1.json")

        with pm.locked_script("demo", "episode_1.json") as script:
            script["scenes"][0]["utterances"] = [
                {"kind": "dialogue", "speaker": "角色A", "text": "改后的台词。"},
                {"kind": "voiceover", "speaker": None, "text": "旁白。"},
            ]

        saved = pm.load_script("demo", "episode_1.json")["scenes"][0]["utterances"]
        assert saved[0] == {"kind": "dialogue", "speaker": "角色A", "text": "改后的台词。"}
        assert saved[1]["kind"] == "voiceover"

    def test_illegal_utterance_kind_speaker_is_rejected(self, tmp_path: Path):
        """Dialogue without nonempty speaker is rejected; preserve old content without corrupting project.json."""
        pm = _pm_drama(tmp_path)
        pm.save_script("demo", _valid_drama_script(), "episode_1.json")

        with pytest.raises(ScriptStructureValidationError), pm.locked_script("demo", "episode_1.json") as script:
            script["scenes"][0]["utterances"] = [{"kind": "dialogue", "speaker": "", "text": "无主台词"}]

        saved = pm.load_script("demo", "episode_1.json")["scenes"][0]["utterances"]
        assert saved == [{"kind": "dialogue", "speaker": "角色A", "text": "你来了。"}]


def _unit(unit_id: str = "E1U1", duration_seconds: int = 8) -> dict:
    return {
        "unit_id": unit_id,
        "text": "中景，@[角色A] 推门。\n近景，他抬眼。",
        "duration_seconds": duration_seconds,
    }


def _reference_script(units: list[dict] | None = None) -> dict:
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "video_units": units if units is not None else [_unit("E1U1", 6), _unit("E1U2", 8)],
    }


class TestAssetWritebackExemption:
    def test_update_scene_asset_succeeds_on_invalid_script(self, tmp_path: Path):
        """Asset writeback (validate=False) succeeds even on already-invalid scripts."""
        pm = _pm(tmp_path)
        pm.save_script("demo", _invalid_script(), "episode_1.json", validate=False)

        pm.update_scene_asset("demo", "episode_1.json", "E1S01", "storyboard_image", "storyboards/E1S01.png")

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["segments"][0]["generated_assets"]["storyboard_image"] == "storyboards/E1S01.png"

    def test_batch_update_scene_assets_succeeds_on_invalid_script(self, tmp_path: Path):
        pm = _pm(tmp_path)
        pm.save_script("demo", _invalid_script(), "episode_1.json", validate=False)

        pm.batch_update_scene_assets("demo", "episode_1.json", [("E1S01", "video_clip", "videos/E1S01.mp4")])

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["segments"][0]["generated_assets"]["video_clip"] == "videos/E1S01.mp4"

    def _seed_corrupted_null_segments(self, pm, tmp_path: Path) -> None:
        """Write a malformed segments=null script directly, bypassing save_script to simulate legacy data."""
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", "segments": null, '
            '"novel": {"title": "n", "chapter": "c"}, "summary": ""}',
            encoding="utf-8",
        )

    def _seed_corrupted_null_video_units(self, tmp_path: Path) -> None:
        """Construct malformed reference-mode video_units=null with reasonable prior metadata (the state before corruption),
        verifying asset writeback does not rewrite existing metadata on corrupt data."""
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", '
            '"generation_mode": "reference_video", "video_units": null, '
            '"novel": {"title": "n", "chapter": "c"}, "summary": "", '
            '"metadata": {"created_at": "2024-01-01T00:00:00+00:00", "status": "draft", '
            '"updated_at": "2024-01-01T00:00:00+00:00", "total_scenes": 5, "estimated_duration_seconds": 40}}',
            encoding="utf-8",
        )

    def test_update_scene_asset_fails_loud_on_corrupted_list_key(self, tmp_path: Path):
        """`update_scene_asset` on `segments: null` must fail loudly with ScriptEditError;
        a silent no-op would make workers report successful writeback despite data loss. Workers should catch it and
        mark failure so operators see corruption rather than empty success."""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_segments(pm, tmp_path)
        with pytest.raises(ScriptEditError, match="必须是列表"):
            pm.update_scene_asset("demo", "episode_1.json", "E1S01", "storyboard_image", "x.png")

    def test_batch_update_scene_assets_fails_loud_on_corrupted_list_key(self, tmp_path: Path):
        """`batch_update_scene_assets` on `segments: null` must fail loudly; silent batch no-ops are worse:
        all N written clips disappear while SSE still broadcasts all updated and the UI stays pending forever."""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_segments(pm, tmp_path)
        with pytest.raises(ScriptEditError, match="必须是列表"):
            pm.batch_update_scene_assets("demo", "episode_1.json", [("E1S01", "video_clip", "videos/E1S01.mp4")])

    def test_writeback_preserves_old_metadata_on_corrupted_list_key(self, tmp_path: Path):
        """The reference-mode `video_units: null` asset-writeback hot path must not rewrite existing metadata: retain
        legacy statistics and refresh only updated_at. No-worse handling must not opportunistically rewrite content it cannot read."""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_video_units(tmp_path)

        # Nonstructural changes use validate=False (the asset-writeback hot path).
        with pm.locked_script("demo", "episode_1.json", validate=False) as script:
            script["generated_assets_demo"] = "anything"

        saved = pm.load_script("demo", "episode_1.json")
        # Preserve all old metadata except the necessarily refreshed updated_at.
        assert saved["metadata"]["total_scenes"] == 5
        assert saved["metadata"]["estimated_duration_seconds"] == 40
        assert saved.get("generated_assets_demo") == "anything"

    def test_get_pending_scenes_warns_and_returns_empty_on_corrupted_list_key(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """Reads (get_pending_scenes) do not block UI rendering on corrupt data: return [] plus observable
        warning logs so operators can repair it manually, keeping degradation visible."""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_segments(pm, tmp_path)
        with caplog.at_level(logging.WARNING, logger="lib.project.project_manager"):
            result = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")
        assert result == []
        assert any("segments" in rec.message and "数据损坏" in rec.message for rec in caplog.records)

    def test_get_scenes_needing_storyboard_warns_and_returns_empty_on_corrupted_list_key(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        """Likewise, get_scenes_needing_storyboard returns [] plus warning on corrupt data."""
        pm = _pm(tmp_path)
        self._seed_corrupted_null_segments(pm, tmp_path)
        with caplog.at_level(logging.WARNING, logger="lib.project.project_manager"):
            result = pm.get_scenes_needing_storyboard("demo", "episode_1.json")
        assert result == []
        assert any("segments" in rec.message and "数据损坏" in rec.message for rec in caplog.records)

    def test_get_pending_scenes_handles_item_without_generated_assets(self, tmp_path: Path):
        """Read tolerance: items missing generated_assets within otherwise valid lists do not raise KeyError;
        count them as ungenerated/pending, matching get_scenes_needing_storyboard tolerance."""
        pm = _pm(tmp_path)
        # Write directly: segments is a valid list whose item lacks generated_assets.
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", '
            '"segments": [{"segment_id": "E1S01", "duration_seconds": 4}], '
            '"novel": {"title": "n", "chapter": "c"}, "summary": ""}',
            encoding="utf-8",
        )
        # No KeyError; missing item field means pending, included in results.
        result = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")
        assert len(result) == 1
        assert result[0]["segment_id"] == "E1S01"

    def test_reference_video_read_helpers_return_units(self, tmp_path: Path):
        """In reference mode, get_pending_scenes / get_scenes_needing_storyboard return video_units work.
        Old `_script_items_shape` fell back to drama's "scenes", absent in reference scripts, silently returning []
        and no-work UI. Shared resolve_items discrimination across all three modes fixes it."""
        pm = _pm(tmp_path)
        pm.save_script("demo", _reference_script(), "episode_1.json")

        pending = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")
        assert [item["unit_id"] for item in pending] == ["E1U1", "E1U2"]

        needing = pm.get_scenes_needing_storyboard("demo", "episode_1.json")
        assert [item["unit_id"] for item in needing] == ["E1U1", "E1U2"]

    def test_reference_video_update_scene_asset_writes_unit(self, tmp_path: Path):
        """In reference mode, update_scene_asset indexes video_units by unit_id for asset writeback.
        The old helper searched "scenes", found no unit_id, and raised KeyError("分镜不存在"),
        masking the actual wrong-path cause as a nonexistent ID."""
        pm = _pm(tmp_path)
        pm.save_script("demo", _reference_script(), "episode_1.json")

        pm.update_scene_asset("demo", "episode_1.json", "E1U1", "storyboard_image", "storyboards/E1U1.png")

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["video_units"][0]["generated_assets"]["storyboard_image"] == "storyboards/E1U1.png"

    @pytest.mark.parametrize(
        "assets_json",
        ["null", '"corrupted"', "[]"],
        ids=["null", "string", "list"],
    )
    def test_get_pending_scenes_handles_non_dict_generated_assets(self, tmp_path: Path, assets_json: str):
        """Read tolerance: null / string / nondict item.generated_assets does not raise AttributeError;
        treat it as ungenerated/pending. `item.get("generated_assets", {}).get(...)` only handles absent keys,
        not None / nondicts; use isinstance checks mirroring write-side update_scene_asset."""
        pm = _pm(tmp_path)
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", '
            f'"segments": [{{"segment_id": "E1S01", "duration_seconds": 4, "generated_assets": {assets_json}}}], '
            '"novel": {"title": "n", "chapter": "c"}, "summary": ""}',
            encoding="utf-8",
        )
        result = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")
        assert len(result) == 1
        # get_scenes_needing_storyboard likewise tolerates these values.
        result2 = pm.get_scenes_needing_storyboard("demo", "episode_1.json")
        assert len(result2) == 1

    def _seed_non_dict_element(self, tmp_path: Path) -> None:
        """Persist a valid list containing a nondict element ("foo") to simulate manually edited/corrupt data."""
        script_dir = tmp_path / "projects" / "demo" / "scripts"
        script_dir.mkdir(parents=True, exist_ok=True)
        (script_dir / "episode_1.json").write_text(
            '{"episode": 1, "title": "x", "content_mode": "narration", '
            '"segments": ["foo", {"segment_id": "E1S01", "duration_seconds": 4}], '
            '"novel": {"title": "n", "chapter": "c"}, "summary": ""}',
            encoding="utf-8",
        )

    def test_read_helpers_skip_non_dict_items(self, tmp_path: Path):
        """Reads skip nondict array elements ("foo"), returning valid dict items without AttributeError,
        matching isinstance filtering in script_editor.new_item_ids."""
        pm = _pm(tmp_path)
        self._seed_non_dict_element(tmp_path)
        pending = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")
        assert [item["segment_id"] for item in pending] == ["E1S01"]
        needing = pm.get_scenes_needing_storyboard("demo", "episode_1.json")
        assert [item["segment_id"] for item in needing] == ["E1S01"]

    def test_update_scene_asset_writes_with_non_dict_sibling(self, tmp_path: Path):
        """Write-side update_scene_asset skips nondict siblings and writes valid IDs normally without AttributeError."""
        pm = _pm(tmp_path)
        self._seed_non_dict_element(tmp_path)
        pm.update_scene_asset("demo", "episode_1.json", "E1S01", "storyboard_image", "storyboards/E1S01.png")
        saved = pm.load_script("demo", "episode_1.json")
        target = next(s for s in saved["segments"] if isinstance(s, dict) and s.get("segment_id") == "E1S01")
        assert target["generated_assets"]["storyboard_image"] == "storyboards/E1S01.png"

    def test_batch_update_scene_assets_writes_with_non_dict_sibling(self, tmp_path: Path):
        """Batch writes filter nondict siblings and write valid IDs successfully without AttributeError."""
        pm = _pm(tmp_path)
        self._seed_non_dict_element(tmp_path)
        pm.batch_update_scene_assets("demo", "episode_1.json", [("E1S01", "video_clip", "videos/E1S01.mp4")])
        saved = pm.load_script("demo", "episode_1.json")
        target = next(s for s in saved["segments"] if isinstance(s, dict) and s.get("segment_id") == "E1S01")
        assert target["generated_assets"]["video_clip"] == "videos/E1S01.mp4"

    def test_batch_update_scene_assets_missing_id_fails_loud_with_non_dict_sibling(self, tmp_path: Path):
        """Nonexistent IDs still fail loudly with KeyError; filtering nondicts must not turn them into false ID matches."""
        pm = _pm(tmp_path)
        self._seed_non_dict_element(tmp_path)
        with pytest.raises(KeyError):
            pm.batch_update_scene_assets("demo", "episode_1.json", [("E1S99", "video_clip", "videos/x.mp4")])
