import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lib.project_manager import ProjectManager


class TestProjectManagerCompatibility(unittest.TestCase):
    def setUp(self):
        self.tmpdir = TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmpdir.cleanup)
        self.pm = ProjectManager(self.tmpdir.name)
        self.project_name = "demo"
        self.pm.create_project(self.project_name)

    def _script_path(self, filename: str) -> Path:
        return self.pm.get_project_path(self.project_name) / "scripts" / filename

    def test_save_script_backfills_missing_metadata_for_narration_segments(self):
        script = {
            "title": "Episode 1",
            "content_mode": "narration",
            "segments": [
                {"segment_id": "E1S01", "duration_seconds": 6},
                {"segment_id": "E1S02", "duration_seconds": 8},
            ],
        }

        self.pm.save_script(self.project_name, script, "episode_1.json")
        saved = self.pm.load_script(self.project_name, "episode_1.json")

        self.assertIn("metadata", saved)
        self.assertEqual(saved["metadata"]["total_scenes"], 2)
        self.assertEqual(saved["metadata"]["estimated_duration_seconds"], 14)
        self.assertIn("created_at", saved["metadata"])
        self.assertIn("updated_at", saved["metadata"])

    def test_save_script_uses_narration_default_duration_when_missing(self):
        script = {
            "title": "Episode 1",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01"}],
        }

        self.pm.save_script(self.project_name, script, "episode_1.json")
        saved = self.pm.load_script(self.project_name, "episode_1.json")

        self.assertEqual(saved["metadata"]["total_scenes"], 1)
        self.assertEqual(saved["metadata"]["estimated_duration_seconds"], 4)

    def test_update_scene_asset_backfills_generated_assets_when_missing(self):
        raw_script = {
            "title": "Episode 1",
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 6}],
        }
        self._script_path("episode_1.json").write_text(
            json.dumps(raw_script, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        self.pm.update_scene_asset(
            project_name=self.project_name,
            script_filename="episode_1.json",
            scene_id="E1S01",
            asset_type="storyboard_image",
            asset_path="storyboards/scene_E1S01.png",
        )

        updated = self.pm.load_script(self.project_name, "episode_1.json")
        segment = updated["segments"][0]

        self.assertEqual(
            segment["generated_assets"]["storyboard_image"],
            "storyboards/scene_E1S01.png",
        )
        self.assertIsNone(segment["generated_assets"]["video_clip"])
        self.assertEqual(segment["generated_assets"]["status"], "storyboard_ready")
        self.assertIn("metadata", updated)


if __name__ == "__main__":
    unittest.main()
