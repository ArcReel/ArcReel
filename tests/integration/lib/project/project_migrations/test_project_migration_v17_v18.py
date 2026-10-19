"""v17→v18 迁移：剪辑时间线修订删去 Agent 轮次字段；补录此前因生成缺口跳过的产物。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from lib.artifacts.artifact_currency import (
    ArtifactCurrencyResolver,
    resolve_current_artifact_basis,
    resolve_current_artifact_target,
)
from lib.artifacts.artifact_manifest import MANIFEST_FILENAME, ArtifactKey, ProjectArtifactManifestAdapter
from lib.artifacts.artifact_planner import plan_artifact_target_state_by_gap_rule
from lib.config.resolver import ConfigResolver
from lib.config.retired_model_ids import TEXT_BACKEND_SETTING_KEYS
from lib.edit_timeline import EditTimelineService
from lib.infra.app_data_dir import app_data_dir
from lib.infra.content_digest import sha256_file
from lib.infra.json_io import atomic_write_json
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import load_migration_report, skipped_artifact_identity
from lib.project.project_migrations import CURRENT_SCHEMA_VERSION, v17_to_v18_timeline_agent_turn
from lib.project.project_migrations.runner import cleanup_stale_backups, migrate_project_dir
from lib.speech.speech_composition import SpeechProblemCode, admit_script_unit
from lib.workflow.workflow_state import WorkflowStateService
from tests.legacy_project_shapes import (
    advance_project_schema,
    without_version_content_digests,
    write_legacy_drama_storyboard_project,
    write_legacy_presentation_project,
    write_legacy_reference_video_project,
    write_legacy_retired_model_references_project,
    write_legacy_storyboard_project,
    write_legacy_timeline_agent_turn_project,
    write_legacy_tts_narration_project,
    write_legacy_unregistrable_asset_sheet_project,
    write_undescribed_style_bases_project,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


async def test_schema_17_renamed_model_references_resolve_to_successors(db_factory) -> None:
    project_dir = write_legacy_retired_model_references_project(app_data_dir() / "projects")
    legacy = _read_json(project_dir / "project.json")
    assert legacy["schema_version"] == 17

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json") == {
        **legacy,
        "default_image_backend": "gemini-aistudio/gemini-3.1-flash-image",
        "image_provider_i2i": "gemini-vertex/gemini-3-pro-image",
        # 已下架而没有后继的模型原样保留，运行时报错由用户重新选择
        "video_backend": "openai/sora-2",
        # 按旧 ID 存的分辨率跟到后继 ID 下；后继 ID 已有设置时以它为准，旧键原样留下
        "model_settings": {
            "gemini-aistudio/gemini-3.1-flash-image": {"resolution": "4K"},
            "gemini-vertex/gemini-3-pro-image-preview": {"resolution": "2K"},
            "gemini-vertex/gemini-3-pro-image": {"resolution": "1K"},
        },
        "schema_version": CURRENT_SCHEMA_VERSION,
    }
    migrated = _read_json(project_dir / "project.json")
    resolver = ConfigResolver(db_factory)
    t2i = await resolver.resolve_image_backend(migrated, None, generation_type="t2i")
    i2i = await resolver.resolve_image_backend(migrated, None, generation_type="i2i")
    assert (t2i.provider_id, t2i.model_id) == ("gemini-aistudio", "gemini-3.1-flash-image")
    assert (i2i.provider_id, i2i.model_id) == ("gemini-vertex", "gemini-3-pro-image")
    assert await resolver.resolve_resolution(migrated, t2i.provider_id, t2i.model_id) == "4K"
    assert await resolver.resolve_resolution(migrated, i2i.provider_id, i2i.model_id) == "1K"


@pytest.mark.parametrize(
    ("retired", "successor"),
    [
        ("ark/doubao-seed-2-0-lite-260215", "ark/doubao-seed-2-1-lite-260915"),
        ("ark/doubao-seed-2-0-mini-260215", "ark/doubao-seed-2-0-mini-260428"),
        ("grok/grok-4-1-fast-reasoning", "grok/grok-4.3"),
        ("grok/grok-4-1-fast-non-reasoning", "grok/grok-4.3"),
    ],
)
def test_schema_17_retired_text_model_references_resolve_to_successors(
    tmp_path: Path, retired: str, successor: str
) -> None:
    project_dir = write_legacy_storyboard_project(tmp_path, name="legacy-retired-text-models")
    advance_project_schema(project_dir, to_version=17)
    project_path = project_dir / "project.json"
    project = {**_read_json(project_path), **dict.fromkeys(TEXT_BACKEND_SETTING_KEYS, retired)}
    project_path.write_text(json.dumps(project), encoding="utf-8")

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_path) == {
        **project,
        **dict.fromkeys(TEXT_BACKEND_SETTING_KEYS, successor),
        "schema_version": CURRENT_SCHEMA_VERSION,
    }


async def test_schema_17_timeline_with_agent_turn_reads_after_migration(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = write_legacy_timeline_agent_turn_project(projects_root)
    timeline_path = project_dir / "edit_timelines" / "episode_1" / "tl-0000abcd.json"
    legacy = _read_json(timeline_path)
    assert _read_json(project_dir / "project.json")["schema_version"] == 17

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_SCHEMA_VERSION
    migrated = _read_json(timeline_path)
    assert migrated == {
        **legacy,
        "revisions": [
            {key: value for key, value in revision.items() if key != "agent_turn"} for revision in legacy["revisions"]
        ],
    }
    assert [path.name for path in timeline_path.parent.glob("tl-0000abcd.json.bak.v17-*")]

    service = EditTimelineService(ProjectManager(tmp_path))
    (summary,) = await service.list_timelines(project_dir.name)
    assert (summary.id, summary.revision, summary.update_summary) == ("tl-0000abcd", 2, "调整音量")
    history = await service.list_revisions(project_dir.name, "tl-0000abcd")
    assert [(item.number, item.author.kind) for item in history.revisions] == [(1, "arcreel_agent"), (2, "creator")]


def test_stale_timeline_backup_is_reclaimed_after_its_timeline_is_deleted(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = write_legacy_timeline_agent_turn_project(projects_root)
    timeline_path = project_dir / "edit_timelines" / "episode_1" / "tl-0000abcd.json"
    assert migrate_project_dir(project_dir) is True
    (backup,) = timeline_path.parent.glob("tl-0000abcd.json.bak.v17-*")
    timeline_path.unlink()
    os.utime(backup, (0, 0))

    cleanup_stale_backups(projects_root)

    assert not backup.exists()


def test_symlinked_timeline_directories_outside_project_are_left_untouched(tmp_path: Path) -> None:
    projects_root = tmp_path / "projects"
    project_dir = write_legacy_timeline_agent_turn_project(projects_root)
    episode_dir = project_dir / "edit_timelines" / "episode_1"
    external_dir = tmp_path / "external-timelines"
    episode_dir.rename(external_dir)
    try:
        episode_dir.symlink_to(external_dir, target_is_directory=True)
        (project_dir / "edit_timelines" / "episode_2").mkdir()
        (project_dir / "edit_timelines" / "episode_2" / "tl-linked.json").symlink_to(external_dir / "tl-0000abcd.json")
    except (NotImplementedError, OSError):
        pytest.skip("symlinks are unavailable on this platform")
    external_file = external_dir / "tl-0000abcd.json"
    before = external_file.read_bytes()

    assert migrate_project_dir(project_dir) is True

    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_SCHEMA_VERSION
    assert external_file.read_bytes() == before
    assert sorted(path.name for path in external_dir.iterdir()) == ["tl-0000abcd.json"]
    assert not list((project_dir / "edit_timelines").rglob("*.bak.v17-*"))


def _manifest_entries(project_dir: Path) -> dict[str, Any]:
    return _read_json(project_dir / MANIFEST_FILENAME)["entries"]


def test_artifacts_skipped_for_generation_input_gaps_are_backfilled_without_touching_registered_entries(
    tmp_path: Path,
) -> None:
    """v17 项目里空描述的场景图没有登记：这一步只补录它，已登记条目逐字节不变，报告不再列它。"""
    project_dir = write_legacy_unregistrable_asset_sheet_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=17)
    scene_key = ArtifactKey.asset_sheet("scene", "祠堂").encode()
    before = _manifest_entries(project_dir)
    assert scene_key not in before

    assert migrate_project_dir(project_dir) is True

    after = _manifest_entries(project_dir)
    assert {key: after[key] for key in before} == before
    assert after[scene_key]["artifact_path"] == "scenes/祠堂.png"
    resolver = ArtifactCurrencyResolver(project_dir)
    for key in set(after) - set(before):
        comparison = resolver.compare(ArtifactKey.decode(key), artifact_path=after[key]["artifact_path"])
        assert comparison.status.value == "current", key
    report = load_migration_report(project_dir)
    assert report is not None
    assert [item for item in report.skipped if item.resource_id == "祠堂"] == []
    assert list(project_dir.glob(f"{MANIFEST_FILENAME}.bak.v17-*"))


def test_a_mixed_speech_stock_video_is_registered_but_its_unit_still_needs_replanning(tmp_path: Path) -> None:
    """说书片段同时写了原文和角色台词：存量视频登记为 current，单位仍报混合发声、生成仍被拒。"""
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][0]["video_prompt"]["dialogue"] = [{"speaker": "张三", "line": "别回头。"}]
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    assert migrate_project_dir(project_dir) is True

    video = ArtifactKey.episode_video(1, "E1S1")
    comparison = ArtifactCurrencyResolver(project_dir).compare(video, artifact_path="videos/scene_E1S1.mp4")
    assert comparison.status.value == "current"
    report = load_migration_report(project_dir)
    assert report is not None
    assert skipped_artifact_identity(video) not in {item.identity for item in report.skipped}
    segment = _read_json(script_path)["segments"][0]
    admission = admit_script_unit("segments", segment)
    assert not admission.allowed
    assert [problem.code for problem in admission.preparation.problems] == [SpeechProblemCode.MIXED_SPEECH]


def test_a_stock_video_of_a_unit_marked_for_replanning_over_mixed_speech_is_registered(tmp_path: Path) -> None:
    """机器产出的混合发声片段落盘时带 needs_replan：存量视频照样登记，片段的分镜图仍不算正式产物。"""
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    segment = script["segments"][0]
    segment["video_prompt"]["dialogue"] = [{"speaker": "张三", "line": "别回头。"}]
    segment["needs_replan"] = True
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    assets = segment["generated_assets"]

    assert migrate_project_dir(project_dir) is True

    video = ArtifactKey.episode_video(1, "E1S1")
    resolver = ArtifactCurrencyResolver(project_dir)
    assert resolver.compare(video, artifact_path=assets["video_clip"]).status.value == "current"
    entries = ProjectArtifactManifestAdapter(project_dir).snapshot_entries()
    assert ArtifactKey.episode_storyboard(1, "E1S1") not in entries
    report = load_migration_report(project_dir)
    assert report is None or not [item for item in report.skipped if item.resource_id == "E1S1"]
    segment = _read_json(script_path)["segments"][0]
    assert segment["needs_replan"] is True
    admission = admit_script_unit("segments", segment)
    assert not admission.allowed
    assert SpeechProblemCode.NEEDS_REPLAN in {problem.code for problem in admission.preparation.problems}


def test_a_stock_video_whose_storyboard_is_gone_still_depends_on_its_end_frame(tmp_path: Path) -> None:
    """分镜图已不在、尾帧还在：补录的视频依据只把分镜图记为缺席，之后换掉尾帧视频读作 stale。"""
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][0]["end_frame_image"] = "end_frames/scene_E1S1.png"
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")
    end_frame = project_dir / "end_frames" / "scene_E1S1.png"
    end_frame.parent.mkdir()
    end_frame.write_bytes(b"end frame")
    (project_dir / "storyboards" / "scene_E1S1.png").unlink()

    assert migrate_project_dir(project_dir) is True

    video = ArtifactKey.episode_video(1, "E1S1")
    entry = _manifest_entries(project_dir)[video.encode()]
    assert ArtifactCurrencyResolver(project_dir).compare(video, artifact_path=entry["artifact_path"]).status.value == (
        "current"
    )
    end_frame.write_bytes(b"another end frame")

    resolver = ArtifactCurrencyResolver(project_dir)
    assert resolver.compare(video, artifact_path=entry["artifact_path"]).status.value == "stale"


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() == 0, reason="needs POSIX permissions that the current user cannot bypass"
)
def test_a_stock_video_whose_storyboard_cannot_be_opened_is_registered_with_the_storyboard_absent(
    tmp_path: Path,
) -> None:
    """分镜图在盘上却打不开：分镜图进「未能带入」，存量视频照常补录，依据把分镜图记为缺席。"""
    project_dir = write_legacy_storyboard_project(tmp_path / "projects")
    storyboard = project_dir / "storyboards" / "scene_E1S1.png"
    storyboard.chmod(0)
    try:
        assert migrate_project_dir(project_dir) is True
    finally:
        storyboard.chmod(0o644)

    assert ArtifactKey.episode_video(1, "E1S1").encode() in _manifest_entries(project_dir)
    report = load_migration_report(project_dir)
    assert report is not None
    assert ("episode-storyboard", "storyboards/scene_E1S1.png") in {
        (item.kind, item.artifact_path) for item in report.skipped
    }


@pytest.mark.parametrize(
    "write_project",
    [
        write_legacy_storyboard_project,
        write_legacy_drama_storyboard_project,
        write_legacy_reference_video_project,
        write_legacy_presentation_project,
    ],
)
def test_projects_without_generation_input_gaps_plan_identically_under_both_gap_rules(
    tmp_path: Path, write_project: Any
) -> None:
    """输入完整的项目两种缺口口径规划出同一份目标态：这一步不碰它们的清单。"""
    project_dir = write_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=17)
    before = _manifest_entries(project_dir)
    project = _read_json(project_dir / "project.json")

    refused = plan_artifact_target_state_by_gap_rule(project_dir, project, record_gaps=False)
    recorded = plan_artifact_target_state_by_gap_rule(project_dir, project, record_gaps=True)

    assert recorded.entries == refused.entries
    assert migrate_project_dir(project_dir) is True
    assert _manifest_entries(project_dir) == before


def test_a_grid_whose_reference_sheet_is_gone_is_backfilled_with_the_reference_recorded_absent(tmp_path: Path) -> None:
    """宫格记录冻结的参考图已读不到，原先联合图与切格分镜都没登记：补录后读作 current，依据记下缺席的参考。"""
    project_dir = write_undescribed_style_bases_project(tmp_path / "projects", "grid", route="grid")
    advance_project_schema(project_dir, to_version=17)
    grid_path = project_dir / "grids" / "grid_123456789abc.json"
    grid = _read_json(grid_path)
    grid["reference_images"] = [{"path": "characters/已删除.png", "name": "已删除", "ref_type": "character"}]
    grid_path.write_text(json.dumps(grid, ensure_ascii=False), encoding="utf-8")
    grid_key = ArtifactKey.episode_grid(1, "grid_123456789abc")
    members = [ArtifactKey.episode_storyboard(1, resource_id) for resource_id in ("E1S01", "E1S02")]
    manifest_path = project_dir / MANIFEST_FILENAME
    manifest = _read_json(manifest_path)
    for key in (grid_key, *members):
        manifest["entries"].pop(key.encode())
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    assert migrate_project_dir(project_dir) is True

    resolver = ArtifactCurrencyResolver(project_dir)
    entries = _manifest_entries(project_dir)
    for key in (grid_key, *members):
        comparison = resolver.compare(key, artifact_path=entries[key.encode()]["artifact_path"])
        assert comparison.status.value == "current", key
    basis = resolve_current_artifact_basis(project_dir, grid_key)
    assert basis is not None
    assert json.loads(basis.normalized_bytes())["inputs"]["absent"] == [
        {"code": "reference_asset_missing", "type": "character", "id": "已删除"}
    ]


def test_turning_a_presented_unit_into_mixed_speech_reads_its_media_layers_stale(tmp_path: Path) -> None:
    """已登记的单位改成混合发声：字幕与呈现模型的当前依据照常投影（准入问题记为缺席），全部读作 stale。"""
    project_dir = write_legacy_presentation_project(tmp_path / "projects")
    assert migrate_project_dir(project_dir) is True
    keys = (
        ArtifactKey.episode_video(1, "E1S01"),
        ArtifactKey.episode_subtitle(1, "E1S01", "post_production"),
        ArtifactKey.episode_presentation(1, "E1S01", "post_production"),
    )
    entries = _manifest_entries(project_dir)
    resolver = ArtifactCurrencyResolver(project_dir)
    for key in keys:
        assert resolver.compare(key, artifact_path=entries[key.encode()]["artifact_path"]).status.value == "current"
    script_path = project_dir / "scripts" / "episode_1.json"
    script = _read_json(script_path)
    script["segments"][0]["video_prompt"] = {
        "action": "阿离转身",
        "camera_motion": "Static",
        "dialogue": [{"speaker": "阿离", "line": "别回头。"}],
    }
    script_path.write_text(json.dumps(script, ensure_ascii=False), encoding="utf-8")

    resolver = ArtifactCurrencyResolver(project_dir)
    for key in keys:
        assert resolver.compare(key, artifact_path=entries[key.encode()]["artifact_path"]).status.value == "stale", key
    for key in keys[1:]:
        target = resolve_current_artifact_target(project_dir, key)
        assert target is not None, key
        assert target.basis_digest != entries[key.encode()]["basis_digest"]


_CONTENT_FACT_FIELDS = ("content_digest", "content_size", "content_mtime_ns")


def _media_currency(project_dir: Path) -> tuple[Any, ...]:
    """用户看到的视频与配音时效：项目摘要的视频计数，加上制作状态里视频与配音的分组。"""

    projects = ProjectManager(project_dir.parent.parent)
    service = WorkflowStateService(projects)
    summary = service.get_project_summary(project_dir.name)
    artifacts = service.get_status(project_dir.name, 1).artifacts
    groups = ("current_ids", "stale_ids", "missing_ids")
    return (
        summary.episodes[0].videos,
        {name: tuple(artifacts[name][group] for group in groups) for name in ("videos", "audio")},
    )


def _selected_record(project_dir: Path, resource_type: str, resource_id: str) -> dict[str, Any]:
    resource = _read_json(project_dir / "versions" / "versions.json")[resource_type][resource_id]
    (record,) = [record for record in resource["versions"] if record["version"] == resource["current_version"]]
    return record


def _assert_content_recorded(project_dir: Path, key: ArtifactKey, resource_type: str) -> None:
    entry = _manifest_entries(project_dir)[key.encode()]
    formal = project_dir / entry["artifact_path"]
    digest = sha256_file(formal)
    stat = formal.stat()
    assert (entry["content_digest"], entry["content_size"], entry["content_mtime_ns"]) == (
        digest,
        stat.st_size,
        stat.st_mtime_ns,
    ), key
    assert _selected_record(project_dir, resource_type, str(key.components[1]))["content_digest"] == digest, key


def test_stock_videos_and_narration_audio_get_content_digests_across_the_whole_chain(tmp_path: Path) -> None:
    """v7 存量项目一路迁到当前版本：正式文件与选中快照一致的视频和配音，清单条目与版本记录都补上内容指纹。"""
    project_dir = write_legacy_tts_narration_project(tmp_path / "projects")

    assert migrate_project_dir(project_dir) is True

    for unit_id in ("E1S1", "E1S2"):
        _assert_content_recorded(project_dir, ArtifactKey.episode_video(1, unit_id), "videos")
        _assert_content_recorded(project_dir, ArtifactKey.episode_audio(1, unit_id), "audio")
    videos, groups = _media_currency(project_dir)
    assert (videos.total, videos.available, videos.stale) == (2, 2, 0)
    # 项目 TTS 设置取最后一条配音的设置，第一条配音本就 stale，补录不改变它。
    assert groups["audio"] == (["E1S2"], ["E1S1"], [])


def _strip_content_digests(project_dir: Path) -> None:
    """抹掉这一步补录的内容指纹，把项目还原成没有补录时的样子。"""

    manifest_path = project_dir / MANIFEST_FILENAME
    manifest = _read_json(manifest_path)
    for entry in manifest["entries"].values():
        for field in _CONTENT_FACT_FIELDS:
            entry.pop(field, None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    versions_path = project_dir / "versions" / "versions.json"
    versions_path.write_text(
        json.dumps(without_version_content_digests(_read_json(versions_path)), ensure_ascii=False), encoding="utf-8"
    )


def test_media_that_cannot_be_proven_identical_to_its_selected_snapshot_is_not_backfilled(tmp_path: Path) -> None:
    """正式视频被换掉、配音快照损坏：两者都不补录，判 stale，与按「正式文件 = 选中快照」比对的结论相同。

    v17 项目由不记内容事实的旧版本写成，迁移前先抹掉夹具在激活时记下的事实。
    """
    project_dir = write_legacy_tts_narration_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=17)
    _strip_content_digests(project_dir)
    (project_dir / "videos" / "scene_E1S2.mp4").write_bytes(b"replaced outside ArcReel")
    snapshot = project_dir / _selected_record(project_dir, "audio", "E1S1")["file"]
    snapshot.write_bytes(b"corrupted snapshot")

    assert migrate_project_dir(project_dir) is True

    videos, groups = _media_currency(project_dir)
    entries = _manifest_entries(project_dir)
    for key, resource_type in (
        (ArtifactKey.episode_video(1, "E1S2"), "videos"),
        (ArtifactKey.episode_audio(1, "E1S1"), "audio"),
    ):
        assert not set(_CONTENT_FACT_FIELDS) & set(entries[key.encode()]), key
        assert "content_digest" not in _selected_record(project_dir, resource_type, str(key.components[1])), key
    _assert_content_recorded(project_dir, ArtifactKey.episode_video(1, "E1S1"), "videos")
    _assert_content_recorded(project_dir, ArtifactKey.episode_audio(1, "E1S2"), "audio")
    assert (videos.total, videos.available, videos.stale) == (2, 2, 1)
    assert groups == {"videos": (["E1S1"], ["E1S2"], []), "audio": (["E1S2"], ["E1S1"], [])}


def test_a_step_that_fails_after_the_backfill_committed_reruns_to_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """补录已提交、提升 schema 时写盘失败：重跑认得已写好的指纹，迁移照常完成。"""
    project_dir = write_legacy_tts_narration_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=17)

    def full_disk(path: Path, payload: object) -> None:
        if path.name == "project.json":
            raise OSError("disk full")
        atomic_write_json(path, payload)

    with monkeypatch.context() as patch:
        patch.setattr(v17_to_v18_timeline_agent_turn, "atomic_write_json", full_disk)
        with pytest.raises(OSError, match="disk full"):
            migrate_project_dir(project_dir)
    _assert_content_recorded(project_dir, ArtifactKey.episode_video(1, "E1S1"), "videos")

    assert migrate_project_dir(project_dir) is True
    assert _read_json(project_dir / "project.json")["schema_version"] == CURRENT_SCHEMA_VERSION
    _assert_content_recorded(project_dir, ArtifactKey.episode_audio(1, "E1S2"), "audio")


def test_a_failed_content_digest_backfill_leaves_nothing_half_written_and_reruns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """清单提交失败：版本记录回到改写前、项目停在 17；重跑补录完整。"""
    project_dir = write_legacy_tts_narration_project(tmp_path / "projects")
    advance_project_schema(project_dir, to_version=17)
    versions_before = (project_dir / "versions" / "versions.json").read_bytes()
    manifest_before = (project_dir / MANIFEST_FILENAME).read_bytes()

    def refuse(*_args: object, **_kwargs: object) -> bool:
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(ProjectArtifactManifestAdapter, "replace_entries_if_matches_atomically", refuse)
        with pytest.raises(OSError, match="disk full"):
            migrate_project_dir(project_dir)

    assert _read_json(project_dir / "project.json")["schema_version"] == 17
    assert (project_dir / "versions" / "versions.json").read_bytes() == versions_before
    assert (project_dir / MANIFEST_FILENAME).read_bytes() == manifest_before

    assert migrate_project_dir(project_dir) is True
    _assert_content_recorded(project_dir, ArtifactKey.episode_video(1, "E1S1"), "videos")
    _assert_content_recorded(project_dir, ArtifactKey.episode_audio(1, "E1S2"), "audio")
