"""归档导入针对 ad + 参考生视频自包含 video_units 的修复测试。"""

import json
import shutil
import zipfile
from pathlib import Path

from lib.project_manager import ProjectManager
from server.services.project_archive import ProjectArchiveService


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


def _create_ad_reference_project(
    pm: ProjectManager,
    *,
    name: str = "addemo",
    video_units: list[dict] | None = None,
) -> Path:
    pm.create_project(name, content_mode="ad")
    pm.create_project_metadata(name, "AdDemo", "Realistic", "ad", target_duration=12)

    project = pm.load_project(name)
    project["generation_mode"] = "reference_video"
    project["characters"] = {"主播": {"description": "出镜模特"}}
    project["scenes"] = {"客厅": {"description": "现代客厅"}}
    project["props"] = {}
    project["products"] = {"速干杯": {"description": "主推产品"}}
    project["episodes"] = [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}]
    pm.save_project(name, project)

    if video_units is None:
        video_units = [
            {
                "unit_id": "E1U1",
                "duration_seconds": 4,
                "text": "镜头1：@[主播] 展示 @[速干杯]",
            }
        ]
    episode = {
        "episode": 1,
        "title": "第一集",
        "content_mode": "ad",
        "video_units": video_units,
    }

    project_dir = pm.get_project_path(name)
    _write_json(project_dir / "scripts" / "episode_1.json", episode)
    return project_dir


def _import_via_manual_zip(service: ProjectArchiveService, project_dir: Path, tmp_path: Path):
    archive_path = tmp_path / "ad-reference.zip"
    _make_manual_zip(project_dir, archive_path)
    shutil.rmtree(project_dir)
    return service.import_project_archive(archive_path, uploaded_filename="ad-reference.zip")


class TestProjectArchiveAdReference:
    def test_video_units_generated_assets_backfilled(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_ad_reference_project(pm)
        service = ProjectArchiveService(pm)

        result = _import_via_manual_zip(service, project_dir, tmp_path)

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        unit = imported["video_units"][0]
        assert isinstance(unit["generated_assets"], dict)
        assert unit["generated_assets"]["status"] == "pending"
        assert result.diagnostics["auto_fixed"]

    def test_video_units_preserve_existing_assets_and_legacy_signature(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        units = [
            {
                "unit_id": "E1U1",
                "duration_seconds": 4,
                "text": "镜头1：产品特写",
                "generated_assets": {
                    "video_clip": "reference_videos/E1U1.mp4",
                    "status": "completed",
                    "source_signature": "legacy",
                },
            }
        ]
        project_dir = _create_ad_reference_project(pm, video_units=units)
        _write_bytes(project_dir / "reference_videos" / "E1U1.mp4", b"mp4")
        service = ProjectArchiveService(pm)

        result = _import_via_manual_zip(service, project_dir, tmp_path)

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        assets = imported["video_units"][0]["generated_assets"]
        assert assets["video_clip"] == "reference_videos/E1U1.mp4"
        assert assets["status"] == "completed"
        assert assets["source_signature"] == "legacy"
        assert "video_thumbnail" in assets
