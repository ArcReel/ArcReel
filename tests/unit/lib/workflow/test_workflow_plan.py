from __future__ import annotations

import pytest
from pydantic import ValidationError

from lib.generation.batch_admission import BatchAdmission, UnitAdmissionTicket
from lib.generation.generation_result import (
    GenerationAction,
    GenerationProblem,
    GenerationSelectionMode,
    ProviderCheckpoint,
)
from lib.workflow.workflow_plan import (
    WorkflowPlanRequest,
    WorkflowStepState,
    WorkflowTaskObservation,
    build_workflow_plan,
)
from lib.workflow.workflow_rules import WORKFLOW_RULES, workflow_rule
from lib.workflow.workflow_state import (
    EPISODE_COMPLETE_REASON,
    WorkflowActionType,
    WorkflowBlocker,
    WorkflowContent,
    WorkflowNextAction,
    WorkflowProject,
    WorkflowStatus,
    WorkflowTarget,
)


def _status(
    content_mode: str = "narration",
    generation_mode: str = "storyboard",
    *,
    action: str = "generate_videos",
    requested_ids: list[str] | None = None,
) -> WorkflowStatus:
    return WorkflowStatus.model_validate(
        {
            "project_revision": "sha256-v1:project",
            "source_revision": None if content_mode == "ad" else "sha256-v1:source",
            "project": WorkflowProject(
                content_mode=content_mode,
                generation_mode=generation_mode,
                grid_storyboard=False,
            ),
            "target": WorkflowTarget(
                episode=1,
                script="scripts/episode_1.json",
                script_filename="episode_1.json",
                source="source/episode_1.txt",
            ),
            "blockers": [],
            "content": WorkflowContent(
                episode_count=1,
                whole_source="not_applicable" if content_mode == "ad" else "present",
                source_remaining=False,
                ad_inputs="present" if content_mode == "ad" else "not_applicable",
                episode_source="not_applicable" if content_mode == "ad" else "present",
                formal_script="present",
                script_item_count=1,
            ),
            "gates": {"script_plan_review": {"state": "confirmed", "revision": "sha256-v1:script_plan"}},
            "artifacts": {
                "asset_sheets": {},
                "script_plan": {"state": "current" if content_mode != "ad" else "not_applicable"},
                "script": {"state": "current", "path": "scripts/episode_1.json"},
                "storyboards": {"current_ids": ["E1S01"], "stale_ids": [], "missing_ids": []},
                "videos": {
                    "current_ids": [],
                    "stale_ids": [],
                    "missing_ids": requested_ids or ["E1S01"],
                },
                "audio": {"state": "not_applicable", "current_ids": [], "stale_ids": [], "missing_ids": []},
            },
            "next_action": WorkflowNextAction(
                type=WorkflowActionType(action),
                requested_ids=requested_ids or ["E1S01"],
                reason="video clips are missing",
            ),
        }
    )


def _step(plan, step_id: str):
    return next(step for step in plan.steps if step.id == step_id)


def test_rules_exhaust_the_six_content_and_generation_mode_combinations() -> None:
    assert set(WORKFLOW_RULES) == {
        ("narration", "storyboard"),
        ("narration", "reference_video"),
        ("drama", "storyboard"),
        ("drama", "reference_video"),
        ("ad", "storyboard"),
        ("ad", "reference_video"),
    }

    for content_mode, generation_mode in WORKFLOW_RULES:
        rule = workflow_rule(content_mode, generation_mode)
        step_ids = [step.id for step in rule.steps]
        assert "narration_delivery" not in step_ids
        assert "export" not in step_ids
        assert step_ids[-3:] == ["storyboard", "video", "edit"]
        storyboard = next(step for step in rule.steps if step.id == "storyboard")
        assert storyboard.applicable is (generation_mode == "storyboard")
        assert next(step for step in rule.steps if step.id == "edit").applicable is True


@pytest.mark.parametrize(("content_mode", "generation_mode"), sorted(WORKFLOW_RULES))
def test_missing_videos_are_the_next_action_without_a_delivery_choice(content_mode: str, generation_mode: str) -> None:
    status = _status(content_mode=content_mode, generation_mode=generation_mode)
    admission = BatchAdmission(
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        tickets=(UnitAdmissionTicket("E1S01"),),
    )

    plan = build_workflow_plan(status, admission=admission.to_payload())

    assert "narration_delivery" not in plan.model_dump()
    assert _step(plan, "storyboard").required is (generation_mode == "storyboard")
    assert _step(plan, "video").state is WorkflowStepState.READY
    assert _step(plan, "video").artifacts["missing_ids"] == ["E1S01"]
    assert _step(plan, "edit").state is WorkflowStepState.PENDING
    assert plan.next_action == status.next_action


def test_plan_request_no_longer_accepts_a_narration_delivery_choice() -> None:
    with pytest.raises(ValidationError):
        WorkflowPlanRequest.model_validate({"narration_delivery": "use_tts"})


def test_episode_without_edit_timeline_points_to_the_edit_step() -> None:
    status = _status(action="create_edit_timeline")
    status.artifacts["videos"] = {"current_ids": ["E1S01"], "stale_ids": [], "missing_ids": []}
    status.artifacts["edit_timelines"] = {"timeline_ids": []}

    plan = build_workflow_plan(status)

    edit = _step(plan, "edit")
    assert _step(plan, "video").state is WorkflowStepState.COMPLETED
    assert edit.state is WorkflowStepState.READY
    assert edit.action is not None
    assert edit.action.type == "create_edit_timeline"
    assert edit.artifacts == {"timeline_ids": []}
    assert plan.next_action.type == "create_edit_timeline"


def test_episode_with_an_edit_timeline_completes_every_applicable_step() -> None:
    status = _status(generation_mode="reference_video", action="none")
    status.next_action = WorkflowNextAction(type=WorkflowActionType.NONE, reason=EPISODE_COMPLETE_REASON)
    status.artifacts["videos"] = {"current_ids": ["E1S01"], "stale_ids": [], "missing_ids": []}
    status.artifacts["edit_timelines"] = {"timeline_ids": ["tl-0123abcd"]}

    plan = build_workflow_plan(status)

    assert _step(plan, "storyboard").state is WorkflowStepState.SKIPPED
    assert all(step.state is WorkflowStepState.COMPLETED for step in plan.steps if step.required)
    assert _step(plan, "edit").artifacts == {"timeline_ids": ["tl-0123abcd"]}
    assert all(step.action is None for step in plan.steps)
    assert plan.next_action.type == "none"


def test_unreadable_edit_timelines_block_the_edit_step_instead_of_completing_it() -> None:
    status = _status(action="none")
    status.next_action = WorkflowNextAction(type=WorkflowActionType.NONE, reason="edit timelines cannot be read")
    status.artifacts["videos"] = {"current_ids": ["E1S01"], "stale_ids": [], "missing_ids": []}
    status.artifacts["edit_timelines"] = {"timeline_ids": []}
    status.issues = [
        WorkflowBlocker(code="invalid_edit_timelines", path="edit_timelines/episode_1", reason="unreadable")
    ]

    plan = build_workflow_plan(status)

    assert _step(plan, "video").state is WorkflowStepState.COMPLETED
    assert _step(plan, "edit").state is WorkflowStepState.BLOCKED


def test_blocked_videos_own_the_stop_even_when_edit_timelines_are_also_unreadable() -> None:
    status = _status(action="none")
    status.next_action = WorkflowNextAction(type=WorkflowActionType.NONE, reason="video clips cannot be read")
    status.artifacts["videos"] = {"current_ids": [], "stale_ids": [], "missing_ids": [], "state": "blocked"}
    status.artifacts["edit_timelines"] = {"timeline_ids": []}
    status.issues = [
        WorkflowBlocker(code="invalid_edit_timelines", path="edit_timelines/episode_1", reason="unreadable")
    ]

    plan = build_workflow_plan(status)

    assert _step(plan, "video").state is WorkflowStepState.BLOCKED
    assert _step(plan, "edit").state is not WorkflowStepState.BLOCKED


def test_use_tts_preserves_structured_admission_blockers() -> None:
    problem = GenerationProblem(
        code="reference_asset_missing",
        detail="a referenced image is missing",
        action=GenerationAction.FIX_INPUT,
    )
    admission = BatchAdmission(
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        tickets=(UnitAdmissionTicket("E1S01", problems=(problem,)),),
    )

    plan = build_workflow_plan(
        _status(),
        admission=admission.to_payload(),
    )

    video = _step(plan, "video")
    assert video.state is WorkflowStepState.BLOCKED
    assert video.problems == [problem]
    assert video.admission["decision"] == "blocked"
    assert plan.next_action.type == GenerationAction.FIX_INPUT.value


def test_multiple_admission_repairs_preserve_the_first_structured_action() -> None:
    fix_input = GenerationProblem(
        code="reference_asset_missing",
        detail="a referenced image is missing",
        action=GenerationAction.FIX_INPUT,
    )
    configure_provider = GenerationProblem(
        code="video_capability_missing_i2v",
        detail="the selected model cannot generate this video",
        action=GenerationAction.CONFIGURE_PROVIDER,
    )
    admission = BatchAdmission(
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        tickets=(
            UnitAdmissionTicket("E1S01", problems=(fix_input,)),
            UnitAdmissionTicket("E1S02", problems=(configure_provider,)),
        ),
    )

    plan = build_workflow_plan(
        _status(requested_ids=["E1S01", "E1S02"]),
        admission=admission.to_payload(),
    )

    assert plan.problems == [fix_input, configure_provider]
    assert plan.next_action.type == GenerationAction.FIX_INPUT.value


def test_structure_problems_block_before_every_media_step_and_point_to_atomic_edit() -> None:
    problem = GenerationProblem(
        code="mixed_speech",
        detail="character and narrator speech are mixed",
        action=GenerationAction.REPLAN_UNIT,
        params={"unit_id": "E1S01"},
    )

    plan = build_workflow_plan(
        _status(action="generate_storyboards"),
        structure_problems=[problem],
        script_revision="sha256-v1:script",
    )

    assert _step(plan, "script_structure").state is WorkflowStepState.BLOCKED
    assert _step(plan, "storyboard").state is WorkflowStepState.PENDING
    assert _step(plan, "video").state is WorkflowStepState.PENDING
    assert plan.next_action.type == "patch_episode_script"
    assert plan.next_action.args["base_revision"] == "sha256-v1:script"
    assert plan.next_action.requested_ids == ["E1S01"]


def test_needs_replan_uses_the_same_atomic_structure_edit_step() -> None:
    problem = GenerationProblem(
        code="needs_replan",
        detail="unit requires replanning",
        action=GenerationAction.REPLAN_UNIT,
        params={"unit_id": "E1U01"},
    )

    plan = build_workflow_plan(
        _status(generation_mode="reference_video"),
        structure_problems=[problem],
        script_revision="sha256-v1:script",
    )

    assert _step(plan, "storyboard").state is WorkflowStepState.SKIPPED
    assert _step(plan, "script_structure").problems[0].code == "needs_replan"
    assert plan.next_action.type == "patch_episode_script"
    assert plan.next_action.requested_ids == ["E1U01"]


def test_project_blocker_owns_the_first_step_and_states_no_content() -> None:
    status = _status(action="none")
    blocker = WorkflowBlocker(
        code="artifact_currency_unavailable",
        path=".arcreel_artifacts.json",
        reason="manifest is unreadable",
    )
    status.blockers = [blocker]
    status.content = None
    status.next_action = WorkflowNextAction(type=WorkflowActionType.NONE, reason="workflow is blocked")

    plan = build_workflow_plan(status)

    assert plan.blockers == [blocker]
    assert _step(plan, "project_input").state is WorkflowStepState.BLOCKED
    assert _step(plan, "storyboard").state is WorkflowStepState.PENDING
    assert _step(plan, "video").state is WorkflowStepState.PENDING
    assert plan.next_action.type == "none"


def test_artifact_task_and_checkpoint_axes_remain_distinct() -> None:
    status = _status()
    status.artifacts["videos"] = {
        "current_ids": [],
        "stale_ids": ["E1S01"],
        "missing_ids": ["E1S02"],
        "state": "blocked",
    }
    task = WorkflowTaskObservation(
        unit_id="E1S02",
        task_id="task-1",
        task_type="video",
        status="running",
        provider_checkpoint=ProviderCheckpoint(
            submitted=True,
            provider_id="provider-a",
            provider_job_id="job-1",
        ),
    )

    plan = build_workflow_plan(
        status,
        task_observations=[task],
    )

    video = _step(plan, "video")
    assert video.artifacts == status.artifacts["videos"]
    assert video.tasks == [task]
    assert video.state is WorkflowStepState.ACTIVE
    assert video.tasks[0].provider_checkpoint is not None
    assert video.tasks[0].provider_checkpoint.submitted is True
    assert "is_ready" not in video.model_dump()
    assert plan.next_action.type == GenerationAction.WAIT_FOR_TASK.value


def test_stale_video_remains_editable_without_an_implicit_regeneration_step() -> None:
    status = _status(action="create_edit_timeline")
    status.artifacts["videos"] = {
        "current_ids": [],
        "stale_ids": ["E1S01"],
        "missing_ids": [],
    }
    status.next_action = WorkflowNextAction(
        type=WorkflowActionType.CREATE_EDIT_TIMELINE, args={"episode_id": 1}, reason="episode has no edit timeline"
    )

    plan = build_workflow_plan(status)

    video = _step(plan, "video")
    assert video.state is WorkflowStepState.COMPLETED
    assert video.artifacts["stale_ids"] == ["E1S01"]
    assert video.action is None
    assert plan.next_action.type == "create_edit_timeline"


def test_steps_state_their_own_content_instead_of_their_position() -> None:
    """下一步在前面的步骤上时，后面已经齐备的内容照常陈述为完成，不因前一步未完成而显示为待处理。"""
    status = _status(action="author_prompts", requested_ids=["E1S02"])
    status.content.pending_authoring_ids = ["E1S02"]
    status.artifacts["videos"] = {"current_ids": ["E1S01"], "stale_ids": [], "missing_ids": []}

    plan = build_workflow_plan(status)

    assert _step(plan, "final_script").state is WorkflowStepState.READY
    assert _step(plan, "final_script").action == status.next_action
    assert _step(plan, "storyboard").state is WorkflowStepState.COMPLETED
    assert _step(plan, "video").state is WorkflowStepState.COMPLETED


def test_branch_alternatives_travel_with_the_unchanged_next_action() -> None:
    status = _status(action="prepare_script_plan")
    status.content.formal_script = "absent"
    status.content.script_item_count = None
    alternative = WorkflowNextAction(type=WorkflowActionType.START_BLANK_SCRIPT, reason="write by hand")
    status.next_alternatives = [alternative]

    plan = build_workflow_plan(status)

    assert plan.next_action == status.next_action
    assert plan.next_alternatives == [alternative]
    assert _step(plan, "script_plan_content").state is WorkflowStepState.READY
