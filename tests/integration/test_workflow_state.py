from __future__ import annotations

from pathlib import Path

import pytest

from lib.asset_inventory import complete_asset_inventory
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
    assert status.artifacts["asset_inventory"]["state"] == "missing"
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
    atomic_write_json(script_path, script)
    video = service.get_status("demo")
    assert video.state == "VIDEO"

    script["segments"][0]["generated_assets"]["video_clip"] = "videos/E1S01.mp4"
    atomic_write_json(script_path, script)
    audio = service.get_status("demo")
    assert audio.state == "AUDIO"

    script["segments"][0]["generated_assets"]["narration_audio"] = "audio/E1S01.wav"
    atomic_write_json(script_path, script)
    ready = service.get_status("demo")
    assert ready.state == "EXPORT_READY"
    assert ready.next_action.type == "export"


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
