from __future__ import annotations

from pathlib import Path

import pytest

from lib.infra.json_io import atomic_write_json
from lib.project.project_manager import ProjectManager
from lib.script import script_review
from lib.script.blank_script import BlankScriptError, start_blank_script
from lib.script.draft_quarantine import (
    FORMAL_EDIT_META_KEY,
    QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
    write_quarantine,
)
from lib.workflow.workflow_state import WorkflowStateService


def _project(
    tmp_path: Path, mode: str, *, generation_mode: str = "storyboard", title: str = "番外"
) -> tuple[ProjectManager, Path]:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    extras = {"generation_mode": generation_mode, "grid_storyboard": False}
    pm.create_project_metadata("demo", "Demo", "", mode, extras=extras)
    pm.update_project(
        "demo",
        lambda project: project.update(
            episodes=[
                {"episode": 1, "title": title, "script_file": "scripts/episode_1.json", "ledger_status": "planned"}
            ]
        ),
    )
    return pm, pm.get_project_path("demo")


def _drama_plan() -> dict:
    return {
        "scenes": [
            {
                "scene_id": "E1S01",
                "duration_seconds": 8,
                "segment_break": False,
                "characters_in_scene": [],
                "scenes": [],
                "props": [],
                "utterances": [{"kind": "voiceover", "speaker": None, "text": "风吹过旷野。"}],
                "scene_description": "旷野",
            }
        ]
    }


def _write_plan(project_path: Path) -> Path:
    path = project_path / "drafts" / "episode_1" / "script_plan_normalized_script.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(path, _drama_plan())
    return path


@pytest.mark.parametrize(
    ("mode", "generation_mode", "items_key"),
    [
        ("narration", "storyboard", "segments"),
        ("drama", "storyboard", "scenes"),
        ("ad", "storyboard", "shots"),
        ("drama", "reference_video", "video_units"),
    ],
)
def test_blank_start_writes_an_empty_formal_script_that_asks_for_items(
    tmp_path: Path, mode: str, generation_mode: str, items_key: str
) -> None:
    pm, _project_path = _project(tmp_path, mode, generation_mode=generation_mode)

    start_blank_script(pm, "demo", 1)

    script = pm.load_script("demo", "episode_1.json")
    assert script[items_key] == []
    assert script["episode"] == 1
    assert script["title"] == "番外"
    status = WorkflowStateService(pm).get_status("demo")
    assert status.content is not None
    assert status.content.formal_script == "present"
    assert status.next_action.type == "add_script_items"


def test_blank_start_keeps_an_empty_episode_title_empty(tmp_path: Path) -> None:
    pm, _project_path = _project(tmp_path, "narration", title="")

    start_blank_script(pm, "demo", 1)

    assert pm.load_script("demo", "episode_1.json")["title"] == ""
    assert pm.load_project("demo")["episodes"][0]["title"] == ""


def test_blank_start_discards_the_unconfirmed_plan_and_its_draft(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path, "drama")
    plan = _write_plan(project_path)
    draft = write_quarantine(project_path, 1, QUARANTINE_KIND_DRAMA_SCRIPT_PLAN, content=_drama_plan(), violations=[])

    start_blank_script(pm, "demo", 1)

    assert not plan.exists()
    assert not draft.exists()
    project = pm.load_project("demo")
    assert script_review.review_status(project_path, project, 1) == "no_script_plan"


def test_plan_generated_after_a_blank_start_waits_for_confirmation(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path, "drama")
    start_blank_script(pm, "demo", 1)

    _write_plan(project_path)

    project = pm.load_project("demo")
    assert script_review.review_status(project_path, project, 1) == "pending_review"
    assert script_review.formal_script_overwrite(project_path, project, 1) is not None


def test_plan_without_a_confirmation_fingerprint_waits_for_confirmation_over_a_formal_script(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path, "drama")
    pm.save_script("demo", {"episode": 1, "title": "番外", "content_mode": "drama", "scenes": []}, "episode_1.json")
    plan = _write_plan(project_path)

    project = pm.load_project("demo")
    assert script_review.review_status(project_path, project, 1) == "pending_review"

    rewritten = _drama_plan()
    rewritten["scenes"][0]["scene_description"] = "山门"
    atomic_write_json(plan, rewritten)

    assert script_review.review_status(project_path, project, 1) == "pending_review"
    assert script_review.formal_script_plan_confirmed(project_path, project, 1) is False
    assert script_review.formal_script_overwrite(project_path, project, 1) is not None


def test_blank_start_refuses_when_a_formal_script_exists(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path, "drama")
    pm.save_script("demo", {"episode": 1, "title": "番外", "content_mode": "drama", "scenes": []}, "episode_1.json")
    before = (project_path / "scripts" / "episode_1.json").read_bytes()

    with pytest.raises(BlankScriptError) as excinfo:
        start_blank_script(pm, "demo", 1)

    assert excinfo.value.code == "formal_script_exists"
    assert (project_path / "scripts" / "episode_1.json").read_bytes() == before


def test_blank_start_treats_an_unregistered_script_file_as_no_formal_script(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path, "drama")
    stray = {"episode": 1, "title": "番外", "content_mode": "drama", "scenes": [_drama_plan()["scenes"][0]]}
    atomic_write_json(project_path / "scripts" / "episode_1.json", stray)
    before = WorkflowStateService(pm).get_status("demo")
    assert before.content is not None
    assert before.content.formal_script == "absent"

    start_blank_script(pm, "demo", 1)

    assert pm.load_script("demo", "episode_1.json")["scenes"] == []
    after = WorkflowStateService(pm).get_status("demo")
    assert after.content is not None
    assert after.content.formal_script == "present"


def test_blank_start_leaves_an_agent_owned_draft_alone(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path, "drama")
    plan = _write_plan(project_path)
    draft = write_quarantine(
        project_path,
        1,
        QUARANTINE_KIND_DRAMA_SCRIPT_PLAN,
        content=_drama_plan(),
        violations=[],
        meta={FORMAL_EDIT_META_KEY: True},
    )

    with pytest.raises(BlankScriptError) as excinfo:
        start_blank_script(pm, "demo", 1)

    assert excinfo.value.code == "draft_agent_owned"
    assert plan.exists()
    assert draft.exists()
    assert not (project_path / "scripts" / "episode_1.json").exists()


def test_blank_start_refuses_an_unknown_episode(tmp_path: Path) -> None:
    pm, _project_path = _project(tmp_path, "drama")

    with pytest.raises(BlankScriptError) as excinfo:
        start_blank_script(pm, "demo", 9)

    assert excinfo.value.code == "episode_not_found"


def test_an_old_confirmation_does_not_confirm_a_plan_made_after_the_blank_start(tmp_path: Path) -> None:
    pm, project_path = _project(tmp_path, "drama")
    fingerprint = script_review.content_fingerprint_of_data(_drama_plan())
    pm.update_project("demo", lambda project: script_review.apply_confirmation(project, 1, fingerprint, "2026-01-01"))
    start_blank_script(pm, "demo", 1)

    _write_plan(project_path)

    project = pm.load_project("demo")
    assert script_review.review_status(project_path, project, 1) == "pending_review"
