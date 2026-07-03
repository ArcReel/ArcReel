"""归档导入针对 ad + 参考生视频（shots 骨架 + reference_units 派生索引）的修复测试。

覆盖：修复分流此前按 generation_mode==reference_video 走 video_units 专用分支，
ad+参考项目骨架恒为 shots、不含 video_units，导致 shots 的 generated_assets 回填、
scenes/props 字段补全全部静默跳过，reference_units 派生索引也无任何修复路径。
"""

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


def _ad_shot(**overrides) -> dict:
    shot = {
        "shot_id": "E1S01",
        "section": "hook",
        "duration_seconds": 4,
        "voiceover_text": "三秒速干",
        "characters_in_shot": [],
        "image_prompt": "img",
        "video_prompt": "vid",
    }
    shot.update(overrides)
    return shot


def _create_ad_reference_project(
    pm: ProjectManager,
    *,
    name: str = "addemo",
    shots: list[dict] | None = None,
    reference_units: list[dict] | None = None,
) -> Path:
    pm.create_project(name, content_mode="ad")
    pm.create_project_metadata(name, "AdDemo", "Realistic", "ad", target_duration=12)

    project = pm.load_project(name)
    project["generation_mode"] = "reference_video"
    project["characters"] = {"主播": {"description": "出镜模特"}}
    project["scenes"] = {"客厅": {"description": "现代客厅"}}
    project["props"] = {"速干杯": {"description": "主推产品"}}
    project["episodes"] = [{"episode": 1, "title": "第一集", "script_file": "scripts/episode_1.json"}]
    pm.save_project(name, project)

    if shots is None:
        shots = [_ad_shot()]
    episode: dict = {
        "episode": 1,
        "title": "第一集",
        "content_mode": "ad",
        "generation_mode": "reference_video",
        "shots": shots,
    }
    if reference_units is not None:
        episode["reference_units"] = reference_units

    project_dir = pm.get_project_path(name)
    _write_json(project_dir / "scripts" / "episode_1.json", episode)
    return project_dir


def _import_via_manual_zip(service: ProjectArchiveService, project_dir: Path, tmp_path: Path):
    archive_path = tmp_path / "ad-reference.zip"
    _make_manual_zip(project_dir, archive_path)
    shutil.rmtree(project_dir)
    return service.import_project_archive(archive_path, uploaded_filename="ad-reference.zip")


class TestProjectArchiveAdReference:
    def test_shots_generated_assets_and_fields_backfilled(self, tmp_path):
        # shots 缺 generated_assets / scenes / props：走 storyboard 修复分支补全，诊断含 auto_fixed
        pm = ProjectManager(tmp_path / "projects")
        project_dir = _create_ad_reference_project(pm, shots=[_ad_shot()])
        service = ProjectArchiveService(pm)

        result = _import_via_manual_zip(service, project_dir, tmp_path)

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        shot = imported["shots"][0]
        assert isinstance(shot["generated_assets"], dict)
        assert shot["generated_assets"]["status"] == "pending"
        assert shot["scenes"] == []
        assert shot["props"] == []
        assert result.diagnostics["auto_fixed"]

    def test_reference_units_generated_assets_backfilled(self, tmp_path):
        # reference_units 条目缺 generated_assets：就地回填
        pm = ProjectManager(tmp_path / "projects")
        units = [{"unit_id": "E1U1", "shot_ids": ["E1S01"], "references": []}]
        project_dir = _create_ad_reference_project(pm, shots=[_ad_shot()], reference_units=units)
        service = ProjectArchiveService(pm)

        result = _import_via_manual_zip(service, project_dir, tmp_path)

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        unit = imported["reference_units"][0]
        assert isinstance(unit["generated_assets"], dict)
        assert unit["generated_assets"]["status"] == "pending"

    def test_reference_units_preserves_existing_assets(self, tmp_path):
        # reference_units 条目已有 generated_assets（video_clip 已生成）：既有资产不被覆盖
        pm = ProjectManager(tmp_path / "projects")
        units = [
            {
                "unit_id": "E1U1",
                "shot_ids": ["E1S01"],
                "references": [],
                "generated_assets": {"video_clip": "reference_videos/E1U1.mp4", "status": "completed"},
            }
        ]
        project_dir = _create_ad_reference_project(pm, shots=[_ad_shot()], reference_units=units)
        _write_bytes(project_dir / "reference_videos" / "E1U1.mp4", b"mp4")
        service = ProjectArchiveService(pm)

        result = _import_via_manual_zip(service, project_dir, tmp_path)

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        assets = imported["reference_units"][0]["generated_assets"]
        assert assets["video_clip"] == "reference_videos/E1U1.mp4"
        assert assets["status"] == "completed"
        # 缺失字段被补齐，既有值保留
        assert "video_thumbnail" in assets
