import json
import shutil
import stat
import zipfile
from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from server.services import project_archive as project_archive_module
from server.services.project_archive import (
    ARCHIVE_MANIFEST_NAME,
    ProjectArchiveService,
    ProjectArchiveValidationError,
)


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
                "transition_to_next": "cut",
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
    style: str = "Anime",
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
    _write_text(project_dir / "drafts" / "episode_1" / "step1_segments.md", "draft")
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
    """Create agent_runtime_profile and symlinks mimicking production layout."""
    # projects_root is project_dir.parent; project_root is projects_root.parent
    project_root = project_dir.parent.parent
    profile_claude = project_root / "agent_runtime_profile" / ".claude"
    profile_claude.mkdir(parents=True, exist_ok=True)
    (profile_claude / "settings.json").write_text("{}", encoding="utf-8")
    profile_md = project_root / "agent_runtime_profile" / "CLAUDE.md"
    profile_md.write_text("# Agent Runtime", encoding="utf-8")

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
            assert "demo/drafts/episode_1/step1_segments.md" in names
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
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        # Create broken symlinks (targets don't exist)
        (project_dir / ".claude").symlink_to(Path("../../agent_runtime_profile/.claude"))
        (project_dir / "CLAUDE.md").symlink_to(Path("../../agent_runtime_profile/CLAUDE.md"))

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
        _create_project(pm)
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo")
        shutil.rmtree(pm.get_project_path("demo"))

        result = service.import_project_archive(
            archive_path,
            uploaded_filename="demo.zip",
        )

        assert result.project_name == "demo"
        assert result.conflict_resolution == "none"
        assert (pm.get_project_path("demo") / "videos" / "scene_E1S01.mp4").exists()
        assert (pm.get_project_path("demo") / "drafts" / "episode_2").is_dir()

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

    def test_import_rejects_missing_project_json(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        service = ProjectArchiveService(pm)
        archive_path = tmp_path / "missing-project-json.zip"

        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("demo/source/chapter.txt", "source")

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="broken.zip")

        assert exc_info.value.detail == "导入包校验失败"
        assert any("project.json" in error for error in exc_info.value.errors)

    def test_import_rejects_missing_script_reference(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        project_dir = pm.get_project_path("demo")
        (project_dir / "scripts" / "episode_1.json").unlink()

        archive_path = tmp_path / "missing-script.zip"
        _make_manual_zip(project_dir, archive_path)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="broken.zip")

        assert any("episodes[0].script_file" in error for error in exc_info.value.errors)

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

        assert any(field_name in error for error in exc_info.value.errors)

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
        assert exc_info.value.detail == "检测到项目编号冲突"
        assert exc_info.value.extra["conflict_project_name"] == "demo"

    def test_import_overwrite_replaces_existing_project(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm, style="Fresh")
        service = ProjectArchiveService(pm)
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

    def test_import_creates_claude_symlink(self, tmp_path):
        """Imported project should get .claude symlink for agent runtime isolation."""
        # Create agent_runtime_profile in project root (parent of projects/)
        profile_claude = tmp_path / "agent_runtime_profile" / ".claude"
        profile_claude.mkdir(parents=True)

        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm)
        service = ProjectArchiveService(pm)
        archive_path, _ = service.export_project("demo")

        # Import as a new project
        result = service.import_project_archive(
            archive_path,
            uploaded_filename="demo.zip",
            conflict_policy="rename",
        )

        imported_dir = pm.get_project_path(result.project_name)
        symlink = imported_dir / ".claude"
        assert symlink.is_symlink()
        assert symlink.resolve() == profile_claude.resolve()

    def test_import_overwrite_rolls_back_on_install_failure(self, tmp_path, monkeypatch):
        pm = ProjectManager(tmp_path / "projects")
        _create_project(pm, style="Fresh")
        service = ProjectArchiveService(pm)
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

    def test_import_repairs_legacy_narration_payload(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_project(pm)
        service = ProjectArchiveService(pm)

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

        archive_path = tmp_path / "legacy.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(
            archive_path,
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

    def test_import_blocks_missing_scene_definition(self, tmp_path):
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

        assert any("不存在于 project.json 的场景" in error for error in exc_info.value.errors)
        assert exc_info.value.extra["diagnostics"]["blocking"]

    def test_import_blocks_missing_prop_definition(self, tmp_path):
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

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="missing-prop.zip")

        assert any("不存在于 project.json 的道具" in error for error in exc_info.value.errors)
        assert exc_info.value.extra["diagnostics"]["blocking"]

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


class TestExportScope:
    def _create_project_with_versions(self, pm: ProjectManager) -> Path:
        """创建带有 versions 历史的项目"""
        project_dir = _create_project(pm)

        # 添加版本历史文件
        _write_bytes(project_dir / "versions" / "storyboards" / "E1S01_v1.png", b"png-v1")
        _write_bytes(project_dir / "versions" / "storyboards" / "E1S01_v2.png", b"png-v2")
        _write_bytes(project_dir / "versions" / "videos" / "E1S01_v1.mp4", b"mp4-v1")
        _write_bytes(project_dir / "versions" / "characters" / "Hero_v1.png", b"char-v1")
        _write_bytes(project_dir / "versions" / "scenes" / "Temple_v1.png", b"scene-v1")
        _write_bytes(project_dir / "versions" / "props" / "Key_v1.png", b"prop-v1")

        # 创建 versions/versions.json
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
            # 历史版本文件不应包含
            assert "demo/versions/storyboards/E1S01_v1.png" not in names
            assert "demo/versions/storyboards/E1S01_v2.png" not in names
            assert "demo/versions/videos/E1S01_v1.mp4" not in names
            assert "demo/versions/characters/Hero_v1.png" not in names
            assert "demo/versions/scenes/Temple_v1.png" not in names
            assert "demo/versions/props/Key_v1.png" not in names
            # 主资源应保留
            assert "demo/storyboards/scene_E1S01.png" in names
            assert "demo/videos/scene_E1S01.mp4" in names
            # versions.json 应保留（裁剪后）
            assert "demo/versions/versions.json" in names

    def test_export_scope_current_trims_versions_json(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        self._create_project_with_versions(pm)
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("demo", scope="current")

        with zipfile.ZipFile(archive_path) as archive:
            versions_content = json.loads(archive.read("demo/versions/versions.json"))
            # storyboards.E1S01 应只保留 version 3
            sb_versions = versions_content["storyboards"]["E1S01"]["versions"]
            assert len(sb_versions) == 1
            assert sb_versions[0]["version"] == 3
            assert sb_versions[0]["prompt"] == "p3"
            # videos.E1S01 应只保留 version 2
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
