"""Tests for grid generation task executor."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def project_with_script(tmp_path):
    p = tmp_path / "projects" / "test-project"
    for d in ("storyboards", "grids", "scripts", "characters", "clues"):
        (p / d).mkdir(parents=True)
    (p / "project.json").write_text(
        json.dumps(
            {
                "name": "test-project",
                "title": "Test",
                "content_mode": "narration",
                "style": "realistic",
                "generation_mode": "grid",
                "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
                "characters": {},
                "clues": {},
            }
        )
    )
    (p / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": f"E1S0{i}",
                        "episode": 1,
                        "segment_break": i == 3,
                        "duration_seconds": 4,
                        "novel_text": "text",
                        "characters_in_segment": [],
                        "clues_in_segment": [],
                        "image_prompt": {
                            "scene": f"scene{i}",
                            "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                        },
                        "video_prompt": {
                            "action": f"action{i}",
                            "camera_motion": "static",
                            "ambiance_audio": "quiet",
                            "dialogue": [],
                        },
                        "transition_to_next": "cut",
                        "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
                    }
                    for i in range(1, 7)
                ],
            }
        )
    )
    return p


class TestGroupBySegmentBreak:
    def test_groups(self, project_with_script):
        from server.services.generation_tasks import _group_scenes_by_segment_break

        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text())
        items = script["segments"]
        groups = _group_scenes_by_segment_break(items, "segment_id")
        # E1S03 has segment_break=True, so groups: [E1S01,E1S02] and [E1S03,E1S04,E1S05,E1S06]
        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 4

    def test_no_breaks(self):
        from server.services.generation_tasks import _group_scenes_by_segment_break

        items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        groups = _group_scenes_by_segment_break(items, "id")
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_empty_list(self):
        from server.services.generation_tasks import _group_scenes_by_segment_break

        groups = _group_scenes_by_segment_break([], "id")
        assert groups == []

    def test_break_at_first_item(self):
        from server.services.generation_tasks import _group_scenes_by_segment_break

        items = [{"id": "a", "segment_break": True}, {"id": "b"}, {"id": "c"}]
        groups = _group_scenes_by_segment_break(items, "id")
        # segment_break on first item: current is empty so no split, all in one group
        assert len(groups) == 1
        assert len(groups[0]) == 3


class TestCollectGridReferenceImages:
    def test_no_references(self, project_with_script):
        from server.services.generation_tasks import _collect_grid_reference_images

        paths, metadata = _collect_grid_reference_images(
            project_with_script,
            {"script_file": "episode_1.json"},
            ["E1S01", "E1S02"],
        )
        assert paths is None
        assert metadata == []

    def test_with_character_sheet(self, project_with_script):
        from server.services.generation_tasks import _collect_grid_reference_images

        # Add a character with a sheet
        project_data = json.loads((project_with_script / "project.json").read_text())
        project_data["characters"]["hero"] = {"character_sheet": "characters/hero.png"}
        (project_with_script / "project.json").write_text(json.dumps(project_data))
        (project_with_script / "characters" / "hero.png").write_bytes(b"fake-image")

        # Update script to reference the character
        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text())
        script["segments"][0]["characters_in_segment"] = ["hero"]
        (project_with_script / "scripts" / "episode_1.json").write_text(json.dumps(script))

        paths, metadata = _collect_grid_reference_images(
            project_with_script,
            {"script_file": "episode_1.json"},
            ["E1S01"],
        )
        assert paths is not None
        assert len(paths) == 1
        assert Path(str(paths[0])).name == "hero.png"
        assert len(metadata) == 1
        assert metadata[0]["name"] == "hero"
        assert metadata[0]["ref_type"] == "character"

    def test_deduplicates_references(self, project_with_script):
        from server.services.generation_tasks import _collect_grid_reference_images

        project_data = json.loads((project_with_script / "project.json").read_text())
        project_data["characters"]["hero"] = {"character_sheet": "characters/hero.png"}
        (project_with_script / "project.json").write_text(json.dumps(project_data))
        (project_with_script / "characters" / "hero.png").write_bytes(b"fake-image")

        # Both segments reference same character
        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text())
        script["segments"][0]["characters_in_segment"] = ["hero"]
        script["segments"][1]["characters_in_segment"] = ["hero"]
        (project_with_script / "scripts" / "episode_1.json").write_text(json.dumps(script))

        paths, metadata = _collect_grid_reference_images(
            project_with_script,
            {"script_file": "episode_1.json"},
            ["E1S01", "E1S02"],
        )
        assert paths is not None
        assert len(paths) == 1  # Deduplicated
        assert len(metadata) == 1  # Deduplicated


class TestExecuteGridTask:
    @pytest.fixture
    def grid_json(self, project_with_script):
        """Create a grid JSON file."""
        from lib.grid.models import GridGeneration

        grid = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["E1S01", "E1S02", "E1S03"],
            rows=2,
            cols=2,
            grid_size="2K",
            provider="gemini-aistudio",
            model="gemini-2.0-flash-preview-image-generation",
            prompt="test grid prompt",
        )
        grid_path = project_with_script / "grids" / f"{grid.id}.json"
        grid_path.write_text(json.dumps(grid.to_dict(), ensure_ascii=False, indent=2))
        return grid

    async def test_execute_grid_task_success(self, project_with_script, grid_json):
        from PIL import Image

        from server.services.generation_tasks import execute_grid_task

        grid = grid_json

        # Create a fake 400x400 grid image (2x2, each cell 200x200)
        fake_grid_image = Image.new("RGB", (400, 400), color=(128, 200, 100))
        grid_image_path = project_with_script / "grids" / f"{grid.id}.png"
        fake_grid_image.save(grid_image_path, format="PNG")

        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock(return_value=(grid_image_path, 1))

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch("server.services.generation_tasks.get_media_generator", new_callable=AsyncMock) as mock_get_gen,
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = json.loads((project_with_script / "project.json").read_text())
            mock_pm.update_scene_asset.return_value = {}
            mock_pm_fn.return_value = mock_pm
            mock_get_gen.return_value = mock_generator

            result = await execute_grid_task(
                "test-project",
                grid.id,
                {"prompt": "test grid prompt", "script_file": "episode_1.json"},
                user_id="test-user",
            )

        assert result["resource_type"] == "grids"
        assert result["resource_id"] == grid.id
        assert result["version"] == 1
        assert "grids/" in result["file_path"]

        # Verify grid status was updated
        import json as json_mod

        updated_grid_data = json_mod.loads((project_with_script / "grids" / f"{grid.id}.json").read_text())
        assert updated_grid_data["status"] == "completed"
        assert updated_grid_data["grid_image_path"] == f"grids/{grid.id}.png"

    async def test_execute_grid_task_not_found(self):
        from server.services.generation_tasks import execute_grid_task

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = Path("/tmp/nonexistent")
            mock_pm_fn.return_value = mock_pm

            with pytest.raises(ValueError, match="grid not found"):
                await execute_grid_task(
                    "test-project",
                    "grid_nonexistent",
                    {"prompt": "test"},
                    user_id="test-user",
                )


class TestTaskExecutorsRegistry:
    def test_grid_registered(self):
        from server.services.generation_tasks import _TASK_EXECUTORS, execute_grid_task

        assert "grid" in _TASK_EXECUTORS
        assert _TASK_EXECUTORS["grid"] is execute_grid_task
