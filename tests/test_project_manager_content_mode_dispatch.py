"""PM 按 content_mode 选数据结构的守卫回归测试。

锁定一个边缘行为：content_mode=narration 但数据落在 `scenes` 键下（无 `segments` 键）
的畸形脚本，PM 必须沿键存在性守卫回退去读 `scenes`，而非因 content_mode 字面映射到
`segments` 就读到空列表。

收敛字段名分派（→ lib.script_models.script_shape）时若天真改为
`items = script.get(<content_mode 对应 items_key>, [])`、丢掉 `"segments" in script`
守卫，这些断言会变红——这正是它们要守的回归。只断言外部行为，不 patch 私有方法。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.project_manager import ProjectManager


def _pm(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    return pm


def _narration_script_with_data_under_scenes() -> dict:
    """content_mode=narration，但内容存在 `scenes` 键下、无 `segments` 键的畸形脚本。"""
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


@pytest.mark.unit
class TestNarrationDataUnderScenesFallback:
    def test_update_scene_asset_falls_back_to_scenes(self, tmp_path: Path):
        pm = _pm(tmp_path)
        pm.save_script("demo", _narration_script_with_data_under_scenes(), "episode_1.json", validate=False)

        # 不应抛 KeyError；应命中 scenes 下的 E1S01 并回写
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
