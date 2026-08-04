"""归档导入针对 reference_video 模式（video_units）的修复测试。

覆盖 _repair_script_payload 对 generation_mode=reference_video 项目剧本里
video_units 的处理：确保导出-导入往返时 video_units[*].generated_assets
的路径规范化与版本回溯正常触发（该函数按 content_mode 走 segments/scenes
的分支不覆盖 video_units，须单独校验）。
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from lib.config.registry import PROVIDER_REGISTRY
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
        "shots": [{"text": "镜头一"}],
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

    @pytest.mark.integration
    def test_import_migrates_legacy_per_shot_duration_before_validation(self, tmp_path):
        """存量归档的 unit 仍是收编前形状（时长挂在 shots 上、无 unit 级 duration_seconds）：
        结构校验要求 duration_seconds 落在合理区间内，早于迁移执行的话会把这类归档直接拒绝，
        永远走不到能修复它的迁移器。修复须先于校验跑一次迁移。
        """
        pm = ProjectManager(tmp_path / "projects")
        legacy_unit = {
            "unit_id": "E1U1",
            "shots": [{"duration": 4, "text": "镜头一"}],
            "references": [],
            "transition_to_next": "cut",
            "generated_assets": {
                "storyboard_image": None,
                "storyboard_last_image": None,
                "video_clip": "reference_videos/E1U1.mp4",
                "video_thumbnail": "reference_videos/thumbnails/E1U1.jpg",
                "video_uri": REMOTE_VIDEO_URI,
                "grid_id": None,
                "grid_cell_index": None,
                "status": "completed",
            },
        }
        project_dir = _create_reference_video_project(pm, unit=legacy_unit)
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "legacy-duration.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="legacy-duration.zip")

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        unit = imported["video_units"][0]
        assert unit["duration_seconds"] == 4
        assert "duration" not in unit["shots"][0]

    @pytest.mark.integration
    def test_import_resolves_tiers_for_legacy_provider_alias(self, tmp_path):
        """归档修复跑在 provider 归一化之前：video_backend 仍是 legacy 别名时也要解析出档位。

        解析落空会退回结构 clamp 把非档位秒数固化，而迁移幂等——等 migrate_project_dir 把
        provider 归一化之后，已经没有第二次取档的机会。
        """
        pm = ProjectManager(tmp_path / "projects")
        legacy_unit = {
            "unit_id": "E1U1",
            "shots": [{"duration": 6, "text": "镜头一"}, {"duration": 4, "text": "镜头二"}],
            "references": [],
            "transition_to_next": "cut",
            "generated_assets": {
                "storyboard_image": None,
                "storyboard_last_image": None,
                "video_clip": "reference_videos/E1U1.mp4",
                "video_thumbnail": "reference_videos/thumbnails/E1U1.jpg",
                "video_uri": REMOTE_VIDEO_URI,
                "grid_id": None,
                "grid_cell_index": None,
                "status": "completed",
            },
        }
        project_dir = _create_reference_video_project(pm, unit=legacy_unit)
        # legacy 别名：档位只能靠归一化后查 registry 得到。
        project_file = project_dir / "project.json"
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        payload["video_backend"] = "gemini/veo-3.1-generate-preview"
        payload.pop("schema_version", None)
        project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        service = ProjectArchiveService(pm)
        archive_path = tmp_path / "legacy-alias.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="legacy-alias.zip")

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        # 求和 10s 不是该型号档位成员，取档后落盘的秒数必是成员。
        veo_tiers = PROVIDER_REGISTRY["gemini-aistudio"].models["veo-3.1-generate-preview"].supported_durations
        assert imported["video_units"][0]["duration_seconds"] in veo_tiers

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

    def test_import_resets_invalid_generated_assets(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(video_clip=None, generated_assets="corrupted-value")
        project_dir = _create_reference_video_project(pm, unit=unit, write_clip=False, write_thumbnail=False)
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "invalid-assets.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="invalid-assets.zip")

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        assets = imported["video_units"][0]["generated_assets"]
        assert isinstance(assets, dict)
        assert assets["status"] == "pending"
        assert any(item["code"] == "invalid_generated_assets" for item in result.diagnostics["auto_fixed"])

    def test_export_resets_invalid_generated_assets(self, tmp_path):
        # 导出与导入共用 _repair_project_tree，但导出修的是快照副本：包内是干净结构，
        # 源项目磁盘上的脏值保持原样（导出不改用户数据）。
        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(video_clip=None, generated_assets="corrupted-value")
        project_dir = _create_reference_video_project(pm, unit=unit, write_clip=False, write_thumbnail=False)
        service = ProjectArchiveService(pm)

        diagnostics = service.get_export_diagnostics("refdemo")
        assert any(item["code"] == "invalid_generated_assets" for item in diagnostics["auto_fixed"])

        archive_path, _ = service.export_project("refdemo")
        try:
            with zipfile.ZipFile(archive_path) as archive:
                exported = json.loads(archive.read("refdemo/scripts/episode_1.json").decode("utf-8"))
        finally:
            archive_path.unlink(missing_ok=True)

        assets = exported["video_units"][0]["generated_assets"]
        assert isinstance(assets, dict)
        assert assets["status"] == "pending"

        on_disk = json.loads((project_dir / "scripts" / "episode_1.json").read_text(encoding="utf-8"))
        assert on_disk["video_units"][0]["generated_assets"] == "corrupted-value"

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

    @pytest.mark.integration
    def test_import_resolves_nfc_reference_against_nfd_registered_character(self, tmp_path):
        # references 已归一到 NFC（见 lib.asset_types.normalize_asset_name），登记侧的角色
        # key 仍可能是落盘的 NFD 原形；自愈逻辑须把两者判等，而非把已登记角色误判缺失、
        # 补出一份重复的占位定义。
        import unicodedata

        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        assert name_nfd != name_nfc

        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(
            video_clip="reference_videos/E1U1.mp4",
            references=[{"type": "character", "name": name_nfc}],
        )
        project_dir = _create_reference_video_project(pm, unit=unit)
        project = pm.load_project("refdemo")
        project["characters"][name_nfd] = {"description": "x"}
        pm.save_project("refdemo", project)

        service = ProjectArchiveService(pm)
        archive_path = tmp_path / "nfc-nfd.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="nfc-nfd.zip")

        imported_project = pm.load_project(result.project_name)
        assert imported_project["characters"].keys() == {name_nfd}
        assert not any(item["code"] == "placeholder_character_added" for item in result.diagnostics["auto_fixed"])

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
