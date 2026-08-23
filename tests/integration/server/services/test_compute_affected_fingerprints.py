"""Tests for compute_affected_fingerprints."""

from server.services import generation_tasks
from tests.integration.server.services.generation_tasks_support import (
    _FakePM,
)


class TestGenerationTasks:
    def test_grid_fingerprints_include_split_cells(self, monkeypatch, tmp_path):
        """宫格指纹应包含切割覆写的 canonical 分镜图（cache-bust），但拒绝越出项目目录的路径"""
        from lib.grid.models import FrameCell, GridGeneration
        from lib.grid_manager import GridManager

        project_path = tmp_path / "demo"
        (project_path / "storyboards").mkdir(parents=True)
        (project_path / "grids").mkdir()
        (project_path / "grids" / "grid_0123456789ab.png").write_bytes(b"grid")
        (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"img")
        (project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"img2")
        outside = tmp_path / "outside.png"
        outside.write_bytes(b"secret")

        grid = GridGeneration(
            id="grid_0123456789ab",
            episode=1,
            script_file="ep01.json",
            scene_ids=["E1S01"],
            grid_image_path="grids/grid_0123456789ab.png",
            rows=2,
            cols=2,
            cell_count=4,
            frame_chain=[
                FrameCell(
                    index=0,
                    row=0,
                    col=0,
                    frame_type="first",
                    next_scene_id="E1S01",
                    image_path="storyboards/scene_E1S01.png",
                ),
                FrameCell(
                    index=1,
                    row=0,
                    col=1,
                    frame_type="transition",
                    # 项目内的绝对路径：允许纳入，但指纹 key 必须归一为相对路径
                    image_path=str(project_path / "storyboards" / "scene_E1S02.png"),
                ),
                FrameCell(index=2, row=1, col=0, frame_type="transition", image_path="../outside.png"),
                FrameCell(index=3, row=1, col=1, frame_type="transition", image_path=str(outside)),
            ],
            status="completed",
            prompt=None,
            provider="p",
            model="m",
            grid_size="2K",
            created_at="2026-01-01T00:00:00Z",
        )
        GridManager(project_path).save(grid)

        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        fps = generation_tasks.compute_affected_fingerprints("demo", "grid", "grid_0123456789ab")

        assert "grids/grid_0123456789ab.png" in fps
        assert "storyboards/scene_E1S01.png" in fps
        assert "storyboards/scene_E1S02.png" in fps
        assert all("outside" not in key for key in fps)
        assert all(not key.startswith("/") for key in fps)
