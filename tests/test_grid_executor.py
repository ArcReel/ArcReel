"""Tests for grid generation task executor."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.config.resolver import ProviderModel


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
                        "scenes": [],
                        "props": [],
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
            patch(
                "server.services.generation_tasks._resolve_effective_image_backend",
                new=AsyncMock(return_value=ProviderModel("openai", "gpt-image-2")),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = json.loads((project_with_script / "project.json").read_text())
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text()
            )
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

        # 切割写入 canonical 分镜路径须登记版本（先补登旧文件，覆写后记新版本），
        # 否则版本面板的「当前版本」与磁盘内容脱节
        assert mock_generator.versions.ensure_current_tracked.called
        add_calls = mock_generator.versions.add_version.call_args_list
        assert add_calls
        assert all(c.kwargs["resource_type"] == "storyboards" and c.kwargs["source"] == "grid_split" for c in add_calls)

    async def test_execute_grid_task_writes_clean_filenames(self, project_with_script, grid_json):
        """切割后 cell 文件名为 scene_{id}.png（无 _first/_last 后缀），且不再更新 storyboard_last_image。"""
        from PIL import Image

        from server.services.generation_tasks import execute_grid_task

        grid = grid_json

        fake_grid_image = Image.new("RGB", (400, 400), color=(0, 0, 0))
        grid_image_path = project_with_script / "grids" / f"{grid.id}.png"
        fake_grid_image.save(grid_image_path, format="PNG")

        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock(return_value=(grid_image_path, 1))

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch("server.services.generation_tasks.get_media_generator", new_callable=AsyncMock) as mock_get_gen,
            patch(
                "server.services.generation_tasks._resolve_effective_image_backend",
                new=AsyncMock(return_value=ProviderModel("openai", "gpt-image-2")),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = json.loads((project_with_script / "project.json").read_text())
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text()
            )
            mock_pm_fn.return_value = mock_pm
            mock_get_gen.return_value = mock_generator

            await execute_grid_task(
                "test-project",
                grid.id,
                {"prompt": "p", "script_file": "episode_1.json"},
                user_id="test-user",
            )

        storyboards_dir = project_with_script / "storyboards"
        # grid 由 fixture 配置 scene_ids=[E1S01,E1S02,E1S03]，rows=cols=2
        for sid in ("E1S01", "E1S02", "E1S03"):
            assert (storyboards_dir / f"scene_{sid}.png").exists(), f"missing scene_{sid}.png"
            assert not (storyboards_dir / f"scene_{sid}_first.png").exists(), "legacy _first.png must not be written"
            assert not (storyboards_dir / f"scene_{sid}_last.png").exists(), "legacy _last.png must not be written"

        mock_pm.batch_update_scene_assets.assert_called_once()
        updates = mock_pm.batch_update_scene_assets.call_args.kwargs["updates"]
        asset_types = {asset_type for _, asset_type, _ in updates}
        assert "storyboard_last_image" not in asset_types
        # 每个有效 scene 应写入 storyboard_image / grid_id / grid_cell_index
        sb_paths = {sid: path for sid, asset_type, path in updates if asset_type == "storyboard_image"}
        assert sb_paths == {
            "E1S01": "storyboards/scene_E1S01.png",
            "E1S02": "storyboards/scene_E1S02.png",
            "E1S03": "storyboards/scene_E1S03.png",
        }

    async def test_execute_grid_task_skips_missing_scene_ids(self, project_with_script, grid_json, caplog):
        """grid_plan 生成后 agent 改动了剧本(删/拆分镜)→ frame_chain 中部分 next_scene_id
        不再存在于当前剧本时:跳过该 cell 的 PNG 保存 + warning + 不让 batch_update 抛
        KeyError 整批回滚(避免 cell PNG 已落盘但 script 无引用的 orphan PNG)。
        """
        import logging

        from PIL import Image

        from server.services.generation_tasks import execute_grid_task

        grid = grid_json

        fake_grid_image = Image.new("RGB", (400, 400), color=(50, 50, 50))
        grid_image_path = project_with_script / "grids" / f"{grid.id}.png"
        fake_grid_image.save(grid_image_path, format="PNG")

        # 模拟"剧本被并发改动"——load_script 返回的剧本只含 E1S01,故 E1S02 / E1S03 应被 skip
        script_data = json.loads((project_with_script / "scripts" / "episode_1.json").read_text())
        script_data["segments"] = [seg for seg in script_data["segments"] if seg["segment_id"] == "E1S01"]

        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock(return_value=(grid_image_path, 1))

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch("server.services.generation_tasks.get_media_generator", new_callable=AsyncMock) as mock_get_gen,
            patch(
                "server.services.generation_tasks._resolve_effective_image_backend",
                new=AsyncMock(return_value=ProviderModel("openai", "gpt-image-2")),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = json.loads((project_with_script / "project.json").read_text())
            mock_pm.load_script.return_value = script_data
            mock_pm_fn.return_value = mock_pm
            mock_get_gen.return_value = mock_generator

            with caplog.at_level(logging.WARNING, logger="server.services.generation_tasks"):
                await execute_grid_task(
                    "test-project",
                    grid.id,
                    {"prompt": "p", "script_file": "episode_1.json"},
                    user_id="test-user",
                )

        storyboards_dir = project_with_script / "storyboards"
        # E1S01 仍存在 → cell PNG 落盘
        assert (storyboards_dir / "scene_E1S01.png").exists()
        # E1S02 / E1S03 已不存在 → cell PNG 未落盘(避免 orphan)
        assert not (storyboards_dir / "scene_E1S02.png").exists()
        assert not (storyboards_dir / "scene_E1S03.png").exists()

        # warning 显式列出跳过的分镜 id
        warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("E1S02" in m and "E1S03" in m for m in warnings)

        # batch_update 必须被对 valid 分镜(E1S01)调用,且仅包含此 scene id ——
        # 旧的 `if .called:` 条件分支会让「实现完全不写回」时测试静默通过,无法锁定
        # 「有效分镜必须被回写」的契约;改为强制断言。
        assert mock_pm.batch_update_scene_assets.called, (
            "valid 分镜(E1S01)应触发 batch_update_scene_assets 回写,实现未调用"
        )
        updates = mock_pm.batch_update_scene_assets.call_args.kwargs["updates"]
        scene_ids = {sid for sid, _, _ in updates}
        assert scene_ids == {"E1S01"}

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


class TestGridMetadataT2II2ISlotSelection:
    """Bug 2 回归：execute_grid_task 必须按 reference_images 是否非空决定写 T2I 还是 I2I 槽。"""

    @pytest.fixture
    def grid_with_empty_metadata(self, project_with_script):
        """模拟 route 层修复后的状态：grid 创建时 provider/model 为空，由 task 层回填。"""
        from lib.grid.models import GridGeneration

        grid = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["E1S01", "E1S02", "E1S03"],
            rows=2,
            cols=2,
            grid_size="2K",
            provider="",
            model="",
            prompt="test grid prompt",
        )
        grid_path = project_with_script / "grids" / f"{grid.id}.json"
        grid_path.write_text(json.dumps(grid.to_dict(), ensure_ascii=False, indent=2))
        return grid

    async def _run_grid_task(self, project_with_script, grid, payload):
        """Helper：mock 掉 generator 与 project manager，运行 execute_grid_task。"""
        from PIL import Image

        from server.services.generation_tasks import execute_grid_task

        fake_grid_image = Image.new("RGB", (400, 400), color=(128, 128, 128))
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
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text()
            )
            mock_pm.update_scene_asset.return_value = {}
            mock_pm_fn.return_value = mock_pm
            mock_get_gen.return_value = mock_generator

            await execute_grid_task("test-project", grid.id, payload, user_id="test-user")

    async def test_uses_t2i_slot_when_no_reference_images(self, project_with_script, grid_with_empty_metadata):
        """无 character/scene/prop sheet → reference_images 为空 → 写 T2I 槽配置"""
        grid = grid_with_empty_metadata
        payload = {
            "prompt": "test grid prompt",
            "script_file": "episode_1.json",
            "image_provider_t2i": "openai/gpt-image-t2i",
            "image_provider_i2i": "openai/gpt-image-i2i",
        }

        await self._run_grid_task(project_with_script, grid, payload)

        updated = json.loads((project_with_script / "grids" / f"{grid.id}.json").read_text())
        assert updated["provider"] == "openai"
        assert updated["model"] == "gpt-image-t2i"

    async def test_uses_i2i_slot_when_reference_images_present(self, project_with_script, grid_with_empty_metadata):
        """有 character sheet 且 segment 引用了角色 → reference_images 非空 → 写 I2I 槽配置"""
        # 给 project + script 注入 character sheet，让 _collect_grid_reference_images 返回非空
        project_data = json.loads((project_with_script / "project.json").read_text())
        project_data["characters"]["hero"] = {"character_sheet": "characters/hero.png"}
        (project_with_script / "project.json").write_text(json.dumps(project_data))
        (project_with_script / "characters" / "hero.png").write_bytes(b"fake-image")

        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text())
        script["segments"][0]["characters_in_segment"] = ["hero"]
        (project_with_script / "scripts" / "episode_1.json").write_text(json.dumps(script))

        grid = grid_with_empty_metadata
        payload = {
            "prompt": "test grid prompt",
            "script_file": "episode_1.json",
            "image_provider_t2i": "openai/gpt-image-t2i",
            "image_provider_i2i": "openai/gpt-image-i2i",
        }

        await self._run_grid_task(project_with_script, grid, payload)

        updated = json.loads((project_with_script / "grids" / f"{grid.id}.json").read_text())
        assert updated["provider"] == "openai"
        assert updated["model"] == "gpt-image-i2i"
