"""Tests for grid generation task executor."""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from PIL import Image

from lib.artifacts.artifact_activation import activate_artifact_target_state
from lib.config.resolver import ProviderModel
from lib.project.project_migrations.runner import migrate_project_dir
from server.services.tasks.generation_context import GenerationContext, ImageLaneResult


def _image_ctx(
    generator,
    *,
    provider="openai",
    model="gpt-image-2",
    resolution="2K",
    backend_model=None,
    max_reference_images=None,
):
    """Build fake GenerationContext from resolved image-lane data, replacing resolve_generation_context only.

    backend_model may differ from model, simulating disabled custom-target fallback where actual
    backend identity differs from resolved model_id.
    """
    ctx = GenerationContext(
        generator=generator,
        image_lane=ImageLaneResult(
            provider_model=ProviderModel(provider, model),
            backend_name=provider,
            backend_model=backend_model if backend_model is not None else model,
            resolution=resolution,
            max_reference_images=max_reference_images,
        ),
    )

    async def _resolve(*args, **kwargs):
        return ctx

    return _resolve


class _FormalVersions:
    """Minimal VersionManager: commit staged versions and select only after successful ``on_commit``."""

    def __init__(self, version: int = 1):
        self.version = version
        self.current: int | None = None
        self.committed: list[tuple[str, str]] = []

    def commit_staged_version(self, resource_type, resource_id, prompt, *, on_commit=None, **_kwargs):
        self.committed.append((resource_type, resource_id))
        self.current = self.version
        try:
            if on_commit is not None:
                on_commit()
        except BaseException:
            self.current = None
            raise
        return self.version

    def get_current_version(self, resource_type, resource_id):
        return self.current

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"version": self.version, "created_at": "2026-01-01T00:00:00Z"}]}


async def _commit_formal(kwargs: dict, image_path: Path) -> tuple[Path, int]:
    """Like MediaGenerator, commit through formal_output activation and return callback version numbers.

    Commit callbacks write formal files; MediaGenerator invokes through worker-thread
    run_noninterruptible_sync, so the substitute uses a thread too.
    """
    version = await asyncio.to_thread(kwargs["commit_formal_output"], image_path, image_path, {})
    return image_path, version


def _committing_to(image_path: Path):
    """AsyncMock ``generate_image_async`` side_effect commits to ``image_path`` using production semantics."""

    async def _generate(**kwargs) -> tuple[Path, int]:
        return await _commit_formal(kwargs, image_path)

    return _generate


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
                "style_description": "胶片颗粒，低饱和",
                "generation_mode": "storyboard",
                "grid_storyboard": True,
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
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
                        "generated_assets": {"storyboard_image": None, "video_clip": None, "status": "pending"},
                    }
                    for i in range(1, 7)
                ],
            }
        )
    )
    # Production projects use current schema; scripts and episode-source→script_plan provenance are registered.
    (p / "source" / "episode_1.txt").write_text("原文", encoding="utf-8")
    (p / "drafts" / "episode_1" / "script_plan_segments.json").write_text(
        json.dumps({"episode": 1, "segments": []}), encoding="utf-8"
    )
    activate_artifact_target_state(p, bump_schema=True)
    # Manifest activation advances only its version; migrations bring project schema current before artifact-read admission.
    migrate_project_dir(p)
    return p


def _register_sheet(project_path, resource_type, resource_id):
    """Register persisted sheets in manifests; production rejects unregistered images."""
    from lib.artifacts.artifact_activation import register_current_resource_artifact

    assert register_current_resource_artifact(
        project_path,
        resource_type=resource_type,
        resource_id=resource_id,
    )


def _seed_one_hero_per_scene(project_path, grid_json):
    """One registered character sheet per cell (hero1..heroN), identified by @[]; return project/script/persisted-grid path."""
    from PIL import Image

    project = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    script = json.loads((project_path / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
    for index, scene_id in enumerate(grid_json.scene_ids):
        name = f"hero{index + 1}"
        project["characters"][name] = {"description": name, "character_sheet": f"characters/{name}.png"}
        Image.new("RGB", (4, 4)).save(project_path / "characters" / f"{name}.png")
        segment = next(item for item in script["segments"] if item["segment_id"] == scene_id)
        segment["characters_in_segment"] = [name]
        segment["image_prompt"]["scene"] = f"@[{name}]站在门口"
    (project_path / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    (project_path / "scripts" / "episode_1.json").write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    for index in range(len(grid_json.scene_ids)):
        _register_sheet(project_path, "characters", f"hero{index + 1}")
    grid_image_path = project_path / "grids" / f"{grid_json.id}.png"
    Image.new("RGB", (400, 400)).save(grid_image_path, format="PNG")
    return project, script, grid_image_path


class TestGroupBySegmentBreak:
    def test_groups(self, project_with_script):
        from lib.script.storyboard_sequence import group_scenes_by_segment_break

        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        items = script["segments"]
        groups = group_scenes_by_segment_break(items, "segment_id")
        # E1S03 has segment_break=True, so groups: [E1S01,E1S02] and [E1S03,E1S04,E1S05,E1S06]
        assert len(groups) == 2
        assert len(groups[0]) == 2
        assert len(groups[1]) == 4

    def test_no_breaks(self):
        from lib.script.storyboard_sequence import group_scenes_by_segment_break

        items = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        groups = group_scenes_by_segment_break(items, "id")
        assert len(groups) == 1
        assert len(groups[0]) == 3

    def test_empty_list(self):
        from lib.script.storyboard_sequence import group_scenes_by_segment_break

        groups = group_scenes_by_segment_break([], "id")
        assert groups == []

    def test_break_at_first_item(self):
        from lib.script.storyboard_sequence import group_scenes_by_segment_break

        items = [{"id": "a", "segment_break": True}, {"id": "b"}, {"id": "c"}]
        groups = group_scenes_by_segment_break(items, "id")
        # segment_break on first item: current is empty so no split, all in one group
        assert len(groups) == 1
        assert len(groups[0]) == 3


class TestExecuteGridTask:
    @pytest.fixture
    def grid_json(self, project_with_script):
        """Create a grid JSON file."""
        from lib.script.grid.models import GridGeneration

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

        from server.services.tasks.generation_tasks import execute_grid_task

        grid = grid_json

        # Create a fake 400x400 grid image (2x2, each cell 200x200)
        fake_grid_image = Image.new("RGB", (400, 400), color=(128, 200, 100))
        grid_image_path = project_with_script / "grids" / f"{grid.id}.png"
        fake_grid_image.save(grid_image_path, format="PNG")

        mock_generator = MagicMock()
        mock_generator.versions = _FormalVersions()
        mock_generator.generate_image_async = AsyncMock(side_effect=_committing_to(grid_image_path))

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
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
        # created_at comes from selected-version creation time.
        assert result["created_at"] == "2026-01-01T00:00:00Z"
        assert "grids/" in result["file_path"]
        # No reference trimming means no warning key in results.
        assert "warnings" not in result

        # Verify grid status was updated
        import json as json_mod

        updated_grid_data = json_mod.loads(
            (project_with_script / "grids" / f"{grid.id}.json").read_text(encoding="utf-8")
        )
        assert updated_grid_data["status"] == "completed"
        assert updated_grid_data["grid_image_path"] == f"grids/{grid.id}.png"
        # Updating combined-image content resets cell-application state until explicit splitting.
        assert updated_grid_data["split_at"] is None

    @pytest.mark.parametrize("status_before_claim", ["pending", "failed"])
    async def test_provider_changed_since_claim_restores_the_grid_and_skips_the_provider(
        self, project_with_script, grid_json, status_before_claim
    ):
        """Resolved provider differs from claimed slot: no provider call; restore preclaim grid state for task requeue/reclaim."""
        from lib.generation.generation_queue import DispatchProviderChanged
        from server.services.tasks.generation_tasks import execute_grid_task

        record = project_with_script / "grids" / f"{grid_json.id}.json"
        stored = json.loads(record.read_text(encoding="utf-8"))
        stored["status"] = status_before_claim
        stored["error_message"] = "上次失败" if status_before_claim == "failed" else None
        record.write_text(json.dumps(stored, ensure_ascii=False), encoding="utf-8")
        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock()

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
                new=_image_ctx(mock_generator, provider="openai"),
            ),
        ):
            mock_pm_fn.return_value.get_project_path.return_value = project_with_script
            mock_pm_fn.return_value.load_project.return_value = json.loads(
                (project_with_script / "project.json").read_text(encoding="utf-8")
            )
            mock_pm_fn.return_value.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8")
            )

            with pytest.raises(DispatchProviderChanged) as caught:
                await execute_grid_task(
                    "test-project",
                    grid_json.id,
                    {"prompt": "test grid prompt", "script_file": "episode_1.json"},
                    user_id="test-user",
                    claimed_provider_id="gemini-aistudio",
                )

        assert (caught.value.claimed_provider_id, caught.value.actual_provider_id) == ("gemini-aistudio", "openai")
        mock_generator.generate_image_async.assert_not_called()
        restored = json.loads(record.read_text(encoding="utf-8"))
        assert (restored["status"], restored["error_message"]) == (stored["status"], stored["error_message"])

    async def test_reference_snapshots_are_cleaned_when_cancelled_while_freezing(self, project_with_script, grid_json):
        """Cancellation during worker-thread reference freezing still cleans snapshots after freezing, without provider calls."""
        import threading

        from server.services.tasks.generation_tasks import execute_grid_task

        freezing = threading.Event()
        release = threading.Event()
        cleaned: list[bool] = []

        class _Frozen:
            def cleanup(self) -> None:
                cleaned.append(True)

        def _blocking_freeze(*_args, **_kwargs):
            freezing.set()
            release.wait(5)
            return _Frozen()

        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock()

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
                new=_image_ctx(mock_generator),
            ),
            patch("server.services.tasks.generation_tasks.freeze_image_references", new=_blocking_freeze),
        ):
            mock_pm_fn.return_value.get_project_path.return_value = project_with_script
            mock_pm_fn.return_value.load_project.return_value = json.loads(
                (project_with_script / "project.json").read_text(encoding="utf-8")
            )
            mock_pm_fn.return_value.load_script.return_value = json.loads(
                (project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8")
            )

            task = asyncio.create_task(
                execute_grid_task(
                    "test-project",
                    grid_json.id,
                    {"prompt": "test grid prompt", "script_file": "episode_1.json"},
                    user_id="test-user",
                )
            )
            assert await asyncio.to_thread(freezing.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
            release.set()

            with pytest.raises(asyncio.CancelledError):
                await task

        assert cleaned == [True]
        mock_generator.generate_image_async.assert_not_called()

    @staticmethod
    def _complete(project_with_script, grid, *, registered: bool):
        from PIL import Image

        grid.status = "completed"
        grid.grid_image_path = f"grids/{grid.id}.png"
        Image.new("RGB", (400, 400)).save(project_with_script / "grids" / f"{grid.id}.png")
        record = project_with_script / "grids" / f"{grid.id}.json"
        record.write_text(json.dumps(grid.to_dict(), ensure_ascii=False), encoding="utf-8")
        if registered:
            _register_sheet(project_with_script, "grids", grid.id)
        return record

    async def _run_duplicate(self, project_with_script, grid):
        from server.services.tasks.generation_tasks import execute_grid_task

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
                side_effect=AssertionError("不该再次解析供应商出图"),
            ),
        ):
            mock_pm_fn.return_value.get_project_path.return_value = project_with_script
            mock_pm_fn.return_value.load_project.return_value = json.loads(
                (project_with_script / "project.json").read_text(encoding="utf-8")
            )
            return await execute_grid_task(
                "test-project",
                grid.id,
                {"prompt": "test grid prompt", "script_file": "episode_1.json"},
                user_id="test-user",
            )

    async def test_a_task_enqueued_after_the_grid_completed_does_not_generate_again(
        self, project_with_script, grid_json
    ):
        """Reused in-flight grids racing with prior completion are already completed and must not regenerate."""
        record = self._complete(project_with_script, grid_json, registered=True)
        before = record.read_bytes()

        result = await self._run_duplicate(project_with_script, grid_json)

        assert result == {
            "file_path": f"grids/{grid_json.id}.png",
            "resource_type": "grids",
            "resource_id": grid_json.id,
        }
        assert record.read_bytes() == before

    async def test_a_duplicate_task_on_an_unusable_composite_fails_instead_of_reporting_success(
        self, project_with_script, grid_json
    ):
        """Unregistered combined images neither report success nor regenerate."""
        record = self._complete(project_with_script, grid_json, registered=False)
        before = record.read_bytes()

        with pytest.raises(ValueError, match="not usable"):
            await self._run_duplicate(project_with_script, grid_json)

        assert record.read_bytes() == before

    async def test_reference_images_are_clamped_to_the_backend_limit_before_numbering(
        self,
        project_with_script,
        grid_json,
    ):
        """Trim reference tails before numbering to backend limits; declarations/cell “图N” identify only sent images."""
        from server.services.tasks.generation_tasks import execute_grid_task

        project, script, grid_image_path = _seed_one_hero_per_scene(project_with_script, grid_json)
        captured: list[dict] = []

        class _Generator:
            versions = _FormalVersions()

            async def generate_image_async(self, **kwargs):
                captured.append(kwargs)
                return await _commit_formal(kwargs, grid_image_path)

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
                new=_image_ctx(_Generator(), max_reference_images=2),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = project
            mock_pm.load_script.return_value = script
            mock_pm.update_scene_asset.return_value = {}
            mock_pm_fn.return_value = mock_pm

            result = await execute_grid_task(
                "test-project",
                grid_json.id,
                {"prompt": "queued prompt", "script_file": "episode_1.json"},
                user_id="test-user",
            )

        assert len(captured[0]["reference_images"]) == 2
        # Users/Agents must see discarded references through the same warning shape as storyboard/reference-video results.
        assert result["warnings"] == [
            {"key": "ref_too_many_images", "params": {"count": 3, "model": "gpt-image-2", "max_count": 2}}
        ]
        prompt = captured[0]["prompt"]
        assert prompt.startswith("Reference_Images: 图1, 图2: character reference images.")
        assert "图1站在门口" in prompt
        assert "图2站在门口" in prompt
        # Image 3 was not sent; body retains only its bare name, never a nonexistent 图3 reference.
        assert "图3" not in prompt
        assert "hero3站在门口" in prompt
        # Grid records retain complete assembly sets for target-state provenance rebuilding; provider limits stay out of records.
        stored = json.loads((project_with_script / "grids" / f"{grid_json.id}.json").read_text(encoding="utf-8"))
        assert [ref["name"] for ref in stored["reference_images"]] == ["hero1", "hero2", "hero3"]

    async def test_every_member_sheet_is_sent_beyond_six_when_the_backend_allows(
        self,
        project_with_script,
        grid_json,
    ):
        """Grids have no fixed image count: union member assets, deduplicate by path, send/register all."""
        from server.services.tasks.generation_tasks import execute_grid_task

        project = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        names = [f"hero{index}" for index in range(1, 9)]
        for name in names:
            project["characters"][name] = {"description": name, "character_sheet": f"characters/{name}.png"}
            Image.new("RGB", (4, 4)).save(project_with_script / "characters" / f"{name}.png")
        segments = {item["segment_id"]: item for item in script["segments"]}
        segments["E1S01"]["characters_in_segment"] = names[:4]
        segments["E1S02"]["characters_in_segment"] = names[4:]
        segments["E1S03"]["characters_in_segment"] = ["hero1", "hero8"]
        (project_with_script / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        (project_with_script / "scripts" / "episode_1.json").write_text(
            json.dumps(script, ensure_ascii=False), encoding="utf-8"
        )
        for name in names:
            _register_sheet(project_with_script, "characters", name)
        grid_image_path = project_with_script / "grids" / f"{grid_json.id}.png"
        Image.new("RGB", (400, 400)).save(grid_image_path, format="PNG")
        captured: list[dict] = []

        class _Generator:
            versions = _FormalVersions()

            async def generate_image_async(self, **kwargs):
                captured.append(kwargs)
                return await _commit_formal(kwargs, grid_image_path)

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
                new=_image_ctx(_Generator(), max_reference_images=14),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = project
            mock_pm.load_script.return_value = script
            mock_pm_fn.return_value = mock_pm

            result = await execute_grid_task(
                "test-project",
                grid_json.id,
                {"prompt": "queued prompt", "script_file": "episode_1.json"},
                user_id="test-user",
            )

        assert len(captured[0]["reference_images"]) == 8
        assert "warnings" not in result
        stored = json.loads((project_with_script / "grids" / f"{grid_json.id}.json").read_text(encoding="utf-8"))
        assert stored["reference_images"] == [
            {"path": f"characters/{name}.png", "name": name, "ref_type": "character"} for name in names
        ]

    async def test_reference_gaps_fail_before_the_provider_is_resolved(self, project_with_script, grid_json):
        """Unregistered assets/unavailable sheets fail before payment, listing all gaps together."""
        from lib.infra.api_errors import BadRequestError
        from server.services.tasks.generation_tasks import execute_grid_task

        project = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        # hero sheet exists on disk but is unregistered; ghost is not a registered character.
        project["characters"]["hero"] = {"description": "hero", "character_sheet": "characters/hero.png"}
        Image.new("RGB", (4, 4)).save(project_with_script / "characters" / "hero.png")
        segments = {item["segment_id"]: item for item in script["segments"]}
        segments["E1S01"]["characters_in_segment"] = ["hero"]
        segments["E1S03"]["characters_in_segment"] = ["ghost", "hero"]
        (project_with_script / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        (project_with_script / "scripts" / "episode_1.json").write_text(
            json.dumps(script, ensure_ascii=False), encoding="utf-8"
        )
        resolve = AsyncMock()

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch("server.services.tasks.generation_tasks.resolve_generation_context", new=resolve),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = project
            mock_pm.load_script.return_value = script
            mock_pm_fn.return_value = mock_pm

            with pytest.raises(BadRequestError) as refused:
                await execute_grid_task(
                    "test-project",
                    grid_json.id,
                    {"prompt": "queued prompt", "script_file": "episode_1.json"},
                    user_id="test-user",
                )

        resolve.assert_not_called()
        assert refused.value.key == "reference_asset_missing"
        assert refused.value.params["gaps"] == [
            {"code": "reference_asset_missing", "asset_type": "character", "name": "hero"},
            {"code": "reference_asset_unregistered", "asset_type": "character", "name": "ghost"},
        ]
        stored = json.loads((project_with_script / "grids" / f"{grid_json.id}.json").read_text(encoding="utf-8"))
        assert stored["status"] == "failed"

    async def test_a_dropped_reference_changing_before_submit_still_aborts(
        self,
        project_with_script,
        grid_json,
        monkeypatch,
    ):
        """Trimmed third images still affect grid provenance; pre-submit registration deletion blocks submission before provider calls."""
        from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
        from server.services.tasks.generation_tasks import execute_grid_task
        from tests.fakes import hook_claim_recheck

        project, script, grid_image_path = _seed_one_hero_per_scene(project_with_script, grid_json)
        captured: list[dict] = []

        class _Generator:
            versions = _FormalVersions()

            async def generate_image_async(self, **kwargs):
                captured.append(kwargs)
                return await _commit_formal(kwargs, grid_image_path)

        hook_claim_recheck(
            monkeypatch,
            before=lambda: ProjectArtifactManifestAdapter(project_with_script).delete_entry(
                ArtifactKey.asset_sheet("character", "hero3")
            ),
        )

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
                new=_image_ctx(_Generator(), max_reference_images=2),
            ),
        ):
            mock_pm = MagicMock()
            mock_pm.get_project_path.return_value = project_with_script
            mock_pm.load_project.return_value = project
            mock_pm.load_script.return_value = script
            mock_pm.update_scene_asset.return_value = {}
            mock_pm_fn.return_value = mock_pm

            with pytest.raises(ValueError, match="no longer registered"):
                await execute_grid_task(
                    "test-project",
                    grid_json.id,
                    {"prompt": "queued prompt", "script_file": "episode_1.json"},
                    user_id="test-user",
                )

        assert captured == []

    async def test_grid_rejects_an_unclaimed_bound_script_before_provider(
        self,
        project_with_script,
        grid_json,
    ):
        """Scripts bound in episodes without manifest claims reject before reaching providers."""
        from lib.artifacts.artifact_manifest import ArtifactKey, ProjectArtifactManifestAdapter
        from server.services.tasks.generation_tasks import execute_grid_task

        assert ProjectArtifactManifestAdapter(project_with_script).delete_entry(ArtifactKey.episode_script(1))
        project = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))

        mock_generator = MagicMock()
        mock_generator.generate_image_async = AsyncMock(side_effect=AssertionError("provider must remain unreachable"))

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
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
        from lib.artifacts.visual_artifact_provenance import GridStoryboardVisual, build_grid_composite_visual_basis
        from lib.script.grid.layout import grid_aspect_ratio_for
        from server.services.tasks.generation_tasks import execute_grid_task

        script = json.loads((project_with_script / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        project = json.loads((project_with_script / "project.json").read_text(encoding="utf-8"))
        captured = []

        class _Generator:
            versions = _FormalVersions()

            async def generate_image_async(self, **kwargs):
                script["segments"][0]["image_prompt"] = "latest prompt"
                return await _commit_formal(kwargs, project_with_script / "grids" / f"{grid_json.id}.png")

        def _register(*_args, **kwargs):
            captured.append(kwargs["basis"])
            return

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
                new=_image_ctx(_Generator()),
            ),
            patch("server.services.tasks.formal_image_commit.register_formal_task_artifact", side_effect=_register),
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
            style_description="胶片颗粒，低饱和",
            grid_aspect_ratio=grid_aspect_ratio_for(2, 2, "9:16"),
        )
        assert captured == [expected]

    async def test_grid_provider_prompt_is_rebuilt_from_the_same_live_inputs_as_its_basis(
        self,
        project_with_script,
        grid_json,
    ):
        from lib.artifacts.visual_artifact_provenance import GridStoryboardVisual, build_grid_composite_visual_basis
        from lib.script.grid.grid_manager import GridManager
        from lib.script.grid.layout import grid_aspect_ratio_for
        from lib.script.grid.prompt_builder import build_grid_prompt
        from server.services.tasks.generation_tasks import execute_grid_task

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
            versions = _FormalVersions()

            async def generate_image_async(self, **kwargs):
                captured_prompt.append(kwargs["prompt"])
                return await _commit_formal(kwargs, project_with_script / "grids" / f"{grid_json.id}.png")

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
                new=_image_ctx(_Generator()),
            ),
            patch(
                "server.services.tasks.formal_image_commit.register_formal_task_artifact",
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
            style_description="胶片颗粒，低饱和",
            aspect_ratio="9:16",
            grid_aspect_ratio=grid_aspect_ratio_for(2, 2, "9:16"),
        )
        assert "Visual style: 胶片颗粒，低饱和" in expected.splitlines()
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
            style_description="胶片颗粒，低饱和",
            grid_aspect_ratio=grid_aspect_ratio_for(2, 2, "9:16"),
        )
        assert captured_basis == [expected_basis]

    async def test_manifest_failure_keeps_grid_unselected_and_marks_it_failed(
        self,
        project_with_script,
        grid_json,
    ):
        from PIL import Image

        from server.services.tasks.generation_tasks import execute_grid_task

        grid_image_path = project_with_script / "grids" / f"{grid_json.id}.png"
        Image.new("RGB", (400, 400), color=(128, 200, 100)).save(grid_image_path, format="PNG")
        mock_generator = MagicMock()
        mock_generator.versions = _FormalVersions(version=2)
        mock_generator.generate_image_async = AsyncMock(side_effect=_committing_to(grid_image_path))

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
                new=_image_ctx(mock_generator),
            ),
            patch(
                "server.services.tasks.formal_image_commit.register_formal_task_artifact",
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

        assert mock_generator.versions.get_current_version("grids", grid_json.id) is None
        updated_grid_data = json.loads(
            (project_with_script / "grids" / f"{grid_json.id}.json").read_text(encoding="utf-8")
        )
        assert updated_grid_data["status"] == "failed"
        assert updated_grid_data["grid_image_path"] is None

    async def test_execute_grid_task_does_not_touch_storyboards(self, project_with_script, grid_json):
        """Generation produces only combined images: no cell files, script writes, or storyboard versions;
        independent apply_grid_split explicitly applies cells."""
        from PIL import Image

        from server.services.tasks.generation_tasks import execute_grid_task

        grid = grid_json

        # Precreate one storyboard cell to lock unchanged bytes after generation.
        storyboards_dir = project_with_script / "storyboards"
        existing = storyboards_dir / "scene_E1S01.png"
        existing.write_bytes(b"pre-existing-bytes")

        fake_grid_image = Image.new("RGB", (400, 400), color=(0, 0, 0))
        grid_image_path = project_with_script / "grids" / f"{grid.id}.png"
        fake_grid_image.save(grid_image_path, format="PNG")

        mock_generator = MagicMock()
        mock_generator.versions = _FormalVersions()
        mock_generator.generate_image_async = AsyncMock(side_effect=_committing_to(grid_image_path))

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch(
                "server.services.tasks.generation_tasks.resolve_generation_context",
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

        # Existing cell bytes stay unchanged; absent cells are not created.
        assert existing.read_bytes() == b"pre-existing-bytes"
        for sid in ("E1S02", "E1S03"):
            assert not (storyboards_dir / f"scene_{sid}.png").exists()
        # No script writes or storyboard-version registration.
        assert not mock_pm.batch_update_scene_assets.called
        assert mock_generator.versions.committed == [("grids", grid.id)]

    async def test_execute_grid_task_not_found(self):
        from server.services.tasks.generation_tasks import execute_grid_task

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
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
        from server.services.tasks.generation_tasks import _TASK_EXECUTORS, execute_grid_task

        assert "grid" in _TASK_EXECUTORS
        assert _TASK_EXECUTORS["grid"] is execute_grid_task


class TestGridMetadataT2II2ISlotSelection:
    """Bug 2 regression: execute_grid_task selects T2I/I2I slots by nonempty reference_images."""

    @pytest.fixture
    def grid_with_empty_metadata(self, project_with_script):
        """Simulate fixed routes: grid creation leaves provider/model empty for task-layer backfill."""
        from lib.script.grid.models import GridGeneration

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
        """Mock generator/project manager and run execute_grid_task."""
        from PIL import Image

        from server.services.tasks.generation_tasks import execute_grid_task

        fake_grid_image = Image.new("RGB", (400, 400), color=(128, 128, 128))
        grid_image_path = project_with_script / "grids" / f"{grid.id}.png"
        fake_grid_image.save(grid_image_path, format="PNG")

        mock_generator = MagicMock()
        mock_generator.versions = _FormalVersions()
        mock_generator.generate_image_async = AsyncMock(side_effect=_committing_to(grid_image_path))

        async def _cap_aware_resolve(project_name, req_payload, *, image, **kwargs):
            # Generation-type-aware: nonempty reference_images selects i2i, otherwise t2i;
            # fake resolution echoes provider/model from that payload slot, locking selection→metadata backfill.
            provider, model = req_payload[f"image_provider_{image.generation_type}"].split("/")
            return GenerationContext(
                generator=mock_generator,
                image_lane=ImageLaneResult(
                    provider_model=ProviderModel(provider, model),
                    backend_name=provider,
                    backend_model=model,
                    resolution="2K",
                    max_reference_images=None,
                ),
            )

        fake_resolve = resolve_override(mock_generator) if resolve_override is not None else _cap_aware_resolve

        with (
            patch("server.services.tasks.generation_tasks.get_project_manager") as mock_pm_fn,
            patch("server.services.tasks.generation_tasks.resolve_generation_context", new=fake_resolve),
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
        """No character/scene/prop sheets → empty reference_images → T2I slot configuration."""
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
        """Referenced character sheet → nonempty reference_images → I2I slot configuration."""
        # Inject registered character sheets into project/script, making grid references nonempty.
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
        """Disabled custom-target fallback makes actual backend differ from resolved model_id: grid provider
        metadata uses registry identity, model metadata uses the actual invoked backend model."""
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
