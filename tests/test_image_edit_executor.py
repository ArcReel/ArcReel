"""image_edit executor 的编辑独有语义：底图即当前图且是唯一参考图、prompt 即指令、
按资源类型写回、版本带编辑标记、失败不写回；image_size 解析迁移前后同源。"""

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.artifact_activation import register_current_artifact_if_provable
from lib.artifact_manifest import (
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
from lib.config.resolver import ConfigResolver, ProviderModel
from lib.db.base import Base
from lib.project_manager import ProjectManager
from lib.project_migration_failure import ProjectMigrationError
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.resource_paths import resource_relative_path
from lib.version_manager import VersionManager
from server.services import generation_context, generation_tasks, image_edit_tasks
from server.services.generation_context import (
    GenerationContext,
    ImageLaneRequest,
    ImageLaneResult,
    resolve_generation_context,
)
from server.services.image_edit_tasks import (
    IMAGE_EDIT_VERSION_SOURCE,
    execute_image_edit_task,
    resolve_current_image_rel,
)

pytestmark = pytest.mark.unit


@pytest.fixture
async def session_factory(monkeypatch):
    """真实内存 DB：建全部 ORM 表，把 lib.db.async_session_factory 指向它。

    供 image_size 解析等价用例的真实 ConfigResolver 使用（预置供应商无 DB 行，自定义供应商
    默认 resolution 才落 DB）。
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr("lib.db.async_session_factory", factory)
    yield factory
    await engine.dispose()


class _FakeGenerator:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.image_calls = []
        self.reference_bytes = []
        self.tracked = []
        self.versions = self
        # 由 _patch_common 注入：真实 generator 会把新图落到 canonical 路径，
        # 产物清单登记要求这张图确实在盘上。
        self.project_path: Path | None = None

    async def generate_image_async(self, **kwargs):
        if self.fail:
            raise RuntimeError("backend boom")
        self.reference_bytes = [Path(reference).read_bytes() for reference in kwargs["reference_images"]]
        self.image_calls.append(kwargs)
        if self.project_path is not None:
            canonical = self.project_path / resource_relative_path(kwargs["resource_type"], kwargs["resource_id"])
            canonical.parent.mkdir(parents=True, exist_ok=True)
            canonical.write_bytes(b"edited")
        return Path(tempfile.gettempdir()) / "image.png", 2

    def ensure_current_tracked(self, resource_type, resource_id, current_file, prompt, **metadata):
        self.tracked.append({"resource_type": resource_type, "resource_id": resource_id, "prompt": prompt})
        return None

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"created_at": "2026-01-01T00:00:00Z"}]}


class _FakePM:
    def __init__(self, project_path: Path):
        self.project_path = project_path
        # 生产项目一律处于当前 schema，剧本一律在 episodes 账本里绑定。
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
        self.sheet_updates = []
        self.scene_asset_updates = []

    def sync_disk(self):
        """把内存态项目与剧本落盘——产物清单按磁盘上的真实项目做比对。"""

        (self.project_path / "scripts").mkdir(parents=True, exist_ok=True)
        (self.project_path / "project.json").write_text(json.dumps(self.project), encoding="utf-8")
        (self.project_path / "scripts" / "episode_1.json").write_text(json.dumps(self.script), encoding="utf-8")

    def register_artifacts(self):
        """把已落盘的资产图与分镜图登记进产物清单——未登记的产物不被编辑准入。"""

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

    def update_scene_asset(self, **kwargs):
        on_commit = kwargs.pop("on_commit", None)
        self.scene_asset_updates.append(kwargs)
        if on_commit is not None:
            on_commit(self.project_path / "scripts" / kwargs["script_filename"])

    def _update_asset_sheet(self, asset_type, project_name, name, sheet_path, *, on_commit=None):
        self.sheet_updates.append((asset_type, name, sheet_path))
        if on_commit is not None:
            on_commit(self.project_path / "project.json")


def _prepare_files(tmp_path: Path) -> Path:
    project_path = tmp_path / "projects" / "demo"
    (project_path / "characters").mkdir(parents=True, exist_ok=True)
    (project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "storyboards" / "scene_E1S01_first.png").write_bytes(b"png")
    return project_path


def _patch_common(monkeypatch, fake_pm, fake_generator, *, resolution=None, register_artifacts=True):
    """替换项目管理器与 generation context 解析缝：ctx.generator 即 fake_generator，
    image lane 携带指定 resolution。断言编辑恒声明 i2i 槽（capability == "i2i"）。"""
    if isinstance(fake_pm, _FakePM):
        # 真实 ProjectManager 的用例自己造项目与登记，这里只服务假 PM。
        if register_artifacts:
            fake_pm.register_artifacts()
        else:
            fake_pm.sync_disk()
        fake_generator.project_path = fake_pm.project_path
    monkeypatch.setattr(image_edit_tasks, "get_project_manager", lambda: fake_pm)

    async def _fake_resolve(project_name, payload, *, project, image=None, **_kwargs):
        assert image is not None and image.capability == "i2i"
        lane = ImageLaneResult(
            provider_model=ProviderModel("gemini-aistudio", "gemini-image"),
            backend_name="gemini-aistudio",
            backend_model="gemini-image",
            resolution=resolution,
        )
        return GenerationContext(generator=fake_generator, image_lane=lane)

    monkeypatch.setattr(image_edit_tasks, "resolve_generation_context", _fake_resolve)


class TestResolveCurrentImageRel:
    def test_asset_sheet_and_missing(self):
        project = {"characters": {"Alice": {"character_sheet": "characters/Alice.png"}, "Bob": {}}}
        assert resolve_current_image_rel(project, "character", "Alice") == "characters/Alice.png"
        assert resolve_current_image_rel(project, "character", "Bob") is None
        with pytest.raises(KeyError):
            resolve_current_image_rel(project, "character", "不存在")

    def test_asset_sheet_matches_across_nfc_nfd_forms(self):
        """请求里的资产名与桶 key 形态可以不同（登记闸口落 NFC，存量 key 可能是 NFD）。"""
        import unicodedata

        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd

        project = {"characters": {name_nfd: {"character_sheet": "characters/legacy.png"}}}
        assert resolve_current_image_rel(project, "character", name_nfc) == "characters/legacy.png"
        assert resolve_current_image_rel(project, "character", name_nfd) == "characters/legacy.png"

    def test_storyboard_pointer_is_the_only_evidence_of_a_current_image(self):
        """只认登记指针：没有指针就是没有产物，同名文件不构成这个分镜的归属证据。"""

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
                    "schema_version": 8,
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
                current.write_bytes(b"same-basis-replacement")
                await kwargs["before_submit"]()
                provider_reached = True
                raise AssertionError("provider must not receive a replaced formal input")

        generator = _ReplacingGenerator()
        monkeypatch.setattr(image_edit_tasks, "get_project_manager", lambda: pm)

        async def _resolve(*_args, **_kwargs):
            lane = ImageLaneResult(
                provider_model=ProviderModel("gemini-aistudio", "gemini-image"),
                backend_name="gemini-aistudio",
                backend_model="gemini-image",
                resolution=None,
            )
            return GenerationContext(generator=generator, image_lane=lane)

        monkeypatch.setattr(image_edit_tasks, "resolve_generation_context", _resolve)

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
        """未升级到当前数据版本的项目：编辑在触达供应商之前就被阻断并给出原因，
        盘上确实躺着一张同名资产图也不改变判定。"""

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
        monkeypatch.setattr(image_edit_tasks, "get_project_manager", lambda: pm)

        async def _resolve(*_args, **_kwargs):
            lane = ImageLaneResult(
                provider_model=ProviderModel("gemini-aistudio", "gemini-image"),
                backend_name="gemini-aistudio",
                backend_model="gemini-image",
                resolution=None,
            )
            return GenerationContext(generator=generator, image_lane=lane)

        monkeypatch.setattr(image_edit_tasks, "resolve_generation_context", _resolve)

        with pytest.raises(ProjectMigrationError, match="did not reach v8"):
            await execute_image_edit_task(
                "demo",
                "Alice",
                {"resource_type": "character", "prompt": "red hair"},
            )

        assert generator.image_calls == []

    async def test_registration_failure_never_exposes_an_edited_image(self, tmp_path, monkeypatch):
        from lib.artifact_activation import register_current_artifact
        from lib.media_generator import task_image_staging_path

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
                staged.write_bytes(b"edited-image")
                version = kwargs["commit_formal_output"](
                    staged,
                    current,
                    {"aspect_ratio": "16:9", "source": kwargs["source"]},
                )
                return current, version

        _patch_common(monkeypatch, pm, _Generator())
        monkeypatch.setattr(
            generation_tasks,
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
        from lib.artifact_activation import ArtifactCurrencyResolver, register_current_artifact
        from lib.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
        from lib.artifact_version_provenance import parse_image_version_basis
        from lib.media_generator import task_image_staging_path
        from lib.visual_artifact_provenance import (
            VisualReference,
            build_asset_sheet_visual_basis,
            build_storyboard_image_visual_basis,
        )
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
        provider_reference: Path | None = None

        class _Generator:
            def __init__(self) -> None:
                self.versions = manager

            async def generate_image_async(self, **kwargs):
                nonlocal provider_reference
                current.write_bytes(b"source-changed-during-await")
                provider_reference = Path(kwargs["reference_images"][0])
                assert provider_reference.read_bytes() == b"source-before-await"

                def _mutate(project):
                    project["style"] = "style-changed-during-await"
                    if resource_type == "character":
                        project["characters"][resource_id]["description"] = "description changed"

                pm.update_project("demo", _mutate)
                if resource_type == "storyboard":
                    script = pm.load_script("demo", script_file)
                    script["segments"][0]["image_prompt"] = "prompt changed"
                    pm.save_script("demo", script, script_file, validate=False)

                staged = task_image_staging_path(current, kwargs["task_id"])
                staged.write_bytes(b"edited-image")
                version = kwargs["commit_formal_output"](
                    staged,
                    current,
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
        assert provider_reference is not None and not provider_reference.exists()

        adapter.delete_entry(source_key)
        monkeypatch.setattr(versions_router, "get_project_manager", lambda: pm)
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
                "schema_version": 8,
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
                "schema_version": 8,
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
        monkeypatch.setattr("lib.artifact_activation.active_artifact_currency_resolver", _resolver)
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
        # 参考图仅当前图一张；prompt 仅编辑指令（不拼原 image_prompt）
        assert len(call["reference_images"]) == 1
        assert Path(call["reference_images"][0]).name.endswith("Alice.png")
        assert fake_generator.reference_bytes == [b"png"]
        assert not Path(call["reference_images"][0]).exists()
        assert call["prompt"] == "把头发改成红色"
        assert "原始角色 prompt" not in call["prompt"]
        # 新版本带编辑标记 metadata
        assert call["source"] == IMAGE_EDIT_VERSION_SOURCE
        assert call["resource_type"] == "characters"
        # 旧图先以中性元数据补登（不带编辑指令），保证编辑前版本可回滚
        assert fake_generator.tracked == [{"resource_type": "characters", "resource_id": "Alice", "prompt": ""}]
        # 按资源类型写回 canonical 路径；原 image_prompt 字段不被改动
        assert fake_pm.sheet_updates == [("character", "Alice", "characters/Alice.png")]
        assert fake_pm.project["characters"]["Alice"]["image_prompt"] == "原始角色 prompt"

        assert result["resource_type"] == "characters"
        assert result["resource_id"] == "Alice"
        assert result["version"] == 2
        assert result["file_path"] == "characters/Alice.png"

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
        # 底图取 generated_assets 指针（旧宫格项目路径），新图写回 canonical
        assert len(call["reference_images"]) == 1
        assert Path(call["reference_images"][0]).name.endswith("scene_E1S01_first.png")
        assert fake_generator.reference_bytes == [b"png"]
        assert not Path(call["reference_images"][0]).exists()
        assert call["resource_type"] == "storyboards"
        assert fake_pm.scene_asset_updates == [
            {
                "project_name": "demo",
                "script_filename": "episode_1.json",
                "scene_id": "E1S01",
                "asset_type": "storyboard_image",
                "asset_path": "storyboards/scene_E1S01.png",
            }
        ]
        assert result["file_path"] == "storyboards/scene_E1S01.png"

    async def test_no_current_image_raises(self, tmp_path, monkeypatch):
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator()
        _patch_common(monkeypatch, fake_pm, fake_generator)

        with pytest.raises(ValueError, match="no current image"):
            await execute_image_edit_task("demo", "祠堂", {"resource_type": "scene", "prompt": "x"})
        assert fake_generator.image_calls == []
        assert fake_pm.sheet_updates == []

    async def test_backend_failure_skips_writeback(self, tmp_path, monkeypatch):
        """失败零损失：backend 抛错时不写回资源字段（current 图指针由 MediaGenerator 保证不触碰）。
        旧图基线登记先于 backend 调用发生，与成败无关、不因失败回滚，不在本用例断言范围。
        """
        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        fake_generator = _FakeGenerator(fail=True)
        _patch_common(monkeypatch, fake_pm, fake_generator)

        with pytest.raises(RuntimeError, match="backend boom"):
            await execute_image_edit_task("demo", "Alice", {"resource_type": "character", "prompt": "x"})
        assert fake_pm.sheet_updates == []
        assert fake_pm.scene_asset_updates == []

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
    """回声 backend：model 原样反映请求 model_id（无自定义回退，与 registry 身份一致）。"""

    name: str
    model: str


class TestImageSizeResolutionEquivalence:
    """image_size 同源：``ctx.image.resolution`` 等于「先 resolve_image_backend(i2i) 得
    provider/model、再按 model_id 查 resolution」这一独立两步口径的结果。

    编辑走 i2i 槽、预置供应商 backend.model 与 registry model_id 一致（无自定义回退），
    故新路径「按 backend 实际 model 查」与两步口径「按 registry model_id 查」恒等。
    """

    @pytest.fixture
    def _ctx_env(self, monkeypatch, tmp_path):
        """真 ProjectManager（demo 项目目录）+ 回声 assemble 缝，避免 backend 构造触网。"""
        pm = ProjectManager(tmp_path / "projects")
        (tmp_path / "projects" / "demo").mkdir(parents=True)
        monkeypatch.setattr(generation_context, "get_project_manager", lambda: pm)

        async def _assemble(*, provider_id, media_type, model_id, resolver, rate_limiter=None):
            return _EchoBackend(name=provider_id, model=model_id or "default-model")

        monkeypatch.setattr(generation_context, "assemble_backend", _assemble)
        generation_context.invalidate_backend_cache()
        yield
        generation_context.invalidate_backend_cache()

    @staticmethod
    async def _old_image_size(session_factory, project, payload):
        """旧执行层口径：resolve_image_backend(i2i) 得 provider/model，再按 model_id 查 resolution。"""
        resolver = ConfigResolver(session_factory)
        async with resolver.session() as r:
            resolved = await r.resolve_image_backend(project, payload, capability="i2i")
            return await r.resolve_resolution(project, resolved.provider_id, resolved.model_id)

    async def test_model_settings_override(self, session_factory, _ctx_env):
        project = {
            "image_provider_i2i": "gemini-aistudio/gemini-image",
            "model_settings": {"gemini-aistudio/gemini-image": {"resolution": "2048x2048"}},
        }
        old = await self._old_image_size(session_factory, project, None)
        ctx = await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest(capability="i2i"))
        assert old == "2048x2048"
        assert ctx.image.resolution == old

    async def test_default_falls_back_to_none(self, session_factory, _ctx_env):
        project = {"image_provider_i2i": "gemini-aistudio/gemini-image"}
        old = await self._old_image_size(session_factory, project, None)
        ctx = await resolve_generation_context("demo", None, project=project, image=ImageLaneRequest(capability="i2i"))
        assert old is None
        assert ctx.image.resolution == old


class TestImageEditEventMapping:
    def test_emit_maps_image_edit_to_resource_events(self, tmp_path, monkeypatch):
        """编辑完成事件与同资源的生成完成事件同形状：按 payload.resource_type 派发。"""
        from server.services import generation_tasks

        project_path = _prepare_files(tmp_path)
        fake_pm = _FakePM(project_path)
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: fake_pm)

        captured = []
        monkeypatch.setattr(
            generation_tasks, "emit_project_change_batch", lambda name, changes: captured.extend(changes)
        )

        generation_tasks.emit_generation_success_batch(
            task_type="image_edit",
            project_name="demo",
            resource_id="Alice",
            payload={"resource_type": "character", "prompt": "x"},
        )
        assert captured[0]["entity_type"] == "character"
        assert captured[0]["action"] == "updated"
        # 指纹按 character 任务口径计算（characters/Alice.png 存在于磁盘）
        assert "characters/Alice.png" in captured[0]["asset_fingerprints"]

        captured.clear()
        generation_tasks.emit_generation_success_batch(
            task_type="image_edit",
            project_name="demo",
            resource_id="E1S01",
            payload={"resource_type": "storyboard", "prompt": "x", "script_file": "episode_1.json"},
        )
        assert captured[0]["entity_type"] == "segment"
        assert captured[0]["action"] == "storyboard_ready"
        assert captured[0]["script_file"] == "episode_1.json"


def test_image_edit_registered_in_task_executors():
    from server.services.generation_tasks import _TASK_EXECUTORS

    assert "image_edit" in _TASK_EXECUTORS
