"""Tests for the standalone grid split service (apply_grid_split)."""

import json
import logging
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from lib.grid.models import GridGeneration
from server.services.grid_split import GridImageNotReadyError, apply_grid_split

pytestmark = pytest.mark.unit


@pytest.fixture
def project_with_script(tmp_path):
    p = tmp_path / "projects" / "test-project"
    for d in ("storyboards", "grids", "scripts"):
        (p / d).mkdir(parents=True)
    (p / "project.json").write_text(
        json.dumps(
            {
                "name": "test-project",
                "title": "Test",
                "content_mode": "narration",
                "aspect_ratio": "9:16",
                "generation_mode": "storyboard",
                "grid_storyboard": True,
                "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
                "characters": {},
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
                        "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
                    }
                    for i in range(1, 4)
                ],
            }
        )
    )
    return p


@pytest.fixture
def grid_with_image(project_with_script):
    """联合图已就绪（completed、grid_image_path 指向落盘 PNG）的宫格记录。"""
    grid = GridGeneration.create(
        episode=1,
        script_file="episode_1.json",
        scene_ids=["E1S01", "E1S02", "E1S03"],
        rows=2,
        cols=2,
        grid_size="2K",
        provider="openai",
        model="gpt-image-2",
        prompt="p",
    )
    grid.status = "completed"
    grid.grid_image_path = f"grids/{grid.id}.png"
    Image.new("RGB", (400, 400), color=(30, 60, 90)).save(project_with_script / "grids" / f"{grid.id}.png")
    (project_with_script / "grids" / f"{grid.id}.json").write_text(json.dumps(grid.to_dict(), ensure_ascii=False))
    return grid


def _mock_pm(project_with_script, script_data=None):
    pm = MagicMock()
    pm.get_project_path.return_value = project_with_script
    pm.load_project.return_value = json.loads((project_with_script / "project.json").read_text())
    pm.load_script.return_value = (
        script_data
        if script_data is not None
        else json.loads((project_with_script / "scripts" / "episode_1.json").read_text())
    )
    return pm


class TestApplyGridSplit:
    async def test_split_writes_cells_and_versions(self, project_with_script, grid_with_image):
        """切分覆写各分镜格：文件名 scene_{id}.png（无 first/last 后缀）、逐格入版本史、
        剧本回写 storyboard_image / grid_id / grid_cell_index、split_at 落章。"""
        from lib.version_manager import VersionManager

        grid = grid_with_image
        storyboards_dir = project_with_script / "storyboards"
        # 预置旧分镜格：覆写前必须补登版本，否则旧字节丢失
        (storyboards_dir / "scene_E1S01.png").write_bytes(b"old-bytes")

        pm = _mock_pm(project_with_script)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={"grids/x.png": 1}),
        ):
            result = await apply_grid_split("test-project", grid)

        for sid in ("E1S01", "E1S02", "E1S03"):
            assert (storyboards_dir / f"scene_{sid}.png").exists(), f"missing scene_{sid}.png"
            assert not (storyboards_dir / f"scene_{sid}_first.png").exists()
            assert not (storyboards_dir / f"scene_{sid}_last.png").exists()
        assert result.updated_scene_ids == ["E1S01", "E1S02", "E1S03"]
        assert result.missing_scene_ids == []

        # 逐格版本：旧文件补登 + 覆写后新版本（source=grid_split）
        versions = VersionManager(project_with_script)
        e1s01 = versions.get_versions("storyboards", "E1S01")
        assert len(e1s01["versions"]) >= 2
        latest = e1s01["versions"][-1]
        assert latest.get("source") == "grid_split" or latest.get("metadata", {}).get("source") == "grid_split"

        pm.batch_update_scene_assets.assert_called_once()
        updates = pm.batch_update_scene_assets.call_args.kwargs["updates"]
        sb_paths = {sid: path for sid, asset_type, path in updates if asset_type == "storyboard_image"}
        assert sb_paths == {
            "E1S01": "storyboards/scene_E1S01.png",
            "E1S02": "storyboards/scene_E1S02.png",
            "E1S03": "storyboards/scene_E1S03.png",
        }
        asset_types = {asset_type for _, asset_type, _ in updates}
        assert asset_types == {"storyboard_image", "grid_id", "grid_cell_index"}

        saved = json.loads((project_with_script / "grids" / f"{grid.id}.json").read_text())
        assert saved["split_at"] is not None

    async def test_split_skips_missing_scene_ids(self, project_with_script, grid_with_image, caplog):
        """grid plan 生成后剧本被改动（删/拆分镜）→ frame_chain 中已不存在的 next_scene_id
        跳过 cell PNG 保存 + warning + 不让 batch_update 抛 KeyError 整批回滚
        （避免 cell PNG 已落盘但 script 无引用的 orphan PNG）。"""
        grid = grid_with_image
        script_data = json.loads((project_with_script / "scripts" / "episode_1.json").read_text())
        script_data["segments"] = [seg for seg in script_data["segments"] if seg["segment_id"] == "E1S01"]

        pm = _mock_pm(project_with_script, script_data)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={}),
            caplog.at_level(logging.WARNING, logger="server.services.grid_split"),
        ):
            result = await apply_grid_split("test-project", grid)

        storyboards_dir = project_with_script / "storyboards"
        assert (storyboards_dir / "scene_E1S01.png").exists()
        assert not (storyboards_dir / "scene_E1S02.png").exists()
        assert not (storyboards_dir / "scene_E1S03.png").exists()
        assert result.missing_scene_ids == ["E1S02", "E1S03"]

        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("E1S02" in m and "E1S03" in m for m in warnings)

        assert pm.batch_update_scene_assets.called
        updates = pm.batch_update_scene_assets.call_args.kwargs["updates"]
        assert {sid for sid, _, _ in updates} == {"E1S01"}

    async def test_split_requires_grid_image(self, project_with_script, grid_with_image):
        grid = grid_with_image
        (project_with_script / "grids" / f"{grid.id}.png").unlink()

        pm = _mock_pm(project_with_script)
        with patch("server.services.grid_split.get_project_manager", return_value=pm):
            with pytest.raises(GridImageNotReadyError):
                await apply_grid_split("test-project", grid)

    async def test_split_emits_grid_split_event(self, project_with_script, grid_with_image):
        grid = grid_with_image
        pm = _mock_pm(project_with_script)
        with (
            patch("server.services.grid_split.get_project_manager", return_value=pm),
            patch("server.services.generation_tasks.emit_generation_success_batch", return_value={"a": 1}) as emit,
        ):
            result = await apply_grid_split("test-project", grid)

        emit.assert_called_once()
        assert emit.call_args.kwargs["task_type"] == "grid_split"
        assert emit.call_args.kwargs["resource_id"] == grid.id
        assert result.asset_fingerprints == {"a": 1}
