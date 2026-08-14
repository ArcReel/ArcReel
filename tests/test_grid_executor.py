"""Tests for grid generation task executor."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lib.config.resolver import ProviderModel
from server.services.generation_context import GenerationContext, ImageLaneResult

pytestmark = pytest.mark.unit


def _image_ctx(generator, *, provider="openai", model="gpt-image-2", resolution="2K", backend_model=None):
    """把 image lane 解析产物拼成假 GenerationContext，替换 resolve_generation_context 单点。

    backend_model 可与 model 发散，模拟自定义供应商目标 model 被禁用回退时 backend
    实际身份与解析 model_id 不同的场景。
    """
    ctx = GenerationContext(
        generator=generator,
        image_lane=ImageLaneResult(
            provider_model=ProviderModel(provider, model),
            backend_name=provider,
            backend_model=backend_model if backend_model is not None else model,
            resolution=resolution,
        ),
    )

    async def _resolve(*args, **kwargs):
        return ctx

    return _resolve


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
                "creation_type": "narration",
                "style": "realistic",
                "generation_mode": "storyboard",
                "grid_storyboard": True,
                "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
                "characters": {},
                "clues": {},
            }
        )
    )
    (p / "scripts" / "episode_1.json").write_text(
        json.dumps(
            {
                "creation_type": "narration",
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
            video_aspect_ratio="9:16",
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
            patch(
                "server.services.generation_tasks.resolve_generation_context",
                new=_image_ctx(mock_generator),
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
        # 联合图内容更新后落格状态复位，等待显式切分
        assert updated_grid_data["split_at"] is None

    async def test_execute_grid_task_does_not_touch_storyboards(self, project_with_script, grid_json):
        """生成任务只产出联合图：不写任何分镜格文件、不回写剧本、不登记分镜版本——
        落格由独立的切分操作（apply_grid_split）显式执行。"""
        from PIL import Image

        from server.services.generation_tasks import execute_grid_task

        grid = grid_json

        # 预置一个已存在的分镜格，锁定「生成完成后分镜字节不变」
        storyboards_dir = project_with_script / "storyboards"
        existing = storyboards_dir / "scene_E1S01.png"
        existing.write_bytes(b"pre-existing-bytes")

        fake_grid_image = Image.new("RGB", (400, 400), color=(0, 0, 0))
        grid_image_path = project_with_script / "grids" / f"{grid.id}.png"
        fake_grid_image.save(grid_image_path, format="PNG")

        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock(return_value=(grid_image_path, 1))

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.generation_tasks.resolve_generation_context",
                new=_image_ctx(mock_generator),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = json.loads((project_with_script / "project.json").read_text())
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text()
            )
            mock_pm_fn.return_value = mock_pm

            await execute_grid_task(
                "test-project",
                grid.id,
                {"prompt": "p", "script_file": "episode_1.json"},
                user_id="test-user",
            )

        # 已有分镜格字节不变，未预置的分镜格不产生
        assert existing.read_bytes() == b"pre-existing-bytes"
        for sid in ("E1S02", "E1S03"):
            assert not (storyboards_dir / f"scene_{sid}.png").exists()
        # 不回写剧本、不登记分镜版本
        assert not mock_pm.batch_update_scene_assets.called
        assert not mock_generator.versions.ensure_current_tracked.called
        assert not mock_generator.versions.add_version.called

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
                    "grid_ffffffffffff",
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
            video_aspect_ratio="9:16",
            prompt="test grid prompt",
        )
        grid_path = project_with_script / "grids" / f"{grid.id}.json"
        grid_path.write_text(json.dumps(grid.to_dict(), ensure_ascii=False, indent=2))
        return grid

    async def _run_grid_task(self, project_with_script, grid, payload, resolve_override=None):
        """Helper：mock 掉 generator 与 project manager，运行 execute_grid_task。"""
        from PIL import Image

        from server.services.generation_tasks import execute_grid_task

        fake_grid_image = Image.new("RGB", (400, 400), color=(128, 128, 128))
        grid_image_path = project_with_script / "grids" / f"{grid.id}.png"
        fake_grid_image.save(grid_image_path, format="PNG")

        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock(return_value=(grid_image_path, 1))

        async def _cap_aware_resolve(project_name, req_payload, *, image, **kwargs):
            # capability-aware：grid 任务按 reference_images 是否非空选 t2i/i2i 槽，
            # 假解析回显对应 payload 槽的 provider/model，锁定「槽选择 → 元数据回填」契约。
            provider, model = req_payload[f"image_provider_{image.capability}"].split("/")
            return GenerationContext(
                generator=mock_generator,
                image_lane=ImageLaneResult(
                    provider_model=ProviderModel(provider, model),
                    backend_name=provider,
                    backend_model=model,
                    resolution="2K",
                ),
            )

        fake_resolve = resolve_override(mock_generator) if resolve_override is not None else _cap_aware_resolve

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch("server.services.generation_tasks.resolve_generation_context", new=fake_resolve),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = json.loads((project_with_script / "project.json").read_text())
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text()
            )
            mock_pm.update_scene_asset.return_value = {}
            mock_pm_fn.return_value = mock_pm

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

    async def test_metadata_records_backend_actual_model_on_divergence(
        self, project_with_script, grid_with_empty_metadata
    ):
        """自定义供应商目标 model 被禁用回退时，backend 实际身份与解析 model_id 发散：
        grid 元数据 provider 记 registry 身份、model 记 backend 实际调用的 model。"""
        grid = grid_with_empty_metadata
        payload = {"prompt": "test grid prompt", "script_file": "episode_1.json"}

        await self._run_grid_task(
            project_with_script,
            grid,
            payload,
            resolve_override=lambda gen: _image_ctx(gen, provider="custom-1", model="m-dead", backend_model="m-live"),
        )

        updated = json.loads((project_with_script / "grids" / f"{grid.id}.json").read_text())
        assert updated["provider"] == "custom-1"
        assert updated["model"] == "m-live"
