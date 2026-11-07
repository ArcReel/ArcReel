"""Archive reference-video (video_units) repair tests.

Cover _repair_script_payload handling of video_units in generation_mode=reference_video
projects, ensuring export/import roundtrips normalize video_units[*].generated_assets
paths and trace versions correctly. Its content_mode segments/scenes branches
do not cover video_units, requiring separate validation.
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from lib.catalog import query as catalog
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report
from lib.project.resource_paths import resource_relative_path
from server.services.project.project_archive import ProjectArchiveService

REMOTE_VIDEO_URI = "https://cdn.example.com/v/E1U1.mp4"

# Distinguish omitted generated_assets using defaults from explicit None omitting fields.
_DEFAULT_ASSETS = object()


def _durations(provider_id: str, model_id: str) -> list[int]:
    model = catalog.get_model(provider_id, model_id)
    assert model is not None
    return model.supported_durations


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
    generated_assets: dict | object | None = _DEFAULT_ASSETS,
    text: str = "镜头一",
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
        "text": text,
        "duration_seconds": 4,
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
    pm.create_project_metadata(name, "RefDemo", "水墨风格，淡彩", "narration")

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
        # Acceptance: reference_videos uses prefix-free unit_id branches; lib.project.resource_paths exclusively owns path shapes.
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
        # video_uri is remote, never treat it as local overwrite paths.
        assert assets["video_uri"] == REMOTE_VIDEO_URI
        assert (project_dir / "reference_videos" / "E1U1.mp4").exists()
        assert (project_dir / "reference_videos" / "thumbnails" / "E1U1.jpg").exists()

    def test_import_migrates_legacy_per_shot_duration_before_validation(self, tmp_path):
        """Legacy units retain precoercion shapes: durations on shots without unit duration_seconds.
        Structure validation requires plausible duration_seconds; running before migration would reject archives
        before repair migrations can execute. Repair must migrate once before validation; later schema
        migration chains join shot bodies into unit bodies.
        """
        pm = ProjectManager(tmp_path / "projects")
        legacy_unit = {
            "unit_id": "E1U1",
            "shots": [{"duration": 4, "text": "镜头一"}],
            "references": [],
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
        # Missing-schema archives count as earliest versions; imports run full migration chains, including duration coercion.
        project_file = project_dir / "project.json"
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        payload.pop("schema_version", None)
        project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
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
        assert "shots" not in unit
        assert unit["text"] == "镜头一"

    @pytest.mark.parametrize(("legacy_seconds", "expected"), [(4, 4), (9, 10)])
    def test_import_migration_takes_the_union_of_both_bucket_tiers(self, tmp_path, legacy_seconds, expected):
        """Reference units use r2v/i2v by available images; import coercion unions both full declarations, matching live confirmation.

        r2v [6, 10], i2v [4, 6, 8], union [4, 6, 8, 10]: 4s is i2v-only and r2v-only selection changes it to
        6; 9s is invalid in both, i2v-only selection lowers to 8, while unchanged 9 is in neither bucket.
        """
        pm = ProjectManager(tmp_path / "projects")
        legacy_unit = {
            "unit_id": "E1U1",
            "shots": [{"duration": legacy_seconds, "text": "镜头一"}],
            "references": [],
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
        project_file = project_dir / "project.json"
        payload = json.loads(project_file.read_text(encoding="utf-8"))
        payload["video_provider_r2v"] = "minimax/MiniMax-Hailuo-2.3"
        payload["video_provider_i2v"] = "gemini-aistudio/veo-3.1-generate-preview"
        payload.pop("schema_version", None)
        project_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert _durations("minimax", "MiniMax-Hailuo-2.3") == [6, 10]
        assert _durations("gemini-aistudio", "veo-3.1-generate-preview") == [4, 6, 8]

        service = ProjectArchiveService(pm)
        archive_path = tmp_path / "legacy-two-buckets.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="legacy-two-buckets.zip")

        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        assert imported["video_units"][0]["duration_seconds"] == expected

    def test_import_resolves_tiers_for_legacy_provider_alias(self, tmp_path):
        """Archive repair precedes provider normalization; legacy video_backend aliases must still resolve tiers.

        Unresolved tiers fall back to structural clamps, persisting nontier seconds; idempotent migration leaves
        no second tier-selection opportunity after migrate_project_dir normalizes providers.
        """
        pm = ProjectManager(tmp_path / "projects")
        legacy_unit = {
            "unit_id": "E1U1",
            "shots": [{"duration": 6, "text": "镜头一"}, {"duration": 4, "text": "镜头二"}],
            "references": [],
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
        # Legacy aliases require normalized registry lookups to obtain tiers.
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
        # Summed 10s is not a model tier; persisted seconds after tier selection must be members.
        veo_tiers = _durations("gemini-aistudio", "veo-3.1-generate-preview")
        assert imported["video_units"][0]["duration_seconds"] in veo_tiers

    def test_import_restores_video_clip_from_version(self, tmp_path):
        pm = ProjectManager(tmp_path / "projects")
        # video_clip references invalid version paths; versions.json tracing materializes current files.
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
        # Export/import share _repair_project_tree, but exports repair snapshots: archives contain clean structures,
        # while original-project dirty disk values remain unchanged; exports never edit user data.
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

    def test_import_adds_placeholder_for_missing_character_speaker(self, tmp_path):
        # Speaker syntax identifies characters; missing definitions get placeholders like narration/drama.
        # Placeholder descriptions stay empty; repair text never becomes image-generation prompts.
        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(
            video_clip="reference_videos/E1U1.mp4",
            text="@[幽灵] 立在门口。\n@[幽灵]{谁在那儿？}",
        )
        project_dir = _create_reference_video_project(pm, unit=unit)
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "missing-char.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, confirm_report=True, uploaded_filename="missing-char.zip")

        imported_project = pm.load_project(result.project_name)
        assert imported_project["characters"]["幽灵"]["description"] == ""
        # Preimport-confirmed reports already include placeholders; successful diagnostics never repeat them.
        report = load_migration_report(pm.get_project_path(result.project_name))
        assert report is not None
        assert [(item.code, item.params) for item in report.adjusted] == [
            ("placeholder_asset_added", {"asset_type": "character", "name": "幽灵"})
        ]
        assert not any(item["code"] == "placeholder_character_added" for item in result.diagnostics["auto_fixed"])

    def test_import_warns_without_blocking_on_unresolved_mention(self, tmp_path):
        # Body @[名称] mentions derive references at execution; unregistered mentions only omit these references,
        # allowing imports with nonblocking warnings.
        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(
            video_clip="reference_videos/E1U1.mp4",
            text="雨夜的 @[缺失场景] 里，@[缺失道具] 摆在桌上。",
        )
        project_dir = _create_reference_video_project(pm, unit=unit)
        service = ProjectArchiveService(pm)

        archive_path = tmp_path / "unresolved-mention.zip"
        _make_manual_zip(project_dir, archive_path)
        shutil.rmtree(project_dir)

        result = service.import_project_archive(archive_path, uploaded_filename="unresolved-mention.zip")

        warnings = [item for item in result.diagnostics["warnings"] if item["code"] == "unresolved_mention"]
        assert len(warnings) == 1
        assert "缺失场景" in warnings[0]["message"]
        assert "缺失道具" in warnings[0]["message"]
        imported = json.loads(
            (pm.get_project_path(result.project_name) / "scripts" / "episode_1.json").read_text(encoding="utf-8")
        )
        assert imported["video_units"][0]["text"] == unit["text"]
        # Unresolved mentions never get placeholders; only speaker positions establish asset types.
        imported_project = pm.load_project(result.project_name)
        assert "缺失场景" not in imported_project["characters"]

    def test_import_resolves_nfc_speaker_against_nfd_registered_character(self, tmp_path):
        # Speaker names normalize NFC (lib.project.asset_types.normalize_asset_name), while registered character
        # keys may remain persisted NFD; self-healing must equate both rather than misclassify registered characters
        # as missing and add duplicate placeholders.
        import unicodedata

        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        assert name_nfd != name_nfc

        pm = ProjectManager(tmp_path / "projects")
        unit = _build_unit(
            video_clip="reference_videos/E1U1.mp4",
            text=f"@[{name_nfc}] 走进门。\n@[{name_nfc}]{{我回来了。}}",
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
        assert not any(item["code"] == "unresolved_mention" for item in result.diagnostics["warnings"])
