"""Side-effect-free projection of authoritative workflow facts into an executable plan."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lib.generation.generation_result import GenerationAction, GenerationProblem
from lib.project.asset_types import ASSET_SPECS
from lib.script.draft_quarantine import QUARANTINE_KIND_PROMPT_AUTHORING
from lib.workflow.workflow_rules import WorkflowStepRule, workflow_rule
from lib.workflow.workflow_state import (
    INVALID_EDIT_TIMELINES_CODE,
    WorkflowActionType,
    WorkflowBlocker,
    WorkflowNextAction,
    WorkflowStatus,
    workflow_finished,
)

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

    episode_id: int | None = Field(
        default=None,
        ge=1,
        strict=True,
        description="Episode ID to plan (project details episodes[].episode); when omitted, select the current episode in broadcast order",
    )
    confirmed_request_durations: dict[str, PositiveStrictInt] = Field(
        default_factory=dict,
        description="Video-request durations confirmed with the user, { unit ID: seconds }; apply only to video admission in this plan",
    )

    @field_validator("confirmed_request_durations")
    @classmethod
    def _valid_confirmed_durations(cls, value: dict[str, int]) -> dict[str, int]:
        for unit_id in value:
            if not unit_id.strip():
                raise ValueError("confirmed_request_durations keys must be non-empty unit ids")
        return value


class WorkflowProblem(BaseModel):
    """One plan notice: stable problem code, owning unit, and localizable parameters.

    Send only structure UI/Agents can state: ``code`` is a localization machine key; ``params`` contains text-interpolation parameters.
    Exclude raw reason text (``GenerationProblem.detail``) and complete speech-admission payloads. Agents
    needing field-level locations to edit scripts read full problems in ``patch_episode_script`` action
    ``args.problems``."""

    model_config = ConfigDict(extra="forbid")

    code: str
    action: GenerationAction
    unit_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)


#: Params keys excluded from copy parameters: unit IDs are extracted into ``unit_id``; speech-admission payloads provide raw field-level location structure.
_UNLOCALIZABLE_PARAMS = frozenset({"unit_id", "speech_admission"})


def _problem_unit_id(problem: GenerationProblem) -> str | None:
    unit_id = problem.params.get("unit_id")
    if not isinstance(unit_id, str):
        admission = problem.params.get("speech_admission")
        unit_id = admission.get("unit_id") if isinstance(admission, dict) else None
    return unit_id if isinstance(unit_id, str) and unit_id else None


def workflow_problem(problem: GenerationProblem) -> WorkflowProblem:
    """Project generation-contract problems into plan notices: extract owning units, remove raw reasons/location payloads."""

    return WorkflowProblem(
        code=problem.code,
        action=problem.action,
        unit_id=_problem_unit_id(problem),
        params={key: value for key, value in problem.params.items() if key not in _UNLOCALIZABLE_PARAMS},
    )


class WorkflowProviderCheckpoint(BaseModel):
    """Whether the provider accepted submission; state only this fact, excluding provider IDs/job numbers as internal handles."""

    model_config = ConfigDict(extra="forbid")

    submitted: bool


class WorkflowTaskObservation(BaseModel):
    """One task axis, kept separate from provider submission and artifact currency.

    Plans observe only queued/running tasks, which are progress rather than problems; carry no problems here."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    task_id: str
    batch_id: str | None = None
    task_type: str
    status: str
    provider_checkpoint: WorkflowProviderCheckpoint | None = None


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
    problems: list[WorkflowProblem] = Field(default_factory=list)
    tasks: list[WorkflowTaskObservation] = Field(default_factory=list)
    admission: dict[str, Any] | None = None
    contracts: WorkflowStepContracts = Field(default_factory=WorkflowStepContracts)


class WorkflowPlan(BaseModel):
    """Versioned plan shared unchanged by REST and MCP adapters."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[2] = 2
    status: WorkflowStatus
    steps: list[WorkflowPlanStep]
    blockers: list[WorkflowBlocker]
    problems: list[WorkflowProblem]
    """Project-level notices belonging to no step (e.g. data-upgrade failure); step-specific notices appear only once under ``steps[].problems``."""
    next_action: WorkflowNextAction
    next_alternatives: list[WorkflowNextAction] = Field(default_factory=list)


_ARTIFACT_BY_STEP: dict[str, str] = {
    "asset_sheets": "asset_sheets",
    "script_plan_content": "script_plan",
    "script_plan_review": "script_plan",
    "final_script": "script",
    "storyboard": "storyboards",
    "video": "videos",
    "edit": "edit_timelines",
}

#: Draft AI-repair task types; resource IDs in :func:`draft_repair_resource_id`.
TEXT_DRAFT_REPAIR_TASK_TYPE = "text_draft_repair"
_PROMPT_AUTHORING_DRAFT_DOC_TYPE = "reference_prompt_authoring"
#: Two episode-planning occupancy slots. Agent single-batch planning and Web chained windows' first window use the first;
#: running windows queue the next in the other slot before model requests, alternating. Stopping cancels the queued window.
EPISODE_PLANNING_SLOT = "episode-planning"
EPISODE_PLANNING_NEXT_SLOT = "episode-planning-next"
EPISODE_PLANNING_SLOTS = (EPISODE_PLANNING_SLOT, EPISODE_PLANNING_NEXT_SLOT)


def draft_repair_resource_id(episode: int, doc_type: str) -> str:
    """AI draft-repair occupancy slot: one per draft; script-plan and prompt-authoring drafts in the same episode occupy separate slots."""
    return f"episode-{episode}-{doc_type}"


_TASK_STEP: dict[str, str] = {
    "text_episode_plan": "episode_plan",
    "text_drama_script_plan": "script_plan_content",
    "text_narration_script_plan": "script_plan_content",
    "text_reference_script_plan": "script_plan_content",
    "text_episode_script": "final_script",
    **dict.fromkeys(ASSET_SPECS, "asset_sheets"),
    "storyboard": "storyboard",
    "grid": "storyboard",
    "video": "video",
    "reference_video": "video",
}


def _task_step(observation: WorkflowTaskObservation) -> str | None:
    """Task-owning step: draft AI repair belongs to script planning or formal script for prompt-authoring drafts; other tasks map by type."""
    if observation.task_type == TEXT_DRAFT_REPAIR_TASK_TYPE:
        prompt_authoring = observation.unit_id.endswith(f"-{_PROMPT_AUTHORING_DRAFT_DOC_TYPE}")
        return "final_script" if prompt_authoring else "script_plan_content"
    return _TASK_STEP.get(observation.task_type)


#: Suggested-next-step ownership: step order is presentation only; attach actions to their content-owning steps.
_ACTION_STEP: dict[WorkflowActionType, str] = {
    WorkflowActionType.COLLECT_PROJECT_INPUT: "project_input",
    WorkflowActionType.RETRY_PROJECT_MIGRATION: "project_input",
    WorkflowActionType.DRAFT_SELLING_POINTS: "selling_points",
    WorkflowActionType.CREATE_EPISODE: "episode_plan",
    WorkflowActionType.PLAN_EPISODES: "episode_plan",
    WorkflowActionType.RESET_EPISODE_PLANNING: "episode_plan",
    WorkflowActionType.PREPARE_SCRIPT_PLAN: "script_plan_content",
    WorkflowActionType.START_BLANK_SCRIPT: "script_plan_content",
    WorkflowActionType.PROVIDE_EPISODE_SOURCE: "script_plan_content",
    WorkflowActionType.CONFIRM_SCRIPT_PLAN: "script_plan_review",
    WorkflowActionType.GENERATE_SCRIPT: "final_script",
    WorkflowActionType.ADD_SCRIPT_ITEMS: "final_script",
    WorkflowActionType.AUTHOR_PROMPTS: "final_script",
    WorkflowActionType.GENERATE_ASSET_SHEETS: "asset_sheets",
    WorkflowActionType.REPAIR_VIDEO_UNITS: "script_structure",
    WorkflowActionType.GENERATE_STORYBOARDS: "storyboard",
    WorkflowActionType.GENERATE_GRID: "storyboard",
    WorkflowActionType.GENERATE_VIDEOS: "video",
    WorkflowActionType.CREATE_EDIT_TIMELINE: "edit",
}


def _owner_step(status: WorkflowStatus) -> str:
    """Step ID owning the suggested next action; without an action, use ownership of the content whose problems are stated."""
    action = status.next_action
    if action.type is WorkflowActionType.RESOLVE_DRAFT:
        return (
            "final_script"
            if action.args.get("draft_kind") == QUARANTINE_KIND_PROMPT_AUTHORING
            else "script_plan_review"
        )
    if action.type is not WorkflowActionType.NONE:
        return _ACTION_STEP.get(action.type, "project_input")
    if workflow_finished(status):
        return "edit"
    if status.blockers or status.target is None or status.content is None:
        return "project_input"
    if status.content.episode_plan_stale or status.artifacts.get("script_plan", {}).get("state") == "blocked":
        return "script_plan_content"
    if status.artifacts.get("script", {}).get("state") == "blocked":
        return "final_script"
    if status.artifacts.get("storyboards", {}).get("state") == "blocked":
        return "storyboard"
    if status.artifacts.get("videos", {}).get("state") == "blocked":
        return "video"
    if any(issue.code == INVALID_EDIT_TIMELINES_CODE for issue in status.issues):
        return "edit"
    return "video"


def _step_done(step_id: str, status: WorkflowStatus) -> bool:
    """Whether this step's own content is complete, independently of prior steps."""
    content = status.content
    if content is None:
        return False
    artifacts = status.artifacts
    formal = content.formal_script == "present"
    has_items = formal and bool(content.script_item_count)
    if step_id == "project_input":
        if status.project.content_mode == "ad":
            return content.ad_inputs == "present"
        return content.whole_source == "present" or content.episode_count > 0
    if step_id == "selling_points":
        return not content.products_without_selling_points
    if step_id == "episode_plan":
        return content.episode_count > 0 and not content.source_remaining
    if step_id == "script_plan_content":
        return formal or artifacts.get("script_plan", {}).get("state") in {"current", "stale"}
    if step_id == "script_plan_review":
        return formal or status.gates.get("script_plan_review", {}).get("state") == "confirmed"
    if step_id == "final_script":
        return has_items and not content.pending_authoring_ids
    if step_id == "asset_sheets":
        return has_items and not content.referenced_assets_without_sheet
    if step_id == "script_structure":
        return has_items and not content.needs_replan_ids
    if step_id == "storyboard":
        return has_items and not artifacts.get("storyboards", {}).get("missing_ids")
    if step_id == "video":
        return has_items and not artifacts.get("videos", {}).get("missing_ids")
    if step_id == "edit":
        return has_items and bool(artifacts.get("edit_timelines", {}).get("timeline_ids"))
    return False


def _baseline_step_state(rule: WorkflowStepRule, *, owner: str, status: WorkflowStatus) -> WorkflowStepState:
    if not rule.applicable:
        return WorkflowStepState.SKIPPED
    if rule.id == owner:
        if workflow_finished(status):
            return WorkflowStepState.COMPLETED
        if status.blockers or status.next_action.type is WorkflowActionType.NONE:
            return WorkflowStepState.BLOCKED
        return WorkflowStepState.READY
    if _step_done(rule.id, status):
        return WorkflowStepState.COMPLETED
    return WorkflowStepState.PENDING


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
        problems.extend(GenerationProblem.model_validate(raw) for raw in raw_problems)
    return problems


def _problem_unit_ids(problems: list[GenerationProblem]) -> list[str]:
    ids: list[str] = []
    for problem in problems:
        unit_id = _problem_unit_id(problem)
        if unit_id is not None and unit_id not in ids:
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
            "base_revision": script_revision,
            "problems": [problem.model_dump(mode="json") for problem in problems],
        },
        requested_ids=_problem_unit_ids(problems),
        reason=problems[0].code,
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
        reason=problems[0].code if problems else "video batch requires confirmation",
    )


def build_workflow_plan(
    status: WorkflowStatus,
    *,
    project_problems: list[GenerationProblem] | None = None,
    structure_problems: list[GenerationProblem] | None = None,
    script_revision: str | None = None,
    task_observations: list[WorkflowTaskObservation] | None = None,
    admission: dict[str, Any] | None = None,
) -> WorkflowPlan:
    """Project one immutable status snapshot and transient request observations.

    Send each notice once: ``project_problems`` (project-level, e.g. data-upgrade failure) in top-level
    ``problems``, ``structure_problems`` in script structure, and batch-video-admission per-unit problems
    only in the video step's ``admission``."""

    plan_problems = [workflow_problem(problem) for problem in project_problems or []]

    try:
        rules = workflow_rule(status.project.content_mode, status.project.generation_mode).steps
    except ValueError:
        if not status.blockers:
            raise
        return WorkflowPlan(
            status=status,
            steps=[
                WorkflowPlanStep(
                    id="project_input", state=WorkflowStepState.BLOCKED, required=True, action=status.next_action
                )
            ],
            blockers=list(status.blockers),
            problems=plan_problems,
            next_action=status.next_action,
        )
    owner = _owner_step(status)
    complete = workflow_finished(status)
    structure_problems = list(structure_problems or [])
    task_observations = list(task_observations or [])
    admission_problems = _admission_problems(admission)
    steps: list[WorkflowPlanStep] = []

    for step_rule in rules:
        artifact_key = _ARTIFACT_BY_STEP.get(step_rule.id)
        # Finished workflows have no next action to attach; completed editing carries no action.
        owns = step_rule.id == owner and not complete
        step = WorkflowPlanStep(
            id=step_rule.id,
            state=_baseline_step_state(step_rule, owner=owner, status=status),
            required=step_rule.applicable,
            action=status.next_action if owns else None,
            requested_ids=list(status.next_action.requested_ids) if owns else [],
            artifacts=dict(status.artifacts.get(artifact_key, {})) if artifact_key is not None else {},
            contracts=(
                WorkflowStepContracts(script_edit="script_batch_edit/v1")
                if step_rule.id == "script_structure"
                else WorkflowStepContracts(batch_admission="video_batch_admission/v1")
                if step_rule.id == "video"
                else WorkflowStepContracts()
            ),
        )
        if step.artifacts.get("state") == "blocked" and step.state is not WorkflowStepState.SKIPPED:
            step.state = WorkflowStepState.BLOCKED
        steps.append(step)

    by_id = {step.id: step for step in steps}
    structure_step = by_id["script_structure"]
    if structure_problems:
        structure_step.state = WorkflowStepState.BLOCKED
        structure_step.problems = [workflow_problem(problem) for problem in structure_problems]
        structure_step.requested_ids = _problem_unit_ids(structure_problems)
        structure_step.action = _structure_action(structure_problems, script_revision=script_revision)
        for media_step in ("storyboard", "video"):
            if by_id[media_step].state is not WorkflowStepState.SKIPPED:
                by_id[media_step].state = WorkflowStepState.PENDING
                by_id[media_step].action = None

    for observation in task_observations:
        step_id = _task_step(observation)
        if step_id is None:
            continue
        step = by_id[step_id]
        step.tasks.append(observation)
        if observation.status in {"queued", "running"}:
            step.state = WorkflowStepState.ACTIVE

    video_step = by_id["video"]
    video_step.admission = admission
    if admission is not None and admission.get("decision") != "admitted" and not video_step.tasks:
        video_step.state = WorkflowStepState.BLOCKED

    active_tasks = [task for task in task_observations if task.status in {"queued", "running"}]
    if status.blockers:
        next_action = status.next_action
    elif structure_problems:
        next_action = _structure_action(structure_problems, script_revision=script_revision)
    elif active_tasks:
        # ponytail: fixed five-minute remote observation window; replace with per-task ETA only if providers expose it.
        batch_ids = list(dict.fromkeys(task.batch_id for task in active_tasks if task.batch_id is not None))
        next_action = WorkflowNextAction(
            type=WorkflowActionType.WAIT_FOR_TASK,
            args={
                "task_ids": [task.task_id for task in active_tasks],
                **({"batch_ids": batch_ids} if batch_ids else {}),
                "poll_after_seconds": 10,
                "max_poll_attempts": 30,
            },
            requested_ids=[task.unit_id for task in active_tasks],
            reason="workflow has active generation tasks",
        )
        for step in steps:
            if step.state is WorkflowStepState.ACTIVE:
                step.action = next_action
        if video_step.state is not WorkflowStepState.ACTIVE:
            video_step.action = None
    elif admission is not None and admission.get("decision") != "admitted":
        next_action = _admission_action(admission, admission_problems, list(status.next_action.requested_ids))
        video_step.action = next_action
    else:
        next_action = status.next_action

    return WorkflowPlan(
        status=status,
        steps=steps,
        blockers=list(status.blockers),
        problems=plan_problems,
        next_action=next_action,
        next_alternatives=list(status.next_alternatives) if next_action is status.next_action else [],
    )


__all__ = [
    "WorkflowPlan",
    "WorkflowPlanRequest",
    "WorkflowPlanStep",
    "WorkflowProblem",
    "WorkflowProviderCheckpoint",
    "WorkflowStepContracts",
    "WorkflowStepState",
    "WorkflowTaskObservation",
    "build_workflow_plan",
    "workflow_problem",
]
