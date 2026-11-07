"""Tests for emit_generation_success_batch."""

import pytest

from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE
from server.services.tasks import generation_tasks
from tests.fakes import use_project_manager
from tests.integration.server.services.tasks.generation_tasks_support import (
    _FakePM,
)


class TestGenerationTasks:
    def test_emit_success_batch_includes_fingerprints(self, monkeypatch, tmp_path):
        """Generation-success events carry asset_fingerprints."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        (project_path / "storyboards").mkdir()
        sb = project_path / "storyboards" / "scene_E1S01.png"
        sb.write_bytes(b"img")

        fake_pm = _FakePM(project_path)
        use_project_manager(monkeypatch, fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="storyboard",
            project_name="demo",
            resource_id="E1S01",
            payload={"script_file": "ep01.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert "asset_fingerprints" in change
        assert "storyboards/scene_E1S01.png" in change["asset_fingerprints"]
        assert isinstance(change["asset_fingerprints"]["storyboards/scene_E1S01.png"], int)

    def test_emit_success_batch_includes_derivative_sheet_fingerprint(self, monkeypatch, tmp_path):
        """Derivative-image completion events include its fingerprint for frontend cache busting, preventing overlays retaining old images."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        sheet = project_path / "characters" / "derivatives" / "阿岚" / "战斗装.png"
        sheet.parent.mkdir(parents=True)
        sheet.write_bytes(b"img")
        use_project_manager(monkeypatch, _FakePM(project_path))

        generation_tasks.emit_generation_success_batch(
            task_type=DERIVATIVE_TASK_TYPE,
            project_name="demo",
            resource_id="阿岚/战斗装",
            payload={},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_id"] == "阿岚/战斗装"
        fingerprints = change["asset_fingerprints"]
        assert isinstance(fingerprints["characters/derivatives/阿岚/战斗装.png"], int)

    @pytest.mark.parametrize(
        ("task_type", "expected_label_key", "expected_label"),
        [
            pytest.param("grid", "grid", "多宫格分镜「E1G01」", id="grid"),
            pytest.param("grid_split", "grid_split", "多宫格分镜「E1G01」切分", id="grid-split"),
            pytest.param("voice_sample", "voice_sample", "「E1G01」试听样本", id="voice-sample"),
            pytest.param("character", "asset_image_character", "角色「E1G01」资产图", id="character-sheet"),
            pytest.param("prop", "asset_image_prop", "道具「E1G01」资产图", id="prop-sheet"),
            pytest.param(
                DERIVATIVE_TASK_TYPE,
                "asset_image_character_derivative",
                "角色衍生「E1G01」资产图",
                id="character-derivative-sheet",
            ),
        ],
    )
    def test_emit_success_batch_carries_label_key_and_params(
        self, monkeypatch, tmp_path, task_type, expected_label_key, expected_label
    ):
        """Completion events carry stable label_key and parameters for user-language rendering; label is default-language fallback only."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        use_project_manager(monkeypatch, _FakePM(project_path))

        generation_tasks.emit_generation_success_batch(
            task_type=task_type,
            project_name="demo",
            resource_id="E1G01",
            payload={},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["label_key"] == expected_label_key
        assert change["label_params"] == {"id": "E1G01"}
        assert change["label"] == expected_label

    @pytest.mark.parametrize(
        ("script", "expected_entity_type", "expected_label"),
        [
            pytest.param(
                {"content_mode": "drama", "scenes": [{"scene_id": "E1S01"}]},
                "drama_scene",
                "分镜「E1S01」",
                id="drama-scenes",
            ),
            pytest.param(
                {"content_mode": "ad", "shots": [{"shot_id": "E1S01"}]},
                "shot",
                "分镜「E1S01」",
                id="ad-shots",
            ),
            pytest.param(
                {"content_mode": "narration", "segments": [{"segment_id": "E1S01"}]},
                "segment",
                "分镜「E1S01」",
                id="narration-segments",
            ),
        ],
    )
    def test_emit_success_batch_storyboard_entity_type_follows_skeleton(
        self, monkeypatch, tmp_path, script, expected_entity_type, expected_label
    ):
        """Storyboard/video completion notices match per-storyboard events: derive entity types from project skeletons and use the Chinese noun “分镜” across all three storyboard skeletons."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = script
        use_project_manager(monkeypatch, fake_pm)

        for task_type, action in (("storyboard", "storyboard_ready"), ("video", "video_ready")):
            captured.clear()
            generation_tasks.emit_generation_success_batch(
                task_type=task_type,
                project_name="demo",
                resource_id="E1S01",
                payload={"script_file": "ep01.json"},
            )
            assert len(captured) == 1
            change = captured[0][0]
            assert change["entity_type"] == expected_entity_type
            assert change["action"] == action
            assert change["label"] == expected_label

    def test_emit_success_batch_reference_video_entity_type_aligns_with_frontend(self, monkeypatch, tmp_path):
        """Reference-to-video completion entity_type is frontend-recognized "reference_unit", never backend-only "reference_video_unit", so group titles use “视频单元” rather than generic “内容”. Entry wording stays “视频单元” regardless of skeleton nouns."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = {"content_mode": "narration", "video_units": [{"unit_id": "U01"}]}
        use_project_manager(monkeypatch, fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="reference_video",
            project_name="demo",
            resource_id="U01",
            payload={"script_file": "ep01.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "reference_unit"
        assert change["action"] == "reference_video_ready"
        assert change["label_key"] == "skeleton_video_units"
        assert change["label"] == "视频单元「U01」"

    def test_emit_success_batch_reference_video_ad_entity_type_not_shot(self, monkeypatch, tmp_path):
        """Ad skeletons always contain shots[] alongside reference_video-derived video_units. resolve_script_kind would see shots and choose content_mode==ad→shots, mismatching the actual video_unit resource. Resolve explicitly rather than drifting to "shot"."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = {
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01"}],
            "video_units": [{"unit_id": "U01", "shot_ids": ["E1S01"]}],
        }
        use_project_manager(monkeypatch, fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="reference_video",
            project_name="demo",
            resource_id="U01",
            payload={"script_file": "episode_1.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "reference_unit"
        assert change["action"] == "reference_video_ready"
        assert change["label_key"] == "skeleton_video_units"
        assert change["label"] == "视频单元「U01」"

    def test_emit_success_batch_reference_video_tts_entity_type_not_shot(self, monkeypatch, tmp_path):
        """TTS and video tasks share project generation mode; mixed ad reference-to-video skeletons must not classify unit events as shots."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.project.update(content_mode="ad", generation_mode="reference_video")
        fake_pm.script = {
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01"}],
            "video_units": [{"unit_id": "E1U01"}],
        }
        use_project_manager(monkeypatch, fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="tts",
            project_name="demo",
            resource_id="E1U01",
            payload={"script_file": "episode_1.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "reference_unit"
        assert change["action"] == "tts_ready"
        assert change["label_key"] == "narration_audio"
        assert change["label"] == "旁白配音「E1U01」"

    def test_emit_success_batch_falls_back_to_segments_when_script_load_fails(self, monkeypatch, tmp_path):
        """If skeleton lookup cannot read scripts (missing/corrupt), fall back to segments/“分镜” without interrupting notifications."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)

        def _raise_load_script(project_name, script_file):
            raise FileNotFoundError(script_file)

        fake_pm.load_script = _raise_load_script
        use_project_manager(monkeypatch, fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="storyboard",
            project_name="demo",
            resource_id="E1S01",
            payload={"script_file": "missing.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "segment"
        assert change["label"] == "分镜「E1S01」"

    def test_emit_success_batch_falls_back_to_segments_when_script_not_a_dict(self, monkeypatch, tmp_path):
        """Nondict scripts such as top-level arrays fall back to segments/“分镜”, preventing resolve_script_kind .get() AttributeError from interrupting notifications."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = ["not", "a", "dict"]
        use_project_manager(monkeypatch, fake_pm)

        generation_tasks.emit_generation_success_batch(
            task_type="storyboard",
            project_name="demo",
            resource_id="E1S01",
            payload={"script_file": "corrupted.json"},
        )

        assert len(captured) == 1
        change = captured[0][0]
        assert change["entity_type"] == "segment"
        assert change["label"] == "分镜「E1S01」"

    def test_emit_success_batch_attaches_script_file_and_episode(self, monkeypatch, tmp_path):
        """Skeleton-driven completion events include script_file and episode for episode-scoped consumers; explicitly protect these fields against silent loss in future emit changes."""
        captured = []
        monkeypatch.setattr(
            generation_tasks,
            "emit_project_change_batch",
            lambda project_name, changes: captured.append(changes),
        )

        project_path = tmp_path / "demo"
        project_path.mkdir()
        fake_pm = _FakePM(project_path)
        fake_pm.script = {"content_mode": "narration", "episode": 3, "segments": [{"segment_id": "E3S01"}]}
        use_project_manager(monkeypatch, fake_pm)

        for task_type in ("storyboard", "video", "reference_video"):
            captured.clear()
            generation_tasks.emit_generation_success_batch(
                task_type=task_type,
                project_name="demo",
                resource_id="E3S01",
                payload={"script_file": "ep03.json"},
            )
            assert len(captured) == 1
            change = captured[0][0]
            assert change["script_file"] == "ep03.json"
            assert change["episode"] == 3
