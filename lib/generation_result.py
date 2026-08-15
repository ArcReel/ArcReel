"""Shared per-ID selection and result contract for every generation entry point.

Web and Agent adapters consume this module unchanged; it owns three things that
must not be re-derived per entry point:

* **Selection** — an explicit ID set or missing-only. Missing-only reads the
  Artifact Manifest and selects only ``missing``. ``stale`` stays usable, is
  reused rather than regenerated, and never blocks export; ``blocked`` fails
  loud as its own reported gap instead of triggering paid regeneration.
* **Result identity** — ``requested = succeeded ∪ failed ∪ blocked`` with the
  three sets mutually exclusive. Reused units are reported separately as
  ``skipped`` and are deliberately outside ``requested``.
* **Per-item problems** — a stable code, the artifact/unit ID, an
  agent-readable detail and a closed next action. No consumer parses free text
  to decide whether to retry.

Three status axes are reported separately and never collapsed: the queue task
state, the provider submission checkpoint, and the artifact's current/stale
standing. A succeeded task does not imply the artifact matches the current
basis.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from lib.artifact_activation import ArtifactCurrencyResolver
from lib.artifact_manifest import ArtifactBlocker, ArtifactKey, ArtifactManifestError, ArtifactStatus
from lib.task_failure import parse_failure


class GenerationSelectionMode(StrEnum):
    """How the caller chose the target set for one generation request."""

    EXPLICIT = "explicit"
    MISSING_ONLY = "missing_only"


class GenerationItemState(StrEnum):
    """The exclusive per-ID outcome of one generation request."""

    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class GenerationTaskState(StrEnum):
    """Queue-side task state, reported independently of the artifact standing."""

    NOT_QUEUED = "not_queued"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class GenerationProblemCode(StrEnum):
    """Stable problem codes owned by this contract.

    Execution failures carry their own registered code from the persisted task
    failure envelope instead; these cover the gaps this contract itself
    detects.
    """

    UNIT_NOT_FOUND = "generation_unit_not_found"
    UNIT_ID_MISSING = "generation_unit_id_missing"
    UNIT_INPUT_UNUSABLE = "generation_unit_input_unusable"
    UNIT_REQUEST_INVALID = "generation_unit_request_invalid"
    ARTIFACT_STATE_UNAVAILABLE = "generation_artifact_state_unavailable"
    ENQUEUE_FAILED = "generation_enqueue_failed"
    TASK_FAILED = "generation_task_failed"
    TASK_CANCELLED = "generation_task_cancelled"
    POST_PROCESSING_FAILED = "generation_post_processing_failed"
    PROVIDER_UNAVAILABLE = "generation_provider_unavailable"


class GenerationAction(StrEnum):
    """Closed next actions a consumer can dispatch without reading prose."""

    RETRY = "retry"
    RESUME = "resume"
    FIX_INPUT = "fix_input"
    GENERATE_DEPENDENCY = "generate_dependency"
    GENERATE_TTS = "generate_tts"
    REGENERATE_TTS = "regenerate_tts"
    WAIT_FOR_TASK = "wait_for_task"
    REPLAN_UNIT = "replan_unit"
    CONFIRM_REQUEST_DURATION = "confirm_request_duration"
    CONFIGURE_PROVIDER = "configure_provider"
    REPAIR_ARTIFACT_STATE = "repair_artifact_state"
    NONE = "none"


# Registered task-failure codes that carry a sharper next action than a bare
# retry. Anything absent stays ``RETRY`` — a transient provider or queue error
# is the common case and retrying is always the safe suggestion.
_TASK_FAILURE_ACTIONS: dict[str, GenerationAction] = {
    "needs_replan": GenerationAction.REPLAN_UNIT,
    "tts_missing": GenerationAction.GENERATE_TTS,
    "tts_stale": GenerationAction.REGENERATE_TTS,
    "tts_generating": GenerationAction.WAIT_FOR_TASK,
    "tts_not_applicable": GenerationAction.FIX_INPUT,
    "tts_not_configured": GenerationAction.CONFIGURE_PROVIDER,
    "tts_state_unavailable": GenerationAction.REPAIR_ARTIFACT_STATE,
    "tts_duration_unavailable": GenerationAction.REGENERATE_TTS,
    "tts_conflicts_with_active_narrated_video": GenerationAction.WAIT_FOR_TASK,
    "reference_duration_confirmation_required": GenerationAction.CONFIRM_REQUEST_DURATION,
    "reference_asset_missing": GenerationAction.GENERATE_DEPENDENCY,
    "reference_declaration_invalid": GenerationAction.FIX_INPUT,
    "reference_capability_unavailable": GenerationAction.CONFIGURE_PROVIDER,
    "reference_capability_changed": GenerationAction.CONFIGURE_PROVIDER,
    "reference_supported_durations_missing": GenerationAction.CONFIGURE_PROVIDER,
    "reference_supported_durations_invalid": GenerationAction.CONFIGURE_PROVIDER,
    "reference_supported_durations_incompatible": GenerationAction.CONFIGURE_PROVIDER,
    "video_audio_switch_not_supported": GenerationAction.CONFIGURE_PROVIDER,
    "video_capability_missing_i2v": GenerationAction.CONFIGURE_PROVIDER,
    "video_capability_missing_r2v": GenerationAction.CONFIGURE_PROVIDER,
    "video_capability_missing_t2v": GenerationAction.CONFIGURE_PROVIDER,
    "video_capability_reference_unavailable": GenerationAction.CONFIGURE_PROVIDER,
    "image_capability_missing_i2i": GenerationAction.CONFIGURE_PROVIDER,
    "image_capability_missing_t2i": GenerationAction.CONFIGURE_PROVIDER,
    "image_endpoint_mismatch_no_i2i": GenerationAction.CONFIGURE_PROVIDER,
    "image_endpoint_mismatch_no_t2i": GenerationAction.CONFIGURE_PROVIDER,
    "provider_unsupported_media": GenerationAction.CONFIGURE_PROVIDER,
    "execution_identity_unrecoverable": GenerationAction.RETRY,
    "video_shorter_than_tts": GenerationAction.RETRY,
    "script_edit_error": GenerationAction.FIX_INPUT,
}


class GenerationProblem(BaseModel):
    """A machine-readable blocker: stable code, detail, and a closed action."""

    model_config = ConfigDict(extra="forbid")

    code: str
    detail: str
    action: GenerationAction
    params: dict[str, Any] = Field(default_factory=dict)


class ProviderCheckpoint(BaseModel):
    """Provider submission facts, reported apart from task and artifact state."""

    model_config = ConfigDict(extra="forbid")

    submitted: bool
    provider_id: str | None = None
    provider_job_id: str | None = None


class GenerationItemResult(BaseModel):
    """One requested ID's outcome across all three status axes."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    state: GenerationItemState
    artifact_key: str | None = None
    artifact_path: str | None = None
    task_id: str | None = None
    task_state: GenerationTaskState = GenerationTaskState.NOT_QUEUED
    artifact_status: ArtifactStatus | None = None
    provider_checkpoint: ProviderCheckpoint | None = None
    problem: GenerationProblem | None = None

    @model_validator(mode="after")
    def _problem_matches_state(self) -> Self:
        if self.state is GenerationItemState.SUCCEEDED:
            if self.problem is not None:
                raise ValueError("a succeeded item must not carry a problem")
        elif self.problem is None:
            raise ValueError(f"a {self.state.value} item must carry a problem")
        return self


class GenerationSkippedItem(BaseModel):
    """A unit left untouched because its artifact is still usable.

    Skipped units are deliberately outside ``requested``: reporting a reused
    ``stale`` artifact here is what keeps missing-only from regenerating it.
    """

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    artifact_key: str | None = None
    artifact_path: str | None = None
    artifact_status: ArtifactStatus | None = None


class GenerationBatchResult(BaseModel):
    """Shared response model serialized unchanged by REST and MCP adapters."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    operation: str
    selection: GenerationSelectionMode
    requested: list[str]
    succeeded: list[str]
    failed: list[str]
    blocked: list[str]
    skipped: list[GenerationSkippedItem] = Field(default_factory=list)
    items: list[GenerationItemResult] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sets_are_exhaustive_and_exclusive(self) -> Self:
        buckets = {
            GenerationItemState.SUCCEEDED: self.succeeded,
            GenerationItemState.FAILED: self.failed,
            GenerationItemState.BLOCKED: self.blocked,
        }
        for state, ids in buckets.items():
            if len(set(ids)) != len(ids):
                raise ValueError(f"duplicate ids in {state.value}")
        union: list[str] = [*self.succeeded, *self.failed, *self.blocked]
        if len(set(union)) != len(union):
            raise ValueError("succeeded / failed / blocked must be mutually exclusive")
        if sorted(union) != sorted(set(self.requested)) or len(set(self.requested)) != len(self.requested):
            raise ValueError("requested must equal succeeded ∪ failed ∪ blocked without duplicates")
        item_ids = [item.unit_id for item in self.items]
        if sorted(item_ids) != sorted(self.requested):
            raise ValueError("items must cover exactly the requested ids")
        for item in self.items:
            if item.unit_id not in buckets[item.state]:
                raise ValueError(f"item {item.unit_id} state does not match its bucket")
        skipped_ids = [entry.unit_id for entry in self.skipped]
        if len(set(skipped_ids)) != len(skipped_ids):
            raise ValueError("duplicate ids in skipped")
        if set(skipped_ids) & set(self.requested):
            raise ValueError("skipped ids must not appear in requested")
        return self

    @property
    def ok(self) -> bool:
        return not self.failed and not self.blocked


# --- selection -------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GenerationCandidate:
    """One addressable generation unit and the formal artifact it would write."""

    unit_id: str
    artifact_key: ArtifactKey | None = None
    artifact_path: str | None = None


@dataclass(frozen=True, slots=True)
class GenerationTargetState:
    """A candidate paired with its observed artifact standing."""

    candidate: GenerationCandidate
    status: ArtifactStatus | None = None
    blocker: ArtifactBlocker | None = None

    @property
    def unit_id(self) -> str:
        return self.candidate.unit_id

    @property
    def artifact_key(self) -> ArtifactKey | None:
        return self.candidate.artifact_key

    @property
    def artifact_path(self) -> str | None:
        return self.candidate.artifact_path


@dataclass(frozen=True, slots=True)
class GenerationSelection:
    """The resolved target set for one request."""

    mode: GenerationSelectionMode
    targets: tuple[GenerationTargetState, ...] = ()
    skipped: tuple[GenerationTargetState, ...] = ()
    unavailable: tuple[GenerationTargetState, ...] = ()
    unmatched_ids: tuple[str, ...] = ()

    @property
    def target_ids(self) -> tuple[str, ...]:
        return tuple(target.unit_id for target in self.targets)


def observe_artifact_status(
    *,
    resolver: ArtifactCurrencyResolver | None,
    key: ArtifactKey | None,
    artifact_path: object,
) -> tuple[ArtifactStatus | None, ArtifactBlocker | None]:
    """Report one artifact's standing without ever regenerating it.

    ``None`` means the project has no active Manifest, so the artifact axis is
    simply not observable — it is not a synonym for ``missing``.
    """

    if not isinstance(artifact_path, str) or not artifact_path:
        return ArtifactStatus.MISSING, None
    if resolver is None:
        return None, None
    if key is None:
        raise ValueError("an ArtifactKey is required for active currency")
    try:
        comparison = resolver.compare(key, artifact_path=artifact_path)
    except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return ArtifactStatus.BLOCKED, ArtifactBlocker(
            code="artifact_currency_unavailable",
            path=artifact_path,
            detail=str(exc),
        )
    return comparison.status, comparison.blocker


def artifact_is_reusable(state: GenerationTargetState, *, manifest_active: bool) -> bool:
    """Decide whether missing-only leaves this unit alone.

    ``stale`` counts as reusable: it stays viewable, exportable and explicitly
    selectable, and must never be silently re-paid for.
    """

    if not manifest_active:
        return bool(state.artifact_path)
    return state.status in {ArtifactStatus.CURRENT, ArtifactStatus.STALE}


def select_generation_targets(
    *,
    candidates: Sequence[GenerationCandidate],
    requested_ids: Sequence[str] | None,
    resolver: ArtifactCurrencyResolver | None,
    reusable_override: Callable[[GenerationCandidate], bool] | None = None,
) -> GenerationSelection:
    """Resolve one request's targets from an explicit ID set or from ``missing``.

    ``requested_ids is None`` means missing-only. An explicit empty sequence is
    an explicit empty selection and yields no targets — callers treat that as a
    caller error rather than as "everything".
    """

    states = [
        GenerationTargetState(candidate=candidate, status=status, blocker=blocker)
        for candidate in candidates
        for status, blocker in [
            observe_artifact_status(
                resolver=resolver,
                key=candidate.artifact_key,
                artifact_path=candidate.artifact_path,
            )
        ]
    ]

    if requested_ids is not None:
        wanted = list(dict.fromkeys(str(value) for value in requested_ids))
        by_id = {state.unit_id: state for state in states if state.unit_id}
        return GenerationSelection(
            mode=GenerationSelectionMode.EXPLICIT,
            targets=tuple(by_id[unit_id] for unit_id in wanted if unit_id in by_id),
            unmatched_ids=tuple(unit_id for unit_id in wanted if unit_id not in by_id),
        )

    manifest_active = resolver is not None
    targets: list[GenerationTargetState] = []
    skipped: list[GenerationTargetState] = []
    unavailable: list[GenerationTargetState] = []
    for state in states:
        if not state.unit_id:
            continue
        if state.status is ArtifactStatus.BLOCKED:
            unavailable.append(state)
            continue
        if reusable_override is not None and reusable_override(state.candidate):
            skipped.append(state)
            continue
        if artifact_is_reusable(state, manifest_active=manifest_active):
            skipped.append(state)
            continue
        targets.append(state)
    return GenerationSelection(
        mode=GenerationSelectionMode.MISSING_ONLY,
        targets=tuple(targets),
        skipped=tuple(skipped),
        unavailable=tuple(unavailable),
    )


# --- problems --------------------------------------------------------------


def problem_from_task_failure(error_message: str | None, *, cancelled: bool = False) -> GenerationProblem:
    """Lift a persisted task failure into the contract's problem shape.

    A registered structured reason keeps its own stable code and parameters;
    anything else (raw provider text, legacy rows) falls back to the contract's
    own code with the text preserved verbatim as detail.
    """

    if cancelled:
        return GenerationProblem(
            code=GenerationProblemCode.TASK_CANCELLED,
            detail=error_message or "task cancelled",
            action=GenerationAction.RETRY,
        )
    parsed = parse_failure(error_message)
    if parsed is None:
        return GenerationProblem(
            code=GenerationProblemCode.TASK_FAILED,
            detail=error_message or "task failed",
            action=GenerationAction.RETRY,
        )
    code, params = parsed
    return GenerationProblem(
        code=code,
        detail=error_message or code,
        action=_TASK_FAILURE_ACTIONS.get(code, GenerationAction.RETRY),
        params=params,
    )


def artifact_state_problem(state: GenerationTargetState) -> GenerationProblem:
    """Report an unreadable artifact claim as its own gap.

    A damaged sidecar must never be silently treated as ``missing``: that would
    turn corruption into a paid regeneration.
    """

    blocker = state.blocker
    return GenerationProblem(
        code=GenerationProblemCode.ARTIFACT_STATE_UNAVAILABLE,
        detail=blocker.detail if blocker is not None else "artifact state is unavailable",
        action=GenerationAction.REPAIR_ARTIFACT_STATE,
        params={"blocker_code": blocker.code} if blocker is not None else {},
    )


def provider_checkpoint_from_task(task: Mapping[str, Any] | None) -> ProviderCheckpoint | None:
    """Report whether a provider submission exists for this task, if known."""

    if task is None:
        return None
    provider_job_id = task.get("provider_job_id")
    provider_id = task.get("provider_id")
    submitted = bool(provider_job_id)
    if not submitted and provider_id is None and not task.get("execution_checkpoint_json"):
        return None
    return ProviderCheckpoint(
        submitted=submitted,
        provider_id=provider_id if isinstance(provider_id, str) else None,
        provider_job_id=provider_job_id if isinstance(provider_job_id, str) else None,
    )


# --- building --------------------------------------------------------------


class GenerationResultBuilder:
    """Accumulate per-ID outcomes and emit the validated batch contract."""

    def __init__(self, operation: str, selection: GenerationSelectionMode) -> None:
        self._operation = operation
        self._selection = selection
        self._items: list[GenerationItemResult] = []
        self._skipped: list[GenerationSkippedItem] = []
        self._seen: set[str] = set()

    @classmethod
    def from_selection(cls, operation: str, selection: GenerationSelection) -> Self:
        """Seed a builder with a selection's skipped, unmatched and blocked units."""

        builder = cls(operation, selection.mode)
        builder.absorb(selection)
        return builder

    def absorb(self, selection: GenerationSelection) -> None:
        """Fold one selection's non-target units in, so a tool that resolves
        several selections (one per asset type) reports them in one contract."""

        for state in selection.skipped:
            self.skip(state)
        for state in selection.unavailable:
            self.block(
                state.unit_id,
                problem=artifact_state_problem(state),
                artifact_key=state.artifact_key,
                artifact_path=state.artifact_path,
                artifact_status=state.status,
            )
        for unit_id in selection.unmatched_ids:
            self.block(
                unit_id,
                problem=GenerationProblem(
                    code=GenerationProblemCode.UNIT_NOT_FOUND,
                    detail=f"unit {unit_id} does not exist in the current project",
                    action=GenerationAction.FIX_INPUT,
                ),
            )

    @property
    def recorded_ids(self) -> frozenset[str]:
        return frozenset(self._seen)

    @property
    def has_failures(self) -> bool:
        """Whether any recorded unit ran and failed (blocked units never ran)."""

        return any(item.state is GenerationItemState.FAILED for item in self._items)

    def skip(self, state: GenerationTargetState) -> None:
        self.skip_unit(
            state.unit_id,
            artifact_key=state.artifact_key,
            artifact_path=state.artifact_path,
            artifact_status=state.status,
        )

    def skip_unit(
        self,
        unit_id: str,
        *,
        artifact_key: ArtifactKey | None = None,
        artifact_path: str | None = None,
        artifact_status: ArtifactStatus | None = None,
    ) -> None:
        """Record a unit that was left untouched because its artifact is reusable."""

        if unit_id in self._seen:
            raise ValueError(f"unit {unit_id} already recorded")
        self._seen.add(unit_id)
        self._skipped.append(
            GenerationSkippedItem(
                unit_id=unit_id,
                artifact_key=_encode_key(artifact_key),
                artifact_path=artifact_path,
                artifact_status=artifact_status,
            )
        )

    def succeed(
        self,
        unit_id: str,
        *,
        artifact_key: ArtifactKey | None = None,
        artifact_path: str | None = None,
        task_id: str | None = None,
        artifact_status: ArtifactStatus | None = None,
        provider_checkpoint: ProviderCheckpoint | None = None,
    ) -> None:
        self._record(
            GenerationItemResult(
                unit_id=unit_id,
                state=GenerationItemState.SUCCEEDED,
                artifact_key=_encode_key(artifact_key),
                artifact_path=artifact_path,
                task_id=task_id,
                task_state=GenerationTaskState.SUCCEEDED if task_id else GenerationTaskState.NOT_QUEUED,
                artifact_status=artifact_status,
                provider_checkpoint=provider_checkpoint,
            )
        )

    def fail(
        self,
        unit_id: str,
        *,
        problem: GenerationProblem,
        artifact_key: ArtifactKey | None = None,
        artifact_path: str | None = None,
        task_id: str | None = None,
        task_state: GenerationTaskState = GenerationTaskState.FAILED,
        artifact_status: ArtifactStatus | None = None,
        provider_checkpoint: ProviderCheckpoint | None = None,
    ) -> None:
        self._record(
            GenerationItemResult(
                unit_id=unit_id,
                state=GenerationItemState.FAILED,
                artifact_key=_encode_key(artifact_key),
                artifact_path=artifact_path,
                task_id=task_id,
                task_state=task_state,
                artifact_status=artifact_status,
                provider_checkpoint=provider_checkpoint,
                problem=problem,
            )
        )

    def block(
        self,
        unit_id: str,
        *,
        problem: GenerationProblem,
        artifact_key: ArtifactKey | None = None,
        artifact_path: str | None = None,
        artifact_status: ArtifactStatus | None = None,
    ) -> None:
        self._record(
            GenerationItemResult(
                unit_id=unit_id,
                state=GenerationItemState.BLOCKED,
                artifact_key=_encode_key(artifact_key),
                artifact_path=artifact_path,
                task_state=GenerationTaskState.NOT_QUEUED,
                artifact_status=artifact_status,
                problem=problem,
            )
        )

    def _record(self, item: GenerationItemResult) -> None:
        if item.unit_id in self._seen:
            raise ValueError(f"unit {item.unit_id} already recorded")
        self._seen.add(item.unit_id)
        self._items.append(item)

    def build(self) -> GenerationBatchResult:
        by_state: dict[GenerationItemState, list[str]] = {state: [] for state in GenerationItemState}
        for item in self._items:
            by_state[item.state].append(item.unit_id)
        return GenerationBatchResult(
            operation=self._operation,
            selection=self._selection,
            requested=[item.unit_id for item in self._items],
            succeeded=by_state[GenerationItemState.SUCCEEDED],
            failed=by_state[GenerationItemState.FAILED],
            blocked=by_state[GenerationItemState.BLOCKED],
            skipped=list(self._skipped),
            items=list(self._items),
        )


def _encode_key(key: ArtifactKey | None) -> str | None:
    return None if key is None else key.encode()


# --- rendering -------------------------------------------------------------

_STATE_MARKS: dict[GenerationItemState, str] = {
    GenerationItemState.SUCCEEDED: "✓",
    GenerationItemState.FAILED: "✗",
    GenerationItemState.BLOCKED: "⛔",
}


def render_generation_result(result: GenerationBatchResult, *, log: Iterable[str] = ()) -> str:
    """Render the same contract as the agent-facing text summary.

    The text is a projection of the structured payload, never a second source
    of truth: every fact printed here is present as a field.
    """

    header = (
        f"{result.operation} summary: {len(result.succeeded)} succeeded, "
        f"{len(result.failed)} failed, {len(result.blocked)} blocked"
    )
    if result.skipped:
        header += f", {len(result.skipped)} reused"
    lines = [header, *log]
    for item in result.items:
        mark = _STATE_MARKS[item.state]
        line = f"  {mark} {item.unit_id}"
        if item.state is GenerationItemState.SUCCEEDED:
            if item.artifact_path:
                line += f" → {item.artifact_path}"
            if item.artifact_status is ArtifactStatus.STALE:
                line += "（任务成功，但产物已不匹配当前依据）"
        else:
            problem = item.problem
            assert problem is not None
            line += f": [{problem.code}] {problem.detail} → next: {problem.action.value}"
            if item.provider_checkpoint is not None and item.provider_checkpoint.submitted:
                line += "（供应商已提交，可恢复）"
        lines.append(line)
    for entry in result.skipped:
        # 未激活 Manifest 时产物状态不可观测，此处就不假装知道它是 current 还是 stale。
        suffix = f"（{entry.artifact_status.value}）" if entry.artifact_status is not None else ""
        lines.append(f"  ↺ {entry.unit_id}: 复用现有产物{suffix}")
    return "\n".join(lines)


__all__ = [
    "GenerationAction",
    "GenerationBatchResult",
    "GenerationCandidate",
    "GenerationItemResult",
    "GenerationItemState",
    "GenerationProblem",
    "GenerationProblemCode",
    "GenerationResultBuilder",
    "GenerationSelection",
    "GenerationSelectionMode",
    "GenerationSkippedItem",
    "GenerationTargetState",
    "GenerationTaskState",
    "ProviderCheckpoint",
    "artifact_is_reusable",
    "artifact_state_problem",
    "observe_artifact_status",
    "problem_from_task_failure",
    "provider_checkpoint_from_task",
    "render_generation_result",
    "select_generation_targets",
]
