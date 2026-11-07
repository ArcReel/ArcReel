"""Image-edit-specific executor semantics: current image as sole reference, instruction-only prompt, resource-specific writeback, edit-version metadata and no writeback on failure. image_size resolution retains the same source across migration."""

import asyncio
import json
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import register_current_artifact_if_provable
from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactBlocker,
    ArtifactComparison,
    ArtifactKey,
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactManifestError,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.version_manager import VersionManager
from lib.backends.image_backends.base import BOTH_IMAGE_CAPABILITIES, ImageCapability
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.generation.generation_queue import DispatchProviderChanged
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_failure import ProjectMigrationError
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.project.resource_paths import resource_relative_path
from server.services.tasks import formal_image_commit, generation_context, generation_tasks, image_edit_tasks
from server.services.tasks.generation_context import (
    GenerationContext,
    ImageLaneRequest,
    ImageLaneResult,
    resolve_generation_context,
)
from server.services.tasks.image_edit_tasks import (
    IMAGE_EDIT_VERSION_SOURCE,
    execute_image_edit_task,
    resolve_current_image_rel,
)
from tests.fakes import (
    bind_session_factory,
    stage_and_commit,
    use_backend_assembler,
    use_generation_context,
    use_project_manager,
)


@pytest.fixture
async def patched_session_factory(db_factory, monkeypatch):
    """Real in-memory DB: create every ORM table and point lib.db.async_session_factory at it.

    Used by real ConfigResolver in image_size equivalence cases (built-in providers need no DB rows; only custom providers' default resolution is stored)."""
    bind_session_factory(monkeypatch, db_factory)
    return db_factory


class _FakeGenerator:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.image_calls = []
        self.reference_bytes = []
        self.tracked = []
        self.versions = self
        # Injected by _patch_common: real generators persist new images at canonical paths;
        # manifest registration requires the actual image to exist on disk.
        self.project_path: Path | None = None

    async def generate_image_async(self, **kwargs):
        if self.fail:
            raise RuntimeError("backend boom")
        self.image_calls.append(kwargs)
        # Reference reads, output writes and version commits run in worker threads: real backends write artifacts there,
        # and formal commit callbacks run through run_noninterruptible_sync.
        return await asyncio.to_thread(self._produce_and_commit, kwargs)

    def _produce_and_commit(self, kwargs) -> tuple[Path, int]:
        self.reference_bytes = [Path(reference).read_bytes() for reference in kwargs["reference_images"]]
        if self.project_path is not None:
            canonical = self.project_path / resource_relative_path(kwargs["resource_type"], kwargs["resource_id"])
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_bytes(b"edited")
        # Like MediaGenerator, commit versions through formal_output activation and return its version number.
        current = Path(tempfile.gettempdir()) / "image.png"
        version = kwargs["commit_formal_output"](current, current, {"source": kwargs["source"]})
        return current, version

    def commit_staged_version(self, resource_type, resource_id, prompt, *, on_commit=None, **_kwargs):
        if on_commit is not None:
            on_commit()
        return 2

    def get_current_version(self, resource_type, resource_id):
        return 2

    def ensure_current_tracked(self, resource_type, resource_id, current_file, prompt, **metadata):
        self.tracked.append({"resource_type": resource_type, "resource_id": resource_id, "prompt": prompt})
        return

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"version": 2, "created_at": "2026-01-01T00:00:00Z"}]}


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        # Production projects use current schema; scripts are bound in the episodes ledger.
        self.project = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "episodes": [{"episode": 1, "script_file": "episode_1.json"}],
            "generation_mode": "storyboard",
            "content_mode": "narration",
            "image_provider_i2i": "gemini-aistudio/gemini-image",
            "characters": {
                "Alice": {
                    "character_sheet": "characters/Alice.png",
                    "description": "少女剑客",
                    "image_prompt": "原始角色 prompt",
                }
            },
            "scenes": {"祠堂": {"scene_sheet": "", "description": "祠堂"}},
            "props": {},
            "products": {},
        }
        self.script = {
            "episode": 1,
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "image_prompt": {
                        "scene": "山道",
                        "composition": {"shot_type": "medium", "lighting": "natural", "ambiance": "calm"},
                    },
                    "generated_assets": {"storyboard_image": "storyboards/scene_E1S01_first.png"},
                },
                {"segment_id": "E1S02", "generated_assets": {}},
            ],
        }
        self.project_commits = 0
        self.script_commits = []

    def sync_disk(self):
        """Persist in-memory project and scripts; manifest comparisons use the actual on-disk project."""

        (self.project_path / "scripts").mkdir(parents=True, exist_ok=True)
        (self.project_path / "project.json").write_text(json.dumps(self.project), encoding="utf-8")
        (self.project_path / "scripts" / "episode_1.json").write_text(json.dumps(self.script), encoding="utf-8")

    def register_artifacts(self):
        """Register persisted asset and storyboard images in manifests; unregistered artifacts cannot enter editing."""

        self.sync_disk()
        register_current_artifact_if_provable(self.project_path, ArtifactKey.asset_sheet("character", "Alice"))
        register_current_artifact_if_provable(self.project_path, ArtifactKey.episode_storyboard(1, "E1S01"))

    def load_project(self, project_name):
        self.sync_disk()
        return self.project

    def get_project_path(self, project_name):
        return self.project_path

    def load_script(self, project_name, script_file):
        self.sync_disk()
        return self.script

    def update_project(self, project_name, mutate_fn, *, on_commit=None):
        mutate_fn(self.project)
        self.project_commits += 1
        self.sync_disk()
        if on_commit is not None:
            on_commit(self.project_path / "project.json")

    @contextmanager
    def locked_script(self, project_name, script_filename, *, validate=True, on_commit=None):
        yield self.script
        self.script_commits.append(script_filename)
        self.sync_disk()
        if on_commit is not None:
            on_commit(self.project_path / "scripts" / script_filename)

    def _set_scene_asset_in_script(self, script, scene_id, asset_type, asset_path):
        item = next(segment for segment in script["segments"] if segment["segment_id"] == scene_id)
        item.setdefault("generated_assets", {})[asset_type] = asset_path
        return item


def _prepare_files(tmp_path: Path) -> Path:
    project_path = tmp_path / "projects" / "demo"
    (project_path / "characters").mkdir(parents=True, exist_ok=True)
    (project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "storyboards" / "scene_E1S01_first.png").write_bytes(b"png")
    return project_path


def _patch_common(monkeypatch, fake_pm, fake_generator, *, resolution=None, register_artifacts=True):
    """Replace project-manager and generation-context seams: ctx.generator is fake_generator, image lane carries the specified resolution. Assert editing always declares the i2i slot (generation_type == "i2i")."""
    if isinstance(fake_pm, _FakePM):
        # Real ProjectManager cases create/register their own projects; this serves fake PM only.
        if register_artifacts:
            fake_pm.register_artifacts()
        else:
            fake_pm.sync_disk()
        fake_generator.project_path = fake_pm.project_path
    use_project_manager(monkeypatch, fake_pm)

    async def _fake_resolve(project_name, payload, *, project, image=None, **_kwargs):
        assert image is not None
        assert image.generation_type == "i2i"
        lane = ImageLaneResult(
            provider_model=ProviderModel("gemini-aistudio", "gemini-image"),
            backend_name="gemini-aistudio",
            backend_model="gemini-image",
            resolution=resolution,
            max_reference_images=None,
        )
        return GenerationContext(generator=fake_generator, image_lane=lane)

    use_generation_context(monkeypatch, _fake_resolve)


class TestResolveCurrentImageRel:
    def test_asset_sheet_and_missing(self):
        project = {"characters": {"Alice": {"character_sheet": "characters/Alice.png"}, "Bob": {}}}
        assert resolve_current_image_rel(project, "character", "Alice") == "characters/Alice.png"
        assert resolve_current_image_rel(project, "character", "Bob") is None
        with pytest.raises(KeyError):
            resolve_current_image_rel(project, "character", "不存在")

    def test_asset_sheet_matches_across_nfc_nfd_forms(self):
        """Request asset names may differ from bucket-key forms (registration normalizes NFC, existing keys may be NFD)."""
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd

        project = {"characters": {name_nfd: {"character_sheet": "characters/legacy.png"}}}
        assert resolve_current_image_rel(project, "character", name_nfc) == "characters/legacy.png"
        assert resolve_current_image_rel(project, "character", name_nfd) == "characters/legacy.png"

    def test_storyboard_pointer_is_the_only_evidence_of_a_current_image(self):
        """Trust only registered pointers: no pointer means no artifact; same-name files do not prove storyboard ownership."""

        script = {
            "content_mode": "narration",
            "segments": [
                {"segment_id": "E1S01", "generated_assets": {"storyboard_image": "storyboards/scene_E1S01_first.png"}},
                {"segment_id": "E1S02", "generated_assets": {}},
            ],
        }
        assert resolve_current_image_rel({}, "storyboard", "E1S01", script) == "storyboards/scene_E1S01_first.png"
        assert resolve_current_image_rel({}, "storyboard", "E1S02", script) is None
        with pytest.raises(KeyError):
            resolve_current_image_rel({}, "storyboard", "E9S99", script)


class TestExecuteImageEditTask:
    async def test_active_edit_rejects_same_claim_with_replaced_source_bytes_before_submit(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "hero")
        project_path = pm.get_project_path("demo")
        current = project_path / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"selected-image")
        pm.update_project_character_sheet("demo", "Alice", "characters/Alice.png")
        pm.update_project(
            "demo",
            lambda project: project.update(
                {
                    "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                    "generation_mode": "storyboard",
                    "source_kind": "novel",
                    "source_language": "中文",
                    "aspect_ratio": "9:16",
                    "episodes": [],
                }
            ),
        )
        ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
            ArtifactKey.asset_sheet("character", "Alice"),
            artifact_path="characters/Alice.png",
            basis=ArtifactBasis.build("test/asset-sheet", kind_version=1, inputs={}),
        )
        provider_reached = False

        class _ReplacingGenerator(_FakeGenerator):
            async def generate_image_async(self, **kwargs):
                nonlocal provider_reached
                await asyncio.to_thread(current.write_bytes, b"same-basis-replacement")
                await kwargs["before_submit"]()
                provider_reached = True
                raise AssertionError("provider must not receive a replaced formal input")

        generator = _ReplacingGenerator()
        use_project_manager(monkeypatch, pm)

        async def _resolve(*_args, **_kwargs):
            lane = ImageLaneResult(
                provider_model=ProviderModel("gemini-aistudio", "gemini-image"),
                backend_name="gemini-aistudio",
                backend_model="gemini-image",
                resolution=None,
                max_reference_images=None,
            )
            return GenerationContext(generator=generator, image_lane=lane)

        use_generation_context(monkeypatch, _resolve)

        with pytest.raises(ValueError, match="changed since it was selected"):
            await execute_image_edit_task(
                "demo",
                "Alice",
                {"resource_type": "character", "prompt": "red hair"},
            )

        assert provider_reached is False

    async def test_unmigrated_project_blocks_the_edit_with_its_migration_verdict(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Projects below current data version block editing before provider contact and explain why; an actual same-name on-disk asset image does not change admission."""

        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "")
        project_path = pm.get_project_path("demo")
        current = project_path / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"unmigrated-sheet")
        pm.update_project_character_sheet("demo", "Alice", "characters/Alice.png")
        pm.update_project("demo", lambda project: project.__setitem__("schema_version", 7))

        generator = _FakeGenerator()
        use_project_manager(monkeypatch, pm)

        async def _resolve(*_args, **_kwargs):
            lane = ImageLaneResult(
                provider_model=ProviderModel("gemini-aistudio", "gemini-image"),
                backend_name="gemini-aistudio",
                backend_model="gemini-image",
                resolution=None,
                max_reference_images=None,
            )
            return GenerationContext(generator=generator, image_lane=lane)

        use_generation_context(monkeypatch, _resolve)

        with pytest.raises(ProjectMigrationError, match=f"did not reach v{CURRENT_PROJECT_SCHEMA_VERSION}"):
            await execute_image_edit_task(
                "demo",
                "Alice",
                {"resource_type": "character", "prompt": "red hair"},
            )

        assert generator.image_calls == []

    async def test_registration_failure_never_exposes_an_edited_image(self, tmp_path, monkeypatch):
        from lib.artifacts.artifact_activation import register_current_artifact
        from lib.generation.media_generator import task_image_staging_path

        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
        pm.add_character("demo", "Alice", "hero")
        project_path = pm.get_project_path("demo")
        current = project_path / "characters" / "Alice.png"
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"old-image")
        pm.update_project_character_sheet("demo", "Alice", "characters/Alice.png")
        register_current_artifact(project_path, ArtifactKey.asset_sheet("character", "Alice"))
        versions = VersionManager(project_path)

        class _Generator:
            def __init__(self):
                self.versions = versions

            async def generate_image_async(self, **kwargs):
                assert kwargs["formal_output"] is True
                staged = task_image_staging_path(current, kwargs["task_id"])
                version = await stage_and_commit(
                    kwargs["commit_formal_output"],
                    staged,
                    current,
                    b"edited-image",
                    {"aspect_ratio": "16:9", "source": kwargs["source"]},
                )
                return current, version

        _patch_common(monkeypatch, pm, _Generator())
        monkeypatch.setattr(
            formal_image_commit,
            "register_task_current_resource_artifact",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("manifest commit failed")),
        )

        with pytest.raises(RuntimeError, match="manifest commit failed"):
            await execute_image_edit_task(
                "demo",
                "Alice",
                {"resource_type": "character", "prompt": "red hair"},
                task_id="image-edit-task",
            )

        assert current.read_bytes() == b"old-image"
        assert versions.get_current_version("characters", "Alice") == 1
        assert pm.load_project("demo")["characters"]["Alice"]["character_sheet"] == "characters/Alice.png"

    @pytest.mark.parametrize("resource_type", ["character", "storyboard"])
    async def test_edit_persists_the_basis_and_source_bytes_used_by_the_provider(
        self,
        resource_type: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from lib.artifacts.artifact_activation import ArtifactCurrencyResolver, register_current_artifact
        from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
        from lib.artifacts.artifact_version_provenance import parse_image_version_basis
        from lib.artifacts.visual_artifact_provenance import (
            VisualReference,
            build_asset_sheet_visual_basis,
            build_storyboard_image_visual_basis,
        )
        from lib.generation.media_generator import task_image_staging_path
        from server.routers import versions as versions_router

        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata(
            "demo",
            "Demo",
            "Anime",
            "narration",
            extras={"generation_mode": "storyboard"},
        )
        if resource_type == "character":
            resource_id = "Alice"
            version_resource_type = "characters"
            current_rel = "characters/Alice.png"
            script_file = None
            pm.add_character("demo", resource_id, "hero")
            pm.update_project_character_sheet("demo", resource_id, current_rel)
        else:
            resource_id = "E1S01"
            version_resource_type = "storyboards"
            current_rel = "storyboards/scene_E1S01.png"
            script_file = "episode_1.json"
            pm.add_episode("demo", 1, "Episode 1", "scripts/episode_1.json")
            pm.save_script(
                "demo",
                {
                    "episode": 1,
                    "content_mode": "narration",
                    "segments": [
                        {
                            "segment_id": resource_id,
                            "novel_text": "雨夜",
                            "image_prompt": "old prompt",
                            "video_prompt": "镜头前推",
                            "characters_in_segment": [],
                            "scenes": [],
                            "props": [],
                            "generated_assets": {"storyboard_image": current_rel},
                        }
                    ],
                },
                script_file,
                validate=False,
            )

        project_path = pm.get_project_path("demo")
        current = project_path / current_rel
        current.parent.mkdir(parents=True, exist_ok=True)
        current.write_bytes(b"source-before-await")
        source_key = (
            ArtifactKey.episode_storyboard(1, resource_id)
            if resource_type == "storyboard"
            else ArtifactKey.asset_sheet(resource_type, resource_id)
        )
        register_current_artifact(project_path, source_key)
        instruction = "turn the coat red"
        reference = VisualReference(
            path=current,
            role="edit_source",
            logical_type=resource_type,
            logical_id=resource_id,
            kind="current",
        )
        expected_basis = (
            build_storyboard_image_visual_basis(
                resource_id=resource_id,
                image_prompt=instruction,
                style="",
                aspect_ratio="9:16",
                references=(reference,),
            )
            if resource_type == "storyboard"
            else build_asset_sheet_visual_basis(
                asset_type=resource_type,
                asset_id=resource_id,
                description=instruction,
                style="",
                style_description="",
                aspect_ratio="16:9",
                references=(reference,),
            )
        )
        manager = VersionManager(project_path)
        provider_references: list[Path] = []

        class _Generator:
            def __init__(self) -> None:
                self.versions = manager

            async def generate_image_async(self, **kwargs):
                provider_reference = Path(kwargs["reference_images"][0])
                provider_references.append(provider_reference)

                def _edit_concurrently() -> bytes:
                    # Another writer changes the base image, project and script during generation; provider still reads frozen bytes.
                    current.write_bytes(b"source-changed-during-await")

                    def _mutate(project):
                        project["style"] = "style-changed-during-await"
                        if resource_type == "character":
                            project["characters"][resource_id]["description"] = "description changed"

                    pm.update_project("demo", _mutate)
                    if resource_type == "storyboard":
                        script = pm.load_script("demo", script_file)
                        script["segments"][0]["image_prompt"] = "prompt changed"
                        pm.save_script("demo", script, script_file, validate=False)
                    return provider_reference.read_bytes()

                assert await asyncio.to_thread(_edit_concurrently) == b"source-before-await"

                # Direct calls outside queued tasks lack task_id: generators create staging identity internally; the substitute creates its own.
                staged = task_image_staging_path(current, kwargs["task_id"] or "inline-test")
                version = await stage_and_commit(
                    kwargs["commit_formal_output"],
                    staged,
                    current,
                    b"edited-image",
                    {"aspect_ratio": kwargs["aspect_ratio"], "source": kwargs["source"]},
                )
                return current, version

        _patch_common(monkeypatch, pm, _Generator())
        result = await execute_image_edit_task(
            "demo",
            resource_id,
            {
                "resource_type": resource_type,
                "prompt": instruction,
                **({"script_file": script_file} if script_file is not None else {}),
            },
        )

        records = manager.get_versions(version_resource_type, resource_id)["versions"]
        edited_record = next(record for record in records if record["version"] == result["version"])
        assert parse_image_version_basis(version_resource_type, resource_id, edited_record) == expected_basis
        adapter = ProjectArtifactManifestAdapter(project_path)
        assert adapter.get_entry(source_key).basis_digest == expected_basis.digest
        assert (
            ArtifactCurrencyResolver(project_path).compare(source_key, artifact_path=current_rel).status
            is ArtifactStatus.STALE
        )
        assert current.read_bytes() == b"edited-image"
        assert len(provider_references) == 1
        assert not provider_references[0].exists()

        adapter.delete_entry(source_key)
        use_project_manager(monkeypatch, pm)
        versions_router._restore_non_typed_version(
            versions=manager,
            resource_type=version_resource_type,
            project_name="demo",
            resource_id=resource_id,
            version=result["version"],
            current_file=current,
            file_path=current_rel,
            project_path=project_path,
        )
        assert adapter.get_entry(source_key).basis_digest == expected_basis.digest

    async def test_storyboard_edit_rejects_an_unbound_script_before_provider(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project["episodes"] = []
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator, register_artifacts=False)

        with pytest.raises(ValueError, match="not bound"):
            await execute_image_edit_task(
                "demo",
                "E1S01",
                {
                    "resource_type": "storyboard",
                    "prompt": "去掉背景里的路人",
                    "script_file": "episode_1.json",
                },
            )

        assert fake_generator.tracked == []
        assert fake_generator.image_calls == []

    @pytest.mark.parametrize("resource_type", ["character", "storyboard"])
    @pytest.mark.parametrize(
        ("claim_status", "error_type", "error_match"),
        [
            (ArtifactStatus.MISSING, ValueError, "no current image"),
            (ArtifactStatus.BLOCKED, ArtifactManifestError, "source claim is blocked"),
        ],
    )
    async def test_active_edit_rejects_an_unusable_source_claim_before_provider(
        self,
        resource_type,
        claim_status,
        error_type,
        error_match,
        tmp_path,
        monkeypatch,
    ):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project.update(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "generation_mode": "storyboard",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            }
        )
        fake_pm.script["episode"] = 1
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)
        resource_id = "Alice" if resource_type == "character" else "E1S01"
        artifact_path = "characters/Alice.png" if resource_type == "character" else "storyboards/scene_E1S01_first.png"
        expected_key = (
            ArtifactKey.asset_sheet("character", resource_id)
            if resource_type == "character"
            else ArtifactKey.episode_storyboard(1, resource_id)
        )
        comparisons: list[tuple[ArtifactKey, str]] = []

        class _Currency:
            def compare(self, key, *, artifact_path):
                comparisons.append((key, artifact_path))
                blocker = (
                    ArtifactBlocker(
                        code="manifest_unreadable",
                        path=artifact_path,
                        detail="source claim is blocked",
                    )
                    if claim_status is ArtifactStatus.BLOCKED
                    else None
                )
                return ArtifactComparison(status=claim_status, artifact_path=artifact_path, blocker=blocker)

            def resolve_usable_entry(self, key, *, artifact_path):
                comparison = self.compare(key, artifact_path=artifact_path)
                if comparison.status is ArtifactStatus.BLOCKED:
                    assert comparison.blocker is not None
                    raise ArtifactManifestError(comparison.blocker.detail)
                if comparison.status not in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}:
                    return None
                return ArtifactManifestEntry(artifact_path=artifact_path, basis_digest="selected")

        monkeypatch.setattr(
            image_edit_tasks,
            "active_artifact_currency_resolver",
            lambda *_args: _Currency(),
            raising=False,
        )
        monkeypatch.setattr(
            generation_tasks,
            "active_artifact_currency_resolver",
            lambda *_args: _Currency(),
        )
        payload = {
            "resource_type": resource_type,
            "prompt": "局部调整",
            **({"script_file": "episode_1.json"} if resource_type == "storyboard" else {}),
        }

        with pytest.raises(error_type, match=error_match):
            await execute_image_edit_task("demo", resource_id, payload)

        assert comparisons == [(expected_key, artifact_path)]
        assert fake_generator.tracked == []
        assert fake_generator.image_calls == []

    @pytest.mark.parametrize("resource_type", ["character", "storyboard"])
    @pytest.mark.parametrize("successful_rechecks", [0, 1])
    @pytest.mark.parametrize(
        ("claim_status", "error_type", "error_match"),
        [
            (ArtifactStatus.MISSING, ValueError, "no longer registered"),
            (ArtifactStatus.BLOCKED, ArtifactManifestError, "source claim changed to blocked"),
        ],
    )
    async def test_active_edit_rechecks_the_selected_source_before_provider_submission(
        self,
        resource_type,
        successful_rechecks,
        claim_status,
        error_type,
        error_match,
        tmp_path,
        monkeypatch,
    ):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_pm.project.update(
            {
                "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
                "generation_mode": "storyboard",
                "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            }
        )
        fake_pm.script["episode"] = 1
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)
        resource_id = "Alice" if resource_type == "character" else "E1S01"
        expected_key = (
            ArtifactKey.asset_sheet("character", resource_id)
            if resource_type == "character"
            else ArtifactKey.episode_storyboard(1, resource_id)
        )
        statuses = [ArtifactStatus.CURRENT, *([ArtifactStatus.CURRENT] * successful_rechecks), claim_status]
        comparisons: list[tuple[ArtifactKey, str, ArtifactStatus]] = []

        class _Currency:
            def __init__(self, status):
                self.status = status

            def compare(self, key, *, artifact_path):
                comparisons.append((key, artifact_path, self.status))
                blocker = (
                    ArtifactBlocker(
                        code="manifest_unreadable",
                        path=artifact_path,
                        detail="source claim changed to blocked",
                    )
                    if self.status is ArtifactStatus.BLOCKED
                    else None
                )
                return ArtifactComparison(status=self.status, artifact_path=artifact_path, blocker=blocker)

            def resolve_usable_entry(self, key, *, artifact_path):
                comparison = self.compare(key, artifact_path=artifact_path)
                if comparison.status is ArtifactStatus.BLOCKED:
                    assert comparison.blocker is not None
                    raise ArtifactManifestError(comparison.blocker.detail)
                if comparison.status not in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}:
                    return None
                return ArtifactManifestEntry(artifact_path=artifact_path, basis_digest="selected")

            def compare_frozen_entry(self, key, entry):
                return self.compare(key, artifact_path=entry.artifact_path)

            def artifact_content_digest(self, artifact_path):
                return "0" * 64

        def _resolver(*_args):
            return _Currency(statuses.pop(0))

        monkeypatch.setattr(image_edit_tasks, "active_artifact_currency_resolver", _resolver, raising=False)
        monkeypatch.setattr("lib.artifacts.artifact_input_claims.active_artifact_currency_resolver", _resolver)
        payload = {
            "resource_type": resource_type,
            "prompt": "局部调整",
            **({"script_file": "episode_1.json"} if resource_type == "storyboard" else {}),
        }

        with pytest.raises(error_type, match=error_match):
            await execute_image_edit_task("demo", resource_id, payload)

        assert [key for key, _path, _status in comparisons] == [expected_key] * (3 + successful_rechecks)
        assert statuses == []
        if successful_rechecks:
            assert fake_generator.tracked == [
                {
                    "resource_type": "characters" if resource_type == "character" else "storyboards",
                    "resource_id": resource_id,
                    "prompt": "",
                }
            ]
        else:
            assert fake_generator.tracked == []
        assert fake_generator.image_calls == []

    async def test_character_edit_uses_current_image_as_sole_reference(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        result = await execute_image_edit_task(
            "demo",
            "Alice",
            {"resource_type": "character", "prompt": "把头发改成红色"},
        )

        call = fake_generator.image_calls[0]
        # Current image is the only reference; prompt is editing instructions only, without original image_prompt.
        assert len(call["reference_images"]) == 1
        assert Path(call["reference_images"][0]).name.endswith("Alice.png")
        assert fake_generator.reference_bytes == [b"png"]
        assert not Path(call["reference_images"][0]).exists()  # noqa: ASYNC240 -- Test-local small-file I/O/assertions, outside production event loops
        assert call["prompt"] == "把头发改成红色"
        assert "原始角色 prompt" not in call["prompt"]
        # New version includes edit metadata.
        assert call["source"] == IMAGE_EDIT_VERSION_SOURCE
        assert call["resource_type"] == "characters"
        # Backfill old image with neutral metadata (no edit instructions) so its pre-edit version can be restored.
        assert fake_generator.tracked == [{"resource_type": "characters", "resource_id": "Alice", "prompt": ""}]
        # Write canonical paths by resource type; preserve original image_prompt fields.
        assert fake_pm.project_commits == 1
        assert fake_pm.project["characters"]["Alice"]["character_sheet"] == "characters/Alice.png"
        assert fake_pm.project["characters"]["Alice"]["image_prompt"] == "原始角色 prompt"

        assert result["resource_type"] == "characters"
        assert result["resource_id"] == "Alice"
        assert result["version"] == 2
        assert result["file_path"] == "characters/Alice.png"

    async def test_reference_snapshot_is_cleaned_when_cancelled_while_preparing(self, tmp_path, monkeypatch):
        """If cancellation occurs while a worker prepares the base snapshot, reclaim the completed snapshot without calling the provider."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)
        freezing = threading.Event()
        release = threading.Event()
        snapshots: list[Path] = []
        original_freeze = image_edit_tasks.freeze_image_references

        def _blocking_freeze(*args, **kwargs):
            freezing.set()
            release.wait(5)
            frozen = original_freeze(*args, **kwargs)
            snapshots.extend(Path(ref).parent for ref in frozen.reference_images or ())
            return frozen

        monkeypatch.setattr(image_edit_tasks, "freeze_image_references", _blocking_freeze)
        task = asyncio.create_task(
            execute_image_edit_task("demo", "Alice", {"resource_type": "character", "prompt": "把头发改成红色"})
        )
        try:
            assert await asyncio.to_thread(freezing.wait, 5)
            task.cancel()
            await asyncio.sleep(0)
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert snapshots
        assert not any(path.exists() for path in snapshots)
        assert fake_generator.image_calls == []

    async def test_storyboard_edit_reads_pointer_writes_canonical(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        result = await execute_image_edit_task(
            "demo",
            "E1S01",
            {"resource_type": "storyboard", "prompt": "去掉路人", "script_file": "episode_1.json"},
        )

        call = fake_generator.image_calls[0]
        # Base image comes from generated_assets (legacy grid-project path); new image writes to canonical path.
        assert len(call["reference_images"]) == 1
        assert Path(call["reference_images"][0]).name.endswith("scene_E1S01_first.png")
        assert fake_generator.reference_bytes == [b"png"]
        assert not Path(call["reference_images"][0]).exists()  # noqa: ASYNC240 -- Test-local small-file I/O/assertions, outside production event loops
        assert call["resource_type"] == "storyboards"
        assert fake_pm.script_commits == ["episode_1.json"]
        assert fake_pm.script["segments"][0]["generated_assets"]["storyboard_image"] == "storyboards/scene_E1S01.png"
        assert result["file_path"] == "storyboards/scene_E1S01.png"

    async def test_provider_changed_since_claim_is_raised_before_the_provider_call(self, tmp_path, monkeypatch):
        """If resolved i2i provider differs from the claimed slot, neither backfill old images nor contact the provider; worker updates the record and requeues."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        with pytest.raises(DispatchProviderChanged) as caught:
            await execute_image_edit_task(
                "demo",
                "Alice",
                {"resource_type": "character", "prompt": "把头发改成红色"},
                claimed_provider_id="openai",
            )

        assert (caught.value.claimed_provider_id, caught.value.actual_provider_id) == ("openai", "gemini-aistudio")
        assert fake_generator.image_calls == []
        assert fake_generator.tracked == []
        assert fake_pm.project_commits == 0

    async def test_no_current_image_raises(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        with pytest.raises(ValueError, match="no current image"):
            await execute_image_edit_task("demo", "祠堂", {"resource_type": "scene", "prompt": "x"})
        assert fake_generator.image_calls == []
        assert fake_pm.project_commits == 0

    async def test_backend_failure_skips_writeback(self, tmp_path, monkeypatch):
        """Zero-loss failure: backend exceptions do not write resource fields (MediaGenerator guarantees the current-image pointer remains untouched). Old-image baseline registration precedes backend calls regardless of success and is not rolled back on failure; it is outside this test's assertions."""
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator(fail=True)
        _patch_common(monkeypatch, fake_pm, fake_generator)

        with pytest.raises(RuntimeError, match="backend boom"):
            await execute_image_edit_task("demo", "Alice", {"resource_type": "character", "prompt": "x"})
        assert fake_pm.project_commits == 0
        assert fake_pm.script_commits == []

    async def test_invalid_payload_rejected(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        with pytest.raises(ValueError, match="resource_type"):
            await execute_image_edit_task("demo", "g1", {"resource_type": "grid", "prompt": "x"})
        with pytest.raises(ValueError, match="instruction"):
            await execute_image_edit_task("demo", "Alice", {"resource_type": "character", "prompt": "   "})
        with pytest.raises(ValueError, match="script_file"):
            await execute_image_edit_task("demo", "E1S01", {"resource_type": "storyboard", "prompt": "x"})


@dataclass
class _EchoBackend:
    """Echo backend: model exactly reflects request model_id, without custom fallback, matching registry identity."""

    name: str
    model: str
    max_reference_images: int | None = None
    capabilities: frozenset[ImageCapability] = BOTH_IMAGE_CAPABILITIES

    async def reference_image_limit(self) -> int | None:
        return self.max_reference_images


class TestImageSizeResolutionEquivalence:
    """Shared image_size source: ``ctx.image.resolution`` equals the independent two-step result of resolve_image_backend(i2i) for provider/model, then resolution lookup by model_id.

    Editing uses i2i, and built-in backend.model equals registry model_id without custom fallback, so actual-backend-model lookup and registry-model_id lookup are identical."""

    @pytest.fixture
    def _ctx_env(self, monkeypatch, tmp_path):
        """Real ProjectManager (demo project directory) plus an echo assemble seam, preventing network access during backend construction."""
        pm = ProjectManager(tmp_path)
        (pm.projects_dir / "demo").mkdir(parents=True)
        use_project_manager(monkeypatch, pm)

        async def _assemble(
            *,
            provider_id,
            media_type,
            model_id,
            resolver,
            rate_limiter=None,
            generation_type=None,
            submitted_job=False,
            builtin_endpoint=None,
        ):
            return _EchoBackend(name=provider_id, model=model_id or "default-model")

        use_backend_assembler(monkeypatch, _assemble)
        generation_context.invalidate_backend_cache()
        yield
        generation_context.invalidate_backend_cache()

    @staticmethod
    async def _old_image_size(patched_session_factory, project, payload):
        """Old execution semantics: resolve_image_backend(i2i) for provider/model, then resolution lookup by model_id."""
        resolver = ConfigResolver(patched_session_factory)
        async with resolver.session() as r:
            resolved = await r.resolve_image_backend(project, payload, generation_type="i2i")
            return await r.resolve_resolution(project, resolved.provider_id, resolved.model_id)

    @pytest.mark.usefixtures("_ctx_env")
    async def test_model_settings_override(self, patched_session_factory):
        project = {
            "image_provider_i2i": "gemini-aistudio/gemini-image",
            "model_settings": {"gemini-aistudio/gemini-image": {"resolution": "2048x2048"}},
        }
        old = await self._old_image_size(patched_session_factory, project, None)
        ctx = await resolve_generation_context(
            "demo", None, project=project, image=ImageLaneRequest(generation_type="i2i")
        )
        assert old == "2048x2048"
        assert ctx.image.resolution == old

    @pytest.mark.usefixtures("_ctx_env")
    async def test_default_falls_back_to_none(self, patched_session_factory):
        project = {"image_provider_i2i": "gemini-aistudio/gemini-image"}
        old = await self._old_image_size(patched_session_factory, project, None)
        ctx = await resolve_generation_context(
            "demo", None, project=project, image=ImageLaneRequest(generation_type="i2i")
        )
        assert old is None
        assert ctx.image.resolution == old


class TestImageEditEventMapping:
    def test_emit_maps_image_edit_to_resource_events(self, tmp_path, monkeypatch):
        """Edit-completion events share generation-completion shape for the same resource; dispatch by payload.resource_type."""
        from lib.project.project_change_hints import register_project_change_batch_listener
        from server.services.tasks import generation_tasks

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        use_project_manager(monkeypatch, fake_pm)

        # Subscribe publicly to the real event bus; assert batches actually received by the subscriber on the SSE broadcast side.
        delivered: list[dict] = []
        unregister = register_project_change_batch_listener(
            lambda _project_name, _source, changes: delivered.extend(changes)
        )
        try:
            generation_tasks.emit_generation_success_batch(
                task_type="image_edit",
                project_name="demo",
                resource_id="Alice",
                payload={"resource_type": "character", "prompt": "x"},
            )
            assert delivered[0]["entity_type"] == "character"
            assert delivered[0]["action"] == "updated"
            # Compute fingerprints using character-task semantics (characters/Alice.png exists on disk).
            assert "characters/Alice.png" in delivered[0]["asset_fingerprints"]

            delivered.clear()
            generation_tasks.emit_generation_success_batch(
                task_type="image_edit",
                project_name="demo",
                resource_id="E1S01",
                payload={"resource_type": "storyboard", "prompt": "x", "script_file": "episode_1.json"},
            )
            assert delivered[0]["entity_type"] == "segment"
            assert delivered[0]["action"] == "storyboard_ready"
            assert delivered[0]["script_file"] == "episode_1.json"
        finally:
            unregister()


def test_image_edit_registered_in_task_executors():
    from server.services.tasks.generation_tasks import _TASK_EXECUTORS

    assert "image_edit" in _TASK_EXECUTORS
