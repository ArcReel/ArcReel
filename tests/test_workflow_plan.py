from __future__ import annotations

import pytest

from lib.artifact_manifest import ArtifactStatus
from lib.batch_admission import BatchAdmission, UnitAdmissionTicket
from lib.generation_result import (
    GenerationAction,
    GenerationBatchResult,
    GenerationItemResult,
    GenerationItemState,
    GenerationProblem,
    GenerationSelectionMode,
    GenerationTaskState,
    ProviderCheckpoint,
)
from lib.narration_delivery import POST_PRODUCTION, USE_TTS, NarrationDelivery
from lib.workflow_plan import (
    WorkflowStepState,
    WorkflowTaskObservation,
    build_workflow_plan,
)
from lib.workflow_rules import WORKFLOW_RULES, workflow_rule
from lib.workflow_state import (
    WorkflowBlocker,
    WorkflowNextAction,
    WorkflowProject,
    WorkflowStatus,
    WorkflowTarget,
)

pytestmark = pytest.mark.unit


def _status(
    content_mode: str = "narration",
    generation_mode: str = "storyboard",
    *,
    state: str = "VIDEO",
    action: str = "generate_videos",
    requested_ids: list[str] | None = None,
) -> WorkflowStatus:
    return WorkflowStatus.model_validate(
        {
            "schema_version": 1,
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
            "state": state,
            "blockers": [],
            "gates": {"step1_review": {"state": "confirmed", "revision": "sha256-v1:step1"}},
            "artifacts": {
                "asset_inventory": {"state": "current" if content_mode != "ad" else "not_applicable"},
                "asset_sheets": {},
                "step1": {"state": "current" if content_mode != "ad" else "not_applicable"},
                "script": {"state": "current", "path": "scripts/episode_1.json"},
                "storyboards": {"current_ids": ["E1S01"], "stale_ids": [], "missing_ids": []},
                "videos": {
                    "current_ids": [],
                    "stale_ids": [],
                    "missing_ids": requested_ids or ["E1S01"],
                },
                "audio": {"current_ids": [], "stale_ids": [], "missing_ids": ["E1S01"]},
            },
            "next_action": WorkflowNextAction(
                type=action,
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
        assert step_ids.index("script_structure") < step_ids.index("storyboard")
        assert step_ids.index("storyboard") < step_ids.index("narration_delivery")
        assert step_ids.index("narration_delivery") < step_ids.index("video")
        storyboard = next(step for step in rule.steps if step.id == "storyboard")
        assert storyboard.applicable is (generation_mode == "storyboard")
        assert next(step for step in rule.steps if step.id == "narration_delivery").applicable is True


@pytest.mark.parametrize("content_mode,generation_mode", sorted(WORKFLOW_RULES))
@pytest.mark.parametrize("narration_delivery", [POST_PRODUCTION, USE_TTS])
def test_every_route_keeps_each_transient_narration_delivery_choice(
    content_mode: str,
    generation_mode: str,
    narration_delivery: NarrationDelivery,
) -> None:
    status = _status(content_mode=content_mode, generation_mode=generation_mode)
    admission = BatchAdmission(
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        narration_delivery=narration_delivery,
        tickets=(UnitAdmissionTicket("E1S01"),),
    )

    plan = build_workflow_plan(
        status,
        narration_delivery=narration_delivery,
        admission=admission.to_payload(),
    )

    assert plan.narration_delivery.selected == narration_delivery
    assert plan.narration_delivery.persisted is False
    assert _step(plan, "storyboard").required is (generation_mode == "storyboard")
    assert _step(plan, "narration_delivery").state is WorkflowStepState.COMPLETED
    assert _step(plan, "video").state is WorkflowStepState.READY


def test_reference_route_skips_only_storyboard_media_not_delivery_choice() -> None:
    plan = build_workflow_plan(_status(generation_mode="reference_video"))

    assert _step(plan, "storyboard").state is WorkflowStepState.SKIPPED
    assert _step(plan, "narration_delivery").state is WorkflowStepState.READY
    assert plan.next_action.type == "choose_narration_delivery"


def test_post_production_keeps_video_executable_when_tts_is_missing() -> None:
    status = _status()
    admission = BatchAdmission(
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        narration_delivery=POST_PRODUCTION,
        tickets=(UnitAdmissionTicket("E1S01"),),
    )

    plan = build_workflow_plan(
        status,
        narration_delivery=POST_PRODUCTION,
        admission=admission.to_payload(),
    )

    assert plan.narration_delivery.selected == POST_PRODUCTION
    assert _step(plan, "narration_delivery").state is WorkflowStepState.COMPLETED
    assert _step(plan, "video").state is WorkflowStepState.READY
    assert _step(plan, "video").artifacts["missing_ids"] == ["E1S01"]
    assert plan.next_action == status.next_action


def _status_with_blocked_audio() -> WorkflowStatus:
    status = _status()
    artifacts = dict(status.artifacts)
    artifacts["audio"] = {"state": "blocked", "current_ids": [], "stale_ids": [], "missing_ids": ["E1S01"]}
    return status.model_copy(update={"artifacts": artifacts})


def test_blocked_audio_artifact_survives_the_delivery_step_projection() -> None:
    plan = build_workflow_plan(_status_with_blocked_audio(), narration_delivery=USE_TTS)

    assert _step(plan, "narration_delivery").state is WorkflowStepState.BLOCKED
    assert _step(plan, "narration_delivery").artifacts["state"] == "blocked"


@pytest.mark.parametrize("delivery", [POST_PRODUCTION, None])
def test_blocked_audio_artifact_does_not_block_the_post_production_path(delivery: NarrationDelivery | None) -> None:
    plan = build_workflow_plan(_status_with_blocked_audio(), narration_delivery=delivery)

    expected = WorkflowStepState.COMPLETED if delivery is not None else WorkflowStepState.READY
    assert _step(plan, "narration_delivery").state is expected


def test_use_tts_preserves_structured_admission_blockers() -> None:
    problem = GenerationProblem(
        code="tts_missing",
        detail="current TTS is missing",
        action=GenerationAction.GENERATE_TTS,
    )
    admission = BatchAdmission(
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        narration_delivery=USE_TTS,
        tickets=(UnitAdmissionTicket("E1S01", problems=(problem,)),),
    )

    plan = build_workflow_plan(
        _status(),
        narration_delivery=USE_TTS,
        admission=admission.to_payload(),
    )

    video = _step(plan, "video")
    assert video.state is WorkflowStepState.BLOCKED
    assert video.problems == [problem]
    assert video.admission["decision"] == "blocked"
    assert plan.next_action.type == GenerationAction.GENERATE_TTS.value


def test_multiple_admission_repairs_preserve_the_first_structured_action() -> None:
    generate_tts = GenerationProblem(
        code="tts_missing",
        detail="current TTS is missing",
        action=GenerationAction.GENERATE_TTS,
    )
    configure_provider = GenerationProblem(
        code="video_capability_missing_i2v",
        detail="the selected model cannot generate this video",
        action=GenerationAction.CONFIGURE_PROVIDER,
    )
    admission = BatchAdmission(
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        narration_delivery=USE_TTS,
        tickets=(
            UnitAdmissionTicket("E1S01", problems=(generate_tts,)),
            UnitAdmissionTicket("E1S02", problems=(configure_provider,)),
        ),
    )

    plan = build_workflow_plan(
        _status(requested_ids=["E1S01", "E1S02"]),
        narration_delivery=USE_TTS,
        admission=admission.to_payload(),
    )

    assert plan.problems == [generate_tts, configure_provider]
    assert plan.next_action.type == GenerationAction.GENERATE_TTS.value


def test_structure_problems_block_before_every_media_step_and_point_to_atomic_edit() -> None:
    problem = GenerationProblem(
        code="mixed_speech",
        detail="character and narrator speech are mixed",
        action=GenerationAction.REPLAN_UNIT,
        params={"unit_id": "E1S01"},
    )

    plan = build_workflow_plan(
        _status(state="STORYBOARD", action="generate_storyboards"),
        structure_problems=[problem],
        script_revision="sha256-v1:script",
    )

    assert _step(plan, "script_structure").state is WorkflowStepState.BLOCKED
    assert _step(plan, "storyboard").state is WorkflowStepState.PENDING
    assert _step(plan, "video").state is WorkflowStepState.PENDING
    assert plan.next_action.type == "patch_episode_script"
    assert plan.next_action.args["expected_revision"] == "sha256-v1:script"
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


def test_unrepairable_structural_blocker_stays_a_status_blocker_before_media() -> None:
    status = _status(state="PROJECT_INPUT", action="none")
    blocker = WorkflowBlocker(
        code="invalid_script_structure",
        path="scripts/episode_1.json",
        reason="script container is not repairable through media actions",
    )
    status.blockers = [blocker]
    status.next_action = WorkflowNextAction(type="none", reason="workflow is blocked")

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
        narration_delivery=POST_PRODUCTION,
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


def test_partial_execution_result_does_not_reinterpret_admission_or_artifact_status() -> None:
    admission = BatchAdmission(
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        narration_delivery=POST_PRODUCTION,
        tickets=(UnitAdmissionTicket("E1S01"), UnitAdmissionTicket("E1S02")),
    )
    failed_problem = GenerationProblem(
        code="provider_failed",
        detail="provider rejected one unit",
        action=GenerationAction.RETRY,
    )
    result = GenerationBatchResult(
        operation="generate_videos",
        selection=GenerationSelectionMode.MISSING_ONLY,
        requested=["E1S01", "E1S02"],
        succeeded=["E1S01"],
        failed=["E1S02"],
        blocked=[],
        items=[
            GenerationItemResult(
                unit_id="E1S01",
                state=GenerationItemState.SUCCEEDED,
                task_state=GenerationTaskState.SUCCEEDED,
                artifact_status=ArtifactStatus.STALE,
            ),
            GenerationItemResult(
                unit_id="E1S02",
                state=GenerationItemState.FAILED,
                task_state=GenerationTaskState.FAILED,
                artifact_status=ArtifactStatus.MISSING,
                problem=failed_problem,
            ),
        ],
    )

    plan = build_workflow_plan(
        _status(requested_ids=["E1S01", "E1S02"]),
        narration_delivery=POST_PRODUCTION,
        admission=admission.to_payload(),
        generation_result=result,
    )

    video = _step(plan, "video")
    assert video.admission["decision"] == "admitted"
    assert video.generation_result == result
    assert video.generation_result.succeeded == ["E1S01"]
    assert video.generation_result.failed == ["E1S02"]
    assert video.generation_result.items[0].artifact_status is ArtifactStatus.STALE
    assert video.generation_result.items[1].artifact_status is ArtifactStatus.MISSING


def test_stale_video_remains_exportable_without_an_implicit_regeneration_step() -> None:
    status = _status(state="EXPORT_READY", action="export")
    status.artifacts["videos"] = {
        "current_ids": [],
        "stale_ids": ["E1S01"],
        "missing_ids": [],
    }
    status.next_action = WorkflowNextAction(type="export", reason="usable media is ready")

    plan = build_workflow_plan(status)

    video = _step(plan, "video")
    assert video.state is WorkflowStepState.COMPLETED
    assert video.artifacts["stale_ids"] == ["E1S01"]
    assert video.action is None
    assert plan.next_action.type == "export"
