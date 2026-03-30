import json

import pytest

from lib.project_manager import ProjectManager


@pytest.fixture()
def pm_env(tmp_path):
    pm = ProjectManager(str(tmp_path))
    project_name = "demo"
    pm.create_project(project_name)
    return pm, project_name


def _script_path(pm, project_name, filename):
    return pm.get_project_path(project_name) / "scripts" / filename


class TestProjectManagerCompatibility:
    def test_save_script_backfills_missing_metadata_for_narration_segments(self, pm_env):
        pm, project_name = pm_env
        script = {
            "title": "Episode 1",
            "content_mode": "narration",
            "segments": [
                {"segment_id": "E1S01", "duration_seconds": 6},
                {"segment_id": "E1S02", "duration_seconds": 8},
            ],
        }

        pm.save_script(project_name, script, "episode_1.json")
        saved = pm.load_script(project_name, "episode_1.json")

        assert "metadata" in saved
        assert saved["metadata"]["total_scenes"] == 2
        assert saved["metadata"]["estimated_duration_seconds"] == 14
        assert "created_at" in saved["metadata"]
        assert "updated_at" in saved["metadata"]

    def test_save_script_uses_narration_default_duration_when_missing(self, pm_env):
        pm, project_name = pm_env
        script = {
            "title": "Episode 1",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01"}],
        }

        pm.save_script(project_name, script, "episode_1.json")
        saved = pm.load_script(project_name, "episode_1.json")

        assert saved["metadata"]["total_scenes"] == 1
        assert saved["metadata"]["estimated_duration_seconds"] == 4

    def test_save_script_uses_scene_default_duration_when_content_mode_missing(self, pm_env):
        pm, project_name = pm_env
        script = {
            "title": "Episode 1",
            "scenes": [{"scene_id": "001"}],
        }

        pm.save_script(project_name, script, "episode_1.json")
        saved = pm.load_script(project_name, "episode_1.json")

        assert saved["metadata"]["total_scenes"] == 1
        assert saved["metadata"]["estimated_duration_seconds"] == 8

    def test_update_scene_asset_backfills_generated_assets_when_missing(self, pm_env):
        pm, project_name = pm_env
        raw_script = {
            "title": "Episode 1",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 6}],
        }
        _script_path(pm, project_name, "episode_1.json").write_text(
            json.dumps(raw_script, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        pm.update_scene_asset(
            project_name=project_name,
            script_filename="episode_1.json",
            scene_id="E1S01",
            asset_type="storyboard_image",
            asset_path="storyboards/scene_E1S01.png",
        )

        updated = pm.load_script(project_name, "episode_1.json")
        segment = updated["segments"][0]

        assert segment["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S01.png"
        assert segment["generated_assets"]["video_clip"] is None
        assert segment["generated_assets"]["status"] == "storyboard_ready"
        assert "metadata" in updated
