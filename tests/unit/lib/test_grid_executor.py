"""Tests for grid generation task executor."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from lib.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ProviderModel
from lib.project_migrations.runner import migrate_project_dir
from server.services.generation_context import GenerationContext, ImageLaneResult


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
    for d in ("storyboards", "grids", "scripts", "characters", "clues", "source", "drafts/episode_1"):
        (p / d).mkdir(parents=True)
    (p / "project.json").write_text(
        json.dumps(
            {
                "name": "test-project",
                "title": "Test",
                "schema_version": 7,
                "content_mode": "narration",
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
                "episode": 1,
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
    # 生产项目一律处于当前 schema，剧本与其取证链（分集原文 → step1）均已登记进产物清单
    (p / "source" / "episode_1.txt").write_text("原文", encoding="utf-8")
    (p / "drafts" / "episode_1" / "step1_segments.json").write_text(
        json.dumps({"episode": 1, "segments": []}), encoding="utf-8"
    )
    activate_artifact_target_state(p, bump_schema=True)
    # 清单激活只落到清单版本，后续迁移把项目补到当前 schema，产物读路径才准入
    migrate_project_dir(p)
    return p


def _register_sheet(project_path, resource_type, resource_id):
    """把已落盘的资产图登记进产物清单——未登记的图不被生产准入。"""
    from lib.artifact_activation import register_current_resource_artifact

    assert register_current_resource_artifact(
        project_path,
        resource_type=resource_type,
        resource_id=resource_id,
    )


class TestGroupBySegmentBreak:
    def test_groups(self, project_with_script):
        from server.services.generation_tasks import _group_scenes_by_segment_break

        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
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


def _grid_reference_images(project_path, scene_ids):
    """按生产入口调用：清单口径的 resolver 是必选参数。"""
    from lib.artifact_activation import active_artifact_currency_resolver
    from server.services.generation_tasks import _collect_grid_reference_images

    project = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    return _collect_grid_reference_images(
        project_path,
        {"script_file": "episode_1.json"},
        scene_ids,
        currency_resolver=active_artifact_currency_resolver(project_path, project),
    )


class TestCollectGridReferenceImages:
    def test_no_references(self, project_with_script):
        paths, metadata = _grid_reference_images(project_with_script, ["E1S01", "E1S02"])
        assert paths is None
        assert metadata == []

    def test_with_character_sheet(self, project_with_script):
        # Add a character with a sheet
        project_data = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        project_data["characters"]["hero"] = {"description": "hero", "character_sheet": "characters/hero.png"}
        (project_with_script / "project.json").write_text(json.dumps(project_data))
        Image.new("RGB", (4, 4)).save(project_with_script / "characters" / "hero.png")

        # Update script to reference the character
        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        script["segments"][0]["characters_in_segment"] = ["hero"]
        (project_with_script / "scripts" / "episode_1.json").write_text(json.dumps(script))
        _register_sheet(project_with_script, "characters", "hero")

        paths, metadata = _grid_reference_images(project_with_script, ["E1S01"])
        assert paths is not None
        assert len(paths) == 1
        assert Path(str(paths[0])).name == "hero.png"
        assert len(metadata) == 1
        assert metadata[0]["name"] == "hero"
        assert metadata[0]["ref_type"] == "character"

    def test_deduplicates_references(self, project_with_script):
        project_data = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        project_data["characters"]["hero"] = {"description": "hero", "character_sheet": "characters/hero.png"}
        (project_with_script / "project.json").write_text(json.dumps(project_data))
        Image.new("RGB", (4, 4)).save(project_with_script / "characters" / "hero.png")

        # Both segments reference same character
        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        script["segments"][0]["characters_in_segment"] = ["hero"]
        script["segments"][1]["characters_in_segment"] = ["hero"]
        (project_with_script / "scripts" / "episode_1.json").write_text(json.dumps(script))
        _register_sheet(project_with_script, "characters", "hero")

        paths, metadata = _grid_reference_images(project_with_script, ["E1S01", "E1S02"])
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
            mock_pm.load_project.return_value = json.loads(
                (project_with_script / "project.json").read_text(encoding="utf-8")
            )
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8")
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

        updated_grid_data = json_mod.loads(
            (project_with_script / "grids" / f"{grid.id}.json").read_text(encoding="utf-8")
        )
        assert updated_grid_data["status"] == "completed"
        assert updated_grid_data["grid_image_path"] == f"grids/{grid.id}.png"
        # 联合图内容更新后落格状态复位，等待显式切分
        assert updated_grid_data["split_at"] is None

    async def test_grid_rejects_an_unclaimed_bound_script_before_provider(
        self,
        project_with_script,
        grid_json,
    ):
        """剧本已在 episodes 账本里绑定但清单里没有认领 → 在触达供应商之前就拒绝。"""
        from lib.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
        from server.services.generation_tasks import execute_grid_task

        assert ProjectArtifactManifestAdapter(project_with_script).delete_entry(ArtifactKey.episode_script(1))
        project = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))

        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock(side_effect=AssertionError("provider must remain unreachable"))

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.generation_tasks.resolve_generation_context",
                new=_image_ctx(mock_generator),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = project
            mock_pm.load_script.return_value = script
            mock_pm_fn.return_value = mock_pm

            with pytest.raises(ValueError, match="episode script is not registered"):
                await execute_grid_task(
                    "test-project",
                    grid_json.id,
                    {"prompt": "test grid prompt", "script_file": "episode_1.json"},
                    user_id="test-user",
                )

        mock_generator.generate_image_async.assert_not_awaited()

    async def test_grid_registers_generation_frozen_basis_when_script_changes_in_flight(
        self,
        project_with_script,
        grid_json,
    ):
        from lib.grid.layout import grid_aspect_ratio_for
        from lib.visual_artifact_provenance import GridStoryboardVisual, build_grid_composite_visual_basis
        from server.services.generation_tasks import execute_grid_task

        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        project = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        captured = []

        class _Generator:
            versions = MagicMock()

            async def generate_image_async(self, **_kwargs):
                script["segments"][0]["image_prompt"] = "latest prompt"
                return project_with_script / "grids" / f"{grid_json.id}.png", 1

        def _register(*_args, **kwargs):
            captured.append(kwargs["basis"])
            return None

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.generation_tasks.resolve_generation_context",
                new=_image_ctx(_Generator()),
            ),
            patch("server.services.generation_tasks.register_formal_task_artifact", side_effect=_register),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = project
            mock_pm.load_script.return_value = script
            mock_pm_fn.return_value = mock_pm

            await execute_grid_task(
                "test-project",
                grid_json.id,
                {"prompt": "test grid prompt", "script_file": "episode_1.json"},
                user_id="test-user",
            )

        members = tuple(
            GridStoryboardVisual(
                resource_id=f"E1S0{i}",
                image_prompt={
                    "scene": f"scene{i}",
                    "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                },
                video_prompt={
                    "action": f"action{i}",
                    "camera_motion": "static",
                    "ambiance_audio": "quiet",
                    "dialogue": [],
                },
            )
            for i in range(1, 4)
        )
        expected = build_grid_composite_visual_basis(
            group_id=grid_json.id,
            members=members,
            rows=2,
            columns=2,
            style="realistic",
            grid_aspect_ratio=grid_aspect_ratio_for(2, 2, "9:16"),
        )
        assert captured == [expected]

    async def test_grid_provider_prompt_is_rebuilt_from_the_same_live_inputs_as_its_basis(
        self,
        project_with_script,
        grid_json,
    ):
        from lib.grid.layout import grid_aspect_ratio_for
        from lib.grid.prompt_builder import build_grid_prompt
        from lib.grid_manager import GridManager
        from lib.visual_artifact_provenance import GridStoryboardVisual, build_grid_composite_visual_basis
        from server.services.generation_tasks import execute_grid_task

        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        project = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        script["segments"][0]["image_prompt"]["scene"] = "live scene prompt"
        (project_with_script / "scripts" / "episode_1.json").write_text(
            json.dumps(script, ensure_ascii=False),
            encoding="utf-8",
        )
        captured_prompt: list[str] = []
        captured_basis = []

        class _Generator:
            versions = MagicMock()

            async def generate_image_async(self, **kwargs):
                captured_prompt.append(kwargs["prompt"])
                return project_with_script / "grids" / f"{grid_json.id}.png", 1

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.generation_tasks.resolve_generation_context",
                new=_image_ctx(_Generator()),
            ),
            patch(
                "server.services.generation_tasks.register_formal_task_artifact",
                side_effect=lambda *_args, **kwargs: captured_basis.append(kwargs["basis"]),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = project
            mock_pm.load_script.return_value = script
            mock_pm_fn.return_value = mock_pm

            await execute_grid_task(
                "test-project",
                grid_json.id,
                {"prompt": "stale queued prompt", "script_file": "episode_1.json"},
                user_id="test-user",
            )

        scenes_by_id = {scene["segment_id"]: scene for scene in script["segments"]}
        expected = build_grid_prompt(
            scenes=[scenes_by_id[scene_id] for scene_id in grid_json.scene_ids],
            id_field="segment_id",
            rows=2,
            cols=2,
            style="realistic",
            aspect_ratio="9:16",
            grid_aspect_ratio=grid_aspect_ratio_for(2, 2, "9:16"),
        )
        assert captured_prompt == [expected]
        assert GridManager(project_with_script).get(grid_json.id).prompt == expected
        expected_basis = build_grid_composite_visual_basis(
            group_id=grid_json.id,
            members=tuple(
                GridStoryboardVisual(
                    resource_id=scene_id,
                    image_prompt=scenes_by_id[scene_id]["image_prompt"],
                    video_prompt=scenes_by_id[scene_id]["video_prompt"],
                )
                for scene_id in grid_json.scene_ids
            ),
            rows=2,
            columns=2,
            style="realistic",
            grid_aspect_ratio=grid_aspect_ratio_for(2, 2, "9:16"),
        )
        assert captured_basis == [expected_basis]

    async def test_manifest_failure_rejects_selected_grid_before_marking_failed(
        self,
        project_with_script,
        grid_json,
    ):
        from PIL import Image

        from server.services.generation_tasks import execute_grid_task

        grid_image_path = project_with_script / "grids" / f"{grid_json.id}.png"
        Image.new("RGB", (400, 400), color=(128, 200, 100)).save(grid_image_path, format="PNG")
        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock(return_value=(grid_image_path, 2))

        def _reject_before_failure(*_args, **_kwargs):
            current_grid = json.loads(
                (project_with_script / "grids" / f"{grid_json.id}.json").read_text(encoding="utf-8")
            )
            assert current_grid["status"] != "failed"
            return True

        mock_generator.versions.reject_current_version.side_effect = _reject_before_failure

        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.generation_tasks.resolve_generation_context",
                new=_image_ctx(mock_generator),
            ),
            patch(
                "server.services.generation_tasks.register_formal_task_artifact",
                side_effect=RuntimeError("manifest commit failed"),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = json.loads(
                (project_with_script / "project.json").read_text(encoding="utf-8")
            )
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8")
            )
            mock_pm_fn.return_value = mock_pm

            with pytest.raises(RuntimeError, match="manifest commit failed"):
                await execute_grid_task(
                    "test-project",
                    grid_json.id,
                    {"prompt": "test grid prompt", "script_file": "episode_1.json"},
                    user_id="test-user",
                )

        mock_generator.versions.reject_current_version.assert_called_once_with(
            "grids",
            grid_json.id,
            rejected_version=2,
            current_file=grid_image_path,
        )
        updated_grid_data = json.loads(
            (project_with_script / "grids" / f"{grid_json.id}.json").read_text(encoding="utf-8")
        )
        assert updated_grid_data["status"] == "failed"
        assert updated_grid_data["grid_image_path"] is None

    async def test_terminal_cancellation_restores_grid_selection_and_preserves_later_edits(
        self,
        project_with_script,
        grid_json,
    ):
        from lib.generation_queue import CompensableGenerationResult
        from lib.grid_manager import GridManager
        from lib.version_manager import VersionManager
        from server.services.generation_tasks import execute_grid_task

        grid_id = grid_json.id
        current = project_with_script / "grids" / f"{grid_id}.png"
        current.write_bytes(b"old-grid")
        versions = VersionManager(project_with_script)
        old_version = versions.add_version("grids", grid_id, "old", source_file=current)

        class _Generator:
            def __init__(self):
                self.versions = versions

            async def generate_image_async(self, **_kwargs):
                current.write_bytes(b"cancelled-grid")
                selected = self.versions.add_version("grids", grid_id, "new", source_file=current)
                return current, selected

        compensated: list[str] = []

        class _ManifestReceipt:
            def compensate_cancelled(self) -> None:
                compensated.append("manifest")

        generator = _Generator()
        with (
            patch("server.services.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.generation_tasks.resolve_generation_context",
                new=_image_ctx(generator),
            ),
            patch(
                "server.services.generation_tasks.register_formal_task_artifact",
                return_value=_ManifestReceipt(),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = json.loads(
                (project_with_script / "project.json").read_text(encoding="utf-8")
            )
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8")
            )
            mock_pm_fn.return_value = mock_pm

            result = await execute_grid_task(
                "test-project",
                grid_id,
                {"prompt": "test grid prompt", "script_file": "episode_1.json"},
                user_id="test-user",
                task_id="grid-task",
            )

        assert isinstance(result, CompensableGenerationResult)
        GridManager(project_with_script).update(grid_id, lambda grid: setattr(grid, "provider", "later-provider"))

        result.compensate_cancelled()

        restored = GridManager(project_with_script).get(grid_id)
        assert restored is not None
        assert restored.status == "pending"
        assert restored.grid_image_path is None
        assert restored.reference_images is None
        assert restored.provider == "later-provider"
        assert versions.get_current_version("grids", grid_id) == old_version
        assert current.read_bytes() == b"old-grid"
        assert compensated == ["manifest"]

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
            mock_pm.load_project.return_value = json.loads(
                (project_with_script / "project.json").read_text(encoding="utf-8")
            )
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8")
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
            mock_pm.load_project.return_value = json.loads(
                (project_with_script / "project.json").read_text(encoding="utf-8")
            )
            mock_pm.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8")
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

        updated = json.loads((project_with_script / "grids" / f"{grid.id}.json").read_text(encoding="utf-8"))
        assert updated["provider"] == "openai"
        assert updated["model"] == "gpt-image-t2i"

    async def test_uses_i2i_slot_when_reference_images_present(self, project_with_script, grid_with_empty_metadata):
        """有 character sheet 且 segment 引用了角色 → reference_images 非空 → 写 I2I 槽配置"""
        # 给 project + script 注入 character sheet，让 _collect_grid_reference_images 返回非空
        project_data = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        project_data["characters"]["hero"] = {"description": "hero", "character_sheet": "characters/hero.png"}
        (project_with_script / "project.json").write_text(json.dumps(project_data))
        Image.new("RGB", (4, 4)).save(project_with_script / "characters" / "hero.png")

        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        script["segments"][0]["characters_in_segment"] = ["hero"]
        (project_with_script / "scripts" / "episode_1.json").write_text(json.dumps(script))
        _register_sheet(project_with_script, "characters", "hero")

        grid = grid_with_empty_metadata
        payload = {
            "prompt": "test grid prompt",
            "script_file": "episode_1.json",
            "image_provider_t2i": "openai/gpt-image-t2i",
            "image_provider_i2i": "openai/gpt-image-i2i",
        }

        await self._run_grid_task(project_with_script, grid, payload)

        updated = json.loads((project_with_script / "grids" / f"{grid.id}.json").read_text(encoding="utf-8"))
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

        updated = json.loads((project_with_script / "grids" / f"{grid.id}.json").read_text(encoding="utf-8"))
        assert updated["provider"] == "custom-1"
        assert updated["model"] == "m-live"
