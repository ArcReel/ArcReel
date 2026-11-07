"""Regression guards for PM content_mode-based structure dispatch.

Pin an edge case: malformed content_mode=narration scripts storing data under `scenes` without `segments`.
PM must retain key-presence fallback to `scenes`, rather than return an empty list by mapping content_mode literally
to `segments`.

When consolidating field dispatch through lib.script.script_skeleton evidence-based resolution, naively replacing it with
`items = script.get(<content_mode 对应 items_key>, [])` and removing the `"segments" in script`
guard makes these assertions fail, precisely the regression they protect. Assert external behavior only; do not patch private methods.
"""

from __future__ import annotations

from pathlib import Path

from lib.project.project_manager import ProjectManager


def _pm(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    return pm


def _narration_script_with_data_under_scenes() -> dict:
    """Malformed content_mode=narration script with data under `scenes` and no `segments`."""
    return {
        "episode": 1,
        "title": "标题",
        "content_mode": "narration",
        "summary": "摘要",
        "novel": {"title": "小说", "chapter": "第一章"},
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "characters_in_scene": ["角色A"],
                "generated_assets": {},
            }
        ],
    }


class TestNarrationDataUnderScenesFallback:
    def test_update_scene_asset_falls_back_to_scenes(self, tmp_path: Path):
        pm = _pm(tmp_path)
        pm.save_script("demo", _narration_script_with_data_under_scenes(), "episode_1.json", validate=False)

        # No KeyError: find E1S01 under scenes and write back.
        pm.update_scene_asset("demo", "episode_1.json", "E1S01", "storyboard_image", "storyboards/scene_E1S01.png")

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["scenes"][0]["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S01.png"

    def test_batch_update_scene_assets_falls_back_to_scenes(self, tmp_path: Path):
        pm = _pm(tmp_path)
        pm.save_script("demo", _narration_script_with_data_under_scenes(), "episode_1.json", validate=False)

        pm.batch_update_scene_assets("demo", "episode_1.json", [("E1S01", "video_clip", "videos/scene_E1S01.mp4")])

        saved = pm.load_script("demo", "episode_1.json")
        assert saved["scenes"][0]["generated_assets"]["video_clip"] == "videos/scene_E1S01.mp4"

    def test_get_pending_scenes_reads_scenes(self, tmp_path: Path):
        pm = _pm(tmp_path)
        pm.save_script("demo", _narration_script_with_data_under_scenes(), "episode_1.json", validate=False)

        pending = pm.get_pending_scenes("demo", "episode_1.json", "storyboard_image")

        assert [item["scene_id"] for item in pending] == ["E1S01"]

    def test_get_scenes_needing_storyboard_reads_scenes(self, tmp_path: Path):
        pm = _pm(tmp_path)
        pm.save_script("demo", _narration_script_with_data_under_scenes(), "episode_1.json", validate=False)

        needing = pm.get_scenes_needing_storyboard("demo", "episode_1.json")

        assert [item["scene_id"] for item in needing] == ["E1S01"]
