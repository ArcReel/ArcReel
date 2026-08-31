"""Durable generation batch request and read-model contracts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.artifact_activation import ArtifactCurrencyResolver
from lib.artifact_manifest import ArtifactKey, ArtifactStatus
from lib.generation_result import (
    GenerationBatchResult,
    GenerationItemResult,
    GenerationItemState,
    GenerationProblem,
    GenerationSelectionMode,
    GenerationSkippedItem,
    GenerationTargetState,
    GenerationTaskState,
    enqueue_problem,
    observe_artifact_status,
    problem_from_task_failure,
    provider_checkpoint_from_task,
)
from lib.task_terminal_events import TERMINAL_TASK_STATUSES

GenerationBatchMemberStatus = Literal["queued", "running", "cancelling", "succeeded", "failed", "cancelled", "blocked"]


class GenerationBatchRequestedItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    unit_id: str = Field(min_length=1)
    artifact_key: str | None = None
    artifact_path: str | None = None
    artifact_status: ArtifactStatus | None = None
    admission: dict[str, Any] = Field(default_factory=dict)


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
    cancelling: int = 0
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
    cancelling: list[str] = Field(default_factory=list)
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
) -> tuple[GenerationBatchRequestSnapshot, list[GenerationBatchBlockedItem]]:
    """Project a tool's completed selection/preflight into the durable batch snapshot."""

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
                admission=admission_by_id.get(unit_id, {}),
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
            items.append(
                GenerationItemResult(
                    unit_id=unit_id,
                    artifact_key=requested.artifact_key,
                    artifact_path=requested.artifact_path,
                    artifact_status=requested.artifact_status,
                    state=GenerationItemState.FAILED,
                    task_state=GenerationTaskState.NOT_QUEUED,
                    problem=enqueue_problem(None),
                )
            )
            continue
        status = task["status"]
        task_result = task.get("result") or {}
        unit_result = (task_result.get("unit_results") or {}).get(unit_id) or {}
        common = {
            "unit_id": unit_id,
            "artifact_key": requested.artifact_key,
            "artifact_path": unit_result.get("file_path") or task_result.get("file_path") or requested.artifact_path,
            "task_id": task["task_id"],
            "provider_checkpoint": provider_checkpoint_from_task(task),
        }
        if status == "succeeded" and not unit_result.get("problem"):
            artifact_status = None
            if resolver is not None and requested.artifact_key is not None:
                artifact_status, _blocker = observe_artifact_status(
                    resolver=resolver,
                    key=ArtifactKey.decode(requested.artifact_key),
                    artifact_path=common["artifact_path"],
                )
            items.append(
                GenerationItemResult(
                    **common,
                    state=GenerationItemState.SUCCEEDED,
                    task_state=GenerationTaskState.SUCCEEDED,
                    artifact_status=artifact_status,
                )
            )
        else:
            items.append(
                GenerationItemResult(
                    **common,
                    state=GenerationItemState.FAILED,
                    task_state=(
                        GenerationTaskState.SUCCEEDED
                        if status == "succeeded"
                        else GenerationTaskState.CANCELLED
                        if status == "cancelled"
                        else GenerationTaskState.FAILED
                    ),
                    artifact_status=requested.artifact_status,
                    problem=(
                        GenerationProblem.model_validate(unit_result["problem"])
                        if unit_result.get("problem")
                        else problem_from_task_failure(task.get("error_message"), cancelled=status == "cancelled")
                    ),
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
                    problem=enqueue_problem(None),
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
