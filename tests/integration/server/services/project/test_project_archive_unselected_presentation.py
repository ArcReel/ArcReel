"""单元换了选中视频后，基于旧视频版本物化的字幕与呈现登记在导出与导入时暂不适用，不让导出失败。"""

from __future__ import annotations

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.version_manager import VersionManager, selected_manual_upload_snapshot
from lib.project.project_manager import ProjectManager
from lib.speech.speech_presentation import presentation_artifact_paths
from server.services.currency import upload_finalize
from server.services.currency.typed_media_restore import restore_typed_media_resource
from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService
from server.services.project.project_archive import ARCHIVE_MANIFEST_NAME, ProjectArchiveService
from tests.factories import make_test_video
from tests.integration.server.services.presentation.timeline_render_support import (
    _install_video,
    edited_timeline,
    narration_segment,
    setup_project,
)

_SUBTITLE = ArtifactKey.episode_subtitle(1, "E1S01", "use_tts")
_PRESENTATION = ArtifactKey.episode_presentation(1, "E1S01", "use_tts")
_SUBTITLE_PATH, _PRESENTATION_PATH = presentation_artifact_paths(1, "E1S01", "use_tts")


async def _project_with_materialized_presentations(tmp_path: Path) -> tuple[ProjectManager, Path, str]:
    """两个分镜都有 AI 视频，导出过带旁白的剪映草稿，字幕与呈现已物化并登记。"""

    pm, project_path = setup_project(tmp_path)
    # 补齐剧本必填字段，导出的包才能通过导入校验
    with pm.locked_script("demo", "episode_1.json", validate=False) as script:
        script["title"] = "One"
        for segment in script["segments"]:
            segment["characters_in_segment"] = []
    timeline_id = await edited_timeline(pm)
    await TimelineJianyingDraftService(pm).render("demo", timeline_id, narration="with_narration")
    entries = ProjectArtifactManifestAdapter(project_path).snapshot_entries()
    assert {_SUBTITLE, _PRESENTATION} <= set(entries)
    return pm, project_path, timeline_id


async def _upload_e1s01_video(pm: ProjectManager, project_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(upload_finalize, "get_project_manager", lambda: pm)
    source = project_path.parent.parent / "upload" / "clip.mp4"
    make_test_video(source, duration_sec=1.0, fps=10)
    target = project_path / "videos" / "scene_E1S01.mp4"
    with source.open("rb") as stream:
        staged = await upload_finalize.stage_uploaded_video_stream(stream, target, max_bytes=50 * 1024 * 1024)
    await upload_finalize.finalize_shot_video_upload(
        project_name="demo",
        script_file="episode_1.json",
        shot_id="E1S01",
        project_path=project_path,
        video_rel="videos/scene_E1S01.mp4",
        staged_video=staged,
        versions=VersionManager(project_path),
        original_filename="clip.mp4",
    )
    history = VersionManager(project_path).get_versions("videos", "E1S01")
    assert selected_manual_upload_snapshot(history, "videos") is not None


async def _restore_e1s01_video(pm: ProjectManager, project_path: Path, version: int) -> None:
    await restore_typed_media_resource(
        project_manager=pm,
        versions=VersionManager(project_path),
        project_name="demo",
        resource_type="videos",
        resource_id="E1S01",
        version=version,
    )


def _archived_claims(archive_path: Path) -> set[str]:
    with zipfile.ZipFile(archive_path) as archive:
        envelope = json.loads(archive.read(f"demo/{ARCHIVE_MANIFEST_NAME}"))
    return set(envelope["artifact_manifest"]["entries"])


def _status(project_path: Path, key: ArtifactKey, artifact_path: str) -> ArtifactStatus:
    return ArtifactCurrencyResolver(project_path).compare(key, artifact_path=artifact_path).status


async def test_manual_upload_keeps_export_available_and_full_archive_keeps_the_old_claims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, project_path, timeline_id = await _project_with_materialized_presentations(tmp_path)
    claims_before = ProjectArtifactManifestAdapter(project_path).snapshot_entries()

    await _upload_e1s01_video(pm, project_path, monkeypatch)

    # 剪映草稿下载：下载令牌走下面的导出校验，草稿打包本身照常可用
    package, _name = await TimelineJianyingDraftService(pm).package_download(
        "demo", timeline_id, narration="with_narration", draft_root="/Users/me/JianyingPro Drafts", jianying_version="6"
    )
    shutil.rmtree(package.parent)

    # 上传只改视频：同单元的字幕与呈现登记原样保留
    claims_after = ProjectArtifactManifestAdapter(project_path).snapshot_entries()
    assert {key: claims_after[key] for key in (_SUBTITLE, _PRESENTATION)} == {
        key: claims_before[key] for key in (_SUBTITLE, _PRESENTATION)
    }
    service = ProjectArchiveService(pm)
    for scope in ("current", "full"):
        service.get_export_diagnostics("demo", scope=scope)

    # 仅当前版本的包不带旧 AI 视频的快照，旧登记在包里无从核验，不随包；完整包连同快照一起带上
    current_archive, _ = service.export_project("demo", scope="current")
    assert not {_SUBTITLE.encode(), _PRESENTATION.encode()} & _archived_claims(current_archive)
    full_archive, _ = service.export_project("demo", scope="full")
    assert {_SUBTITLE.encode(), _PRESENTATION.encode()} <= _archived_claims(full_archive)

    # 完整包导入后切回 AI 版本，旧登记照常有效
    shutil.rmtree(project_path)
    service.import_project_archive(full_archive, uploaded_filename="demo.zip")
    imported = pm.get_project_path("demo")
    imported_claims = ProjectArtifactManifestAdapter(imported).snapshot_entries()
    assert imported_claims[_SUBTITLE] == claims_before[_SUBTITLE]
    await _restore_e1s01_video(pm, imported, 1)
    assert _status(imported, _SUBTITLE, _SUBTITLE_PATH) is ArtifactStatus.CURRENT
    assert _status(imported, _PRESENTATION, _PRESENTATION_PATH) is ArtifactStatus.CURRENT


async def test_current_archive_without_the_old_claims_imports(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm, project_path, _timeline_id = await _project_with_materialized_presentations(tmp_path)
    await _upload_e1s01_video(pm, project_path, monkeypatch)
    service = ProjectArchiveService(pm)
    archive_path, _ = service.export_project("demo", scope="current")

    shutil.rmtree(project_path)
    service.import_project_archive(archive_path, uploaded_filename="demo.zip")

    imported_claims = ProjectArtifactManifestAdapter(pm.get_project_path("demo")).snapshot_entries()
    assert ArtifactKey.episode_video(1, "E1S01") in imported_claims
    assert not {_SUBTITLE, _PRESENTATION} & set(imported_claims)


async def test_switching_back_to_the_ai_video_after_upload_keeps_the_old_claims_current(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, project_path, _timeline_id = await _project_with_materialized_presentations(tmp_path)
    await _upload_e1s01_video(pm, project_path, monkeypatch)

    await _restore_e1s01_video(pm, project_path, 1)

    assert _status(project_path, _SUBTITLE, _SUBTITLE_PATH) is ArtifactStatus.CURRENT
    assert _status(project_path, _PRESENTATION, _PRESENTATION_PATH) is ArtifactStatus.CURRENT
    archive_path, _ = ProjectArchiveService(pm).export_project("demo", scope="current")
    assert {_SUBTITLE.encode(), _PRESENTATION.encode()} <= _archived_claims(archive_path)


async def test_regenerating_or_switching_the_ai_video_keeps_export_available(tmp_path: Path) -> None:
    pm, project_path, _timeline_id = await _project_with_materialized_presentations(tmp_path)
    service = ProjectArchiveService(pm)

    # AI 重新生成出版本 2，字幕与呈现尚未按新视频重新物化
    _install_video(project_path, narration_segment("E1S01", "旁白一句"), 1.0)
    for scope in ("current", "full"):
        service.get_export_diagnostics("demo", scope=scope)

    # 切回版本 1，再切到版本 2：两次切换后导出都可用
    await _restore_e1s01_video(pm, project_path, 1)
    service.get_export_diagnostics("demo", scope="current")
    await _restore_e1s01_video(pm, project_path, 2)
    service.get_export_diagnostics("demo", scope="current")
    full_archive, _ = service.export_project("demo", scope="full")
    assert {_SUBTITLE.encode(), _PRESENTATION.encode()} <= _archived_claims(full_archive)


async def test_old_claims_that_do_not_hold_for_their_own_video_version_still_fail_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, project_path, _timeline_id = await _project_with_materialized_presentations(tmp_path)
    await _upload_e1s01_video(pm, project_path, monkeypatch)
    subtitle_file = project_path / _SUBTITLE_PATH
    subtitle = json.loads(subtitle_file.read_text(encoding="utf-8"))
    subtitle["cues"][0]["text"] = "被改过的字幕"
    subtitle_file.write_text(json.dumps(subtitle, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unprovable formal claims"):
        ProjectArchiveService(pm).get_export_diagnostics("demo", scope="current")


async def test_old_claims_naming_a_missing_video_version_still_fail_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, project_path, _timeline_id = await _project_with_materialized_presentations(tmp_path)
    await _upload_e1s01_video(pm, project_path, monkeypatch)
    versions_path = project_path / "versions" / "versions.json"
    versions = json.loads(versions_path.read_text(encoding="utf-8"))
    bucket = versions["videos"]["E1S01"]
    bucket["versions"] = [record for record in bucket["versions"] if record["version"] != 1]
    versions_path.write_text(json.dumps(versions, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError, match="unprovable formal claims"):
        ProjectArchiveService(pm).get_export_diagnostics("demo", scope="current")
