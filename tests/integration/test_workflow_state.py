from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

import lib.script_review as script_review
from lib.asset_inventory import complete_asset_inventory
from lib.episode_ledger import SOURCE_FINGERPRINTS_KEY, compute_source_fingerprints, discover_sources
from lib.json_io import atomic_write_json
from lib.project_manager import ProjectManager
from lib.source_revision import SourceScope, compute_source_revision
from lib.workflow_state import WorkflowStateService


def _make_project(tmp_path: Path, mode: str, *, generation_mode: str = "storyboard") -> tuple[ProjectManager, Path]:
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
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "ad",
            "shots": [
                {
                    "shot_id": "E1S01",
                    "duration_seconds": 4,
                    "generated_assets": {
                        "storyboard_image": "../outside.png",
                        "video_clip": "videos/missing.mp4",
                    },
                }
            ],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "STORYBOARD"
    assert status.artifacts["storyboards"]["current_ids"] == []
    assert status.artifacts["storyboards"]["missing_ids"] == ["E1S01"]
    assert status.artifacts["videos"]["current_ids"] == []
    assert status.artifacts["videos"]["missing_ids"] == ["E1S01"]


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
def test_narration_progresses_through_storyboard_video_audio_to_export(tmp_path: Path) -> None:
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
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    script_path = project_path / "scripts" / "episode_1.json"
    script = {
        "episode": 1,
        "content_mode": "narration",
        "segments": [{"segment_id": "E1S01", "duration_seconds": 4, "generated_assets": {}}],
    }
    atomic_write_json(script_path, script)
    service = WorkflowStateService(pm)

    storyboard = service.get_status("demo")
    assert storyboard.state == "STORYBOARD"
    assert storyboard.next_action.requested_ids == ["E1S01"]

    script["segments"][0]["generated_assets"]["storyboard_image"] = "storyboards/E1S01.png"
    _write_artifact(project_path, "storyboards/E1S01.png")
    atomic_write_json(script_path, script)
    video = service.get_status("demo")
    assert video.state == "VIDEO"

    script["segments"][0]["generated_assets"]["video_clip"] = "videos/E1S01.mp4"
    _write_artifact(project_path, "videos/E1S01.mp4")
    atomic_write_json(script_path, script)
    audio = service.get_status("demo")
    assert audio.state == "AUDIO"

    script["segments"][0]["generated_assets"]["narration_audio"] = "audio/E1S01.wav"
    _write_artifact(project_path, "audio/E1S01.wav")
    atomic_write_json(script_path, script)
    ready = service.get_status("demo")
    assert ready.state == "EXPORT_READY"
    assert ready.next_action.type == "export"

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
    assert replanning.next_action.type == "plan_episodes"


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
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    generated_assets = {
        "storyboard_image": "storyboards/E1S01.png",
        "video_clip": "videos/E1S01.mp4",
        "narration_audio": "audio/E1S01.wav",
    }
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4, "generated_assets": generated_assets}],
        },
    )
    for relative_path in generated_assets.values():
        _write_artifact(project_path, relative_path)

    original_load_project = pm.load_project
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

    monkeypatch.setattr(pm, "load_project", _counted_load_project)
    monkeypatch.setattr(WorkflowStateService, "_source_inventory", _counted_source_inventory)
    monkeypatch.setattr(WorkflowStateService, "_asset_sheets", _counted_asset_sheets)
    status = WorkflowStateService(pm).get_status("demo")

    assert load_calls == 1
    assert source_inventory_calls == 1
    assert asset_sheet_calls == 1
    assert status.target is not None
    assert status.target.episode == 2
    assert status.state == "STEP1_CONTENT"
    assert status.next_action.type == "prepare_step1"


@pytest.mark.integration
def test_malformed_script_collection_is_a_blocker_not_an_exception(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {"episode": 1, "content_mode": "ad", "shots": {"not": "a list"}},
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "invalid_script_collection"
    assert status.next_action.type == "none"


@pytest.mark.integration
def test_non_object_script_is_a_blocker_not_an_exception(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    atomic_write_json(project_path / "scripts" / "episode_1.json", [])

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "invalid_script"


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
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "narration",
            "scenes": [{"scene_id": "E1S01", "duration_seconds": 4, "generated_assets": {}}],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "STORYBOARD"
    assert status.artifacts["script"]["state"] == "current"
    assert status.next_action.requested_ids == ["E1S01"]


@pytest.mark.integration
def test_empty_script_collection_is_a_blocker_not_completed_work(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {"episode": 1, "content_mode": "ad", "shots": []},
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "blocked"
    assert status.blockers[0].code == "invalid_script_collection"


@pytest.mark.integration
def test_script_entry_without_required_id_is_a_blocker(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {"episode": 1, "content_mode": "ad", "shots": [{"duration_seconds": 4}]},
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.blockers[0].code == "invalid_script_id"
    assert status.blockers[0].path.endswith("shots[0].shot_id")


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
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01", "duration_seconds": 4, "generated_assets": {}}],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.artifacts["asset_sheets"]["product"]["missing_ids"] == ["杯子"]
    assert status.state == "STORYBOARD"
    assert status.next_action.type == "generate_storyboards"


@pytest.mark.integration
def test_ad_reference_video_reads_completion_from_reference_units(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad", generation_mode="reference_video")
    video_path = "reference_videos/E1U1.mp4"
    _write_artifact(project_path, video_path)
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01", "duration_seconds": 4}],
            "reference_units": [
                {
                    "unit_id": "E1U1",
                    "shot_ids": ["E1S01"],
                    "references": [],
                    "generated_assets": {"video_clip": video_path},
                }
            ],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "EXPORT_READY"
    assert status.artifacts["videos"] == {
        "state": "current",
        "current_ids": ["E1U1"],
        "missing_ids": [],
        "stale_ids": [],
    }


@pytest.mark.integration
def test_ad_reference_video_without_derived_units_requires_video_generation(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad", generation_mode="reference_video")
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01", "duration_seconds": 4}],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "VIDEO"
    assert status.artifacts["videos"]["state"] == "missing"
    assert status.next_action.type == "generate_videos"


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
    atomic_write_json(draft_dir / "step1_segments.json", {"episode": 1, "segments": []})
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "generated_assets": {}}],
        },
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
    rebuilt = service.get_status("demo")

    assert rebuilt.state == "STEP1_REVIEW"
    assert rebuilt.next_action.type == "confirm_step1"


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
    step1_path = draft_dir / "step1_segments.json"
    atomic_write_json(step1_path, {"segments": [{"segment_id": "E1S01", "novel_text": "旧内容"}]})
    old_revision = script_review.content_fingerprint(step1_path)
    assert old_revision is not None
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "narration",
            "segments": [{"segment_id": "E1S01", "duration_seconds": 4, "generated_assets": {}}],
            "metadata": {script_review.SCRIPT_STEP1_REVISION_FIELD: old_revision},
        },
    )

    atomic_write_json(step1_path, {"segments": [{"segment_id": "E1S01", "novel_text": "新内容"}]})
    new_revision = script_review.content_fingerprint(step1_path)
    assert new_revision is not None
    pm.update_project(
        "demo", lambda project: script_review.apply_confirmation(project, 1, new_revision, "2026-08-11T00:00:00Z")
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "FINAL_SCRIPT"
    assert status.artifacts["script"]["state"] == "stale"
    assert status.next_action.type == "generate_script"


@pytest.mark.integration
def test_script_id_must_match_the_shared_storyboard_pattern(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad")
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
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
def test_ad_reference_unit_requires_shot_membership(tmp_path: Path) -> None:
    pm, project_path = _make_project(tmp_path, "ad", generation_mode="reference_video")
    video_path = "reference_videos/E1U1.mp4"
    _write_artifact(project_path, video_path)
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "ad",
            "shots": [{"shot_id": "E1S01", "duration_seconds": 4}],
            "reference_units": [
                {
                    "unit_id": "E1U1",
                    "references": [],
                    "generated_assets": {"video_clip": video_path},
                }
            ],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "VIDEO"
    assert status.artifacts["videos"]["state"] == "blocked"
    assert status.blockers[0].code == "invalid_reference_unit"


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

    assert WorkflowStateService._planning_complete(project_path, project, source) is True


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

    assert WorkflowStateService._planning_complete(project_path, project, source) is True


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
    atomic_write_json(draft_dir / "step1_reference_units.json", {"units": []})
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
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
    assert status.blockers[0].code == "duplicate_script_id"


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
    atomic_write_json(draft_dir / "step1_reference_units.json", {"units": []})
    atomic_write_json(
        project_path / "scripts" / "episode_1.json",
        {
            "episode": 1,
            "content_mode": "drama",
            "video_units": [{"unit_id": "E1U01", "duration_seconds": 8, "generated_assets": {}}],
        },
    )

    status = WorkflowStateService(pm).get_status("demo")

    assert status.state == "VIDEO"
    assert status.artifacts["storyboards"]["state"] == "not_applicable"
    assert status.artifacts["audio"]["state"] == "not_applicable"
    assert status.next_action.requested_ids == ["E1U01"]
