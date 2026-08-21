from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

import lib.script_review as script_review
from lib.artifact_activation import (
    activate_artifact_target_state,
    register_current_artifact,
    register_current_artifact_if_provable,
)
from lib.artifact_manifest import ArtifactBasisDescriptor, ArtifactKey, ProjectArtifactManifestAdapter
from lib.asset_inventory import complete_asset_inventory
from lib.episode_ledger import SOURCE_FINGERPRINTS_KEY, compute_source_fingerprints, discover_sources
from lib.json_io import atomic_write_json
from lib.narration_delivery import (
    TtsSynthesisSettings,
    build_narration_audio_basis,
    canonical_narration_text,
)
from lib.project_manager import ProjectManager
from lib.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    MIGRATION_FAILURE_FILENAME,
    RETRY_MIGRATION_ACTION,
)
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.resource_paths import resource_relative_path
from lib.source_revision import SourceScope, compute_source_revision
from lib.speech_composition import admit_script_unit
from lib.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.workflow_state import WorkflowStateService, planning_docs


def _make_project(
    tmp_path: Path,
    mode: str,
    *,
    generation_mode: str = "storyboard",
) -> tuple[ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    extras = {"generation_mode": generation_mode, "grid_storyboard": False}
    if mode == "ad":
        pm.create_project_metadata("demo", "Demo", "", mode, extras=extras, target_duration=30)
    else:
        pm.create_project_metadata("demo", "Demo", "", mode, extras=extras)
    return pm, pm.get_project_path("demo")


def _write_source_and_complete(pm: ProjectManager, project_path: Path, text: str = "原文") -> str:
    source = project_path / "source" / "novel.txt"
    source.write_text(text, encoding="utf-8")
    scope = SourceScope(kind="all")
    revision = compute_source_revision(project_path, pm.load_project("demo"), scope).revision
    assert revision is not None
    complete_asset_inventory(pm, "demo", scope, revision)
    return revision


def _write_artifact(project_path: Path, relative_path: str) -> None:
    path = project_path / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"artifact")


def _register_produced_artifacts(project_path: Path) -> None:
    """把已落盘的产物按规范目标态补录进产物清单。

    产物清单是读取已生成产物的唯一口径，落盘本身不代表已登记；这里走的是生产的补录路径。
    """
    activate_artifact_target_state(project_path, bump_schema=False)


def _write_registered_script(
    project_path: Path,
    script: dict,
    *,
    episode: int = 1,
    filename: str = "episode_1.json",
) -> None:
    """写剧本并登记认领——落盘本身不进读取口径，未登记的剧本一律按 missing 处理。

    剧集剧本的取证以 step1 为输入，故一并登记 step1（广告/短片无 step1，登记为不可取证即跳过）。
    """
    atomic_write_json(project_path / "scripts" / filename, script)
    register_current_artifact_if_provable(project_path, ArtifactKey.episode_step1(episode))
    register_current_artifact(project_path, ArtifactKey.episode_script(episode))


def _register_step1(project_path: Path, episode: int = 1) -> None:
    """把已落盘的 step1 登记认领——step1 同样只按产物清单读取。"""
    register_current_artifact(project_path, ArtifactKey.episode_step1(episode))


def _edit_claimed_script(
    project_path: Path,
    payload: object,
    *,
    filename: str = "episode_1.json",
) -> None:
    """把一份已登记认领的剧本在盘上改成 payload（外部编辑造成的脏数据）。

    未登记的文件根本不进读取口径，所以脏数据要能被观察到，必须先有认领。
    """
    path = project_path / "scripts" / filename
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        atomic_write_json(path, payload)


def _write_episode_source(project_path: Path, episode: int = 1, text: str = "原文") -> None:
    """写分集原文——step1 的取证以它为输入，缺了 step1 就无法登记。"""
    path = project_path / "source" / f"episode_{episode}.txt"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit_media_version(project_path: Path, resource_type: str, resource_id: str) -> str:
    """按规范路径落一个媒体产物并提交版本记录，返回项目内相对路径。

    视频 / 旁白配音的清单取证只认版本记录里的执行事实，落盘文件本身不构成证据。
    """
    relative = resource_relative_path(resource_type, resource_id)
    current = project_path / relative
    current.parent.mkdir(parents=True, exist_ok=True)
    staged = current.parent / f".{resource_id}.upload{current.suffix}"
    staged.write_bytes(b"artifact")
    VersionManager(project_path).commit_staged_version(
        resource_type,
        resource_id,
        "",
        staged_file=staged,
        current_file=current,
        source=MANUAL_UPLOAD_VERSION_SOURCE,
    )
    return relative


_TTS_SETTINGS = TtsSynthesisSettings(
    provider_id="dashscope",
    model_id="qwen3-tts-flash",
    voice="Cherry",
    speed=None,
)


def _commit_audio_version(
    project_path: Path,
    item: dict,
    resource_id: str,
    *,
    skeleton_kind: str = "segments",
    episode: int = 1,
    script_file: str = "episode_1.json",
) -> str:
    """按 TTS 执行事实提交一条旁白配音版本记录，返回项目内相对路径。"""
    preparation = admit_script_unit(skeleton_kind, item).preparation
    basis = ArtifactBasisDescriptor.from_basis(build_narration_audio_basis(preparation, _TTS_SETTINGS))
    relative = resource_relative_path("audio", resource_id)
    current = project_path / relative
    current.parent.mkdir(parents=True, exist_ok=True)
    current.write_bytes(b"audio")
    VersionManager(project_path).add_version(
        "audio",
        resource_id,
        canonical_narration_text(preparation),
        source_file=current,
        artifact_episode=episode,
        artifact_audio_basis=basis.to_dict(),
        execution_script_file=script_file,
        tts_actual_duration_seconds=5.0,
        tts_provider_id=_TTS_SETTINGS.provider_id,
        tts_model_id=_TTS_SETTINGS.model_id,
        tts_voice=_TTS_SETTINGS.voice,
        tts_speed=_TTS_SETTINGS.speed,
        tts_basis_digest=basis.digest,
    )
    return relative


def _complete_episode_media(project_path: Path, resource_id: str = "E1S01") -> dict[str, str]:
    """落齐一个分镜的分镜图与视频（含版本记录），返回可写进 generated_assets 的指针。"""
    storyboard = resource_relative_path("storyboards", resource_id)
    _write_artifact(project_path, storyboard)
    return {
        "storyboard_image": storyboard,
        "video_clip": _commit_media_version(project_path, "videos", resource_id),
    }


def _valid_narration_segment(**overrides: object) -> dict:
    segment = {
        "segment_id": "E1S01",
        "duration_seconds": 4,
        "novel_text": "原文",
        "characters_in_segment": [],
        "scenes": [],
        "props": [],
        "image_prompt": "画面",
        "video_prompt": "动作",
        "generated_assets": {},
    }
    segment.update(overrides)
    return segment


def _valid_drama_scene(**overrides: object) -> dict:
    scene = {
        "scene_id": "E1S01",
        "duration_seconds": 4,
        "characters_in_scene": [],
        "scenes": [],
        "props": [],
        "image_prompt": "画面",
        "video_prompt": "动作",
        "generated_assets": {},
    }
    scene.update(overrides)
    return scene


def _valid_ad_shot(**overrides: object) -> dict:
    shot = {
        "shot_id": "E1S01",
        "duration_seconds": 4,
        "voiceover_text": "",
        "characters_in_shot": [],
        "scenes": [],
        "props": [],
        "products_in_shot": [],
        "image_prompt": "画面",
        "video_prompt": "动作",
        "generated_assets": {},
    }
    shot.update(overrides)
    return shot


def _valid_video_unit(**overrides: object) -> dict:
    unit = {
        "unit_id": "E1U01",
        "duration_seconds": 8,
        "text": "镜头",
        "generated_assets": {},
    }
    unit.update(overrides)
    return unit


@pytest.mark.integration
def test_narration_empty_inventory_completes_and_advances_to_episode_plan(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    revision = _write_source_and_complete(pm, project_path)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.schema_version == 1
    assert status.project.content_mode == "narration"
    assert status.source_revision == revision
    assert status.state == "EPISODE_PLAN"
    assert status.artifacts["asset_inventory"]["state"] == "current"
    assert status.artifacts["asset_sheets"] == {
        "character": {"current_ids": [], "missing_ids": [], "stale_ids": []},
        "scene": {"current_ids": [], "missing_ids": [], "stale_ids": []},
        "prop": {"current_ids": [], "missing_ids": [], "stale_ids": []},
        "product": {"current_ids": [], "missing_ids": [], "stale_ids": []},
    }
    assert status.next_action.type == "plan_episodes"


@pytest.mark.integration
def test_drama_target_comes_from_ledger_not_derived_filenames(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "drama")
    _write_source_and_complete(pm, project_path)
    (project_path / "source" / "episode_1.txt").write_text("派生集文件", encoding="utf-8")
    (project_path / "scripts" / "episode_1.json").write_text("{}", encoding="utf-8")

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 2,
                "title": "第二集",
                "script_file": "scripts/custom-name.json",
                "ledger_status": "planned",
                "source_range": {"source_file": "source/novel.txt", "start": 0, "end": 2},
            }
        ]

    pm.update_project("demo", _plan)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.target is not None
    assert status.target.episode == 2
    assert status.target.script == "scripts/custom-name.json"
    assert status.target.script_filename == "custom-name.json"
    assert status.state == "STEP1_CONTENT"
    assert status.next_action.type == "prepare_step1"
    assert status.next_action.args["preprocessor"] == "normalize-drama-script"


@pytest.mark.integration
def test_ad_is_episode_one_and_skips_asset_inventory_and_step1(tmp_path: Path) -> None:
    pm, _project_path = _make_project(tmp_path, "ad")

    status = WorkflowStateService(pm).get_status("demo")

    assert status.target is not None
    assert status.target.episode == 1
    assert status.artifacts["asset_inventory"]["state"] == "not_applicable"
    assert status.gates["step1_review"]["state"] == "not_applicable"
    assert status.state == "FINAL_SCRIPT"
    assert status.next_action.type == "generate_script"


@pytest.mark.integration
def test_media_paths_must_resolve_to_project_files_before_becoming_current(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "shots": [
                _valid_ad_shot(
                    generated_assets={
                        "storyboard_image": "../outside.png",
                        "video_clip": "videos/missing.mp4",
                    }
                )
            ],
        },
    )
    status = WorkflowStateService(pm).get_status("demo")

    # 越界指针是硬阻断（不是「当作没生成」），项目内但未登记的指针才是 missing。
    assert status.artifacts["storyboards"]["state"] == "blocked"
    assert status.artifacts["storyboards"]["current_ids"] == []
    assert [blocker.code for blocker in status.blockers] == ["artifact_path_invalid"]
    assert "../outside.png" in status.blockers[0].reason
    assert status.artifacts["videos"]["current_ids"] == []
    assert status.artifacts["videos"]["missing_ids"] == ["E1S01"]
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_appended_source_only_refreshes_inventory_and_preserves_existing_work(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    old_revision = _write_source_and_complete(pm, project_path, "第一段")

    def _seed(project: dict) -> None:
        project["characters"] = {"阿离": {"description": "角色"}}
        project["episodes"] = [
            {
                "episode": 1,
                "title": "第一集",
                "script_file": "scripts/episode_1.json",
                "ledger_status": "planned",
                "source_range": {"source_file": "source/novel.txt", "start": 0, "end": 3},
            }
        ]

    pm.update_project("demo", _seed)
    (project_path / "source" / "novel.txt").write_text("第一段\n追加段落", encoding="utf-8")

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "ASSET_INVENTORY"
    assert status.source_revision != old_revision
    assert status.artifacts["asset_inventory"]["state"] == "stale"
    assert status.next_action.type == "analyze_assets"
    assert status.next_action.args == {
        "scope": {"kind": "all", "files": []},
        "expected_source_revision": status.source_revision,
    }
    stored = pm.load_project("demo")
    assert list(stored["characters"]) == ["阿离"]
    assert stored["episodes"][0]["episode"] == 1


@pytest.mark.integration
def test_partial_inventory_scope_never_unlocks_full_workflow(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    (project_path / "source" / "novel.txt").write_text("原文", encoding="utf-8")
    scope = SourceScope(kind="files", files=["source/novel.txt"])
    revision = compute_source_revision(project_path, pm.load_project("demo"), scope).revision
    assert revision is not None
    complete_asset_inventory(pm, "demo", scope, revision)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "ASSET_INVENTORY"
    assert status.artifacts["asset_inventory"]["state"] == "partial"
    assert status.artifacts["asset_inventory"]["recorded_scope"] == {
        "kind": "files",
        "files": ["source/novel.txt"],
    }


@pytest.mark.integration
def test_unsafe_source_returns_blocker_instead_of_skipping_or_raising(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    target = project_path / "target.txt"
    target.write_text("source", encoding="utf-8")
    (project_path / "source" / "novel.txt").symlink_to(target)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert status.blockers[0].code == "source_symlink"
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_narration_progresses_through_storyboard_video_to_export(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "title": "第一集",
                "script_file": "scripts/episode_1.json",
                "ledger_status": "consumed",
                "source_range": {"source_file": "source/novel.txt", "start": 0, "end": len(source_text)},
            }
        ]
        project["planning_cursor"] = {"source_file": "source/novel.txt", "offset": len(source_text)}
        project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path))

    pm.update_project("demo", _plan)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    _write_episode_source(project_path, 1, source_text)
    script_path = project_path / "scripts" / "episode_1.json"
    script = {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "segments": [_valid_narration_segment()],
    }
    atomic_write_json(script_path, script)
    _register_produced_artifacts(project_path)
    service = WorkflowStateService(pm)

    storyboard = service.get_status("demo")
    assert storyboard.state == "STORYBOARD"
    assert storyboard.next_action.requested_ids == ["E1S01"]

    storyboard_path = resource_relative_path("storyboards", "E1S01")
    script["segments"][0]["generated_assets"]["storyboard_image"] = storyboard_path
    _write_artifact(project_path, storyboard_path)
    atomic_write_json(script_path, script)
    _register_produced_artifacts(project_path)
    video = service.get_status("demo")
    assert video.state == "VIDEO"

    script["segments"][0]["generated_assets"]["video_clip"] = _commit_media_version(project_path, "videos", "E1S01")
    atomic_write_json(script_path, script)
    _register_produced_artifacts(project_path)
    # 视频齐备即可导出：缺旁白配音只在 artifacts["audio"] 里如实报告，
    # 既不推进状态机也不拦导出（补 TTS 由用户显式发起）。
    ready = service.get_status("demo")
    assert ready.state == "EXPORT_READY"
    assert ready.next_action.type == "export"
    assert ready.artifacts["audio"]["missing_ids"] == ["E1S01"]

    script["segments"][0]["generated_assets"]["narration_audio"] = _commit_audio_version(
        project_path, script["segments"][0], "E1S01"
    )
    atomic_write_json(script_path, script)
    _register_produced_artifacts(project_path)
    still_ready = service.get_status("demo")
    assert still_ready.state == "EXPORT_READY"
    assert still_ready.artifacts["audio"]["missing_ids"] == []

    (project_path / "source" / "novel.txt").write_text("全新文本", encoding="utf-8")
    refreshed_revision = compute_source_revision(
        project_path,
        pm.load_project("demo"),
        SourceScope(kind="all"),
    ).revision
    assert refreshed_revision is not None
    complete_asset_inventory(pm, "demo", SourceScope(kind="all"), refreshed_revision)

    replanning = service.get_status("demo")
    assert replanning.state == "EPISODE_PLAN"
    assert replanning.next_action.type == "reset_episode_planning"
    assert replanning.next_action.args == {"from_episode": 1}


@pytest.mark.integration
def test_narration_audio_manifest_state_unreadable_does_not_block_export(tmp_path: Path, monkeypatch) -> None:
    """旁白配音只作为信息报告，不参与状态推进：即便 Manifest 判定该条 TTS 状态不可读
    （BLOCKED），也不能让它借道共享 blockers 列表把工作流钉在 VIDEO——视频齐备时仍须
    到达 EXPORT_READY，不可读事实只经 artifacts["audio"]["state"] 报告。用一个只对
    narration_audio 键抛错的假 resolver 隔离验证，不牵扯 step1/script Manifest 激活的
    全套前置状态。"""
    from lib.artifact_manifest import ArtifactComparison, ArtifactStatus

    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "title": "第一集",
                "script_file": "scripts/episode_1.json",
                "ledger_status": "consumed",
                "source_range": {"source_file": "source/novel.txt", "start": 0, "end": len(source_text)},
            }
        ]
        project["planning_cursor"] = {"source_file": "source/novel.txt", "offset": len(source_text)}
        project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path))
        project["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION

    pm.update_project("demo", _plan)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    script_path = project_path / "scripts" / "episode_1.json"
    audio_path = "audio/E1S01.wav"
    script = {
        "episode": 1,
        "title": "第一集",
        "content_mode": "narration",
        "segments": [
            _valid_narration_segment(
                generated_assets={
                    "storyboard_image": "storyboards/E1S01.png",
                    "video_clip": "videos/E1S01.mp4",
                    "narration_audio": audio_path,
                }
            )
        ],
    }
    _write_artifact(project_path, "storyboards/E1S01.png")
    _write_artifact(project_path, "videos/E1S01.mp4")
    _write_artifact(project_path, audio_path)
    atomic_write_json(script_path, script)

    class _AudioBlockedResolver:
        """narration_audio 键 compare 抛错（BLOCKED），其余键一律 current。"""

        def compare(self, key: ArtifactKey, *, artifact_path: str) -> ArtifactComparison:
            if key == ArtifactKey.episode_audio(1, "E1S01"):
                raise RuntimeError("manifest sidecar unreadable")
            return ArtifactComparison(status=ArtifactStatus.CURRENT, artifact_path=artifact_path)

    monkeypatch.setattr(
        f"{WorkflowStateService.__module__}.ArtifactCurrencyResolver", lambda _project_path: _AudioBlockedResolver()
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EXPORT_READY"
    assert status.artifacts["audio"]["state"] == "blocked"
    assert not any(b.path == audio_path for b in status.blockers)


@pytest.mark.integration
def test_unplanned_source_with_legacy_episode_without_source_range_requires_full_reset(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "title": "第一集",
                "script_file": "scripts/episode_1.json",
                "ledger_status": "consumed",
            }
        ]
        project["planning_cursor"] = {"source_file": "source/novel.txt", "offset": 1}
        project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path))

    pm.update_project("demo", _plan)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    generated_assets = _complete_episode_media(project_path)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [_valid_narration_segment(generated_assets=generated_assets)],
        },
    )
    _register_produced_artifacts(project_path)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EPISODE_PLAN"
    assert status.next_action.type == "reset_episode_planning"
    assert status.next_action.args == {"from_episode": 1}


def _count_source_reads(monkeypatch: pytest.MonkeyPatch, project_path: Path) -> dict[str, int]:
    """记录 ``source/`` 直下每份源文被读取的次数，字节与文本两条读法都算。"""

    counts: dict[str, int] = {}
    source_dir = (project_path / "source").resolve()
    original_read_bytes = Path.read_bytes
    original_read_text = Path.read_text

    def _record(path: Path) -> None:
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved.parent == source_dir:
            counts[resolved.name] = counts.get(resolved.name, 0) + 1

    def _counted_read_bytes(self: Path) -> bytes:
        _record(self)
        return original_read_bytes(self)

    def _counted_read_text(self: Path, *args: object, **kwargs: object) -> str:
        _record(self)
        return original_read_text(self, *args, **kwargs)  # pyright: ignore[reportArgumentType, reportCallIssue]

    monkeypatch.setattr(Path, "read_bytes", _counted_read_bytes)
    monkeypatch.setattr(Path, "read_text", _counted_read_text)
    return counts


@pytest.mark.integration
def test_status_reads_each_source_file_exactly_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """修订号计算与分集排布共用同一次读取；源文越多，重复读的代价越大。"""

    pm, project_path = _make_project(tmp_path, "narration")
    (project_path / "source" / "novel.txt").write_text("第一份原文", encoding="utf-8")
    (project_path / "source" / "extra.md").write_text("第二份原文", encoding="utf-8")

    source_reads = _count_source_reads(monkeypatch, project_path)
    WorkflowStateService(pm).get_status("demo")

    assert source_reads == {"novel.txt": 1, "extra.md": 1}


@pytest.mark.integration
def test_completed_first_episode_does_not_hide_later_incomplete_episode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": episode,
                "script_file": f"scripts/episode_{episode}.json",
                "ledger_status": "planned",
            }
            for episode in (1, 2)
        ]
        project["planning_cursor"] = {"source_file": "source/novel.txt", "offset": 1}

    pm.update_project("demo", _plan)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    generated_assets = _complete_episode_media(project_path)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [_valid_narration_segment(generated_assets=generated_assets)],
        },
    )
    _register_produced_artifacts(project_path)

    original_load_project = pm.load_project_readonly
    load_calls = 0
    source_inventory_calls = 0
    asset_sheet_calls = 0
    original_source_inventory = WorkflowStateService._source_inventory
    original_asset_sheets = WorkflowStateService._asset_sheets

    def _counted_load_project(project_name: str) -> dict:
        nonlocal load_calls
        load_calls += 1
        return original_load_project(project_name)

    def _counted_source_inventory(*args, **kwargs):
        nonlocal source_inventory_calls
        source_inventory_calls += 1
        return original_source_inventory(*args, **kwargs)

    def _counted_asset_sheets(*args, **kwargs):
        nonlocal asset_sheet_calls
        asset_sheet_calls += 1
        return original_asset_sheets(*args, **kwargs)

    monkeypatch.setattr(pm, "load_project_readonly", _counted_load_project)
    monkeypatch.setattr(WorkflowStateService, "_source_inventory", _counted_source_inventory)
    monkeypatch.setattr(WorkflowStateService, "_asset_sheets", _counted_asset_sheets)
    source_reads = _count_source_reads(monkeypatch, project_path)
    status = WorkflowStateService(pm).get_status("demo")

    assert load_calls == 1
    assert source_inventory_calls == 1
    assert asset_sheet_calls == 1
    # 整本源文仍只读一次；分集原文是产物清单比对的输入（据其重建 step1 基线），
    # 由现势解析器按项目根与分集两层各读一次。
    assert source_reads == {"novel.txt": 1, "episode_1.txt": 2}
    assert status.target is not None
    assert status.target.episode == 2
    assert status.state == "STEP1_CONTENT"
    assert status.next_action.type == "prepare_step1"


@pytest.mark.integration
def test_completed_first_episode_does_not_hide_later_planning_reset(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "script_file": "scripts/episode_1.json",
                "ledger_status": "planned",
            },
            {
                "episode": 2,
                "script_file": "scripts/episode_2.json",
                "ledger_status": "stale",
            },
        ]
        project["planning_cursor"] = {"source_file": "source/novel.txt", "offset": len(source_text)}
        project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path))

    pm.update_project("demo", _plan)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    generated_assets = _complete_episode_media(project_path)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [_valid_narration_segment(generated_assets=generated_assets)],
        },
    )
    _register_produced_artifacts(project_path)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EPISODE_PLAN"
    assert status.target is not None
    assert status.target.episode == 2
    assert status.next_action.type == "reset_episode_planning"
    assert status.next_action.args == {"from_episode": 2}


@pytest.mark.integration
def test_legacy_stale_episode_without_baseline_requires_planning_reset(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[
                {
                    "episode": 1,
                    "script_file": "scripts/episode_1.json",
                    "ledger_status": "stale",
                }
            ]
        ),
    )
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": [{"segment_id": "E1S01"}]})

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EPISODE_PLAN"
    assert status.next_action.type == "reset_episode_planning"
    assert status.next_action.args == {"from_episode": 1}


@pytest.mark.integration
def test_requested_missing_episode_is_blocked_when_source_is_fully_planned(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[{"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "planned"}],
            planning_cursor={"source_file": "source/novel.txt", "offset": len(source_text)},
            **{SOURCE_FINGERPRINTS_KEY: compute_source_fingerprints(discover_sources(project_path))},
        ),
    )

    status = WorkflowStateService(pm).get_status("demo", 2)

    assert status.state == "EPISODE_PLAN"
    assert status.target is None
    assert status.blockers[0].code == "episode_unavailable"
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_source_inserted_before_cursor_requires_planning_reset(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_dir = project_path / "source"
    (source_dir / "a.txt").write_text("已规划", encoding="utf-8")
    scope = SourceScope(kind="all")
    project = pm.load_project("demo")
    initial = compute_source_revision(project_path, project, scope).revision
    assert initial is not None
    complete_asset_inventory(pm, "demo", scope, initial)
    pm.update_project(
        "demo",
        lambda data: data.update(
            planning_cursor={"source_file": "source/a.txt", "offset": 3},
            **{SOURCE_FINGERPRINTS_KEY: compute_source_fingerprints(discover_sources(project_path))},
        ),
    )
    (source_dir / "0.txt").write_text("新增", encoding="utf-8")
    refreshed = compute_source_revision(project_path, pm.load_project("demo"), scope).revision
    assert refreshed is not None
    complete_asset_inventory(pm, "demo", scope, refreshed)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EPISODE_PLAN"
    assert status.next_action.type == "reset_episode_planning"
    assert status.next_action.args == {"from_episode": 1}


@pytest.mark.integration
def test_decomposed_recorded_source_does_not_trigger_repeated_planning_reset(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_dir = project_path / "source"
    decomposed_name = unicodedata.normalize("NFD", "é.txt")
    source_path = source_dir / decomposed_name
    source_path.write_text("已规划", encoding="utf-8")
    scope = SourceScope(kind="all")
    project = pm.load_project("demo")
    revision = compute_source_revision(project_path, project, scope).revision
    assert revision is not None
    complete_asset_inventory(pm, "demo", scope, revision)
    pm.update_project(
        "demo",
        lambda data: data.update(
            planning_cursor={"source_file": f"source/{decomposed_name}", "offset": 3},
            **{SOURCE_FINGERPRINTS_KEY: compute_source_fingerprints(discover_sources(project_path))},
        ),
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EPISODE_PLAN"
    assert status.next_action.type == "plan_episodes"


@pytest.mark.integration
def test_later_raw_sorted_source_does_not_trigger_planning_reset(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_dir = project_path / "source"
    decomposed_name = unicodedata.normalize("NFD", "á.txt")
    (source_dir / decomposed_name).write_text("已规划", encoding="utf-8")
    scope = SourceScope(kind="all")
    project = pm.load_project("demo")
    revision = compute_source_revision(project_path, project, scope).revision
    assert revision is not None
    complete_asset_inventory(pm, "demo", scope, revision)
    pm.update_project(
        "demo",
        lambda data: data.update(
            planning_cursor={"source_file": f"source/{decomposed_name}", "offset": 3},
            **{SOURCE_FINGERPRINTS_KEY: compute_source_fingerprints(discover_sources(project_path))},
        ),
    )
    (source_dir / "b.txt").write_text("后续", encoding="utf-8")
    refreshed = compute_source_revision(project_path, pm.load_project("demo"), scope).revision
    assert refreshed is not None
    complete_asset_inventory(pm, "demo", scope, refreshed)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EPISODE_PLAN"
    assert status.next_action.type == "plan_episodes"


@pytest.mark.integration
def test_whitespace_only_source_is_missing_project_input(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path, " \n\t ")

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert status.next_action.type == "collect_project_input"


@pytest.mark.integration
def test_non_boolean_grid_storyboard_blocks_route_dispatch(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    pm.update_project("demo", lambda project: project.update(grid_storyboard="false"))

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert status.project.grid_storyboard is False
    assert status.blockers[0].code == "invalid_grid_storyboard"
    assert status.next_action.type == "none"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("field", "value", "blocker_code"),
    [
        ("content_mode", [], "invalid_content_mode"),
        ("generation_mode", {}, "invalid_generation_mode"),
    ],
)
def test_non_string_project_mode_returns_blocker(tmp_path: Path, field: str, value: object, blocker_code: str) -> None:
    pm, _project_path = _make_project(tmp_path, "ad")
    pm.update_project("demo", lambda project: project.update({field: value}))

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert status.blockers[0].code == blocker_code
    assert status.next_action.type == "none"


@pytest.mark.integration
@pytest.mark.parametrize("ledger_status", [[], {}])
def test_non_string_ledger_status_returns_blocker(tmp_path: Path, ledger_status: object) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[
                {
                    "episode": 1,
                    "script_file": "scripts/episode_1.json",
                    "ledger_status": ledger_status,
                }
            ]
        ),
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert status.target is None
    assert status.blockers[0].code == "invalid_ledger_status"
    assert status.next_action.type == "none"


@pytest.mark.integration
@pytest.mark.parametrize("target_duration", [None, 0, -1, False, "30"])
def test_invalid_ad_target_duration_blocks_script_generation(tmp_path: Path, target_duration: object) -> None:
    pm, _project_path = _make_project(tmp_path, "ad")
    pm.update_project("demo", lambda project: project.update(target_duration=target_duration))

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert status.blockers[0].code == "invalid_target_duration"
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_invalid_asset_definition_blocks_existing_sheet(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    sheet = "characters/invalid.png"
    _write_artifact(project_path, sheet)
    pm.update_project(
        "demo",
        lambda project: project.update(characters={"无描述角色": {"character_sheet": sheet}}),
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert status.blockers[0].code == "invalid_asset_definitions"
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_missing_ledger_script_binding_is_a_blocker(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)
    pm.update_project(
        "demo",
        lambda project: project.update(episodes=[{"episode": 1, "ledger_status": "planned"}]),
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert status.target is None
    assert status.blockers[0].code == "invalid_script_binding"
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_script_episode_must_match_ledger_target(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[
                {
                    "episode": 2,
                    "script_file": "scripts/episode_1.json",
                    "ledger_status": "planned",
                }
            ]
        ),
    )
    draft_dir = project_path / "drafts" / "episode_2"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 2)
    step1_path = draft_dir / "step1_segments.json"
    atomic_write_json(step1_path, {"episode": 2, "segments": [{"segment_id": "E2S01"}]})
    revision = script_review.content_fingerprint(step1_path)
    assert revision is not None

    def _confirm(project: dict) -> None:
        script_review.apply_confirmation(project, 2, revision, "now")

    pm.update_project("demo", _confirm)
    bound = {
        "episode": 2,
        "title": "第二集",
        "content_mode": "narration",
        "segments": [_valid_narration_segment(segment_id="E2S01")],
    }
    _write_registered_script(project_path, bound, episode=2)
    _edit_claimed_script(project_path, {**bound, "episode": 1})

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "artifact_currency_unavailable"
    assert "does not match its project binding" in status.blockers[0].reason
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_malformed_script_collection_is_a_blocker_not_an_exception(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    _write_registered_script(
        project_path, {"episode": 1, "title": "广告", "content_mode": "ad", "shots": [_valid_ad_shot()]}
    )
    _edit_claimed_script(project_path, {"episode": 1, "content_mode": "ad", "shots": {"not": "a list"}})

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "artifact_currency_unavailable"
    assert "shots" in status.blockers[0].reason
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_non_object_script_is_a_blocker_not_an_exception(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    _write_registered_script(
        project_path, {"episode": 1, "title": "广告", "content_mode": "ad", "shots": [_valid_ad_shot()]}
    )
    _edit_claimed_script(project_path, [])

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "artifact_currency_unavailable"
    assert "must contain an object" in status.blockers[0].reason


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mode", "step1_filename", "step1_payload", "items_key"),
    [
        ("narration", "step1_segments.json", {"segments": []}, "segments"),
        ("drama", "step1_normalized_script.json", {"scenes": []}, "scenes"),
    ],
)
def test_legacy_storyboard_script_without_duration_remains_resumable(
    tmp_path: Path,
    mode: str,
    step1_filename: str,
    step1_payload: dict,
    items_key: str,
) -> None:
    pm, project_path = _make_project(tmp_path, mode)
    _write_source_and_complete(pm, project_path)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[{"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "planned"}]
        ),
    )
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    step1_path = draft_dir / step1_filename
    atomic_write_json(step1_path, step1_payload)
    revision = script_review.content_fingerprint(step1_path)
    assert revision is not None
    pm.update_project(
        "demo", lambda project: script_review.apply_confirmation(project, 1, revision, "2026-08-11T00:00:00Z")
    )
    item = (
        _valid_narration_segment(duration_seconds=None)
        if mode == "narration"
        else _valid_drama_scene(duration_seconds=None)
    )
    item.pop("duration_seconds")
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": mode,
            items_key: [item],
            "metadata": {script_review.SCRIPT_STEP1_REVISION_FIELD: revision},
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "STORYBOARD"
    assert status.artifacts["script"]["state"] == "current"
    assert not status.blockers


@pytest.mark.integration
def test_legacy_narration_scenes_skeleton_remains_resumable(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "完整原文"
    _write_source_and_complete(pm, project_path, source_text)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "script_file": "scripts/episode_1.json",
                "ledger_status": "planned",
            }
        ]

    pm.update_project("demo", _plan)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "scenes": [_valid_drama_scene()],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "STORYBOARD"
    assert status.artifacts["script"]["state"] == "current"
    assert status.next_action.requested_ids == ["E1S01"]


@pytest.mark.integration
def test_empty_script_collection_is_a_blocker_not_completed_work(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    _write_registered_script(
        project_path,
        {"episode": 1, "content_mode": "ad", "shots": []},
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "invalid_script_collection"


@pytest.mark.integration
def test_script_entry_without_required_id_is_a_blocker(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    _write_registered_script(
        project_path, {"episode": 1, "title": "广告", "content_mode": "ad", "shots": [_valid_ad_shot()]}
    )
    _edit_claimed_script(project_path, {"episode": 1, "content_mode": "ad", "shots": [{"duration_seconds": 4}]})

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "artifact_currency_unavailable"
    assert "item 0 has no identity" in status.blockers[0].reason


@pytest.mark.integration
def test_optional_product_sheet_does_not_block_ad_media(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    product_image = "products/original.png"
    _write_artifact(project_path, product_image)

    def _add_product(project: dict) -> None:
        project["products"] = {
            "杯子": {
                "description": "透明杯",
                "reference_images": [product_image],
                "selling_points": ["轻便"],
            }
        }

    pm.update_project("demo", _add_product)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "shots": [_valid_ad_shot()],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.artifacts["asset_sheets"]["product"]["missing_ids"] == ["杯子"]
    assert status.state == "STORYBOARD"
    assert status.next_action.type == "generate_storyboards"


@pytest.mark.integration
def test_schema8_ad_reference_video_does_not_treat_an_unregistered_file_as_current(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad", generation_mode="reference_video")
    video_path = "reference_videos/E1U1.mp4"
    _write_artifact(project_path, video_path)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "video_units": [_valid_video_unit(unit_id="E1U1", generated_assets={"video_clip": video_path})],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "VIDEO"
    assert status.artifacts["videos"] == {
        "current_ids": [],
        "missing_ids": ["E1U1"],
        "stale_ids": [],
    }


@pytest.mark.integration
def test_schema8_workflow_accepts_the_exact_selected_manual_reference_video(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad", generation_mode="reference_video")
    video_path = "reference_videos/E1U1.mp4"
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "video_units": [_valid_video_unit(unit_id="E1U1", generated_assets={"video_clip": video_path})],
        },
    )
    staged = project_path / "reference_videos" / ".E1U1.upload.mp4"
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(b"manual-video")
    VersionManager(project_path).commit_staged_version(
        "reference_videos",
        "E1U1",
        "",
        staged_file=staged,
        current_file=project_path / video_path,
        source=MANUAL_UPLOAD_VERSION_SOURCE,
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EXPORT_READY"
    assert status.artifacts["videos"] == {
        "current_ids": ["E1U1"],
        "missing_ids": [],
        "stale_ids": [],
    }
    assert status.next_action.type == "export"


@pytest.mark.integration
def test_schema8_workflow_does_not_parse_an_unclaimed_malformed_script(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    (project_path / "scripts" / "episode_1.json").write_text("{", encoding="utf-8")

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"] == {"state": "missing", "path": "scripts/episode_1.json"}
    assert status.blockers == []
    assert status.next_action.type == "generate_script"


@pytest.mark.integration
def test_schema8_manifest_reports_current_stale_missing_and_blocked_without_file_fallback(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    sheet_path = "characters/Alice.png"
    storyboard_path = "storyboards/scene_E1S01.png"
    _write_artifact(project_path, sheet_path)
    _write_artifact(project_path, storyboard_path)

    def _seed(project: dict) -> None:
        project["characters"] = {
            "Alice": {
                "description": "red coat",
                "character_sheet": sheet_path,
            }
        }

    pm.update_project("demo", _seed)
    script_path = project_path / "scripts" / "episode_1.json"
    script = {
        "episode": 1,
        "title": "广告",
        "content_mode": "ad",
        "shots": [
            _valid_ad_shot(
                image_prompt="red coat hero",
                generated_assets={"storyboard_image": storyboard_path},
            )
        ],
    }
    atomic_write_json(script_path, script)
    _register_produced_artifacts(project_path)

    current = WorkflowStateService(pm).get_status("demo")
    assert current.state == "VIDEO"
    assert current.artifacts["script"]["state"] == "current"
    assert current.artifacts["asset_sheets"]["character"]["current_ids"] == ["Alice"]
    assert current.artifacts["storyboards"]["current_ids"] == ["E1S01"]

    pm.update_project("demo", lambda project: project["characters"]["Alice"].update(description="blue coat"))
    script["shots"][0]["image_prompt"] = "blue coat hero"
    atomic_write_json(script_path, script)
    stale = WorkflowStateService(pm).get_status("demo")
    assert stale.state == "VIDEO"
    assert stale.artifacts["asset_sheets"]["character"]["stale_ids"] == ["Alice"]
    assert stale.artifacts["storyboards"]["stale_ids"] == ["E1S01"]

    adapter = ProjectArtifactManifestAdapter(project_path)
    adapter.delete_entry(ArtifactKey.episode_storyboard(1, "E1S01"))
    missing = WorkflowStateService(pm).get_status("demo")
    assert missing.state == "STORYBOARD"
    assert missing.artifacts["storyboards"]["missing_ids"] == ["E1S01"]

    register_current_artifact(project_path, ArtifactKey.episode_storyboard(1, "E1S01"))
    storyboard_file = project_path / storyboard_path
    storyboard_file.unlink()
    storyboard_file.symlink_to(project_path / sheet_path)
    blocked = WorkflowStateService(pm).get_status("demo")
    assert blocked.state == "VIDEO"
    assert blocked.artifacts["storyboards"]["state"] == "blocked"
    assert any(item.code == "artifact_symlink" for item in blocked.blockers)


@pytest.mark.integration
def test_ad_reference_video_does_not_hydrate_legacy_shots(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad", generation_mode="reference_video")
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "shots": [_valid_ad_shot()],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert any(blocker.code == "invalid_project_mode" for blocker in status.blockers)


@pytest.mark.integration
def test_stale_episode_requires_step1_even_when_old_artifacts_exist(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "script_file": "scripts/episode_1.json",
                "ledger_status": "stale",
            }
        ]

    pm.update_project("demo", _plan)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "generated_assets": {}}],
        },
    )
    step1_path = draft_dir / "step1_segments.json"
    pm.update_project(
        "demo",
        lambda project: project["episodes"][0].update(
            {script_review.STALE_STEP1_REVISION_FIELD: script_review.content_fingerprint(step1_path)}
        ),
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "STEP1_CONTENT"
    assert status.artifacts["step1"]["state"] == "stale"
    assert status.next_action.type == "prepare_step1"


@pytest.mark.integration
def test_stale_episode_advances_after_step1_is_rebuilt(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    step1_path = draft_dir / "step1_segments.json"
    atomic_write_json(step1_path, {"episode": 1, "segments": [{"segment_id": "E1S01"}]})
    old_revision = script_review.content_fingerprint(step1_path)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "script_file": "scripts/episode_1.json",
                "ledger_status": "stale",
                script_review.STALE_STEP1_REVISION_FIELD: old_revision,
            }
        ]

    pm.update_project("demo", _plan)
    service = WorkflowStateService(pm)
    assert service.get_status("demo").state == "STEP1_CONTENT"

    atomic_write_json(step1_path, {"episode": 1, "segments": [{"segment_id": "E1S02"}]})
    _register_step1(project_path)
    rebuilt = service.get_status("demo")

    assert rebuilt.state == "STEP1_REVIEW"
    assert rebuilt.next_action.type == "confirm_step1"
    assert rebuilt.next_action.requires_confirmation is True


@pytest.mark.integration
def test_identical_stale_step1_rebuild_advances_after_explicit_completion(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    step1_path = draft_dir / "step1_segments.json"
    content = {"episode": 1, "segments": [{"segment_id": "E1S01"}]}
    atomic_write_json(step1_path, content)
    baseline = script_review.content_fingerprint(step1_path)
    assert baseline is not None
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[
                {
                    "episode": 1,
                    "script_file": "scripts/episode_1.json",
                    "ledger_status": "stale",
                    script_review.STALE_STEP1_REVISION_FIELD: baseline,
                }
            ]
        ),
    )
    service = WorkflowStateService(pm)
    before = service.get_status("demo")
    assert before.next_action.type == "prepare_step1"
    assert before.next_action.args["expected_stale_step1_revision"] == baseline

    atomic_write_json(step1_path, content)
    _register_step1(project_path)
    still_pending = service.get_status("demo")
    assert still_pending.next_action.type == "prepare_step1"
    script_review.complete_stale_step1_rebuild(pm, "demo", 1, baseline)

    completed = service.get_status("demo")
    assert completed.state == "STEP1_REVIEW"
    assert completed.next_action.type == "confirm_step1"


@pytest.mark.integration
def test_null_baseline_stale_rebuild_invalidates_grandfathered_script(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[
                {
                    "episode": 1,
                    "script_file": "scripts/episode_1.json",
                    "ledger_status": "stale",
                    script_review.STALE_STEP1_REVISION_FIELD: None,
                }
            ]
        ),
    )
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    step1_path = draft_dir / "step1_segments.json"
    atomic_write_json(step1_path, {"episode": 1, "segments": [{"segment_id": "E1S00"}]})
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "旧剧本",
            "content_mode": "narration",
            "segments": [_valid_narration_segment()],
        },
    )
    # 剧本的认领留存，但它所依据的 step1 已不在盘上——这正是待重建的祖传剧本形态。
    step1_path.unlink()
    service = WorkflowStateService(pm)
    assert service.get_status("demo").next_action.type == "prepare_step1"

    atomic_write_json(step1_path, {"episode": 1, "segments": [{"segment_id": "E1S01"}]})
    _register_step1(project_path)
    script_review.complete_stale_step1_rebuild(pm, "demo", 1, None)

    pending_review = service.get_status("demo")
    assert pending_review.state == "STEP1_REVIEW"
    assert pending_review.next_action.type == "confirm_step1"
    revision = script_review.content_fingerprint(step1_path)
    assert revision is not None

    def _confirm(project: dict) -> None:
        script_review.apply_confirmation(project, 1, revision, "now")

    pm.update_project("demo", _confirm)
    regenerate = service.get_status("demo")
    # 祖传剧本按重建后的 step1 重算取证即判陈旧；陈旧可用，流程继续向下游推进。
    assert regenerate.artifacts["script"]["state"] == "stale"
    assert regenerate.state == "STORYBOARD"
    assert regenerate.next_action.type == "generate_storyboards"


@pytest.mark.integration
def test_quarantined_step1_is_a_blocker_not_a_confirmation_loop(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "drama", generation_mode="reference_video")
    _write_source_and_complete(pm, project_path)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[{"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "planned"}]
        ),
    )
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_reference_units.json", {"units": []})
    quarantine = script_review.step1_quarantine_path(project_path, pm.load_project("demo"), 1)
    assert quarantine is not None
    atomic_write_json(quarantine, {})

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "STEP1_REVIEW"
    assert status.artifacts["step1"]["state"] == "blocked"
    assert status.blockers[0].code == "step1_quarantined"
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_confirmed_step1_change_marks_old_final_script_stale(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[{"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "planned"}]
        ),
    )
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    step1_path = draft_dir / "step1_segments.json"
    atomic_write_json(step1_path, {"segments": [{"segment_id": "E1S01", "novel_text": "旧内容"}]})
    old_revision = script_review.content_fingerprint(step1_path)
    assert old_revision is not None
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [_valid_narration_segment()],
            "metadata": {script_review.SCRIPT_STEP1_REVISION_FIELD: old_revision},
        },
    )

    atomic_write_json(step1_path, {"segments": [{"segment_id": "E1S01", "novel_text": "新内容"}]})
    new_revision = script_review.content_fingerprint(step1_path)
    assert new_revision is not None
    pm.update_project(
        "demo", lambda project: script_review.apply_confirmation(project, 1, new_revision, "2026-08-11T00:00:00Z")
    )

    _register_step1(project_path)

    status = WorkflowStateService(pm).get_status("demo")

    # 剧本按新 step1 重算取证即判陈旧；陈旧仍可用，故流程继续向下游推进而非退回重写剧本。
    assert status.artifacts["script"]["state"] == "stale"
    assert status.state == "STORYBOARD"
    assert status.next_action.type == "generate_storyboards"


@pytest.mark.integration
def test_blocked_final_script_is_not_reclassified_as_stale_by_provenance(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "drama")
    _write_source_and_complete(pm, project_path)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[{"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "planned"}]
        ),
    )
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    step1_path = draft_dir / "step1_normalized_script.json"
    atomic_write_json(step1_path, {"scenes": []})
    revision = script_review.content_fingerprint(step1_path)
    assert revision is not None
    pm.update_project(
        "demo", lambda project: script_review.apply_confirmation(project, 1, revision, "2026-08-11T00:00:00Z")
    )
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "drama",
            "scenes": [_valid_drama_scene()],
            "metadata": {script_review.SCRIPT_STEP1_REVISION_FIELD: revision},
        },
    )
    _edit_claimed_script(project_path, [])

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "artifact_currency_unavailable"
    assert "must contain an object" in status.blockers[0].reason
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_script_id_must_match_the_shared_storyboard_pattern(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "content_mode": "ad",
            "shots": [{"shot_id": "bad id", "duration_seconds": 4, "generated_assets": {}}],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.blockers[0].code == "invalid_script_id"


@pytest.mark.integration
def test_ad_reference_replan_shell_requests_repair_before_generation(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad", generation_mode="reference_video")
    video_path = "reference_videos/E1U1.mp4"
    _write_artifact(project_path, video_path)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "video_units": [
                {
                    "unit_id": "E1U1",
                    "text": "",
                    "duration_seconds": 0,
                    "needs_replan": True,
                    "generated_assets": {"video_clip": video_path},
                }
            ],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "VIDEO"
    assert status.artifacts["videos"]["stale_ids"] == ["E1U1"]
    assert status.next_action.type == "repair_video_units"
    assert status.next_action.requested_ids == ["E1U1"]
    assert not status.blockers


@pytest.mark.integration
@pytest.mark.parametrize("missing_field", ["voiceover_text", "image_prompt", "video_prompt"])
def test_structurally_incomplete_ad_script_blocks_media_progress(tmp_path: Path, missing_field: str) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    shot = _valid_ad_shot()
    shot.pop(missing_field)
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "shots": [shot],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "invalid_script_structure"
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_narration_script_without_source_text_blocks_media_progress(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_text = "原文"
    _write_source_and_complete(pm, project_path, source_text)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[{"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "consumed"}],
            planning_cursor={"source_file": "source/novel.txt", "offset": len(source_text)},
            **{SOURCE_FINGERPRINTS_KEY: compute_source_fingerprints(discover_sources(project_path))},
        ),
    )
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_segments.json", {"segments": []})
    segment = _valid_narration_segment()
    segment.pop("novel_text")
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "segments": [segment],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "invalid_script_structure"


@pytest.mark.integration
def test_invalid_required_script_field_blocks_export(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01", "duration_seconds": -7, "generated_assets": {}}],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "invalid_script_structure"
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_planning_completion_resolves_nfc_cursor_to_nfd_filesystem_path(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    decomposed_name = unicodedata.normalize("NFD", "truyện.txt")
    source_path = project_path / "source" / decomposed_name
    source_path.write_text("完整原文", encoding="utf-8")
    project = pm.load_project("demo")
    source = compute_source_revision(project_path, project, SourceScope(kind="all"))
    assert source.revision is not None
    project["planning_cursor"] = {"source_file": source.files[-1], "offset": 4}
    project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path))

    assert WorkflowStateService._planning_complete(project, source, planning_docs(source)) is True


@pytest.mark.integration
def test_planning_without_source_fingerprint_baseline_is_incomplete(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_path = project_path / "source" / "novel.txt"
    source_path.write_text("完整原文", encoding="utf-8")
    project = pm.load_project("demo")
    source = compute_source_revision(project_path, project, SourceScope(kind="all"))
    assert source.revision is not None
    project["planning_cursor"] = {"source_file": source.files[-1], "offset": 4}

    assert WorkflowStateService._planning_complete(project, source, planning_docs(source)) is False


@pytest.mark.integration
def test_new_source_file_continues_planning_without_resetting_existing_fingerprints(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    original_path = project_path / "source" / "a.txt"
    original_path.write_text("已规划原文", encoding="utf-8")
    project = pm.load_project("demo")
    project["planning_cursor"] = {"source_file": "source/a.txt", "offset": len("已规划原文")}
    project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(discover_sources(project_path))
    pm.save_project("demo", project)
    initial_revision = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert initial_revision is not None
    complete_asset_inventory(pm, "demo", SourceScope(kind="all"), initial_revision)
    (project_path / "source" / "z.txt").write_text("新增原文", encoding="utf-8")
    revision = compute_source_revision(project_path, pm.load_project("demo"), SourceScope(kind="all")).revision
    assert revision is not None
    complete_asset_inventory(pm, "demo", SourceScope(kind="all"), revision)

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EPISODE_PLAN"
    assert status.next_action.type == "plan_episodes"


@pytest.mark.integration
def test_planning_completion_preserves_planner_order_for_canonical_paths(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "narration")
    source_dir = project_path / "source"
    (source_dir / unicodedata.normalize("NFD", "á.txt")).write_text("第一份", encoding="utf-8")
    (source_dir / "b.txt").write_text("第二份", encoding="utf-8")
    project = pm.load_project("demo")
    docs = discover_sources(project_path)
    source = compute_source_revision(project_path, project, SourceScope(kind="all"))
    assert source.revision is not None
    assert source.files == [unicodedata.normalize("NFC", doc.rel_path) for doc in docs]
    project["planning_cursor"] = {"source_file": docs[-1].rel_path, "offset": len(docs[-1].text)}
    project[SOURCE_FINGERPRINTS_KEY] = compute_source_fingerprints(docs)

    assert WorkflowStateService._planning_complete(project, source, planning_docs(source)) is True


@pytest.mark.integration
def test_duplicate_reference_video_unit_ids_block_completion(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "drama", generation_mode="reference_video")
    source_text = "原文"
    _write_source_and_complete(pm, project_path, source_text)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "script_file": "scripts/episode_1.json",
                "ledger_status": "consumed",
            }
        ]

    pm.update_project("demo", _plan)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_reference_units.json", {"units": []})
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "content_mode": "drama",
            "video_units": [
                {"unit_id": "E1U01", "duration_seconds": 4, "generated_assets": {}},
                {"unit_id": "E1U02", "duration_seconds": 4, "generated_assets": {}},
            ],
        },
    )
    _edit_claimed_script(
        project_path,
        {
            "episode": 1,
            "content_mode": "drama",
            "video_units": [
                {"unit_id": "E1U01", "duration_seconds": 4, "generated_assets": {}},
                {"unit_id": "E1U01", "duration_seconds": 4, "generated_assets": {}},
            ],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "artifact_currency_unavailable"
    assert "duplicate resource identity 'E1U01'" in status.blockers[0].reason


@pytest.mark.integration
def test_reference_video_route_skips_storyboards_and_audio(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "drama", generation_mode="reference_video")
    source_text = "原文"
    _write_source_and_complete(pm, project_path, source_text)

    def _plan(project: dict) -> None:
        project["episodes"] = [
            {
                "episode": 1,
                "script_file": "scripts/episode_1.json",
                "ledger_status": "consumed",
            }
        ]
        project["planning_cursor"] = {"source_file": "source/novel.txt", "offset": len(source_text)}

    pm.update_project("demo", _plan)
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_reference_units.json", {"units": []})
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "drama",
            "video_units": [_valid_video_unit()],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "VIDEO"
    assert status.artifacts["storyboards"]["state"] == "not_applicable"
    assert status.artifacts["audio"]["state"] == "not_applicable"
    assert status.next_action.requested_ids == ["E1U01"]


@pytest.mark.integration
def test_workflow_status_does_not_persist_read_time_script_migrations(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "drama", generation_mode="reference_video")
    source_text = "原文"
    _write_source_and_complete(pm, project_path, source_text)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[{"episode": 1, "script_file": "scripts/episode_1.json", "ledger_status": "consumed"}],
            planning_cursor={"source_file": "source/novel.txt", "offset": len(source_text)},
        ),
    )
    draft_dir = project_path / "drafts" / "episode_1"
    draft_dir.mkdir(parents=True)
    _write_episode_source(project_path, 1)
    atomic_write_json(draft_dir / "step1_reference_units.json", {"units": []})
    script_path = project_path / "scripts" / "episode_1.json"
    _write_registered_script(
        project_path,
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "drama",
            "video_units": [
                {
                    "unit_id": "E1U01",
                    "text": "镜头",
                    "duration_seconds": 8,
                    "generated_assets": {},
                }
            ],
        },
    )
    before = script_path.read_bytes()

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "VIDEO"
    assert script_path.read_bytes() == before
    assert not (script_path.parent / ".episode_1.json.lock").exists()


@pytest.mark.integration
def test_unmigrated_project_reports_only_the_migration_blocker(tmp_path: Path) -> None:
    """产物清单是读取已生成产物的唯一口径：schema 未到 8 的项目没有可读的登记，
    get_status 只报「未迁移」这一条阻断，不按文件是否在磁盘上倒推任何产物状态。"""
    pm, project_path = _make_project(tmp_path, "narration")
    _write_source_and_complete(pm, project_path)
    _write_artifact(project_path, resource_relative_path("storyboards", "E1S01"))
    pm.update_project("demo", lambda project: project.update(schema_version=7))

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert [blocker.code for blocker in status.blockers] == [MIGRATION_FAILURE_CODE]
    assert status.blockers[0].path == MIGRATION_FAILURE_FILENAME
    assert status.blockers[0].reason == (
        f"project schema v7 has not been upgraded to v{CURRENT_PROJECT_SCHEMA_VERSION}; "
        "produced artifacts cannot be read until it is"
    )
    assert status.next_action.type == RETRY_MIGRATION_ACTION
    assert status.artifacts["script"] == {"state": "missing"}
    assert status.artifacts["storyboards"] == {"current_ids": [], "missing_ids": [], "stale_ids": []}
    assert status.artifacts["asset_inventory"] == {}


@pytest.mark.integration
def test_workflow_status_does_not_persist_read_time_project_migrations(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    project_path_json = project_path / "project.json"
    project = pm.load_project("demo")
    project.pop("style_template_id", None)
    project["style"] = "Anime"
    atomic_write_json(project_path_json, project)
    before = project_path_json.read_bytes()

    status = WorkflowStateService(pm).get_status("demo")

    assert status.project.content_mode == "ad"
    assert project_path_json.read_bytes() == before


@pytest.mark.integration
def test_nested_ledger_script_path_is_blocked_before_dispatch(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[
                {
                    "episode": 1,
                    "title": "广告",
                    "script_file": "scripts/archive/custom.json",
                    "ledger_status": "planned",
                }
            ]
        ),
    )
    nested_script = project_path / "scripts" / "archive" / "custom.json"
    nested_script.parent.mkdir(parents=True)
    atomic_write_json(
        nested_script,
        {
            "episode": 1,
            "title": "广告",
            "content_mode": "ad",
            "shots": [_valid_ad_shot()],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "PROJECT_INPUT"
    assert status.target is not None
    assert status.target.script_filename == "archive/custom.json"
    assert status.blockers[0].code == "invalid_script_path"
    assert status.next_action.type == "none"
