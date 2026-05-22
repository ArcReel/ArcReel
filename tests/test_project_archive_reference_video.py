"""归档导入针对 reference_video 模式（video_units）的修复测试。

覆盖 issue：_repair_script_payload 此前只按 content_mode 走 segments/scenes，
未处理 generation_mode=reference_video 项目剧本里的 video_units，导致导出-导入往返时
video_units[*].generated_assets 的路径规范化与版本回溯不触发。
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from lib.project_manager import ProjectManager
from lib.resource_paths import resource_relative_path
from server.services.project_archive import ProjectArchiveService, ProjectArchiveValidationError

REMOTE_VIDEO_URI = "https://cdn.example.com/v/E1U1.mp4"

# 区分"未传 generated_assets（用默认）"与"显式传 None（不写该字段）"
_DEFAULT_ASSETS = object()


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_manual_zip(project_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(project_dir.rglob("*")):
            relative = item.relative_to(project_dir)
            if item.is_dir():
                info = zipfile.ZipInfo(relative.as_posix().rstrip("/") + "/")
                archive.writestr(info, b"")
            else:
                archive.write(item, arcname=relative.as_posix())


def _build_unit(
    *,
    video_clip: str | None,
    generated_assets: dict | None | object = _DEFAULT_ASSETS,
    references: list[dict] | None = None,
) -> dict:
    if generated_assets is _DEFAULT_ASSETS:
        generated_assets = {
            "storyboard_image": None,
            "storyboard_last_image": None,
            "video_clip": video_clip,
            "video_thumbnail": "reference_videos/thumbnails/E1U1.jpg",
            "video_uri": REMOTE_VIDEO_URI,
            "grid_id": None,
            "grid_cell_index": None,
            "status": "completed",
        }
    unit: dict = {
        "unit_id": "E1U1",
        "shots": [{"duration": 4, "text": "镜头一"}],
        "references": references if references is not None else [],
        "duration_seconds": 4,
        "transition_to_next": "cut",
    }
    if generated_assets is not None:
        unit["generated_assets"] = generated_assets
    return unit


def _build_reference_episode(unit: dict) -> dict:
    return {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "generation_mode": "reference_video",
        "duration_seconds": 4,
        "summary": "demo",
        "novel": {"title": "RefDemo", "chapter": "第一章"},
        "video_units": [unit],
    }


def _create_reference_video_project(
    pm: ProjectManager,
    *,
    name: str = "refdemo",
    unit: dict | None = None,
    write_clip: bool = True,
    write_thumbnail: bool = True,
) -> Path:
    pm.create_project(name)
    pm.create_project_metadata(name, "RefDemo", "Anime", "narration")

    project_dir = pm.get_project_path(name)
    project = pm.load_project(name)
    project["generation_mode"] = "reference_video"
    project["style_image"] = "style_reference.png"
    project["episodes"] = [
        {
            "episode": 1,
            "title": "第一集",
            "script_file": "scripts/episode_1.json",
        }
    ]
    pm.save_project(name, project)

    _write_bytes(project_dir / "style_reference.png", b"png")
    if write_clip:
        _write_bytes(project_dir / "reference_videos" / "E1U1.mp4", b"mp4")
    if write_thumbnail:
        _write_bytes(project_dir / "reference_videos" / "thumbnails" / "E1U1.jpg", b"jpg")

    if unit is None:
        unit = _build_unit(video_clip="reference_videos/E1U1.mp4")
    _write_json(project_dir / "scripts" / "episode_1.json", _build_reference_episode(unit))
    return project_dir


class TestProjectArchiveReferenceVideo:
    def test_canonical_resource_path_reference_videos(self):
        # 验收项：reference_videos 走 unit_id 无前缀分支（路径形状由 lib.resource_paths 独家拥有）
        assert resource_relative_path("reference_videos", "E1U1") == "reference_videos/E1U1.mp4"

    def test_round_trip_preserves_video_units(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        _create_reference_video_project(pm)
        service = ProjectArchiveService(pm)

        archive_path, _ = service.export_project("refdemo")
        shutil.rmtree(pm.get_project_path("refdemo"))

        result = service.import_project_archive(archive_path, uploaded_filename="refdemo.zip")

        assert result.project_name == "refdemo"
        project_dir = pm.get_project_path("refdemo")
        imported = json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        assets = imported["video_units"][0]["generated_assets"]
        assert assets["video_clip"] == "reference_videos/E1U1.mp4"
        assert assets["video_thumbnail"] == "reference_videos/thumbnails/E1U1.jpg"
        # video_uri 是远端 URL，绝不能被当成本地路径覆盖
        assert assets["video_uri"] == REMOTE_VIDEO_URI
        assert (project_dir / "reference_videos" / "E1U1.mp4").exists()
        assert (project_dir / "reference_videos" / "thumbnails" / "E1U1.jpg").exists()

    def test_import_restores_video_clip_from_version(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        # video_clip 指向失效的版本路径，靠 versions.json 回溯物化当前文件
        unit = _build_unit(
            video_clip="versions/reference_videos/E1U1_v9.mp4",
            generated_assets={
                "storyboard_image": None,
                "video_clip": "versions/reference_videos/E1U1_v9.mp4",
                "video_thumbnail": None,
                "video_uri": None,
                "status": "completed",
            },
        )
        project_dir = _create_reference_video_project(pm, unit=unit, write_clip=False, write_thumbnail=False)
        service = ProjectArchiveService(pm)

        _write_json(
            project_dir / "versions" / "versions.json",
            {
                "reference_videos": {
                    "E1U1": {
                        "current_version": 1,
                        "versions": [
                            {
                                "version": 1,
                                "file": "versions/reference_videos/E1U1_v1.mp4",
                                "prompt": "vp1",
                                "created_at": "2024-01-01",
                            }
                        ],
                    }
                }
            },
        )
        _write_bytes(project_dir / "versions" / "reference_videos" / "E1U1_v1.mp4", b"mp4-v1")

        archive_path = tmp_path / "legacy.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="legacy.zip")

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        assert imported["video_units"][0]["generated_assets"]["video_clip"] == "reference_videos/E1U1.mp4"
        assert (pm.get_project_path(result.project_name) / "reference_videos" / "E1U1.mp4").exists()
        assert result.diagnostics["auto_fixed"]

    def test_import_backfills_missing_generated_assets(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(video_clip=None, generated_assets=None)
        project_dir = _create_reference_video_project(pm, unit=unit, write_clip=False, write_thumbnail=False)
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "legacy.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="legacy.zip")

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        assets = imported["video_units"][0]["generated_assets"]
        assert isinstance(assets, dict)
        assert "video_thumbnail" in assets
        assert assets["status"] == "pending"

    def test_import_adds_placeholder_for_missing_character_reference(self, tmp_path):
        # 与 narration/drama 对齐：references 引用了 project.json 缺失的角色 → 自动补占位定义
        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(
            video_clip="reference_videos/E1U1.mp4",
            references=[{"type": "character", "name": "幽灵"}],
        )
        project_dir = _create_reference_video_project(pm, unit=unit)
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "missing-char.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="missing-char.zip")

        imported_project = pm.load_project(result.project_name)
        assert "幽灵" in imported_project["characters"]
        assert result.diagnostics["auto_fixed"]

    def test_import_blocks_missing_scene_reference(self, tmp_path):
        # 与 narration/drama 对齐：references 引用了缺失的场景 → 阻断导入
        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(
            video_clip="reference_videos/E1U1.mp4",
            references=[{"type": "scene", "name": "缺失场景"}],
        )
        project_dir = _create_reference_video_project(pm, unit=unit)
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "missing-scene.zip"
        _make_manual_zip(project_dir, archive_path)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="missing-scene.zip")

        assert exc_info.value.extra["diagnostics"]["blocking"]

    def test_import_blocks_missing_prop_reference(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(
            video_clip="reference_videos/E1U1.mp4",
            references=[{"type": "prop", "name": "缺失道具"}],
        )
        project_dir = _create_reference_video_project(pm, unit=unit)
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "missing-prop.zip"
        _make_manual_zip(project_dir, archive_path)

        with pytest.raises(ProjectArchiveValidationError) as exc_info:
            service.import_project_archive(archive_path, uploaded_filename="missing-prop.zip")

        assert exc_info.value.extra["diagnostics"]["blocking"]
