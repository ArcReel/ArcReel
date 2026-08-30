"""Tests for formal_image_finalization."""

import asyncio
import json
import threading

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
)
from lib.generation_queue import CompensableGenerationResult
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.services import generation_tasks
from tests.integration.server.services.generation_tasks_support import (
    FakeGenerator,
    _fake_resolve_ctx,
    _FakePM,
    _persist_active_fake_project,
    _prepare_files,
    _register_asset_sheet_claims,
    _register_stale_visual_claim,
)


class TestGenerationTasks:
    async def test_storyboard_registers_manifest_only_after_finalization_succeeds(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        registered: list[tuple[tuple[object, ...], dict[str, object]]] = []

        class _BrokenVersionLookup(FakeGenerator):
            def get_versions(self, resource_type, resource_id):
                raise RuntimeError("injected finalization failure")

        fake_generator = _BrokenVersionLookup()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(
            generation_tasks,
            "register_current_resource_artifact",
            lambda *args, **kwargs: registered.append((args, kwargs)),
        )

        with pytest.raises(RuntimeError, match="injected finalization failure"):
            await generation_tasks.execute_storyboard_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
            )

        assert registered == []

    async def test_storyboard_registers_manifest_after_successful_formal_commit(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        registered: list[tuple[tuple[object, ...], dict[str, object]]] = []
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(
            generation_tasks,
            "register_current_resource_artifact",
            lambda *args, **kwargs: registered.append((args, kwargs)),
        )

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": "direct prompt"},
        )

        assert len(registered) == 1
        args, kwargs = registered[0]
        assert args == (project_path,)
        assert kwargs["resource_type"] == "storyboards"
        assert kwargs["resource_id"] == "E1S01"
        assert kwargs["script_file"] == "episode_1.json"
        assert kwargs["artifact_path"] == "storyboards/scene_E1S01.png"
        assert isinstance(kwargs["basis"], ArtifactBasis)

    async def test_schema8_storyboard_excludes_unclaimed_formal_references(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        _persist_active_fake_project(fake_pm)
        fake_generator = FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", lambda *_args, **_kwargs: True)

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S02",
            {"script_file": "episode_1.json", "prompt": "direct prompt"},
        )

        assert fake_generator.image_calls[0]["reference_images"] is None

    async def test_schema8_storyboard_rejects_an_unclaimed_bound_script_before_provider(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path, register_script=False)
        _persist_active_fake_project(fake_pm, register_script=False)
        fake_generator = FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="episode script is not registered"):
            await generation_tasks.execute_storyboard_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
            )

        assert fake_generator.image_calls == []

    async def test_schema8_video_rejects_an_unclaimed_bound_script_before_provider(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path, register_script=False)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        _persist_active_fake_project(fake_pm, register_script=False)
        _register_stale_visual_claim(
            project_path,
            ArtifactKey.episode_storyboard(1, "E1S01"),
            "storyboards/scene_E1S01.png",
        )
        fake_generator = FakeGenerator()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        with pytest.raises(ValueError, match="episode script is not registered"):
            await generation_tasks.execute_video_task(
                "demo",
                "E1S01",
                {
                    "script_file": "episode_1.json",
                    "prompt": {"action": "跑", "camera_motion": "Static", "dialogue": []},
                },
            )

        assert fake_generator.video_calls == []

    def test_grid_completion_serializes_manifest_registration_with_schema_activation(self, tmp_path):
        from lib.artifact_activation import activate_artifact_target_state
        from lib.formal_write import project_metadata_lock
        from lib.grid.models import GridGeneration
        from lib.grid_manager import GridManager
        from lib.project_migrations.runner import migrate_project_dir
        from lib.version_manager import VersionManager

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project.update(
            {
                "schema_version": 7,
                "generation_mode": "storyboard",
                "aspect_ratio": "9:16",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            }
        )
        fake_pm.script["episode"] = 1
        (project_path / "scripts").mkdir(exist_ok=True)
        (project_path / "project.json").write_text(
            json.dumps(fake_pm.project, ensure_ascii=False),
            encoding="utf-8",
        )
        (project_path / "scripts" / "episode_1.json").write_text(
            json.dumps(fake_pm.script, ensure_ascii=False),
            encoding="utf-8",
        )
        grid = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["E1S01"],
            rows=1,
            cols=1,
            grid_size="grid_1",
            provider="test",
            model="test",
            video_aspect_ratio="9:16",
        )
        grid.status = "generating"
        manager = GridManager(project_path)
        manager.save(grid)
        staged = project_path / "grids" / f".{grid.id}.staged.png"
        staged.write_bytes(b"paid-grid")
        current = manager.image_path(grid.id)
        basis = ArtifactBasis.build("test/grid", kind_version=1, inputs={"grid": grid.id})
        outcomes = []
        commit = generation_tasks._grid_formal_image_callback(
            project_path=project_path,
            grid_manager=manager,
            grid=grid,
            initial_grid=grid.to_dict(),
            resource_id=grid.id,
            prompt="grid",
            versions=VersionManager(project_path),
            task_id=None,
            basis=basis,
            outcome_box=outcomes,
        )

        activation_started = threading.Event()
        activation_done = threading.Event()
        writer_started = threading.Event()
        writer_done = threading.Event()
        failures: list[BaseException] = []

        def _activate() -> None:
            activation_started.set()
            try:
                activate_artifact_target_state(project_path, bump_schema=True)
            except Exception as exc:
                failures.append(exc)
            finally:
                activation_done.set()

        def _complete_grid() -> None:
            writer_started.set()
            try:
                commit(staged, current, {})
            except Exception as exc:
                failures.append(exc)
            finally:
                writer_done.set()

        # 互斥判据由「主线程持锁时对方进不来」双向给出：清单激活与形式产物提交各自在项目
        # 元数据锁上排队，因而彼此也不可能交错。两段等待窗口都是单向否证——互斥若被破坏，
        # 慢机上也可能因窗口内尚未跑完而漏报，但绝不会把守规矩的实现判成失败。
        activation_thread = threading.Thread(target=_activate)
        with project_metadata_lock(project_path):
            activation_thread.start()
            assert activation_started.wait(timeout=5)
            assert not activation_done.wait(timeout=0.2), "清单激活没有在项目元数据锁上排队"
        activation_thread.join(timeout=5)

        # 清单激活只把项目送到 v8；形式产物注册要求当前 schema，剩余迁移由启动扫描跑完。
        migrate_project_dir(project_path)

        writer_thread = threading.Thread(target=_complete_grid)
        with project_metadata_lock(project_path):
            writer_thread.start()
            assert writer_started.wait(timeout=5)
            assert not writer_done.wait(timeout=0.2), "形式产物提交没有在项目元数据锁上排队"
        writer_thread.join(timeout=5)

        assert not activation_thread.is_alive()
        assert not writer_thread.is_alive()
        assert failures == []
        assert (
            json.loads((project_path / "project.json").read_text(encoding="utf-8"))["schema_version"]
            == CURRENT_PROJECT_SCHEMA_VERSION
        )
        entry = ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_grid(1, grid.id))
        assert entry is not None
        assert entry.basis_digest == basis.digest

    async def test_storyboard_rechecks_selected_manifest_claims_before_provider(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][2]["scenes"] = []
        fake_pm.script["segments"][2]["props"] = []
        _persist_active_fake_project(fake_pm)
        key = ArtifactKey.asset_sheet("character", "Alice")
        _register_stale_visual_claim(project_path, key, "characters/Alice.png")
        fake_generator = FakeGenerator()
        resolve_context = _fake_resolve_ctx(fake_generator)

        async def _delete_claim_then_resolve(*args, **kwargs):
            ProjectArtifactManifestAdapter(project_path).delete_entry(key)
            return await resolve_context(*args, **kwargs)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _delete_claim_then_resolve)

        with pytest.raises(ValueError, match="no longer registered"):
            await generation_tasks.execute_storyboard_task(
                "demo",
                "E1S03",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
            )

        assert fake_generator.image_calls == []

    async def test_storyboard_rejects_same_basis_bytes_replaced_before_provider(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][2]["scenes"] = []
        fake_pm.script["segments"][2]["props"] = []
        _persist_active_fake_project(fake_pm)
        key = ArtifactKey.asset_sheet("character", "Alice")
        artifact_path = "characters/Alice.png"
        _register_stale_visual_claim(project_path, key, artifact_path)
        fake_generator = FakeGenerator()
        resolve_context = _fake_resolve_ctx(fake_generator)

        async def _replace_bytes_then_resolve(*args, **kwargs):
            (project_path / artifact_path).write_bytes(b"replacement")
            return await resolve_context(*args, **kwargs)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _replace_bytes_then_resolve)

        with pytest.raises(ValueError, match="changed since it was selected"):
            await generation_tasks.execute_storyboard_task(
                "demo",
                "E1S03",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
            )

        assert fake_generator.image_calls == []

    async def test_legacy_storyboard_rejects_sheet_replaced_after_reference_freeze(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        _register_asset_sheet_claims(fake_pm)
        provider_submissions: list[str] = []

        class _SubmittingGenerator(FakeGenerator):
            async def generate_image_async(self, **kwargs):
                await kwargs["before_submit"]()
                provider_submissions.append("submitted")
                return await super().generate_image_async(**kwargs)

        fake_generator = _SubmittingGenerator(project_path)
        resolve_context = _fake_resolve_ctx(fake_generator)
        character_path = project_path / "characters" / "Alice.png"

        async def _replace_sheet_then_resolve(*args, **kwargs):
            character_path.write_bytes(b"replacement")
            return await resolve_context(*args, **kwargs)

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _replace_sheet_then_resolve)

        with pytest.raises(ValueError, match="changed since it was selected"):
            await generation_tasks.execute_storyboard_task(
                "demo",
                "E1S02",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
            )

        assert provider_submissions == []
        assert fake_generator.image_calls == []

    async def test_storyboard_provider_reads_the_same_reference_bytes_as_its_basis(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.visual_artifact_provenance import VisualReference, build_storyboard_image_visual_basis

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.script["segments"][0]["generated_assets"] = {"storyboard_image": "storyboards/scene_E1S01.png"}
        fake_pm.script["segments"][1]["scenes"] = []
        fake_pm.script["segments"][1]["props"] = []
        _persist_active_fake_project(fake_pm)
        character_path = project_path / "characters" / "Alice.png"
        previous_path = project_path / "storyboards" / "scene_E1S01.png"
        character_path.write_bytes(b"selected-character")
        previous_path.write_bytes(b"selected-previous")
        _register_stale_visual_claim(
            project_path,
            ArtifactKey.asset_sheet("character", "Alice"),
            "characters/Alice.png",
        )
        _register_stale_visual_claim(
            project_path,
            ArtifactKey.episode_storyboard(1, "E1S01"),
            "storyboards/scene_E1S01.png",
        )
        expected_basis = build_storyboard_image_visual_basis(
            resource_id="E1S02",
            image_prompt=fake_pm.script["segments"][1]["image_prompt"],
            style="Anime",
            style_description="cinematic",
            aspect_ratio="9:16",
            references=(
                VisualReference(
                    path=character_path,
                    role="asset_sheet",
                    logical_type="character",
                    logical_id="Alice",
                    kind="sheet",
                ),
                VisualReference(
                    path=previous_path,
                    role="previous_storyboard",
                    logical_type="storyboard",
                    logical_id="E1S01",
                ),
            ),
        )
        captured_basis: list[ArtifactBasis] = []

        class _RacingGenerator(FakeGenerator):
            def __init__(self):
                super().__init__()
                self.reference_bytes: list[bytes] = []

            async def generate_image_async(self, **kwargs):
                character_path.write_bytes(b"changed-character")
                previous_path.write_bytes(b"changed-previous")
                refs = kwargs["reference_images"]
                self.reference_bytes = [(ref["image"] if isinstance(ref, dict) else ref).read_bytes() for ref in refs]
                return await super().generate_image_async(**kwargs)

        fake_generator = _RacingGenerator()
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        def _register(*_args, **kwargs):
            captured_basis.append(kwargs["basis"])
            return True

        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", _register)

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S02",
            {"script_file": "episode_1.json", "prompt": "direct prompt"},
        )

        assert fake_generator.reference_bytes == [b"selected-character", b"selected-previous"]
        assert captured_basis == [expected_basis]

    async def test_formal_image_commit_requires_the_exact_selected_version_record(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        current = project_path / "characters" / "Alice.png"
        staged = project_path / "characters" / ".Alice.stage.png"

        class _IncompleteVersions:
            def __init__(self):
                self.versions = self

            async def generate_image_async(self, **kwargs):
                staged.write_bytes(b"generated")
                version = kwargs["commit_formal_output"](staged, current, {})
                return current, version

            def commit_staged_version(self, **kwargs):
                kwargs["current_file"].write_bytes(kwargs["staged_file"].read_bytes())
                kwargs["on_commit"]()
                return 2

            def get_current_version(self, resource_type, resource_id):
                return 2

            def get_versions(self, resource_type, resource_id):
                return {"versions": [{"version": 1, "created_at": "2026-01-01T00:00:00Z"}]}

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(
            generation_tasks,
            "resolve_generation_context",
            _fake_resolve_ctx(_IncompleteVersions()),
        )
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", lambda *_args, **_kwargs: True)

        with pytest.raises(RuntimeError, match="creation timestamp"):
            await generation_tasks.execute_character_task(
                "demo",
                "Alice",
                {"prompt": "hero"},
            )

    async def test_storyboard_registers_generation_frozen_basis_when_script_changes_in_flight(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.visual_artifact_provenance import build_storyboard_image_visual_basis

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        captured: list[ArtifactBasis] = []
        original_generate = fake_generator.generate_image_async

        async def _generate(**kwargs):
            result = await original_generate(**kwargs)
            fake_pm.script["segments"][0]["image_prompt"] = "latest persisted prompt"
            return result

        fake_generator.generate_image_async = _generate
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        class _Receipt:
            def compensate_cancelled(self) -> None:
                pass

        def _register(*_args, **kwargs):
            captured.append(kwargs["basis"])
            return _Receipt()

        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", _register)

        await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S01",
            {
                "script_file": "episode_1.json",
                "prompt": "queued prompt",
            },
            task_id="storyboard-task",
        )

        expected = build_storyboard_image_visual_basis(
            resource_id="E1S01",
            image_prompt="首镜头",
            style="Anime",
            style_description="cinematic",
            aspect_ratio="9:16",
            references=(),
        )
        latest = build_storyboard_image_visual_basis(
            resource_id="E1S01",
            image_prompt="latest persisted prompt",
            style="Anime",
            style_description="cinematic",
            aspect_ratio="9:16",
            references=(),
        )
        assert captured == [expected]
        assert captured[0].digest != latest.digest
        assert fake_generator.image_calls[0]["prompt"].startswith("Style: Anime\nVisual style: cinematic")
        assert "首镜头" in fake_generator.image_calls[0]["prompt"]
        assert "queued prompt" not in fake_generator.image_calls[0]["prompt"]

    async def test_asset_sheet_registers_generation_frozen_basis_when_definition_changes_in_flight(
        self,
        tmp_path,
        monkeypatch,
    ):
        from lib.visual_artifact_provenance import VisualReference, build_asset_sheet_visual_basis

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        captured: list[ArtifactBasis] = []
        original_generate = fake_generator.generate_image_async

        async def _generate(**kwargs):
            result = await original_generate(**kwargs)
            fake_pm.project["characters"]["Alice"]["description"] = "latest definition"
            return result

        fake_generator.generate_image_async = _generate
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))

        class _Receipt:
            def compensate_cancelled(self) -> None:
                pass

        def _register(*_args, **kwargs):
            captured.append(kwargs["basis"])
            return _Receipt()

        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", _register)

        await generation_tasks.execute_character_task(
            "demo",
            "Alice",
            {"prompt": "queued definition"},
            task_id="character-task",
        )

        references = (
            VisualReference(
                path=project_path / "characters" / "refs" / "Alice-ref.png",
                role="source",
                logical_type="character",
                logical_id="Alice",
                kind="original",
            ),
        )
        expected = build_asset_sheet_visual_basis(
            asset_type="character",
            asset_id="Alice",
            description="queued definition",
            style="Anime",
            style_description="cinematic",
            aspect_ratio="16:9",
            references=references,
        )
        latest = build_asset_sheet_visual_basis(
            asset_type="character",
            asset_id="Alice",
            description="latest definition",
            style="Anime",
            style_description="cinematic",
            aspect_ratio="16:9",
            references=references,
        )
        assert captured == [expected]
        assert captured[0].digest != latest.digest

    async def test_formal_image_version_records_complete_frozen_basis_evidence(self, tmp_path, monkeypatch):
        from lib.media_generator import task_image_staging_path
        from lib.project_manager import ProjectManager
        from lib.version_manager import VersionManager
        from lib.visual_artifact_provenance import build_asset_sheet_visual_basis

        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "queued definition")
        project_path = pm.get_project_path("demo")
        current = project_path / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        version_manager = VersionManager(project_path)

        class _Generator:
            versions = version_manager

            async def generate_image_async(self, **kwargs):
                staged = task_image_staging_path(current, kwargs["task_id"])
                staged.write_bytes(b"generated-sheet")
                version = kwargs["commit_formal_output"](
                    staged,
                    current,
                    {"aspect_ratio": "16:9"},
                )
                return current, version

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(_Generator()))
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", lambda *_args, **_kwargs: True)

        result = await generation_tasks.execute_character_task(
            "demo",
            "Alice",
            {"prompt": "queued definition"},
        )

        record = next(
            item
            for item in version_manager.get_versions("characters", "Alice")["versions"]
            if item["version"] == result["version"]
        )
        project = pm.load_project("demo")
        expected = build_asset_sheet_visual_basis(
            asset_type="character",
            asset_id="Alice",
            description="queued definition",
            style=str(project.get("style") or ""),
            style_description=str(project.get("style_description") or ""),
            aspect_ratio="16:9",
        )
        assert ArtifactBasis.from_evidence_dict(record["artifact_image_basis"]) == expected

    async def test_storyboard_cancellation_waits_for_registration_and_returns_compensation(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = FakeGenerator()
        registration_started = threading.Event()
        finish_registration = threading.Event()
        compensated: list[str] = []

        class _Receipt:
            def compensate_cancelled(self) -> None:
                compensated.append("manifest")

        def _register(*_args, **_kwargs):
            registration_started.set()
            assert finish_registration.wait(timeout=5)
            return _Receipt()

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(fake_generator))
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", _register)
        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", _register, raising=False)

        task = asyncio.create_task(
            generation_tasks.execute_storyboard_task(
                "demo",
                "E1S01",
                {"script_file": "episode_1.json", "prompt": "direct prompt"},
                task_id="storyboard-task",
            )
        )
        assert await asyncio.to_thread(registration_started.wait, 5)
        task.cancel()
        finish_registration.set()

        result = await task

        assert isinstance(result, CompensableGenerationResult)
        result.compensate_cancelled()
        assert compensated == ["manifest"]

    async def test_storyboard_cancellation_restores_selected_media_version_and_metadata(self, tmp_path, monkeypatch):
        from lib.project_manager import ProjectManager
        from lib.version_manager import VersionManager

        projects_root = tmp_path / "projects"
        pm = ProjectManager(projects_root)
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_episode("demo", 1, "E1", "scripts/episode_1.json")
        pm.save_script(
            "demo",
            {
                "episode": 1,
                "content_mode": "narration",
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "novel_text": "旁白",
                        "image_prompt": "queued prompt",
                        "generated_assets": {"storyboard_image": "storyboards/old.png", "status": "pending"},
                    }
                ],
            },
            "episode_1.json",
            validate=False,
        )
        project_path = pm.get_project_path("demo")
        current = project_path / "storyboards" / "scene_E1S01.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"old-image")
        version_manager = VersionManager(project_path)
        old_version = version_manager.add_version("storyboards", "E1S01", "old", source_file=current)
        current.write_bytes(b"cancelled-image")
        selected_version = version_manager.add_version("storyboards", "E1S01", "new", source_file=current)

        class _Generator:
            versions = version_manager

            async def generate_image_async(self, **_kwargs):
                return current, selected_version

        compensated: list[str] = []

        class _Receipt:
            def compensate_cancelled(self) -> None:
                compensated.append("manifest")

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(_Generator()))
        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", lambda *_a, **_kw: _Receipt())
        ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
            ArtifactKey.episode_script(1),
            artifact_path="scripts/episode_1.json",
            basis=ArtifactBasis.build("test/episode-script", kind_version=1, inputs={}),
        )

        result = await generation_tasks.execute_storyboard_task(
            "demo",
            "E1S01",
            {"script_file": "episode_1.json", "prompt": "queued prompt"},
            task_id="storyboard-task",
        )
        committed = pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"]
        assert committed["storyboard_image"] == "storyboards/scene_E1S01.png"
        assert version_manager.get_current_version("storyboards", "E1S01") == selected_version

        assert isinstance(result, CompensableGenerationResult)
        result.compensate_cancelled()

        restored = pm.load_script("demo", "episode_1.json")["segments"][0]["generated_assets"]
        assert restored == {"storyboard_image": "storyboards/old.png", "status": "pending"}
        assert version_manager.get_current_version("storyboards", "E1S01") == old_version
        assert current.read_bytes() == b"old-image"
        assert compensated == ["manifest"]

    async def test_asset_sheet_cancellation_uses_the_same_full_selection_compensation(self, tmp_path, monkeypatch):
        from lib.project_manager import ProjectManager
        from lib.version_manager import VersionManager

        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "hero")

        def _set_old_sheet(project):
            project["characters"]["Alice"]["character_sheet"] = "characters/old.png"

        pm.update_project("demo", _set_old_sheet)
        project_path = pm.get_project_path("demo")
        current = project_path / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"old-sheet")
        version_manager = VersionManager(project_path)
        old_version = version_manager.add_version("characters", "Alice", "old", source_file=current)
        current.write_bytes(b"cancelled-sheet")
        selected_version = version_manager.add_version("characters", "Alice", "new", source_file=current)

        class _Generator:
            versions = version_manager

        class _Receipt:
            def compensate_cancelled(self) -> None:
                pass

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "register_task_current_resource_artifact", lambda *_a, **_kw: _Receipt())

        _created_at, receipt = await generation_tasks._finalize_asset_sheet_task(
            asset_type="character",
            project_name="demo",
            resource_id="Alice",
            sheet_path="characters/Alice.png",
            generator=_Generator(),
            version=selected_version,
            task_id="character-task",
        )
        assert receipt is not None
        assert pm.load_project("demo")["characters"]["Alice"]["character_sheet"] == "characters/Alice.png"

        receipt.compensate_cancelled()

        assert pm.load_project("demo")["characters"]["Alice"]["character_sheet"] == "characters/old.png"
        assert version_manager.get_current_version("characters", "Alice") == old_version
        assert current.read_bytes() == b"old-sheet"

    async def test_asset_generation_registration_failure_never_exposes_uncommitted_image(self, tmp_path, monkeypatch):
        from lib.media_generator import task_image_staging_path
        from lib.project_manager import ProjectManager
        from lib.version_manager import VersionManager

        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "queued definition")
        project_path = pm.get_project_path("demo")
        current = project_path / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"old-sheet")
        versions = VersionManager(project_path)
        old_version = versions.add_version("characters", "Alice", "old", source_file=current)

        class _Generator:
            def __init__(self):
                self.versions = versions

            async def generate_image_async(self, **kwargs):
                assert kwargs["formal_output"] is True
                staged = task_image_staging_path(current, kwargs["task_id"])
                staged.write_bytes(b"new-sheet")
                version = kwargs["commit_formal_output"](staged, current, {"aspect_ratio": "16:9"})
                return current, version

        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", _fake_resolve_ctx(_Generator()))
        monkeypatch.setattr(
            generation_tasks,
            "register_task_current_resource_artifact",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("manifest commit failed")),
        )

        with pytest.raises(RuntimeError, match="manifest commit failed"):
            await generation_tasks.execute_character_task(
                "demo",
                "Alice",
                {"prompt": "queued definition"},
                task_id="character-task",
            )

        assert current.read_bytes() == b"old-sheet"
        assert versions.get_current_version("characters", "Alice") == old_version
        assert pm.load_project("demo")["characters"]["Alice"].get("character_sheet") == ""
