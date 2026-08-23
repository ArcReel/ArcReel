"""Side-effect-free projection of authoritative workflow facts into an executable plan."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lib.generation_result import GenerationAction, GenerationProblem, ProviderCheckpoint
from lib.narration_delivery import POST_PRODUCTION, USE_TTS, NarrationDelivery
from lib.workflow_rules import WorkflowStepRule, workflow_rule
from lib.workflow_state import WorkflowActionType, WorkflowBlocker, WorkflowNextAction, WorkflowStatus

PositiveStrictInt = Annotated[int, Field(strict=True, gt=0)]


class WorkflowStepState(StrEnum):
    """Progress of the orchestration step, independent from artifact and task state."""

    COMPLETED = "completed"
    READY = "ready"
    ACTIVE = "active"
    BLOCKED = "blocked"
    PENDING = "pending"
    SKIPPED = "skipped"


class WorkflowPlanRequest(BaseModel):
    """Transient choices used to plan one request without changing project workflow state."""

    model_config = ConfigDict(extra="forbid")

    episode: int | None = Field(default=None, ge=1, strict=True)
    narration_delivery: NarrationDelivery | None = None
    confirmed_request_durations: dict[str, PositiveStrictInt] = Field(default_factory=dict)

    @field_validator("confirmed_request_durations")
    @classmethod
    def _valid_confirmed_durations(cls, value: dict[str, int]) -> dict[str, int]:
        for unit_id in value:
            if not unit_id.strip():
                raise ValueError("confirmed_request_durations keys must be non-empty unit ids")
        return value


class WorkflowNarrationDelivery(BaseModel):
    """The non-persistent delivery choice attached to this plan only."""

    model_config = ConfigDict(extra="forbid")

    selected: NarrationDelivery | None
    options: tuple[Literal["post_production"], Literal["use_tts"]] = (POST_PRODUCTION, USE_TTS)
    persisted: Literal[False] = False


class WorkflowTaskObservation(BaseModel):
    """One task axis, kept separate from provider submission and artifact currency."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    task_id: str
    task_type: str
    status: str
    provider_checkpoint: ProviderCheckpoint | None = None
    problem: GenerationProblem | None = None


class WorkflowStepContracts(BaseModel):
    """Existing deep-module contracts an action must cross when it executes."""

    model_config = ConfigDict(extra="forbid")

    script_edit: Literal["script_batch_edit/v1"] | None = None
    batch_admission: Literal["video_batch_admission/v1"] | None = None


class WorkflowPlanStep(BaseModel):
    """One ordered step without collapsing its artifact, task, and execution axes."""

    model_config = ConfigDict(extra="forbid")

    id: str
    state: WorkflowStepState
    required: bool
    action: WorkflowNextAction | None = None
    requested_ids: list[str] = Field(default_factory=list)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    problems: list[GenerationProblem] = Field(default_factory=list)
    tasks: list[WorkflowTaskObservation] = Field(default_factory=list)
    admission: dict[str, Any] | None = None
    contracts: WorkflowStepContracts = Field(default_factory=WorkflowStepContracts)


class WorkflowPlan(BaseModel):
    """Versioned plan shared unchanged by REST and MCP adapters."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    status: WorkflowStatus
    narration_delivery: WorkflowNarrationDelivery
    steps: list[WorkflowPlanStep]
    blockers: list[WorkflowBlocker]
    problems: list[GenerationProblem]
    next_action: WorkflowNextAction


_ARTIFACT_BY_STEP: dict[str, str] = {
    "asset_inventory": "asset_inventory",
    "asset_sheets": "asset_sheets",
    "step1_content": "step1",
    "step1_review": "step1",
    "final_script": "script",
    "storyboard": "storyboards",
    "narration_delivery": "audio",
    "video": "videos",
}

_TASK_STEP: dict[str, str] = {
    "storyboard": "storyboard",
    "grid": "storyboard",
    "tts": "narration_delivery",
    "video": "video",
    "reference_video": "video",
}


def _baseline_step_state(
    rule: WorkflowStepRule,
    *,
    index: int,
    current_index: int,
    status: WorkflowStatus,
) -> WorkflowStepState:
    if not rule.applicable:
        return WorkflowStepState.SKIPPED
    if index < current_index:
        return WorkflowStepState.COMPLETED
    if index > current_index:
        return WorkflowStepState.PENDING
    if status.blockers or status.next_action.type is WorkflowActionType.NONE:
        return WorkflowStepState.BLOCKED
    return WorkflowStepState.READY


def _current_rule_index(status: WorkflowStatus, rules: tuple[WorkflowStepRule, ...]) -> int:
    if status.next_action.type is WorkflowActionType.REPAIR_VIDEO_UNITS:
        return next(index for index, rule in enumerate(rules) if rule.id == "script_structure")
    for index, rule in enumerate(rules):
        if rule.applicable and rule.checkpoint == status.state:
            return index
    raise ValueError(f"workflow state {status.state!r} is absent from its mode rule")


def _admission_problems(admission: dict[str, Any] | None) -> list[GenerationProblem]:
    if not admission:
        return []
    problems: list[GenerationProblem] = []
    for unit in admission.get("units", []):
        if not isinstance(unit, dict):
            continue
        raw_problems = unit.get("problems", [])
        if not isinstance(raw_problems, list):
            continue
        for raw in raw_problems:
            problems.append(GenerationProblem.model_validate(raw))
    return problems


def _problem_unit_ids(problems: list[GenerationProblem]) -> list[str]:
    ids: list[str] = []
    for problem in problems:
        unit_id = problem.params.get("unit_id")
        if not isinstance(unit_id, str):
            admission = problem.params.get("speech_admission")
            unit_id = admission.get("unit_id") if isinstance(admission, dict) else None
        if isinstance(unit_id, str) and unit_id and unit_id not in ids:
            ids.append(unit_id)
    return ids


def _structure_action(
    problems: list[GenerationProblem],
    *,
    script_revision: str | None,
) -> WorkflowNextAction:
    return WorkflowNextAction(
        type=WorkflowActionType.PATCH_EPISODE_SCRIPT,
        args={
            "expected_revision": script_revision,
            "problems": [problem.model_dump(mode="json") for problem in problems],
        },
        requested_ids=_problem_unit_ids(problems),
        reason=problems[0].detail,
    )


def _admission_action(
    admission: dict[str, Any],
    problems: list[GenerationProblem],
    requested_ids: list[str],
) -> WorkflowNextAction:
    requires_confirmation = admission.get("decision") == "confirmation_required"
    action = problems[0].action if problems else GenerationAction.CONFIRM_REQUEST_DURATION
    return WorkflowNextAction(
        type=WorkflowActionType(action.value),
        args={"admission": admission},
        requested_ids=requested_ids,
        requires_confirmation=requires_confirmation,
        reason=problems[0].detail if problems else "video batch requires confirmation",
    )


def build_workflow_plan(
    status: WorkflowStatus,
    *,
    narration_delivery: NarrationDelivery | None = None,
    structure_problems: list[GenerationProblem] | None = None,
    script_revision: str | None = None,
    task_observations: list[WorkflowTaskObservation] | None = None,
    admission: dict[str, Any] | None = None,
) -> WorkflowPlan:
    """Project one immutable status snapshot and transient request observations."""

    rule = workflow_rule(status.project.content_mode, status.project.generation_mode)
    rules = rule.steps
    current_index = _current_rule_index(status, rules)
    structure_problems = list(structure_problems or [])
    task_observations = list(task_observations or [])
    admission_problems = _admission_problems(admission)
    steps: list[WorkflowPlanStep] = []

    for index, step_rule in enumerate(rules):
        artifact_key = _ARTIFACT_BY_STEP.get(step_rule.id)
        step = WorkflowPlanStep(
            id=step_rule.id,
            state=_baseline_step_state(step_rule, index=index, current_index=current_index, status=status),
            required=step_rule.applicable,
            action=status.next_action if step_rule.checkpoint == status.state else None,
            requested_ids=(list(status.next_action.requested_ids) if step_rule.checkpoint == status.state else []),
            artifacts=dict(status.artifacts.get(artifact_key, {})) if artifact_key is not None else {},
            contracts=(
                WorkflowStepContracts(script_edit="script_batch_edit/v1")
                if step_rule.id == "script_structure"
                else WorkflowStepContracts(batch_admission="video_batch_admission/v1")
                if step_rule.id == "video"
                else WorkflowStepContracts()
            ),
        )
        if step_rule.id == "narration_delivery":
            step.required = status.state == "VIDEO" and status.next_action.type is WorkflowActionType.GENERATE_VIDEOS
        if step.artifacts.get("state") == "blocked" and step.state is not WorkflowStepState.SKIPPED:
            step.state = WorkflowStepState.BLOCKED
        steps.append(step)

    by_id = {step.id: step for step in steps}
    structure_step = by_id["script_structure"]
    if structure_problems:
        structure_step.state = WorkflowStepState.BLOCKED
        structure_step.problems = structure_problems
        structure_step.requested_ids = _problem_unit_ids(structure_problems)
        structure_step.action = _structure_action(structure_problems, script_revision=script_revision)
        for media_step in ("storyboard", "narration_delivery", "video_prompt_optimization", "video"):
            if by_id[media_step].state is not WorkflowStepState.SKIPPED:
                by_id[media_step].state = WorkflowStepState.PENDING
                by_id[media_step].action = None

    delivery_step = by_id["narration_delivery"]
    delivery_index = next(index for index, item in enumerate(rules) if item.id == "narration_delivery")
    if not structure_problems and current_index >= delivery_index:
        # 音轨产物 blocked 只对本次选择 TTS 的请求成立：后期配音路径不消费 TTS 产物，
        # 交付选择尚未作出时两条路径都还开放，两种情况都不该被音轨阻断吞掉。
        tts_blocked = narration_delivery == USE_TTS and delivery_step.state is WorkflowStepState.BLOCKED
        if not tts_blocked:
            delivery_step.state = (
                WorkflowStepState.COMPLETED if narration_delivery is not None else WorkflowStepState.READY
            )

    for observation in task_observations:
        step_id = _TASK_STEP.get(observation.task_type)
        if step_id is None:
            continue
        step = by_id[step_id]
        step.tasks.append(observation)
        if observation.status in {"queued", "running", "cancelling"}:
            step.state = WorkflowStepState.ACTIVE

    video_step = by_id["video"]
    video_step.admission = admission
    video_step.problems = admission_problems
    if admission is not None and admission.get("decision") != "admitted" and not video_step.tasks:
        video_step.state = WorkflowStepState.BLOCKED

    active_tasks = [task for task in task_observations if task.status in {"queued", "running", "cancelling"}]
    if status.blockers:
        next_action = status.next_action
    elif structure_problems:
        next_action = _structure_action(structure_problems, script_revision=script_revision)
    elif active_tasks:
        next_action = WorkflowNextAction(
            type=WorkflowActionType.WAIT_FOR_TASK,
            args={"task_ids": [task.task_id for task in active_tasks]},
            requested_ids=[task.unit_id for task in active_tasks],
            reason="workflow has active generation tasks",
        )
        for step in steps:
            if step.state is WorkflowStepState.ACTIVE:
                step.action = next_action
        if video_step.state is not WorkflowStepState.ACTIVE:
            video_step.action = None
    elif (
        status.state == "VIDEO"
        and status.next_action.type is WorkflowActionType.GENERATE_VIDEOS
        and narration_delivery is None
    ):
        next_action = WorkflowNextAction(
            type=WorkflowActionType.CHOOSE_NARRATION_DELIVERY,
            args={"options": [POST_PRODUCTION, USE_TTS]},
            requested_ids=list(status.next_action.requested_ids),
            reason="choose narration delivery for this video request",
        )
        delivery_step.action = next_action
        video_step.state = WorkflowStepState.PENDING
        video_step.action = None
    elif admission is not None and admission.get("decision") != "admitted":
        next_action = _admission_action(admission, admission_problems, list(status.next_action.requested_ids))
        video_step.action = next_action
    else:
        next_action = status.next_action

    return WorkflowPlan(
        status=status,
        narration_delivery=WorkflowNarrationDelivery(selected=narration_delivery),
        steps=steps,
        blockers=list(status.blockers),
        problems=[*structure_problems, *admission_problems],
        next_action=next_action,
    )


__all__ = [
    "WorkflowNarrationDelivery",
    "WorkflowPlan",
    "WorkflowPlanRequest",
    "WorkflowPlanStep",
    "WorkflowStepContracts",
    "WorkflowStepState",
    "WorkflowTaskObservation",
    "build_workflow_plan",
]
