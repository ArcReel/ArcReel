import asyncio
import hashlib
import json
import re
import shutil
import stat
import zipfile
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from threading import Event, Thread

import pytest

from lib.artifacts.artifact_activation import (
    ArtifactCurrencyResolver,
    stage_preserved_artifact_claims,
    take_migrated_artifact_claims,
)
from lib.artifacts.artifact_currency import read_artifact_content_digest
from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactBasisDescriptor,
    ArtifactKey,
    ArtifactManifestArchiveSnapshot,
    ArtifactManifestEntry,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.rendered_artifact import commit_rendered_artifact
from lib.artifacts.version_manager import VersionManager
from lib.bgm.library import bgm_key
from lib.bgm.service import BgmLibraryService
from lib.edit_timeline import EditTimelineService, RevisionAuthor
from lib.final_cut.basis import FinalCutVariant, final_cut_artifact_path, final_cut_key
from lib.i18n import _
from lib.infra.validation_messages import default_translate
from lib.jianying_draft.basis import jianying_draft_artifact_path, jianying_draft_key
from lib.project import deleted_projects, project_trash
from lib.project.asset_derivatives import derivative_artifact_key
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.project_migrations.v7_to_v8_artifact_manifest import migrate_v7_to_v8
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.script import script_review
from lib.script.grid.models import GridGeneration
from lib.speech.narration_delivery import TtsSynthesisSettings, build_narration_audio_basis_from_canonical_text
from server.services.project import project_archive as project_archive_module
from server.services.project.project_archive import (
    ARCHIVE_MANIFEST_NAME,
    ProjectArchiveService,
    ProjectArchiveValidationError,
)
from tests.factories import wav_bytes
from tests.legacy_project_shapes import write_legacy_named_reference_bases_project


class _RetireRecorder:
    """Record overwrite-import finalization directory names, optionally failing commit after replacement; import-route tests cover databases."""

    def __init__(self, *, fail_after_replace: bool = False) -> None:
        self.retired: list[str] = []
        self._fail_after_replace = fail_after_replace

    @contextmanager
    def __call__(self, project_dir: Path) -> Generator[str]:
        self.retired.append(project_dir.name)
        yield f"{project_dir.name}#deleted-20261025T000000Z"
        if self._fail_after_replace:
            raise RuntimeError("commit failed")


def _activate_artifact_manifest(project_dir: Path) -> None:
    """Run Artifact Manifest introduction migration, then advance projects to current schemas.

    v7→v8 creates manifests, while manifest currency readers require current project schemas;
    pair both steps or intermediate-schema projects make resolvers refuse service.
    """
    migrate_v7_to_v8(project_dir)
    migrate_project_dir(project_dir)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_episode_payload(*, video_uri: str | None = None) -> dict:
    return {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "duration_seconds": 4,
        "summary": "",
        "novel": {
            "title": "Demo",
            "chapter": "第一章",
        },
        "segments": [
            {
                "segment_id": "E1S01",
                "duration_seconds": 4,
                "segment_break": False,
                "novel_text": "原文",
                "characters_in_segment": ["Hero"],
                "scenes": [],
                "props": ["Key"],
                "image_prompt": "img",
                "video_prompt": "vid",
                "generated_assets": {
                    "storyboard_image": "storyboards/scene_E1S01.png",
                    "video_clip": "videos/scene_E1S01.mp4",
                    "video_uri": video_uri,
                    "status": "completed",
                },
            }
        ],
    }


def _create_project(
    pm: ProjectManager,
    *,
    name: str = "demo",
    title: str = "Demo",
    style: str = "水墨风格，淡彩",
    video_uri: str | None = None,
) -> Path:
    pm.create_project(name)
    pm.create_project_metadata(name, title, style, "narration")

    project_dir = pm.get_project_path(name)
    project = pm.load_project(name)
    project["style_image"] = "style_reference.png"
    project["characters"] = {
        "Hero": {
            "description": "Lead",
            "character_sheet": "characters/Hero.png",
            "reference_image": "characters/refs/Hero.png",
        }
    }
    project["props"] = {
        "Key": {
            "description": "Important prop",
            "prop_sheet": "props/Key.png",
        }
    }
    project["episodes"] = [
        {
            "episode": 1,
            "title": "第一集",
            "script_file": "scripts/episode_1.json",
        }
    ]
    pm.save_project(name, project)

    _write_text(project_dir / "source" / "chapter.txt", "source")
    _write_text(project_dir / "drafts" / "episode_1" / "script_plan_segments.md", "draft")
    (project_dir / "drafts" / "episode_2").mkdir(parents=True, exist_ok=True)
    _write_bytes(project_dir / "style_reference.png", b"png")
    _write_bytes(project_dir / "characters" / "Hero.png", b"png")
    _write_bytes(project_dir / "characters" / "refs" / "Hero.png", b"png")
    _write_bytes(project_dir / "props" / "Key.png", b"png")
    _write_bytes(project_dir / "storyboards" / "scene_E1S01.png", b"png")
    _write_bytes(project_dir / "videos" / "scene_E1S01.mp4", b"mp4")
    _write_bytes(project_dir / "output" / "final.mp4", b"mp4")
    _write_bytes(project_dir / "versions" / "storyboards" / "E1S01_v1.png", b"png")
    _write_json(
        project_dir / "scripts" / "episode_1.json",
        _build_episode_payload(video_uri=video_uri),
    )

    _write_text(project_dir / ".DS_Store", "hidden")
    _write_text(project_dir / ".hidden" / "secret.txt", "hidden")
    return project_dir


def _add_agent_runtime_symlinks(project_dir: Path) -> None:
    """Simulate legacy production layout: create agent_runtime_profile and symlinks.

    ``create_project`` materializes ``.claude`` / ``CLAUDE.md`` as actual directories/files and writes
    manifests, conflicting with legacy symlink scenarios. Remove dest first, then symlink to simulate
    old project-directory shapes persisted through legacy Docker volumes.
    """
    import shutil

    project_root = project_dir.parent.parent
    profile_claude = project_root / "agent_runtime_profile" / ".claude"
    profile_claude.mkdir(parents=True, exist_ok=True)
    (profile_claude / "settings.json").write_text("{}", encoding="utf-8")
    profile_md = project_root / "agent_runtime_profile" / "CLAUDE.md"
    profile_md.write_text("# Agent Runtime", encoding="utf-8")

    # Remove modern sync-materialized .claude/CLAUDE.md/manifest to simulate legacy symlink deployments.
    if (project_dir / ".claude").exists() or (project_dir / ".claude").is_symlink():
        if (project_dir / ".claude").is_symlink() or (project_dir / ".claude").is_file():
            (project_dir / ".claude").unlink()
        else:
            shutil.rmtree(project_dir / ".claude")
    if (project_dir / "CLAUDE.md").exists() or (project_dir / "CLAUDE.md").is_symlink():
        (project_dir / "CLAUDE.md").unlink()
    # Legacy symlinks lack manifests; retaining one makes import/export treat
    # old symlinks + new manifests as normal rather than legacy.
    manifest_path = project_dir / ".arcreel_profile_manifest.json"
    if manifest_path.exists() or manifest_path.is_symlink():
        manifest_path.unlink()

    (project_dir / ".claude").symlink_to(Path("../../agent_runtime_profile/.claude"))
    (project_dir / "CLAUDE.md").symlink_to(Path("../../agent_runtime_profile/CLAUDE.md"))


def _make_manual_zip(project_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(project_dir.rglob("*")):
            relative = item.relative_to(project_dir)
            if item.is_dir():
                info = zipfile.ZipInfo(relative.as_posix().rstrip("/") + "/")
                archive.writestr(info, b"")
            else:
                archive.write(item, arcname=relative.as_posix())


def _stage_legacy_narration_archive(pm: ProjectManager, project_dir: Path, archive_path: Path) -> None:
    """Rewrite demo into old auto-repair formats, archive it and remove original directories."""

    project = pm.load_project("demo")
    project["characters"] = {}
    pm.save_project("demo", project)

    source_dir = project_dir / "source"
    (source_dir / "chapter.txt").unlink()
    _write_text(source_dir / "1-7-0227.txt", "source")

    _write_json(
        project_dir / "versions" / "versions.json",
        {
            "videos": {
                "E1S01_1": {
                    "current_version": 1,
                    "versions": [
                        {
                            "version": 1,
                            "file": "versions/videos/E1S01_1_v1.mp4",
                            "prompt": "vp1",
                            "created_at": "2024-01-01",
                        }
                    ],
                }
            }
        },
    )
    _write_bytes(project_dir / "versions" / "videos" / "E1S01_1_v1.mp4", b"mp4-v1")

    _write_json(
        project_dir / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "novel": {
                "title": "Demo",
                "chapter": "第一章",
                "source_file": "source/1-7-0227.txt",
            },
            "segments": [
                {
                    "segment_id": "E1S01_1",
                    "duration_seconds": 4,
                    "novel_text": "原文",
                    "characters_in_segment": ["Ghost"],
                    "image_prompt": "img",
                    "video_prompt": "vid",
                    "generated_assets": {
                        "storyboard_image": "storyboards/scene_E1S01.png",
                        "video_clip": "versions/videos/E1S01_1_v9.mp4",
                        "video_uri": None,
                        "status": "completed",
                    },
                }
            ],
        },
    )

    _make_manual_zip(project_dir, archive_path)
    shutil.rmtree(project_dir)


class TestProjectArchiveService:
    def test_export_includes_full_snapshot_and_empty_dirs(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)

        archive_path, download_name = service.export_project("demo")
        assert download_name.startswith("demo-")
        assert download_name.endswith(".zip")

        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            assert f"demo/{ARCHIVE_MANIFEST_NAME}" in names
            assert "demo/project.json" in names
            assert "demo/source/chapter.txt" in names
            assert "demo/scripts/episode_1.json" in names
            assert "demo/drafts/episode_1/script_plan_segments.md" in names
            assert "demo/drafts/episode_2/" in names
            assert "demo/characters/Hero.png" in names
            assert "demo/characters/refs/Hero.png" in names
            assert "demo/props/Key.png" in names
            assert "demo/storyboards/scene_E1S01.png" in names
            assert "demo/videos/scene_E1S01.mp4" in names
            assert "demo/output/final.mp4" in names
            assert "demo/versions/storyboards/E1S01_v1.png" in names
            assert "demo/style_reference.png" in names
            assert "demo/.DS_Store" not in names
            assert "demo/.hidden/secret.txt" not in names

    @pytest.mark.parametrize("scope", ["full", "current"])
    def test_export_includes_end_frame_snapshots(self, tmp_path, scope):
        """Registered end_frames root entries automatically include ending-frame snapshots in both export scopes."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        _write_bytes(project_dir / "end_frames" / "scene_E1S01.png", b"png")
        payload = json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        payload["segments"][0]["end_frame_image"] = "end_frames/scene_E1S01.png"
        _write_json(project_dir / "scripts" / "episode_1.json", payload)

        archive_path, _ = ProjectArchiveService(pm).export_project("demo", scope=scope)
        with zipfile.ZipFile(archive_path) as archive:
            assert "demo/end_frames/scene_E1S01.png" in set(archive.namelist())

    @pytest.mark.parametrize("scope", ["full", "current"])
    async def test_edit_timelines_round_trip_through_archive(self, tmp_path, scope):
        """Edit timelines are formal content, archived and readable unchanged after import."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        timelines = EditTimelineService(pm)
        created = await timelines.create_from_script(
            "demo", episode=1, name="完整版", author=RevisionAuthor(kind="creator")
        )
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo", scope=scope)
        shutil.rmtree(pm.get_project_path("demo"))
        service.import_project_archive(archive_path, uploaded_filename="demo.zip")

        imported = await timelines.read("demo", created.timeline.id)
        assert imported == created

    @pytest.mark.parametrize("scope", ["full", "current"])
    async def test_uploaded_bgm_round_trips_through_archive_with_a_current_claim(self, tmp_path, scope):
        """BGM is byte-registered project artifacts; files/registrations/manifest entries roundtrip and remain current."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        track = await BgmLibraryService(pm).upload("demo", filename="主题曲.wav", content=wav_bytes(1.0, tone_hz=330))
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo", scope=scope)
        with zipfile.ZipFile(archive_path) as archive:
            assert f"demo/{track.file}" in set(archive.namelist())
        shutil.rmtree(pm.get_project_path("demo"))
        service.import_project_archive(archive_path, uploaded_filename="demo.zip")

        assert await BgmLibraryService(pm).list("demo") == (track,)
        project_dir = pm.get_project_path("demo")
        resolver = ArtifactCurrencyResolver(project_dir)
        assert resolver.compare(bgm_key(track.id), artifact_path=track.file).status is ArtifactStatus.CURRENT

    async def test_rendered_artifacts_stay_out_of_the_archive_and_read_missing_after_import(self, tmp_path):
        """Rendered outputs/Jianying drafts regenerate anytime: exclude files/claims from archives, avoid unknown-entry export errors, read missing after import."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        created = await EditTimelineService(pm).create_from_script(
            "demo", episode=1, name="完整版", author=RevisionAuthor(kind="creator")
        )
        timeline_id = created.timeline.id
        rendered = {
            final_cut_key(1, timeline_id, FinalCutVariant()): final_cut_artifact_path(
                1, timeline_id, FinalCutVariant()
            ),
            jianying_draft_key(1, timeline_id, "with_narration"): jianying_draft_artifact_path(
                1, timeline_id, "with_narration"
            ),
        }

        async def render(output: Path, _workspace: Path) -> None:
            await asyncio.to_thread(output.write_bytes, b"rendered")

        async def accept(_output: Path) -> None:
            return None

        for key, artifact_path in rendered.items():
            await commit_rendered_artifact(
                project_dir,
                key=key,
                artifact_path=artifact_path,
                basis=ArtifactBasis.build("test/rendered", kind_version=1, inputs={"timeline_id": timeline_id}),
                render=render,
                accept=accept,
            )
            assert ProjectArtifactManifestAdapter(project_dir).get_entry(key) is not None
        service = ProjectArchiveService(pm)

        archive_path, _diagnostics = service.export_project("demo")
        with zipfile.ZipFile(archive_path) as archive:
            names = archive.namelist()
            archive_manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
        shutil.rmtree(project_dir)
        service.import_project_archive(archive_path, uploaded_filename="demo.zip")

        assert not [name for name in names if name.startswith("demo/renders")]
        assert not {key.encode() for key in rendered} & set(archive_manifest["artifact_manifest"]["entries"])
        assert "renders" not in archive_manifest["pass_through_entries"]
        assert "renders" not in json.dumps(archive_manifest["export_diagnostics"], ensure_ascii=False)
        assert not (pm.get_project_path("demo") / "renders").exists()
        imported = ProjectArtifactManifestAdapter(pm.get_project_path("demo"))
        assert all(imported.get_entry(key) is None for key in rendered)

    def test_export_excludes_agent_runtime_symlinks(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        _add_agent_runtime_symlinks(project_dir)

        assert (project_dir / ".claude").is_symlink()
        assert (project_dir / "CLAUDE.md").is_symlink()

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")

        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            assert not any(".claude" in n for n in names)
            assert not any("CLAUDE.md" in n for n in names)
            assert "demo/project.json" in names
            assert "demo/source/chapter.txt" in names

    def test_export_excludes_agent_runtime_real_files(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        (project_dir / "CLAUDE.md").write_text("# Agent", encoding="utf-8")

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")

        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            assert not any("CLAUDE.md" in n for n in names)
            assert "demo/project.json" in names

    def test_export_excludes_broken_agent_runtime_symlinks(self, tmp_path):
        import shutil as _shutil

        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        # Remove modern artifacts, then create broken symlinks simulating old deployments with deleted profile directories.
        if (project_dir / ".claude").is_dir() and not (project_dir / ".claude").is_symlink():
            _shutil.rmtree(project_dir / ".claude")
        if (project_dir / "CLAUDE.md").exists():
            (project_dir / "CLAUDE.md").unlink()
        (project_dir / ".claude").symlink_to(Path("../../nonexistent_profile/.claude"))
        (project_dir / "CLAUDE.md").symlink_to(Path("../../nonexistent_profile/CLAUDE.md"))

        assert (project_dir / ".claude").is_symlink()
        assert not (project_dir / ".claude").exists()

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")

        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            assert not any(".claude" in n for n in names)
            assert not any("CLAUDE.md" in n for n in names)

    def test_import_official_export_round_trip(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        (project_dir / MANIFEST_FILENAME).unlink(missing_ok=True)
        _activate_artifact_manifest(project_dir)
        startup_manifest = json.loads((project_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo")
        with zipfile.ZipFile(archive_path) as archive:
            assert not any(name.endswith(f"/{MANIFEST_FILENAME}") for name in archive.namelist())
        shutil.rmtree(pm.get_project_path("demo"))

        result = service.import_project_archive(
            archive_path,
            uploaded_filename="demo.zip",
        )

        assert result.project_name == "demo"
        assert result.conflict_resolution == "none"
        assert (pm.get_project_path("demo") / "videos" / "scene_E1S01.mp4").exists()
        assert (pm.get_project_path("demo") / "drafts" / "episode_2").is_dir()
        imported_manifest = json.loads((pm.get_project_path("demo") / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert imported_manifest == startup_manifest

    def test_a_healthy_project_round_trips_without_asking_for_confirmation(self, tmp_path):
        """Healthy current-code exports reimport without confirmation/reports; unversioned videos and normal grids/drafts are expected, not omitted content."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        assert pm.load_project("demo")["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
        assert not (project_dir / "versions" / "versions.json").exists()
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo")
        shutil.rmtree(project_dir)
        result = service.import_project_archive(archive_path, uploaded_filename="demo.zip")

        assert result.conflict_resolution == "none"
        assert load_migration_report(pm.get_project_path(result.project_name)) is None

    def test_official_round_trip_accepts_project_without_style(self, tmp_path):
        """Projects without chosen styles export/import normally; diagnostics/validation never treat empty styles as defects."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm, style="")
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo")
        shutil.rmtree(pm.get_project_path("demo"))
        result = service.import_project_archive(archive_path, uploaded_filename="demo.zip")

        assert result.project_name == "demo"
        assert pm.load_project("demo")["style"] == ""

    def test_import_rejects_official_manifest_claim_when_formal_bytes_were_replaced(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")
        tampered_path = tmp_path / "tampered.zip"
        with (
            zipfile.ZipFile(archive_path) as source,
            zipfile.ZipFile(
                tampered_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as target,
        ):
            for member in source.infolist():
                content = source.read(member)
                if member.filename == "demo/characters/Hero.png":
                    content = b"replaced-formal-bytes"
                target.writestr(member, content)
        shutil.rmtree(project_dir)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(tampered_path, uploaded_filename="tampered.zip")

        assert any(item.code == "artifact_activation_failed" for item in exc_info.value.diagnostics.blocking)
        assert not (pm.projects_dir / "demo").exists()

    @staticmethod
    def _rewrite_archive(source_path: Path, target_path: Path, *, rewrite) -> None:
        """Rewrite archive members individually by name, copying others unchanged."""
        with (
            zipfile.ZipFile(source_path) as source,
            zipfile.ZipFile(target_path, "w", compression=zipfile.ZIP_DEFLATED) as target,
        ):
            for member in source.infolist():
                target.writestr(member, rewrite(member.filename, source.read(member)))

    def test_import_reprojects_a_claim_whose_archived_digest_folded_line_endings(self, tmp_path):
        """Text-mode envelope content_digest with irreversible CRLF→LF normalization cannot bind frozen claims; backfill current projections."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)
        key = ArtifactKey.episode_script(1)
        before = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
        assert before is not None
        project = pm.load_project("demo")
        project["overview"] = {"synopsis": "改写后的项目概述"}
        _write_json(project_dir / "project.json", project)
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path="scripts/episode_1.json").status
            is ArtifactStatus.STALE
        )

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")
        legacy_path = tmp_path / "legacy.zip"
        crlf_bytes: dict[str, bytes] = {}

        def _write_script_with_crlf(name: str, content: bytes) -> bytes:
            # Text-mode exports use platform newlines, but envelope digests hash normalized LF bytes.
            if name != "demo/scripts/episode_1.json":
                return content
            crlf_bytes[name] = content.replace(b"\n", b"\r\n")
            return crlf_bytes[name]

        self._rewrite_archive(archive_path, legacy_path, rewrite=_write_script_with_crlf)
        with zipfile.ZipFile(legacy_path) as archive:
            envelope = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            archived_digest = envelope["artifact_manifest"]["entries"][key.encode()]["content_digest"]
        member = crlf_bytes["demo/scripts/episode_1.json"]
        assert archived_digest == hashlib.sha256(member.replace(b"\r\n", b"\n")).hexdigest()
        assert archived_digest != hashlib.sha256(member).hexdigest()
        shutil.rmtree(project_dir)

        service.import_project_archive(legacy_path, uploaded_filename="legacy.zip")

        imported_dir = pm.get_project_path("demo")
        assert (imported_dir / "scripts" / "episode_1.json").read_bytes() == member
        imported = ProjectArtifactManifestAdapter(imported_dir).get_entry(key)
        assert imported is not None
        assert imported != before
        assert (
            ArtifactCurrencyResolver(imported_dir).compare(key, artifact_path="scripts/episode_1.json").status
            is ArtifactStatus.CURRENT
        )

    def test_import_reprojects_a_claim_whose_archived_digest_covers_only_the_bytes_before_0x1a(self, tmp_path):
        """Envelope content_digest ending at first 0x1A proves prefixes only; PNGs with replaced trailing bytes cannot retain frozen claims, backfilling current projections."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        _write_bytes(project_dir / "characters" / "Hero.png", b"\x89PNG\r\n\x1a\nIHDR\r\n")
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)
        key = ArtifactKey.asset_sheet("character", "Hero")
        before = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
        assert before is not None
        project = pm.load_project("demo")
        project["characters"]["Hero"]["description"] = "Changed after generation"
        _write_json(project_dir / "project.json", project)
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path="characters/Hero.png").status
            is ArtifactStatus.STALE
        )
        swapped_bytes = b"\x89PNG\r\n\x1a\nIDAT-other-image\r\n"

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")
        legacy_path = tmp_path / "legacy.zip"

        def _truncate_digest_and_swap_image(name: str, content: bytes) -> bytes:
            if name == "demo/characters/Hero.png":
                return swapped_bytes
            if name != f"demo/{ARCHIVE_MANIFEST_NAME}":
                return content
            envelope = json.loads(content)
            entry = envelope["artifact_manifest"]["entries"][key.encode()]
            entry["content_digest"] = hashlib.sha256(b"\x89PNG\n").hexdigest()
            return json.dumps(envelope, ensure_ascii=False).encode("utf-8")

        self._rewrite_archive(archive_path, legacy_path, rewrite=_truncate_digest_and_swap_image)
        shutil.rmtree(project_dir)

        service.import_project_archive(legacy_path, uploaded_filename="legacy.zip")

        imported_dir = pm.get_project_path("demo")
        assert (imported_dir / "characters" / "Hero.png").read_bytes() == swapped_bytes
        imported = ProjectArtifactManifestAdapter(imported_dir).get_entry(key)
        assert imported is not None
        assert imported != before
        assert (
            ArtifactCurrencyResolver(imported_dir).compare(key, artifact_path="characters/Hero.png").status
            is ArtifactStatus.CURRENT
        )

    def test_official_round_trip_preserves_a_stale_asset_claim(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)

        key = ArtifactKey.asset_sheet("character", "Hero")
        before = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
        assert before is not None
        project = pm.load_project("demo")
        project["characters"]["Hero"]["description"] = "Changed after generation"
        _write_json(project_dir / "project.json", project)
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path="characters/Hero.png").status
            is ArtifactStatus.STALE
        )

        archive_path, _ = ProjectArchiveService(pm).export_project("demo")
        with zipfile.ZipFile(archive_path) as archive:
            archive_manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            assert archive_manifest["artifact_manifest"]["entries"][key.encode()]["basis_digest"] == before.basis_digest
            assert not any(name.endswith(f"/{MANIFEST_FILENAME}") for name in archive.namelist())
        shutil.rmtree(project_dir)

        ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="demo.zip")

        imported_dir = pm.get_project_path("demo")
        assert ProjectArtifactManifestAdapter(imported_dir).get_entry(key) == before
        assert (
            ArtifactCurrencyResolver(imported_dir).compare(key, artifact_path="characters/Hero.png").status
            is ArtifactStatus.STALE
        )

    def test_official_round_trip_preserves_a_stale_script_claim(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _write_text(project_dir / "source" / "episode_1.txt", "原文")
        script_plan_path = project_dir / "drafts" / "episode_1" / "script_plan_segments.json"
        _write_json(script_plan_path, {"segments": [{"segment_id": "E1S01", "text": "原文"}]})
        _activate_artifact_manifest(project_dir)

        key = ArtifactKey.episode_script(1)
        before = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
        assert before is not None
        # Formal scripts do not depend on planning; deleting planning removes its registration only, retaining script registrations/currency.
        assert script_review.delete_script_plan_file(project_dir, 1, script_plan_path)
        assert ProjectArtifactManifestAdapter(project_dir).get_entry(ArtifactKey.episode_script_plan(1)) is None
        assert ProjectArtifactManifestAdapter(project_dir).get_entry(key) == before
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path="scripts/episode_1.json").status
            is ArtifactStatus.CURRENT
        )
        project = pm.load_project("demo")
        project["overview"] = {"synopsis": "改写后的项目概述"}
        _write_json(project_dir / "project.json", project)
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path="scripts/episode_1.json").status
            is ArtifactStatus.STALE
        )
        archive_path, _ = ProjectArchiveService(pm).export_project("demo")
        with zipfile.ZipFile(archive_path) as archive:
            archive_manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            entry = archive_manifest["artifact_manifest"]["entries"][key.encode()]
            content_digest = hashlib.sha256(archive.read(f"demo/{before.artifact_path}")).hexdigest()
            assert entry == {
                "artifact_path": before.artifact_path,
                "basis_digest": before.basis_digest,
                "content_digest": content_digest,
            }
        shutil.rmtree(project_dir)

        ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="demo.zip")

        imported_dir = pm.get_project_path("demo")
        assert ProjectArtifactManifestAdapter(imported_dir).get_entry(key) == before
        assert (
            ArtifactCurrencyResolver(imported_dir).compare(key, artifact_path="scripts/episode_1.json").status
            is ArtifactStatus.STALE
        )

    def test_official_export_preserves_a_grid_claim_after_failed_regeneration(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        project["grid_storyboard"] = True
        _write_json(project_dir / "project.json", project)
        grid = GridGeneration.create(
            episode=1,
            script_file="episode_1.json",
            scene_ids=["E1S01"],
            rows=2,
            cols=2,
            grid_size="grid_4",
            provider="provider",
            model="model",
            video_aspect_ratio="9:16",
            prompt="grid",
        )
        grid.status = "completed"
        grid.grid_image_path = f"grids/{grid.id}.png"
        _write_json(project_dir / "grids" / f"{grid.id}.json", grid.to_dict())
        _write_bytes(project_dir / "grids" / f"{grid.id}.png", b"grid-image")
        _activate_artifact_manifest(project_dir)
        key = ArtifactKey.episode_grid(1, grid.id)
        before = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
        assert before is not None

        grid.status = "failed"
        grid.error_message = "provider failed during regeneration"
        _write_json(project_dir / "grids" / f"{grid.id}.json", grid.to_dict())

        archive_path, _ = ProjectArchiveService(pm).export_project("demo")

        with zipfile.ZipFile(archive_path) as archive:
            archive_manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
        assert archive_manifest["artifact_manifest"]["entries"][key.encode()] == {
            "artifact_path": before.artifact_path,
            "basis_digest": before.basis_digest,
            "content_digest": hashlib.sha256(b"grid-image").hexdigest(),
        }

    def test_export_retries_when_formal_bytes_and_manifest_change_during_snapshot(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)

        key = ArtifactKey.asset_sheet("character", "Hero")
        artifact_path = project_dir / "characters" / "Hero.png"
        adapter = ProjectArtifactManifestAdapter(project_dir)
        service = ProjectArchiveService(pm)
        original_copy = service._copy_visible_tree
        copy_count = 0

        def _copy_then_commit(source_dir: Path, target_dir: Path) -> tuple[tuple[str, str], ...]:
            nonlocal copy_count
            copy_count += 1
            copied = original_copy(source_dir, target_dir)
            if copy_count == 1:
                artifact_path.write_bytes(b"new-formal-bytes")
                adapter.put_entry(
                    key,
                    ArtifactManifestEntry(
                        artifact_path="characters/Hero.png",
                        basis_digest=f"sha256-v1:{'f' * 64}",
                    ),
                )
            return copied

        monkeypatch.setattr(service, "_copy_visible_tree", _copy_then_commit)

        archive_path, _ = service.export_project("demo")

        assert copy_count == 2
        with zipfile.ZipFile(archive_path) as archive:
            assert archive.read("demo/characters/Hero.png") == b"new-formal-bytes"
            archive_manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
        assert archive_manifest["artifact_manifest"]["entries"][key.encode()] == {
            "artifact_path": "characters/Hero.png",
            "basis_digest": f"sha256-v1:{'f' * 64}",
            "content_digest": hashlib.sha256(b"new-formal-bytes").hexdigest(),
        }

    def test_export_waits_for_a_formal_commit_before_snapshotting(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)

        key = ArtifactKey.asset_sheet("character", "Hero")
        artifact_path = project_dir / "characters" / "Hero.png"
        versions_path = project_dir / "versions" / "versions.json"
        adapter = ProjectArtifactManifestAdapter(project_dir)
        service = ProjectArchiveService(pm)
        formal_midpoint = Event()
        release_formal_commit = Event()
        copied_while_formal_commit_was_open = Event()
        failures: list[BaseException] = []
        archive_result: list[Path] = []

        original_copy = service._copy_visible_tree

        def _observe_copy(source_dir: Path, target_dir: Path) -> tuple[tuple[str, str], ...]:
            if formal_midpoint.is_set() and not release_formal_commit.is_set():
                copied_while_formal_commit_was_open.set()
            return original_copy(source_dir, target_dir)

        def _formal_commit() -> None:
            try:
                with project_metadata_lock(project_dir):
                    artifact_path.write_bytes(b"new-formal-bytes")
                    _write_json(versions_path, {"characters": {"Hero": {"current_version": 2, "versions": []}}})
                    formal_midpoint.set()
                    if not release_formal_commit.wait(timeout=5):
                        raise TimeoutError("archive did not finish observing the held formal commit")
                    adapter.put_entry(
                        key,
                        ArtifactManifestEntry(
                            artifact_path="characters/Hero.png",
                            basis_digest=f"sha256-v1:{'f' * 64}",
                        ),
                    )
            except BaseException as exc:  # pragma: no cover - asserted through the parent thread
                failures.append(exc)

        def _export() -> None:
            try:
                archive_result.append(service.export_project("demo")[0])
            except BaseException as exc:  # pragma: no cover - asserted through the parent thread
                failures.append(exc)

        monkeypatch.setattr(service, "_copy_visible_tree", _observe_copy)
        writer = Thread(target=_formal_commit)
        exporter = Thread(target=_export)
        writer.start()
        assert formal_midpoint.wait(timeout=5)
        exporter.start()
        copied_early = copied_while_formal_commit_was_open.wait(timeout=0.5)
        release_formal_commit.set()
        writer.join(timeout=5)
        exporter.join(timeout=5)

        assert not writer.is_alive()
        assert not exporter.is_alive()
        assert failures == []
        assert copied_early is False
        with zipfile.ZipFile(archive_result[0]) as archive:
            assert archive.read("demo/characters/Hero.png") == b"new-formal-bytes"
            archive_manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
        assert archive_manifest["artifact_manifest"]["entries"][key.encode()] == {
            "artifact_path": "characters/Hero.png",
            "basis_digest": f"sha256-v1:{'f' * 64}",
            "content_digest": hashlib.sha256(b"new-formal-bytes").hexdigest(),
        }

    def test_export_retries_when_a_visible_file_disappears_during_copy(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        original_copy = service._copy_visible_tree
        copy_count = 0

        def _interrupt_first_copy(source_dir: Path, target_dir: Path) -> tuple[tuple[str, str], ...]:
            nonlocal copy_count
            copy_count += 1
            if copy_count == 1:
                target_dir.mkdir(parents=True)
                (target_dir / "partial.txt").write_text("partial", encoding="utf-8")
                raise FileNotFoundError("formal file changed during copy")
            return original_copy(source_dir, target_dir)

        monkeypatch.setattr(service, "_copy_visible_tree", _interrupt_first_copy)

        archive_path, _ = service.export_project("demo")

        assert copy_count == 2
        with zipfile.ZipFile(archive_path) as archive:
            assert "demo/partial.txt" not in archive.namelist()

    def test_current_export_drops_non_typed_records_with_omitted_snapshots(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        canonical_files = {
            "storyboards": ("E1S01", project_dir / "storyboards" / "scene_E1S01.png"),
            "characters": ("Hero", project_dir / "characters" / "Hero.png"),
            "scenes": ("Temple", project_dir / "scenes" / "Temple.png"),
            "props": ("Key", project_dir / "props" / "Key.png"),
        }
        _write_bytes(canonical_files["scenes"][1], b"scene")
        versions = VersionManager(project_dir)
        selected_files: dict[str, str] = {}
        for resource_type, (resource_id, current_file) in canonical_files.items():
            selected_version = versions.add_version(resource_type, resource_id, "current", source_file=current_file)
            selected_files[resource_type] = next(
                record["file"]
                for record in versions.get_versions(resource_type, resource_id)["versions"]
                if record["version"] == selected_version
            )

        archive_path, _ = ProjectArchiveService(pm).export_project("demo", scope="current")

        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            payload = json.loads(archive.read("demo/versions/versions.json"))
        for resource_type, selected_file in selected_files.items():
            assert resource_type not in payload
            assert f"demo/{selected_file}" not in names

        shutil.rmtree(project_dir)
        ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="demo.zip")
        imported_versions = VersionManager(pm.get_project_path("demo"))
        for resource_type, (resource_id, current_file) in canonical_files.items():
            assert imported_versions.get_versions(resource_type, resource_id) == {
                "current_version": 0,
                "versions": [],
            }
            assert (pm.get_project_path("demo") / current_file.relative_to(project_dir)).is_file()

    def test_official_round_trip_rekeys_claims_to_repaired_formal_paths(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)

        key = ArtifactKey.asset_sheet("character", "Hero")
        adapter = ProjectArtifactManifestAdapter(project_dir)
        before = adapter.get_entry(key)
        assert before is not None
        _write_bytes(project_dir / "characters" / "legacy.png", b"legacy")
        project = pm.load_project("demo")
        project["characters"]["Hero"]["character_sheet"] = "characters/legacy.png"
        project["characters"]["Hero"]["description"] = "Changed after generation"
        _write_json(project_dir / "project.json", project)
        adapter.put_entry(
            key,
            ArtifactManifestEntry(
                artifact_path="characters/legacy.png",
                basis_digest=before.basis_digest,
            ),
        )

        archive_path, _ = ProjectArchiveService(pm).export_project("demo")
        with zipfile.ZipFile(archive_path) as archive:
            archive_manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            archived_entry = archive_manifest["artifact_manifest"]["entries"][key.encode()]
            content_digest = hashlib.sha256(archive.read("demo/characters/Hero.png")).hexdigest()
            assert archived_entry == {
                "artifact_path": "characters/Hero.png",
                "basis_digest": before.basis_digest,
                "content_digest": content_digest,
            }
        shutil.rmtree(project_dir)

        ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="demo.zip")

        imported_dir = pm.get_project_path("demo")
        assert ProjectArtifactManifestAdapter(imported_dir).get_entry(key) == ArtifactManifestEntry(
            artifact_path="characters/Hero.png",
            basis_digest=before.basis_digest,
        )
        assert (
            ArtifactCurrencyResolver(imported_dir).compare(key, artifact_path="characters/Hero.png").status
            is ArtifactStatus.STALE
        )

    def test_deleted_asset_forgets_its_claim_before_an_official_round_trip(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["characters"][" Hero "] = project["characters"].pop("Hero")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)
        key = ArtifactKey.asset_sheet("character", "Hero")
        assert ProjectArtifactManifestAdapter(project_dir).get_entry(key) is not None

        pm.delete_asset("demo", "characters", " Hero ")

        assert " Hero " not in pm.load_project("demo")["characters"]
        assert ProjectArtifactManifestAdapter(project_dir).get_entry(key) is None
        archive_path, _ = ProjectArchiveService(pm).export_project("demo")
        shutil.rmtree(project_dir)

        ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="demo.zip")

        imported_dir = pm.get_project_path("demo")
        assert ProjectArtifactManifestAdapter(imported_dir).get_entry(key) is None

    def test_asset_delete_manifest_failure_restores_exact_project_and_claim(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)
        project_file = project_dir / "project.json"
        key = ArtifactKey.asset_sheet("character", "Hero")
        before_project = project_file.read_bytes()
        before_entry = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
        assert before_entry is not None
        original_delete = ProjectArtifactManifestAdapter.delete_entry

        def _delete_then_fail(self, candidate):
            changed = original_delete(self, candidate)
            if candidate == key:
                raise RuntimeError("manifest delete failed")
            return changed

        monkeypatch.setattr(ProjectArtifactManifestAdapter, "delete_entry", _delete_then_fail)

        with pytest.raises(RuntimeError, match="manifest delete failed"):
            pm.delete_asset("demo", "characters", "Hero")

        assert project_file.read_bytes() == before_project
        assert ProjectArtifactManifestAdapter(project_dir).get_entry(key) == before_entry

    def test_official_round_trip_preserves_an_empty_manifest_snapshot(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)

        adapter = ProjectArtifactManifestAdapter(project_dir)
        for key in tuple(adapter.snapshot_entries()):
            assert adapter.delete_entry(key)
        assert not (project_dir / MANIFEST_FILENAME).exists()

        archive_path, _ = ProjectArchiveService(pm).export_project("demo")
        with zipfile.ZipFile(archive_path) as archive:
            archive_manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            assert archive_manifest["artifact_manifest"]["entries"] == {}
        shutil.rmtree(project_dir)

        ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="demo.zip")

        imported_dir = pm.get_project_path("demo")
        key = ArtifactKey.asset_sheet("character", "Hero")
        assert ProjectArtifactManifestAdapter(imported_dir).snapshot_entries() == {}
        assert (
            ArtifactCurrencyResolver(imported_dir).compare(key, artifact_path="characters/Hero.png").status
            is ArtifactStatus.MISSING
        )

    def test_unmigrated_export_omits_the_envelope_so_import_backfills(self, tmp_path):
        """Projects outside manifest systems export without envelopes; imports migrate and self-prove backfills normally.

        Their manifests are necessarily empty, indistinguishable from artifact-free projects in empty envelopes;
        faithful empty-manifest restoration would classify every generated artifact missing.
        """

        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        (project_dir / MANIFEST_FILENAME).unlink(missing_ok=True)

        archive_path, _ = ProjectArchiveService(pm).export_project("demo")
        with zipfile.ZipFile(archive_path) as archive:
            archive_manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            assert "artifact_manifest" not in archive_manifest
        shutil.rmtree(project_dir)

        ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="demo.zip")

        imported_dir = pm.get_project_path("demo")
        key = ArtifactKey.asset_sheet("character", "Hero")
        assert ProjectArtifactManifestAdapter(imported_dir).get_entry(key) is not None
        assert (
            ArtifactCurrencyResolver(imported_dir).compare(key, artifact_path="characters/Hero.png").status
            is ArtifactStatus.CURRENT
        )

    def test_current_export_keeps_selected_typed_snapshot_for_import_activation(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        script_path = project_dir / "scripts" / "episode_1.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        script["segments"][0]["generated_assets"]["narration_audio"] = "audio/segment_E1S01.wav"
        _write_json(script_path, script)

        audio = project_dir / "audio" / "segment_E1S01.wav"
        _write_bytes(audio, b"typed-audio")
        settings = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None)
        descriptor = ArtifactBasisDescriptor.from_basis(
            build_narration_audio_basis_from_canonical_text("原文", settings)
        )
        manager = VersionManager(project_dir)
        selected_version = manager.add_version(
            "audio",
            "E1S01",
            "原文",
            source_file=audio,
            artifact_episode=1,
            artifact_audio_basis=descriptor.to_dict(),
            execution_script_file="episode_1.json",
            tts_actual_duration_seconds=4.0,
            tts_provider_id=settings.provider_id,
            tts_model_id=settings.model_id,
            tts_voice=settings.voice,
            tts_speed=settings.speed,
            tts_basis_digest=descriptor.digest,
        )
        record = next(
            item for item in manager.get_versions("audio", "E1S01")["versions"] if item["version"] == selected_version
        )
        selected_snapshot = record["file"]

        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)
        key = ArtifactKey.episode_audio(1, "E1S01").encode()
        startup_manifest = json.loads((project_dir / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        assert key in startup_manifest["entries"]

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo", scope="current")
        with zipfile.ZipFile(archive_path) as archive:
            assert f"demo/{selected_snapshot}" in archive.namelist()

        shutil.rmtree(project_dir)
        service.import_project_archive(archive_path, uploaded_filename="demo.zip")
        imported_manifest = json.loads((pm.get_project_path("demo") / MANIFEST_FILENAME).read_text(encoding="utf-8"))
        imported_entry = imported_manifest["entries"][key]
        claim_fields = ("artifact_path", "basis_digest")
        startup_entry = startup_manifest["entries"][key]
        assert {field: imported_entry[field] for field in claim_fields} == {
            field: startup_entry[field] for field in claim_fields
        }
        imported_audio = pm.get_project_path("demo") / "audio" / "segment_E1S01.wav"
        assert (
            imported_entry["content_digest"],
            imported_entry["content_size"],
            imported_entry["content_mtime_ns"],
        ) == (
            hashlib.sha256(imported_audio.read_bytes()).hexdigest(),
            imported_audio.stat().st_size,
            imported_audio.stat().st_mtime_ns,
        )

    def test_official_round_trip_preserves_a_stale_typed_audio_claim(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        script_path = project_dir / "scripts" / "episode_1.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        script["segments"][0]["generated_assets"]["narration_audio"] = "audio/segment_E1S01.wav"
        _write_json(script_path, script)
        audio = project_dir / "audio" / "segment_E1S01.wav"
        _write_bytes(audio, b"typed-audio")
        settings = TtsSynthesisSettings("dashscope", "qwen3-tts-flash", "Cherry", None)
        descriptor = ArtifactBasisDescriptor.from_basis(
            build_narration_audio_basis_from_canonical_text("原文", settings)
        )
        VersionManager(project_dir).add_version(
            "audio",
            "E1S01",
            "原文",
            source_file=audio,
            artifact_episode=1,
            artifact_audio_basis=descriptor.to_dict(),
            execution_script_file="episode_1.json",
            tts_actual_duration_seconds=4.0,
            tts_provider_id=settings.provider_id,
            tts_model_id=settings.model_id,
            tts_voice=settings.voice,
            tts_speed=settings.speed,
            tts_basis_digest=descriptor.digest,
        )
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        _activate_artifact_manifest(project_dir)
        key = ArtifactKey.episode_audio(1, "E1S01")
        frozen = ProjectArtifactManifestAdapter(project_dir).get_entry(key)
        assert frozen is not None

        script["segments"][0]["novel_text"] = "changed after synthesis"
        _write_json(script_path, script)
        assert (
            ArtifactCurrencyResolver(project_dir).compare(key, artifact_path=frozen.artifact_path).status
            is ArtifactStatus.STALE
        )

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo", scope="current")
        shutil.rmtree(project_dir)
        service.import_project_archive(archive_path, uploaded_filename="demo.zip")

        imported_dir = pm.get_project_path("demo")
        assert ProjectArtifactManifestAdapter(imported_dir).get_entry(key) == frozen
        assert (
            ArtifactCurrencyResolver(imported_dir).compare(key, artifact_path=frozen.artifact_path).status
            is ArtifactStatus.STALE
        )

    def test_import_reports_artifact_activation_failure_as_archive_validation(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        archive_path = tmp_path / "manual.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        monkeypatch.setattr(
            project_archive_module,
            "ensure_imported_artifact_target_state",
            lambda _path, **_kwargs: (_ for _ in ()).throw(ValueError("injected activation failure")),
        )

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="manual.zip")

        assert any(item.code == "artifact_activation_failed" for item in exc_info.value.diagnostics.blocking)

    def test_import_reports_migration_failure_with_its_reason(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 7
        _write_json(project_dir / "project.json", project)
        (project_dir / "scripts" / "episode_1.json").write_text("{", encoding="utf-8")
        archive_path = tmp_path / "broken-v7.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="broken-v7.zip")

        assert exc_info.value.status_code == 400
        [failure] = [item for item in exc_info.value.diagnostics.blocking if item.code == "project_migration_failed"]
        assert failure.message.params["reason"]
        assert failure.location is not None
        assert "scripts/episode_1.json" in failure.location
        assert pm.list_projects() == []

    def test_import_reports_migration_failure_outside_activation_with_its_reason(self, tmp_path):
        """Any migration-step failure rejects imports with reasons, never unexplained server errors."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["schema_version"] = 9
        _write_json(project_dir / "project.json", project)
        (project_dir / "scripts" / "episode_1.json").write_text("{", encoding="utf-8")
        archive_path = tmp_path / "broken-v9.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="broken-v9.zip")

        assert exc_info.value.status_code == 400
        rendered = exc_info.value.render_errors()
        assert any("无法升级到当前版本" in error for error in rendered)

    def test_import_manual_zip_without_manifest(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "manual.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(
            archive_path,
            uploaded_filename="manual.zip",
        )

        assert result.project["title"] == "Demo"
        assert result.project_name != "demo"
        assert (pm.get_project_path(result.project_name) / "project.json").exists()

    @pytest.mark.parametrize("schema_version", [0, 1])
    def test_import_minimal_legacy_archive_has_no_backup_warnings(self, tmp_path, schema_version):
        pm = ProjectManager(tmp_path / "projects")
        archive_path = tmp_path / "legacy.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr(
                "project.json",
                json.dumps({"schema_version": schema_version, "title": "Demo", "content_mode": "narration"}),
            )

        result = ProjectArchiveService(pm).import_project_archive(archive_path, uploaded_filename="legacy.zip")

        assert result.project["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
        assert list(pm.get_project_path(result.project_name).glob("project.json.bak.v*"))
        assert not any(warning.key == "val_unrecognized_entry" for warning in result.warnings)

    def test_import_legacy_v1_archive_runs_migration(self, tmp_path):
        """Old archives imported after startup (schema_version=1 + legacy image_backend) run full migration chains at import."""
        import json as _json

        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)

        # Restore v1 shapes + legacy image_backend to simulate old exports.
        pj = project_dir / "project.json"
        data = _json.loads(pj.read_text(encoding="utf-8"))
        data["schema_version"] = 1
        data["image_backend"] = "vertex/imagen-3"
        data.pop("image_provider_t2i", None)
        data.pop("image_provider_i2i", None)
        pj.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")

        archive_path = tmp_path / "legacy.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="legacy.zip")

        from lib.project.project_migrations import CURRENT_SCHEMA_VERSION

        installed = _json.loads((pm.get_project_path(result.project_name) / "project.json").read_text(encoding="utf-8"))
        assert installed["schema_version"] == CURRENT_SCHEMA_VERSION
        assert installed["image_provider_t2i"] == "gemini-vertex/imagen-3"
        assert installed["image_provider_i2i"] == "gemini-vertex/imagen-3"  # Split image_backend into two slots.
        assert "image_backend" not in installed

    def test_import_v5_archive_migrates_conflicting_asset_namespace(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)

        project_file = project_dir / "project.json"
        project = json.loads(project_file.read_text(encoding="utf-8"))
        project["schema_version"] = 5
        project["scenes"] = {"Hero": {"description": "same-named scene", "scene_sheet": "scenes/Hero.png"}}
        project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
        _write_bytes(project_dir / "scenes" / "Hero.png", b"scene")
        script_file = project_dir / "scripts" / "episode_1.json"
        script = json.loads(script_file.read_text(encoding="utf-8"))
        script["segments"][0]["scenes"] = ["Hero"]
        script_file.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

        archive_path = tmp_path / "v5-conflict.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="v5-conflict.zip")

        installed_dir = pm.get_project_path(result.project_name)
        installed = json.loads((installed_dir / "project.json").read_text(encoding="utf-8"))
        migrated_script = json.loads((installed_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        assert installed["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION
        assert list(installed["characters"]) == ["Hero"]
        assert list(installed["scenes"]) == ["Hero_scene"]
        assert migrated_script["segments"][0]["scenes"] == ["Hero_scene"]
        assert (installed_dir / "scenes" / "Hero_scene.png").is_file()

    def test_import_rejects_missing_project_json(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        service = ProjectArchiveService(pm)
        archive_path = tmp_path / "missing-project-json.zip"

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("demo/source/chapter.txt", "source")

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="broken.zip")

        assert exc_info.value.detail.render(default_translate) == "导入包校验失败"
        assert any("project.json" in error for error in exc_info.value.render_errors())

    @pytest.mark.parametrize(
        "snapshot",
        [
            "../outside/E1S01_v1.png",
            "/tmp/E1S01_v1.png",
            "versions\\storyboards\\E1S01_v1.png",
            "versions/characters/E1S01_v1.png",
            "versions/storyboards/E1S01_v1.txt",
            "versions/storyboards/E1S01\x00_v1.png",
        ],
    )
    def test_import_rejects_unmanaged_version_snapshot_paths(self, tmp_path, snapshot):
        self._assert_import_rejects_storyboard_version_record(tmp_path, {"version": 1, "file": snapshot})

    @pytest.mark.parametrize("record", ["versions/storyboards/E1S01_v1.png", None, 1])
    def test_import_rejects_non_object_version_records(self, tmp_path, record):
        self._assert_import_rejects_storyboard_version_record(tmp_path, record)

    @pytest.mark.parametrize(
        ("storyboards", "location"),
        [
            ([], "storyboards"),
            ({"E1S01": "versions/storyboards/E1S01_v1.png"}, "storyboards/E1S01"),
            ({"E1S01": {"current_version": 1, "versions": {"1": {"version": 1}}}}, "storyboards/E1S01"),
        ],
    )
    def test_import_rejects_malformed_version_history_containers(self, tmp_path, storyboards, location):
        self._assert_import_rejects_versions_payload(tmp_path, {"storyboards": storyboards}, location)

    @classmethod
    def _assert_import_rejects_storyboard_version_record(cls, tmp_path, record):
        cls._assert_import_rejects_versions_payload(
            tmp_path, {"storyboards": {"E1S01": {"current_version": 1, "versions": [record]}}}, "storyboards/E1S01"
        )

    @staticmethod
    def _assert_import_rejects_versions_payload(tmp_path, payload, location):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)
        _write_json(project_dir / "versions" / "versions.json", payload)
        archive_path = tmp_path / "unmanaged-snapshot.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="unmanaged-snapshot.zip")

        assert exc_info.value.detail.render(default_translate) == "导入包校验失败"
        assert any(error.startswith(f"{location}:") for error in exc_info.value.render_errors())
        assert list(pm.projects_dir.iterdir()) == []

    def test_import_drops_version_buckets_of_unknown_resource_types(self, tmp_path, caplog):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)
        storyboards = {
            "E1S01": {"current_version": 1, "versions": [{"version": 1, "file": "versions/storyboards/E1S01_v1.png"}]}
        }
        _write_json(
            project_dir / "versions" / "versions.json",
            {
                "storyboards": storyboards,
                "clues": {
                    "Key": {
                        "current_version": 1,
                        "versions": [{"version": 1, "file": "versions/clues/Key_v1_20260101T000000.png"}],
                    }
                },
            },
        )
        archive_path = tmp_path / "legacy-bucket.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        with caplog.at_level("INFO", logger=project_archive_module.logger.name):
            result = service.import_project_archive(archive_path, uploaded_filename="legacy-bucket.zip")

        installed = json.loads(
            (pm.get_project_path(result.project_name) / "versions" / "versions.json").read_text(encoding="utf-8")
        )
        assert "clues" not in installed
        assert installed["storyboards"] == storyboards
        dropped = [record for record in caplog.records if "clues" in record.getMessage()]
        assert len(dropped) == 1
        assert "versions/clues" not in dropped[0].getMessage()

    def test_import_rejects_missing_script_reference_for_malformed_entry(self, tmp_path):
        """Malformed unparseable episode entries are invalid ledger records; missing scripts still block import."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        project_dir = pm.get_project_path("demo")
        project = pm.load_project("demo")
        del project["episodes"][0]["episode"]
        pm.save_project("demo", project)
        (project_dir / "scripts" / "episode_1.json").unlink()

        archive_path = tmp_path / "missing-script.zip"
        _make_manual_zip(project_dir, archive_path)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="broken.zip")

        assert any("episodes[0].script_file" in error for error in exc_info.value.render_errors())

    def test_import_allows_missing_script_for_ledgered_entry(self, tmp_path):
        """Ledger entries with ledger_status may lack generated scripts; imports pass with warnings."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        project_dir = pm.get_project_path("demo")
        project = pm.load_project("demo")
        project["episodes"][0]["ledger_status"] = "planned"
        pm.save_project("demo", project)
        (project_dir / "scripts" / "episode_1.json").unlink()

        archive_path = tmp_path / "ledgered-missing-script.zip"
        _make_manual_zip(project_dir, archive_path)

        result = service.import_project_archive(archive_path, uploaded_filename="ledgered.zip")
        assert any("episodes[0].script_file" in w for w in (m.render(default_translate) for m in result.warnings))

    def test_import_allows_missing_script_for_entry_without_ledger_status(self, tmp_path):
        """v2→v3 no longer backfills ledger_status, so upgraded entries may never have it;
        structurally valid parseable episode numbers count as normal ledger entries, importing with warnings."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        project_dir = pm.get_project_path("demo")
        (project_dir / "scripts" / "episode_1.json").unlink()

        archive_path = tmp_path / "unledgered-missing-script.zip"
        _make_manual_zip(project_dir, archive_path)

        result = service.import_project_archive(archive_path, uploaded_filename="unledgered.zip")
        assert any("episodes[0].script_file" in w for w in (m.render(default_translate) for m in result.warnings))

    def test_import_rejects_missing_script_reference_for_non_positive_episode_num(self, tmp_path):
        """parse_episode_num can parse zero/negative numbers, but they are invalid; missing scripts still block imports."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        project_dir = pm.get_project_path("demo")
        project = pm.load_project("demo")
        project["episodes"][0]["episode"] = 0
        pm.save_project("demo", project)
        (project_dir / "scripts" / "episode_1.json").unlink()

        archive_path = tmp_path / "zero-episode-missing-script.zip"
        _make_manual_zip(project_dir, archive_path)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="broken.zip")

        assert any("episodes[0].script_file" in error for error in exc_info.value.render_errors())

    def test_migrated_project_archive_roundtrip_with_unscripted_episode(self, tmp_path):
        """Self-healed orphan episodes without positions/generated scripts preserve export→reimport roundtrips."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        project = pm.load_project("demo")
        project["episodes"].append(
            {
                "episode": 2,
                "title": "",
                "script_file": "scripts/episode_2.json",
                "ledger_status": "planned",
            }
        )
        pm.save_project("demo", project)

        archive_path, _ = service.export_project("demo")
        result = service.import_project_archive(
            archive_path,
            uploaded_filename="demo.zip",
            conflict_policy="rename",
        )
        imported = result.project
        assert imported["episodes"][1]["ledger_status"] == "planned"
        # Orphans omit position records; export/import roundtrips must not invent source_range.
        assert "source_range" not in imported["episodes"][1]

    def test_import_surfaces_unconvertible_source_encoding_as_warning(self, tmp_path, monkeypatch):
        """Unknown source encodings never abort entire imports for partial corruption; failed files surface in warnings."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        project_dir = pm.get_project_path("demo")

        archive_path = tmp_path / "bad-encoding.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        from lib.script.source_loader.migration import MigrationSummary

        monkeypatch.setattr(
            project_archive_module,
            "migrate_project_source_encoding",
            lambda _dir: MigrationSummary(failed=["novel.txt"]),
        )

        result = service.import_project_archive(archive_path, uploaded_filename="bad.zip")
        assert any("novel.txt" in w and "编码" in w for w in (m.render(default_translate) for m in result.warnings))

    @pytest.mark.parametrize(
        ("field_name", "target_path"),
        [
            ("characters[Hero].character_sheet", ("characters", "Hero.png")),
            ("props[Key].prop_sheet", ("props", "Key.png")),
            (
                "segments[0].generated_assets.storyboard_image",
                ("storyboards", "scene_E1S01.png"),
            ),
            (
                "segments[0].generated_assets.video_clip",
                ("videos", "scene_E1S01.mp4"),
            ),
        ],
    )
    def test_import_rejects_missing_asset_references(
        self,
        tmp_path,
        field_name,
        target_path,
    ):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        project_dir = pm.get_project_path("demo")
        (project_dir.joinpath(*target_path)).unlink()
        if field_name == "segments[0].generated_assets.storyboard_image":
            (project_dir / "versions" / "storyboards" / "E1S01_v1.png").unlink()

        archive_path = tmp_path / f"{field_name}.zip"
        _make_manual_zip(project_dir, archive_path)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="broken.zip")

        assert any(field_name in error for error in exc_info.value.render_errors())

    def test_import_allows_external_video_uri(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm, video_uri="gs://bucket/video-ref")
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "external-video-uri.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(
            archive_path,
            uploaded_filename="external-video-uri.zip",
        )

        assert result.project["episodes"][0]["script_file"] == "scripts/episode_1.json"

    @pytest.mark.parametrize(
        "archive_builder",
        ["absolute", "traversal", "symlink", "encrypted"],
    )
    def test_import_rejects_unsafe_zip_members(self, tmp_path, archive_builder):
        pm = ProjectManager(tmp_path / "projects")
        service = ProjectArchiveService(pm)
        archive_path = tmp_path / f"{archive_builder}.zip"

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("demo/project.json", json.dumps({"title": "Demo"}))
            if archive_builder == "absolute":
                archive.writestr("/demo/scripts/episode_1.json", "{}")
            elif archive_builder == "traversal":
                archive.writestr("../demo/scripts/episode_1.json", "{}")
            elif archive_builder == "symlink":
                info = zipfile.ZipInfo("demo/source/link.txt")
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
                archive.writestr(info, "target")
            elif archive_builder == "encrypted":
                info = zipfile.ZipInfo("demo/source/chapter.txt")
                info.flag_bits |= 0x1
                archive.writestr(info, "source")

        with pytest.raises(ProjectArchiveValidationError):
            service.import_project_archive(archive_path, uploaded_filename="unsafe.zip")

    def test_import_rename_conflict_generates_new_project_id(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")

        result = service.import_project_archive(
            archive_path,
            uploaded_filename="demo.zip",
            conflict_policy="rename",
        )

        assert result.project_name != "demo"
        assert result.project_name.startswith("demo-")
        assert result.conflict_resolution == "renamed"
        assert pm.get_project_path("demo").exists()
        assert pm.get_project_path(result.project_name).exists()

    def test_import_prompt_conflict_requires_user_confirmation(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(
                archive_path,
                uploaded_filename="demo.zip",
                conflict_policy="prompt",
            )

        assert exc_info.value.status_code == 409
        assert exc_info.value.detail.render(default_translate) == "检测到项目编号冲突"
        assert exc_info.value.extra["conflict_project_name"] == "demo"

    def test_import_overwrite_replaces_existing_project(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm, style="Fresh")
        retire = _RetireRecorder()
        service = ProjectArchiveService(pm, retire_project=retire)
        archive_path, _ = service.export_project("demo")

        project = pm.load_project("demo")
        project["style"] = "Stale"
        pm.save_project("demo", project)
        _write_text(pm.get_project_path("demo") / "source" / "chapter.txt", "stale")

        result = service.import_project_archive(
            archive_path,
            uploaded_filename="demo.zip",
            conflict_policy="overwrite",
        )

        assert result.project_name == "demo"
        assert result.conflict_resolution == "overwritten"
        assert pm.load_project("demo")["style"] == "Fresh"
        assert (pm.get_project_path("demo") / "source" / "chapter.txt").read_text(encoding="utf-8") == "source"
        assert retire.retired == ["demo"]
        assert not list(pm.projects_dir.glob(".import-*"))
        # Overwritten old projects become deleted projects.
        [replaced] = deleted_projects.list_entries(pm.layout)
        assert replaced.name == "demo"
        replaced_dir = pm.layout.deleted_projects_dir / replaced.id
        assert json.loads((replaced_dir / "project.json").read_text(encoding="utf-8"))["style"] == "Stale"

    def test_import_overwrite_drops_the_project_memory_of_the_replaced_project(self, tmp_path):
        """Overwrite-import equals deleting existing projects then importing; old memory moves into deleted projects, never new projects."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm, style="Fresh")
        service = ProjectArchiveService(pm, retire_project=_RetireRecorder())
        archive_path, _ = service.export_project("demo")
        _write_text(pm.get_project_path("demo") / ".arcreel" / "memory" / "MEMORY.md", "index")

        result = service.import_project_archive(
            archive_path,
            uploaded_filename="demo.zip",
            conflict_policy="overwrite",
        )

        assert result.conflict_resolution == "overwritten"
        assert pm.load_project("demo")["style"] == "Fresh"
        assert not (pm.get_project_path("demo") / ".arcreel").exists()
        assert not list(pm.projects_dir.glob(".import-*"))

    def test_export_omits_project_memory_and_trash(self, tmp_path):
        """Private project areas, memory/trash, never export in archives."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        _write_text(project_dir / ".arcreel" / "memory" / "MEMORY.md", "index")
        _write_text(project_dir / "notes.txt", "deleted later")
        project_trash.record_deletion(project_dir, {"kind": "file", "path": "notes.txt"}, ["notes.txt"])

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")

        with zipfile.ZipFile(archive_path) as archive:
            assert not [name for name in archive.namelist() if ".arcreel/" in name]
        assert (project_dir / ".arcreel" / "memory" / "MEMORY.md").read_text(encoding="utf-8") == "index"

    def test_rename_import_drops_project_memory_carried_by_the_archive(self, tmp_path):
        """Import dot-directory filters exclude archive .arcreel/memory/ from new projects."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")

        smuggled_path = tmp_path / "smuggled.zip"
        with (
            zipfile.ZipFile(archive_path) as source,
            zipfile.ZipFile(smuggled_path, "w", compression=zipfile.ZIP_DEFLATED) as target,
        ):
            for member in source.infolist():
                target.writestr(member, source.read(member))
            target.writestr("demo/.arcreel/memory/MEMORY.md", "index")

        result = service.import_project_archive(
            smuggled_path,
            uploaded_filename="smuggled.zip",
            conflict_policy="rename",
        )

        assert result.project_name != "demo"
        assert not (pm.get_project_path(result.project_name) / ".arcreel").exists()

    def test_import_materializes_claude_with_manifest(self, tmp_path, monkeypatch):
        """Imports materialize actual .claude directories and write manifests, never symlinks.

        Manifest-driven Profile sync uses no symlinks; archives without manifests trigger first synchronization.
        """
        from lib.agent.profile_manifest import MANIFEST_FILENAME

        # Prepare profiles with at least one materializable file, avoiding ProfileEmptyError.
        profile_dir = tmp_path / "agent_runtime_profile"
        (profile_dir / ".claude" / "skills" / "demo").mkdir(parents=True)
        (profile_dir / ".claude" / "skills" / "demo" / "SKILL.md").write_text("demo")
        (profile_dir / "CLAUDE.md").write_text("prompt")
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_dir))

        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")

        result = service.import_project_archive(
            archive_path,
            uploaded_filename="demo.zip",
            conflict_policy="rename",
        )

        imported_dir = pm.get_project_path(result.project_name)
        claude_dir = imported_dir / ".claude"
        assert claude_dir.is_dir()
        assert not claude_dir.is_symlink()
        # Import triggers sync_agent_profile → first migration full reset → manifest writes.
        assert (imported_dir / MANIFEST_FILENAME).is_file()
        # Profile contents actually persist.
        assert (claude_dir / "skills" / "demo" / "SKILL.md").read_text() == "demo"

    def test_import_overwrite_rolls_back_on_install_failure(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm, style="Fresh")
        retire = _RetireRecorder()
        service = ProjectArchiveService(pm, retire_project=retire)
        archive_path, _ = service.export_project("demo")

        project = pm.load_project("demo")
        project["style"] = "Stale"
        pm.save_project("demo", project)

        original_move = project_archive_module.shutil.move

        def boom(src, dst):
            raise RuntimeError("move failed")

        monkeypatch.setattr(project_archive_module.shutil, "move", boom)

        with pytest.raises(RuntimeError):
            service.import_project_archive(
                archive_path,
                uploaded_filename="demo.zip",
                conflict_policy="overwrite",
            )

        monkeypatch.setattr(project_archive_module.shutil, "move", original_move)
        assert pm.load_project("demo")["style"] == "Stale"
        assert retire.retired == []

    def test_import_overwrite_rolls_back_on_profile_sync_failure(self, tmp_path, monkeypatch):
        """sync_agent_profile failures retain existing projects unchanged, without finalizing their records."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm, style="Fresh")
        retire = _RetireRecorder()
        service = ProjectArchiveService(pm, retire_project=retire)
        archive_path, _ = service.export_project("demo")

        project = pm.load_project("demo")
        project["style"] = "Stale"
        pm.save_project("demo", project)

        # Make sync_agent_profile raise inside _install_project_dir after shutil.move.
        def boom(self_pm, target_dir, **kwargs):
            raise RuntimeError("profile sync failed")

        monkeypatch.setattr(ProjectManager, "sync_agent_profile", boom)

        with pytest.raises(RuntimeError, match="profile sync failed"):
            service.import_project_archive(
                archive_path,
                uploaded_filename="demo.zip",
                conflict_policy="overwrite",
            )

        monkeypatch.undo()
        assert pm.load_project("demo")["style"] == "Stale"
        assert retire.retired == []
        assert not list(pm.projects_dir.glob(".import-*"))

    def test_import_overwrite_restores_the_old_directory_when_retirement_fails_after_replacing(self, tmp_path):
        """Commit failures after directory replacement restore original directories and memory unchanged."""
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm, style="Fresh")
        service = ProjectArchiveService(pm, retire_project=_RetireRecorder(fail_after_replace=True))
        archive_path, _ = service.export_project("demo")

        project = pm.load_project("demo")
        project["style"] = "Stale"
        pm.save_project("demo", project)
        _write_text(pm.get_project_path("demo") / ".arcreel" / "memory" / "MEMORY.md", "index")

        with pytest.raises(RuntimeError, match="commit failed"):
            service.import_project_archive(
                archive_path,
                uploaded_filename="demo.zip",
                conflict_policy="overwrite",
            )

        assert pm.load_project("demo")["style"] == "Stale"
        memory = pm.get_project_path("demo") / ".arcreel" / "memory" / "MEMORY.md"
        assert memory.read_text(encoding="utf-8") == "index"
        assert not list(pm.projects_dir.glob(".import-*"))

    def test_create_project_rolls_back_on_profile_sync_failure(self, tmp_path, monkeypatch):
        """create_project sync_agent_profile failures must rmtree partial project_dir,
        preventing FileExistsError on same-name retries.
        """
        pm = ProjectManager(tmp_path / "projects")

        def boom(self_pm, target_dir, **kwargs):
            raise RuntimeError("profile sync failed")

        monkeypatch.setattr(ProjectManager, "sync_agent_profile", boom)

        with pytest.raises(RuntimeError, match="profile sync failed"):
            pm.create_project("ghost")

        # Partial directories cleaned; same-name creation succeeds after undoing fixture sync failures.
        monkeypatch.undo()
        assert not (pm.projects_dir / "ghost").exists()
        pm.create_project("ghost")  # No FileExistsError collision.
        assert (pm.projects_dir / "ghost").is_dir()

    def test_import_repairs_legacy_narration_payload(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)
        archive_path = tmp_path / "legacy.zip"
        _stage_legacy_narration_archive(pm, project_dir, archive_path)

        result = service.import_project_archive(
            archive_path,
            confirm_report=True,
            uploaded_filename="legacy.zip",
        )

        imported_project = pm.load_project(result.project_name)
        imported_script = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )

        assert "Ghost" in imported_project["characters"]
        assert "source_file" not in imported_script["novel"]
        assert imported_script["segments"][0]["scenes"] == []
        assert imported_script["segments"][0]["props"] == []
        assert "clues_in_segment" not in imported_script["segments"][0]
        assert imported_script["segments"][0]["generated_assets"]["video_clip"] == "videos/scene_E1S01_1.mp4"
        assert result.diagnostics["auto_fixed"]

    def test_export_repairs_narration_audio_from_version_history(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)

        _write_json(
            project_dir / "versions" / "versions.json",
            {
                "audio": {
                    "E1S01": {
                        "current_version": 1,
                        "versions": [
                            {
                                "version": 1,
                                "file": "versions/audio/E1S01_v1.wav",
                                "prompt": "旁白",
                                "created_at": "2024-01-01",
                            }
                        ],
                    }
                }
            },
        )
        _write_bytes(project_dir / "versions" / "audio" / "E1S01_v1.wav", b"wav-v1")
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "novel": {"title": "Demo", "chapter": "第一章"},
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 4,
                        "novel_text": "原文",
                        "characters_in_segment": ["Hero"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                        "generated_assets": {
                            "storyboard_image": "storyboards/scene_E1S01.png",
                            # Missing current files with retained histories: archive repair traces versions/audio back to canonical paths.
                            "narration_audio": "versions/audio/E1S01_v9.wav",
                            "status": "completed",
                        },
                    }
                ],
            },
        )

        archive_path, _ = service.export_project("demo", scope="full")

        with zipfile.ZipFile(archive_path) as archive:
            exported_script = json.loads(archive.read("demo/scripts/episode_1.json"))
            # Rewrite JSON paths and materialize recovered current files into archives.
            assert "demo/audio/segment_E1S01.wav" in archive.namelist()

        assert exported_script["segments"][0]["generated_assets"]["narration_audio"] == "audio/segment_E1S01.wav"

    def test_import_adds_placeholder_for_missing_scene_after_confirmation(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)

        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "novel": {
                    "title": "Demo",
                    "chapter": "第一章",
                },
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 4,
                        "novel_text": "原文",
                        "characters_in_segment": ["Hero"],
                        "scenes": ["Missing"],
                        "props": [],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        archive_path = tmp_path / "missing-scene.zip"
        _make_manual_zip(project_dir, archive_path)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="missing-scene.zip")

        # Nonempty reports require preimport consent; projects remain absent from lists.
        assert exc_info.value.status_code == 409
        assert exc_info.value.extra["confirmation_required"] is True
        adjusted = exc_info.value.extra["migration_report"]["adjusted"]
        assert ("placeholder_asset_added", {"asset_type": "scene", "name": "Missing"}) in [
            (item["code"], item["params"]) for item in adjusted
        ]
        assert pm.list_projects() == ["demo"]

        result = service.import_project_archive(
            archive_path, uploaded_filename="missing-scene.zip", confirm_report=True
        )

        assert result.project["scenes"]["Missing"]["description"] == ""
        report = load_migration_report(pm.get_project_path(result.project_name))
        assert report is not None
        assert report.acknowledged_at is not None

    def test_a_current_schema_archive_with_an_unreadable_record_asks_for_confirmation(self, tmp_path):
        """Unregisterable import-activation entries enter reports regardless of archive schema; projects appear only after confirmation, then import normally."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)
        (project_dir / "grids").mkdir(exist_ok=True)
        (project_dir / "grids" / "grid_0123456789ab.json").write_text("{broken", encoding="utf-8")
        archive_path = tmp_path / "unreadable-grid.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="unreadable-grid.zip")

        assert exc_info.value.status_code == 409
        skipped = exc_info.value.extra["migration_report"]["skipped"]
        assert ("episode-grid", "grids/grid_0123456789ab.json") in [
            (item["kind"], item["artifact_path"]) for item in skipped
        ]
        assert pm.list_projects() == []

        result = service.import_project_archive(
            archive_path, uploaded_filename="unreadable-grid.zip", confirm_report=True
        )

        imported_dir = pm.get_project_path(result.project_name)
        assert (imported_dir / "grids" / "grid_0123456789ab.json").read_text(encoding="utf-8") == "{broken"
        report = load_migration_report(imported_dir)
        assert report is not None
        assert "grids/grid_0123456789ab.json" in [item.artifact_path for item in report.skipped]

    def test_media_of_a_unit_that_needs_replanning_does_not_ask_for_confirmation(self, tmp_path):
        """Replanning entries have separate UI markers; omit their unregistered media from reports, importing directly if reports empty."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        script_path = project_dir / "scripts" / "episode_1.json"
        script = json.loads(script_path.read_text(encoding="utf-8"))
        script["segments"][0]["needs_replan"] = True
        _write_json(script_path, script)
        service = ProjectArchiveService(pm)
        archive_path = tmp_path / "replan.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="replan.zip")

        assert load_migration_report(pm.get_project_path(result.project_name)) is None

    def test_import_resolves_nfd_script_refs_against_nfc_registered_assets(self, tmp_path):
        """Scripts/project.json may independently use NFC/NFD; repair membership checks resolve each coordinate system,
        preventing duplicate character placeholders whose later writes overwrite actual metadata,
        or falsely blocking registered scenes/props as missing."""
        import unicodedata

        character_nfc = unicodedata.normalize("NFC", "Hiếu")
        character_nfd = unicodedata.normalize("NFD", "Hiếu")
        scene_nfc = unicodedata.normalize("NFC", "Quán")
        scene_nfd = unicodedata.normalize("NFD", "Quán")
        prop_nfc = unicodedata.normalize("NFC", "Kiếm")
        prop_nfd = unicodedata.normalize("NFD", "Kiếm")

        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)

        project = pm.load_project("demo")
        project["characters"][character_nfc] = {"description": "已登记角色"}
        project["scenes"] = {scene_nfc: {"description": "已登记场景"}}
        project["props"][prop_nfc] = {"description": "已登记道具"}
        pm.save_project("demo", project)

        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "novel": {"title": "Demo", "chapter": "第一章"},
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 4,
                        "novel_text": "原文",
                        "characters_in_segment": [character_nfd],
                        "scenes": [scene_nfd],
                        "props": [prop_nfd],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        archive_path = tmp_path / "nfc-nfd.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="nfc-nfd.zip")

        imported = pm.load_project(result.project_name)
        assert character_nfd not in imported["characters"]  # Never add duplicate placeholder characters.
        assert scene_nfd not in imported["scenes"]
        assert prop_nfd not in imported["props"]
        assert scene_nfc in imported["scenes"]
        assert prop_nfc in imported["props"]
        assert not any(item["code"] == "placeholder_character_added" for item in result.diagnostics["auto_fixed"])

    def test_import_adds_placeholder_for_missing_prop(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)

        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "novel": {
                    "title": "Demo",
                    "chapter": "第一章",
                },
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 4,
                        "novel_text": "原文",
                        "characters_in_segment": ["Hero"],
                        "scenes": [],
                        "props": ["Missing"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                    }
                ],
            },
        )

        archive_path = tmp_path / "missing-prop.zip"
        _make_manual_zip(project_dir, archive_path)

        result = service.import_project_archive(archive_path, uploaded_filename="missing-prop.zip", confirm_report=True)

        assert "Missing" in result.project["props"]
        # Placeholders already appeared in preimport-confirmed reports; successful-import diagnostics do not repeat them.
        report = load_migration_report(pm.get_project_path(result.project_name))
        assert report is not None
        assert ("placeholder_asset_added", {"asset_type": "prop", "name": "Missing"}) in [
            (item.code, item.params) for item in report.adjusted
        ]
        assert not any(item["code"] == "placeholder_prop_added" for item in result.diagnostics["auto_fixed"])

    def test_export_dirty_project_emits_diagnostics_and_repairs_snapshot(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)

        _write_text(project_dir / "run_video_gen.py", "print('helper')")
        _write_json(
            project_dir / "versions" / "versions.json",
            {
                "videos": {
                    "E1S01": {
                        "current_version": 1,
                        "versions": [
                            {
                                "version": 1,
                                "file": "versions/videos/E1S01_v1.mp4",
                                "prompt": "vp1",
                                "created_at": "2024-01-01",
                            }
                        ],
                    }
                }
            },
        )
        _write_bytes(project_dir / "versions" / "videos" / "E1S01_v1.mp4", b"mp4-v1")
        _write_json(
            project_dir / "scripts" / "episode_1.json",
            {
                "episode": 1,
                "title": "第一集",
                "content_mode": "narration",
                "novel": {
                    "title": "Demo",
                    "chapter": "第一章",
                },
                "segments": [
                    {
                        "segment_id": "E1S01",
                        "duration_seconds": 4,
                        "novel_text": "原文",
                        "characters_in_segment": ["Hero"],
                        "image_prompt": "img",
                        "video_prompt": "vid",
                        "generated_assets": {
                            "storyboard_image": "storyboards/scene_E1S01.png",
                            "video_clip": "versions/videos/E1S01_v9.mp4",
                            "video_uri": None,
                            "status": "completed",
                        },
                    }
                ],
            },
        )

        archive_path, _ = service.export_project("demo", scope="full")

        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            exported_script = json.loads(archive.read("demo/scripts/episode_1.json"))

        assert manifest["format_version"] == 2
        assert manifest["script_schema_version"] == 2
        assert "run_video_gen.py" in manifest["pass_through_entries"]
        assert manifest["export_diagnostics"]["auto_fixed"]
        assert exported_script["segments"][0]["scenes"] == []
        assert exported_script["segments"][0]["props"] == []
        assert "clues_in_segment" not in exported_script["segments"][0]
        assert exported_script["segments"][0]["generated_assets"]["video_clip"] == "videos/scene_E1S01.mp4"

    def test_export_includes_reference_audio_file_both_scopes(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["characters"]["Hero"]["reference_audio"] = "characters/refs_audio/Hero.wav"
        pm.save_project("demo", project)
        _write_bytes(project_dir / "characters" / "refs_audio" / "Hero.wav", b"wav-bytes")

        service = ProjectArchiveService(pm)
        for scope in ("full", "current"):
            archive_path, _ = service.export_project("demo", scope=scope)
            with zipfile.ZipFile(archive_path) as archive:
                names = set(archive.namelist())
            assert "demo/characters/refs_audio/Hero.wav" in names

    def test_export_repairs_reference_audio_canonical_path(self, tmp_path):
        """reference_audio, unlike reference_image, has no forced extension; canonical paths derive from field
        extensions, rewriting stale pointers, such as manually restored paths, when canonical files exist."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        project = pm.load_project("demo")
        project["characters"]["Hero"]["reference_audio"] = "characters/stray/Hero_old.wav"
        pm.save_project("demo", project)
        _write_bytes(project_dir / "characters" / "refs_audio" / "Hero.wav", b"wav-bytes")

        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo", scope="full")

        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            exported_project = json.loads(archive.read("demo/project.json"))

        assert exported_project["characters"]["Hero"]["reference_audio"] == "characters/refs_audio/Hero.wav"
        assert manifest["export_diagnostics"]["auto_fixed"]


class TestExportScope:
    def _create_project_with_versions(self, pm: ProjectManager) -> Path:
        """Create projects with versions histories"""
        project_dir = _create_project(pm)

        # Add historical version files.
        _write_bytes(project_dir / "versions" / "storyboards" / "E1S01_v1.png", b"png-v1")
        _write_bytes(project_dir / "versions" / "storyboards" / "E1S01_v2.png", b"png-v2")
        _write_bytes(project_dir / "versions" / "videos" / "E1S01_v1.mp4", b"mp4-v1")
        _write_bytes(project_dir / "versions" / "characters" / "Hero_v1.png", b"char-v1")
        _write_bytes(project_dir / "versions" / "scenes" / "Temple_v1.png", b"scene-v1")
        _write_bytes(project_dir / "versions" / "props" / "Key_v1.png", b"prop-v1")

        # Create versions/versions.json.
        versions_data = {
            "storyboards": {
                "E1S01": {
                    "current_version": 3,
                    "versions": [
                        {"version": 1, "prompt": "p1", "created_at": "2024-01-01"},
                        {"version": 2, "prompt": "p2", "created_at": "2024-01-02"},
                        {"version": 3, "prompt": "p3", "created_at": "2024-01-03"},
                    ],
                }
            },
            "videos": {
                "E1S01": {
                    "current_version": 2,
                    "versions": [
                        {"version": 1, "prompt": "vp1", "created_at": "2024-01-01"},
                        {"version": 2, "prompt": "vp2", "created_at": "2024-01-02"},
                    ],
                }
            },
        }
        _write_json(project_dir / "versions" / "versions.json", versions_data)
        return project_dir

    def test_export_scope_full_includes_version_history(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        self._create_project_with_versions(pm)
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo", scope="full")

        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            assert "demo/versions/storyboards/E1S01_v1.png" in names
            assert "demo/versions/storyboards/E1S01_v2.png" in names
            assert "demo/versions/videos/E1S01_v1.mp4" in names
            assert "demo/versions/characters/Hero_v1.png" in names
            assert "demo/versions/scenes/Temple_v1.png" in names
            assert "demo/versions/props/Key_v1.png" in names

    def test_export_scope_current_skips_version_history_files(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        self._create_project_with_versions(pm)
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo", scope="current")

        with zipfile.ZipFile(archive_path) as archive:
            names = set(archive.namelist())
            # Exclude historical version files.
            assert "demo/versions/storyboards/E1S01_v1.png" not in names
            assert "demo/versions/storyboards/E1S01_v2.png" not in names
            assert "demo/versions/videos/E1S01_v1.mp4" not in names
            assert "demo/versions/characters/Hero_v1.png" not in names
            assert "demo/versions/scenes/Temple_v1.png" not in names
            assert "demo/versions/props/Key_v1.png" not in names
            # Retain primary resources.
            assert "demo/storyboards/scene_E1S01.png" in names
            assert "demo/videos/scene_E1S01.mp4" in names
            # Retain pruned versions.json.
            assert "demo/versions/versions.json" in names

    def test_export_scope_current_trims_versions_json(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        self._create_project_with_versions(pm)
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo", scope="current")

        with zipfile.ZipFile(archive_path) as archive:
            versions_content = json.loads(archive.read("demo/versions/versions.json"))
            # Untyped current files export directly from canonical paths without historical snapshots;
            # corresponding version records must not retain dangling references.
            assert "storyboards" not in versions_content
            # videos.E1S01 retains version 2 only.
            vid_versions = versions_content["videos"]["E1S01"]["versions"]
            assert len(vid_versions) == 1
            assert vid_versions[0]["version"] == 2

    def test_export_scope_current_manifest_scope_field(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        self._create_project_with_versions(pm)
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo", scope="current")

        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            assert manifest["scope"] == "current"

    def test_export_scope_full_manifest_scope_field(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        self._create_project_with_versions(pm)
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo", scope="full")

        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
            assert manifest["scope"] == "full"


class TestArchiveDiagnosticsLocalization:
    """Supplied translators render structured diagnostics in target languages, including auto_fixed on successful imports."""

    @staticmethod
    def _en(key: str, **kwargs: object) -> str:
        return _(key, locale="en", **kwargs)

    def test_import_success_diagnostics_render_in_target_language(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)
        archive_path = tmp_path / "legacy.zip"
        _stage_legacy_narration_archive(pm, project_dir, archive_path)

        result = service.import_project_archive(
            archive_path,
            confirm_report=True,
            uploaded_filename="legacy.zip",
            translate=self._en,
        )

        assert result.diagnostics["auto_fixed"]
        rendered = json.dumps(result.diagnostics, ensure_ascii=False) + json.dumps(
            [warning.render(self._en) for warning in result.warnings], ensure_ascii=False
        )
        assert not re.search(r"[一-鿿]", rendered)

    def test_export_diagnostics_snapshot_in_archive_stays_default_language(self, tmp_path):
        """Bundled diagnostic snapshots never depend on exporters' request languages; importers may use other languages."""
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        _write_text(project_dir / "notes.txt", "scratch")
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo")

        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
        messages = [item["message"] for item in manifest["export_diagnostics"]["warnings"]]
        assert any("notes.txt" in message and re.search(r"[一-鿿]", message) for message in messages)


def test_an_archive_exported_at_schema_18_keeps_storyboards_and_grids_current_after_import(tmp_path, monkeypatch):
    """Envelope claims transfer through migration like in-place upgrades; v18→v19 changes storyboard/grid bases, retaining current after import."""
    source = ProjectManager(tmp_path / "source")
    write_legacy_named_reference_bases_project(source.projects_dir, name="demo")
    # Simulate schema-18 exporters treating 18 as current and placing manifests inside envelopes.
    monkeypatch.setattr(project_archive_module, "project_schema_is_current", lambda _project: True)
    archive_path, _ = ProjectArchiveService(source).export_project("demo")
    monkeypatch.undo()
    with zipfile.ZipFile(archive_path) as archive:
        assert "artifact_manifest" in json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
    target = ProjectManager(tmp_path / "target")

    ProjectArchiveService(target).import_project_archive(
        archive_path, uploaded_filename="demo.zip", confirm_report=True
    )

    project_dir = target.get_project_path("demo")
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    resolver = ArtifactCurrencyResolver(project_dir)
    for key in (
        ArtifactKey.episode_grid(1, "grid_123456789abc"),
        ArtifactKey.episode_storyboard(1, "E1S01"),
        ArtifactKey.episode_storyboard(1, "E1S02"),
        ArtifactKey.episode_storyboard(1, "E1S03"),
    ):
        assert resolver.compare(key, artifact_path=entries[key].artifact_path).status is ArtifactStatus.CURRENT


def test_claims_the_migration_chain_rekeys_keep_their_archived_content_digests(tmp_path):
    """Migrated claim rekeying/image moves with unchanged bases/bytes retain envelope old-key content digests independently for identical-byte entries.

    Migration-backfilled entries lacking old claims have no digests.
    """
    project_dir = tmp_path / "staging"
    _write_json(project_dir / "project.json", {"schema_version": CURRENT_PROJECT_SCHEMA_VERSION - 1})
    basis = ArtifactBasis.build("test/uploaded-sheet", kind_version=1, inputs={"bytes": "same"})
    moves = {
        derivative_artifact_key("Ann", name): (
            derivative_artifact_key("Alice", name),
            f"characters/derivatives/Ann/{name}.png",
            f"characters/derivatives/Alice/{name}.png",
        )
        for name in ("Hat", "Cap")
    }
    for _new_key, old_path, _new_path in moves.values():
        _write_bytes(project_dir / old_path, b"same bytes")
    _write_bytes(project_dir / "characters/Bob.png", b"bob")
    claims = {
        old_key: ArtifactManifestEntry(artifact_path=old_path, basis_digest=basis.digest)
        for old_key, (_new_key, old_path, _new_path) in moves.items()
    }
    adapter = ProjectArtifactManifestAdapter(project_dir)
    digest = read_artifact_content_digest(adapter, next(iter(claims.values())).artifact_path)
    preserved = ArtifactManifestArchiveSnapshot(entries=claims, content_digests=dict.fromkeys(claims, digest))

    assert stage_preserved_artifact_claims(project_dir, preserved)
    (project_dir / "characters/derivatives/Alice").mkdir(parents=True)
    replacements: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
    for old_key, (new_key, old_path, new_path) in moves.items():
        (project_dir / old_path).rename(project_dir / new_path)
        replacements[old_key] = None
        replacements[new_key] = replace(claims[old_key], artifact_path=new_path)
    bob_basis = ArtifactBasis.build("test/asset-sheet", kind_version=1, inputs={"sheet": "bob"})
    replacements[_BOB_SHEET] = ArtifactManifestEntry(artifact_path="characters/Bob.png", basis_digest=bob_basis.digest)
    assert adapter.replace_entries_if_matches_atomically(expected=claims, replacements=replacements)

    carried = take_migrated_artifact_claims(project_dir, preserved)

    assert carried.entries == {new_key: replacements[new_key] for new_key, _old, _new in moves.values()}
    assert carried.content_digests == {new_key: digest for new_key, _old, _new in moves.values()}
    assert not (project_dir / MANIFEST_FILENAME).exists()


_BOB_SHEET = ArtifactKey.asset_sheet("character", "Bob")
