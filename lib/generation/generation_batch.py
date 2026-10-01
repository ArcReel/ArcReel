"""Durable generation batch request and read-model contracts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.generation.generation_result import (
    GenerationBatchResult,
    GenerationItemResult,
    GenerationItemState,
    GenerationProblem,
    GenerationSelectionMode,
    GenerationSkippedItem,
    GenerationTargetState,
    GenerationTaskState,
    dependency_failure_problem,
    enqueue_problem,
    generation_warnings_from_result,
    observe_artifact_status,
    problem_from_task_failure,
    provider_checkpoint_from_task,
)
from lib.generation.task_terminal_events import TERMINAL_TASK_STATUSES

GenerationBatchMemberStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "blocked"]


class GenerationBatchRequestedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_id: str = Field(min_length=1)
    artifact_key: str | None = None
    artifact_path: str | None = None
    artifact_status: ArtifactStatus | None = None
    prior_artifact_key: str | None = None
    prior_artifact_path: str | None = None
    prior_artifact_status: ArtifactStatus | None = None
    admission: dict[str, Any] = Field(default_factory=dict)
    #: 同批内的前置成员：它的任务成功后本成员才执行，失败则本成员不提交给供应商。
    depends_on: str | None = None


class GenerationBatchBlockedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item: GenerationItemResult
    admission: dict[str, Any]

    @model_validator(mode="after")
    def _item_is_blocked(self) -> GenerationBatchBlockedItem:
        if self.item.state is not GenerationItemState.BLOCKED:
            raise ValueError("blocked snapshot may only contain blocked items")
        return self


class GenerationBatchRequestSnapshot(BaseModel):
    """Admission-time facts needed to reconstruct the final shared result."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    selection: GenerationSelectionMode
    requested: list[GenerationBatchRequestedItem] = Field(default_factory=list)
    skipped: list[GenerationSkippedItem] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ids_are_unique(self) -> GenerationBatchRequestSnapshot:
        requested = [item.unit_id for item in self.requested]
        skipped = [item.unit_id for item in self.skipped]
        if len(set(requested)) != len(requested):
            raise ValueError("duplicate requested unit ids")
        if len(set(skipped)) != len(skipped):
            raise ValueError("duplicate skipped unit ids")
        if set(requested) & set(skipped):
            raise ValueError("skipped ids must not appear in requested")
        known = set(requested)
        for item in self.requested:
            if item.depends_on is not None and (item.depends_on not in known or item.depends_on == item.unit_id):
                raise ValueError(f"requested unit {item.unit_id} depends on a unit outside this batch")
        return self


class GenerationBatchMember(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_id: str
    task_id: str | None = None
    task_type: str | None = None
    status: GenerationBatchMemberStatus
    deduped: bool = False
    problem: GenerationProblem | None = None
    admission: dict[str, Any] = Field(default_factory=dict)


class GenerationBatchCounts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    queued: int = 0
    running: int = 0
    succeeded: int = 0
    failed: int = 0
    cancelled: int = 0
    blocked: int = 0
    total: int = 0


class GenerationBatchReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str
    project: str
    operation: str
    created_at: str
    members: list[GenerationBatchMember]
    skipped: list[GenerationSkippedItem] = Field(default_factory=list)
    counts: GenerationBatchCounts
    done: bool
    poll_after_seconds: int | None = None
    generation_result: GenerationBatchResult | None = None


class GenerationBatchCancelResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    cancelled: list[str] = Field(default_factory=list)
    #: 已开始执行、不可取消的成员：照常跑完，结果照常成为产物。
    skipped_running: list[str] = Field(default_factory=list)
    skipped_terminal: list[str] = Field(default_factory=list)


def validate_blocked_items(
    snapshot: GenerationBatchRequestSnapshot,
    blocked: list[GenerationBatchBlockedItem],
) -> None:
    requested = {item.unit_id for item in snapshot.requested}
    blocked_ids = [entry.item.unit_id for entry in blocked]
    if len(set(blocked_ids)) != len(blocked_ids) or not set(blocked_ids) <= requested:
        raise ValueError("blocked snapshot must uniquely identify requested units")


def build_generation_batch_admission(
    *,
    preflight: GenerationBatchResult,
    pending_ids: Sequence[str],
    states: Mapping[str, GenerationTargetState] | None = None,
    admission: Mapping[str, dict[str, Any]] | None = None,
    dependencies: Mapping[str, str] | None = None,
) -> tuple[GenerationBatchRequestSnapshot, list[GenerationBatchBlockedItem]]:
    """Project a tool's completed selection/preflight into the durable batch snapshot.

    ``dependencies`` maps a pending unit to the pending unit it must wait for in
    this same batch; the queue runs it only after that unit's task succeeded.
    """

    if preflight.succeeded or preflight.failed:
        raise ValueError("generation batch admission cannot contain executed outcomes")
    state_by_id = states or {}
    admission_by_id = admission or {}
    blocked_by_id = {item.unit_id: item for item in preflight.items}
    requested_ids = list(dict.fromkeys([*pending_ids, *preflight.blocked]))
    requested = []
    for unit_id in requested_ids:
        state = state_by_id.get(unit_id)
        item = blocked_by_id.get(unit_id)
        requested.append(
            GenerationBatchRequestedItem(
                unit_id=unit_id,
                artifact_key=(
                    state.artifact_key.encode() if state and state.artifact_key else item.artifact_key if item else None
                ),
                artifact_path=state.artifact_path if state else item.artifact_path if item else None,
                artifact_status=state.status if state else item.artifact_status if item else None,
                prior_artifact_key=(state.prior_artifact_key.encode() if state and state.prior_artifact_key else None),
                prior_artifact_path=state.prior_artifact_path if state else None,
                prior_artifact_status=state.prior_artifact_status if state else None,
                admission=admission_by_id.get(unit_id, {}),
                depends_on=(dependencies or {}).get(unit_id),
            )
        )
    blocked = [
        GenerationBatchBlockedItem(item=blocked_by_id[unit_id], admission=admission_by_id.get(unit_id, {}))
        for unit_id in preflight.blocked
    ]
    return (
        GenerationBatchRequestSnapshot(
            selection=preflight.selection,
            requested=requested,
            skipped=preflight.skipped,
        ),
        blocked,
    )


def _failure_artifact_report(
    requested: GenerationBatchRequestedItem,
    *,
    fallback_path: str | None,
    resolver: ArtifactCurrencyResolver | None,
) -> tuple[str | None, str | None, ArtifactStatus | None]:
    """Report the artifact that survives this failure, if the request recorded one."""

    has_prior_artifact = (
        requested.prior_artifact_key is not None
        or requested.prior_artifact_path is not None
        or requested.prior_artifact_status is not None
    )
    if not has_prior_artifact:
        return requested.artifact_key, fallback_path, requested.artifact_status

    artifact_key = requested.prior_artifact_key
    artifact_path = requested.prior_artifact_path
    artifact_status = requested.prior_artifact_status
    if resolver is not None and artifact_key is not None:
        artifact_status, _blocker = observe_artifact_status(
            resolver=resolver,
            key=ArtifactKey.decode(artifact_key),
            artifact_path=artifact_path,
        )
    return artifact_key, artifact_path, artifact_status


def _terminal_result(
    operation: str,
    snapshot: GenerationBatchRequestSnapshot,
    tasks: dict[str, dict[str, Any]],
    blocked: dict[str, GenerationItemResult],
    resolver: ArtifactCurrencyResolver | None,
) -> GenerationBatchResult:
    items: list[GenerationItemResult] = []
    for requested in snapshot.requested:
        unit_id = requested.unit_id
        if unit_id in blocked:
            items.append(blocked[unit_id])
            continue
        task = tasks.get(unit_id)
        if task is None:
            artifact_key, artifact_path, artifact_status = _failure_artifact_report(
                requested,
                fallback_path=requested.artifact_path,
                resolver=resolver,
            )
            items.append(
                GenerationItemResult(
                    unit_id=unit_id,
                    artifact_key=artifact_key,
                    artifact_path=artifact_path,
                    artifact_status=artifact_status,
                    state=GenerationItemState.FAILED,
                    task_state=GenerationTaskState.NOT_QUEUED,
                    problem=dependency_failure_problem(
                        enqueue_problem(None),
                        requested.depends_on,
                        dependency_not_queued=requested.depends_on not in tasks,
                    ),
                )
            )
            continue
        status = task["status"]
        task_result = task.get("result") or {}
        unit_result = (task_result.get("unit_results") or {}).get(unit_id) or {}
        task_artifact_path = unit_result.get("file_path") or task_result.get("file_path") or requested.artifact_path
        if status == "succeeded" and not unit_result.get("problem"):
            artifact_status = None
            if resolver is not None and requested.artifact_key is not None:
                artifact_status, _blocker = observe_artifact_status(
                    resolver=resolver,
                    key=ArtifactKey.decode(requested.artifact_key),
                    artifact_path=task_artifact_path,
                )
            items.append(
                GenerationItemResult(
                    unit_id=unit_id,
                    artifact_key=requested.artifact_key,
                    artifact_path=task_artifact_path,
                    task_id=task["task_id"],
                    provider_checkpoint=provider_checkpoint_from_task(task),
                    state=GenerationItemState.SUCCEEDED,
                    task_state=GenerationTaskState.SUCCEEDED,
                    artifact_status=artifact_status,
                    warnings=generation_warnings_from_result(task_result),
                )
            )
        else:
            artifact_key, artifact_path, artifact_status = _failure_artifact_report(
                requested,
                fallback_path=task_artifact_path,
                resolver=resolver,
            )
            items.append(
                GenerationItemResult(
                    unit_id=unit_id,
                    artifact_key=artifact_key,
                    artifact_path=artifact_path,
                    task_id=task["task_id"],
                    provider_checkpoint=provider_checkpoint_from_task(task),
                    state=GenerationItemState.FAILED,
                    task_state=(
                        GenerationTaskState.SUCCEEDED
                        if status == "succeeded"
                        else GenerationTaskState.CANCELLED
                        if status == "cancelled"
                        else GenerationTaskState.FAILED
                    ),
                    artifact_status=artifact_status,
                    problem=(
                        GenerationProblem.model_validate(unit_result["problem"])
                        if unit_result.get("problem")
                        else dependency_failure_problem(
                            problem_from_task_failure(task.get("error_message"), cancelled=status == "cancelled"),
                            requested.depends_on,
                        )
                    ),
                    warnings=generation_warnings_from_result(task_result),
                )
            )
    succeeded = [item.unit_id for item in items if item.state is GenerationItemState.SUCCEEDED]
    failed = [item.unit_id for item in items if item.state is GenerationItemState.FAILED]
    blocked_ids = [item.unit_id for item in items if item.state is GenerationItemState.BLOCKED]
    return GenerationBatchResult(
        operation=operation,
        selection=snapshot.selection,
        requested=[item.unit_id for item in items],
        succeeded=succeeded,
        failed=failed,
        blocked=blocked_ids,
        skipped=snapshot.skipped,
        items=items,
    )


def _poll_after_seconds(tasks: list[dict[str, Any]], queue_depth: dict[str, int]) -> int:
    active = [task for task in tasks if task["status"] not in TERMINAL_TASK_STATUSES]
    bases = {"video": 10, "reference_video": 10, "grid": 8, "tts": 4}
    base = max((bases.get(str(task["task_type"]), 3) for task in active), default=3)
    depth = max((queue_depth.get(str(task["task_type"]), 0) for task in active), default=0)
    # ponytail: coarse queue heuristic; replace with measured completion percentiles if polling load becomes material.
    return min(30, base + depth // 5)


def build_generation_batch_read_model(
    batch: dict[str, Any],
    memberships: list[dict[str, Any]],
    queue_depth: dict[str, int],
    resolver: ArtifactCurrencyResolver | None = None,
) -> GenerationBatchReadModel:
    snapshot = GenerationBatchRequestSnapshot.model_validate(batch["requested"])
    blocked_entries = [GenerationBatchBlockedItem.model_validate(item) for item in batch["blocked"]]
    validate_blocked_items(snapshot, blocked_entries)
    blocked_snapshots = {entry.item.unit_id: entry for entry in blocked_entries}
    blocked = {unit_id: entry.item for unit_id, entry in blocked_snapshots.items()}
    tasks = {str(item["unit_id"]): item for item in memberships}

    members: list[GenerationBatchMember] = []
    for requested in snapshot.requested:
        unit_id = requested.unit_id
        if entry := blocked_snapshots.get(unit_id):
            members.append(
                GenerationBatchMember(
                    unit_id=unit_id,
                    status="blocked",
                    problem=entry.item.problem,
                    admission=entry.admission,
                )
            )
            continue
        task = tasks.get(unit_id)
        if task is None:
            members.append(
                GenerationBatchMember(
                    unit_id=unit_id,
                    status="failed",
                    problem=dependency_failure_problem(
                        enqueue_problem(None),
                        requested.depends_on,
                        dependency_not_queued=requested.depends_on not in tasks,
                    ),
                    admission=requested.admission,
                )
            )
            continue
        members.append(
            GenerationBatchMember(
                unit_id=unit_id,
                task_id=task["task_id"],
                task_type=task["task_type"],
                status=task["status"],
                deduped=bool(task["deduped"]),
                admission=requested.admission,
            )
        )

    counted = Counter(member.status for member in members)
    counts = GenerationBatchCounts(
        **{status: counted[status] for status in GenerationBatchMemberStatus.__args__},
        total=len(members),
    )
    done = all(member.status in TERMINAL_TASK_STATUSES or member.status == "blocked" for member in members)
    terminal = _terminal_result(batch["operation"], snapshot, tasks, blocked, resolver) if done else None
    return GenerationBatchReadModel(
        batch_id=batch["batch_id"],
        project=batch["project_name"],
        operation=batch["operation"],
        created_at=batch["created_at"],
        members=members,
        skipped=snapshot.skipped,
        counts=counts,
        done=done,
        poll_after_seconds=None if done else _poll_after_seconds(memberships, queue_depth),
        generation_result=terminal,
    )


__all__ = [
    "GenerationBatchBlockedItem",
    "GenerationBatchCancelResult",
    "GenerationBatchReadModel",
    "GenerationBatchRequestSnapshot",
    "GenerationBatchRequestedItem",
    "build_generation_batch_admission",
    "build_generation_batch_read_model",
    "validate_blocked_items",
]
