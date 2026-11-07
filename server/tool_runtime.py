"""Host-independent ArcReel tool handlers and their typed call contract."""

from __future__ import annotations

import asyncio
import contextlib
import copy
import functools
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic.json_schema import SkipJsonSchema
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.agent.profile_manifest import ContentMode
from lib.artifacts.artifact_activation import ArtifactCurrencyResolver, active_artifact_currency_resolver
from lib.backends.text_backends.base import TextOutputTruncatedError
from lib.catalog.errors import ModelNotInCatalogError
from lib.config.resolver import ConfigResolver, caps_generation_mode, video_bucket_for_generation_mode
from lib.db import async_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.task_repo import TaskNotCancellableError
from lib.episode.episode_ids import describe_episode_for_agent, episode_position
from lib.episode.episode_ledger import is_derived_episode_name, normalize_source_text
from lib.episode.episode_paths import (
    DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    SCRIPT_PLAN_LEGACY_FILENAMES,
    episode_source_path,
    episode_source_relpath,
)
from lib.episode.episode_planner import (
    CandidatePlanResult,
    EpisodePlanner,
    LedgerStats,
    NoCutPointError,
    PlanResult,
)
from lib.episode.episode_replan import (
    ReplanError,
    create_replan_candidate,
    discard_replan_candidate,
    record_replan_interruption,
    replan_candidate,
    replan_scope,
    resume_replan_candidate,
)
from lib.episode.episode_reset import (
    EpisodeResetError,
    ResetConfirmationRequired,
)
from lib.episode.episode_reset import (
    reset_episode_planning as reset_episode_planning_service,
)
from lib.episode.episode_source_commands import (
    EpisodeSourceError,
    add_own_source_episode,
    set_episode_source_text,
)
from lib.episode.episode_sources import first_cut_episode_id, whole_source_files
from lib.episode.episode_target_duration import (
    EPISODE_TARGET_DURATION_FIELD,
    MAX_EPISODE_TARGET_DURATION,
    MIN_EPISODE_TARGET_DURATION,
    is_valid_episode_target_duration,
)
from lib.episode.episode_target_volume import EPISODE_TARGET_UNITS_FIELD
from lib.episode.source_file_changes import (
    SourceFileChangeError,
    SourceFileChangeOutcome,
    edit_whole_source_file,
    insert_whole_source_file,
    render_source_file_impact_text,
    replace_whole_source_file,
)
from lib.episode.source_kinds import SourceKind
from lib.episode.source_trash import trash_source_file
from lib.generation.generation_batch import (
    GenerationBatchReadModel,
    GenerationBatchRequestedItem,
    GenerationBatchRequestSnapshot,
    build_generation_batch_admission,
)
from lib.generation.generation_queue import (
    ActiveTaskRequestConflict,
    GenerationBatchNotFound,
    GenerationQueue,
    cleanup_fresh_generation_batch,
    get_generation_queue,
    text_task_request_facts,
)
from lib.generation.generation_queue_client import (
    BatchTaskResult,
    TaskSpec,
    WorkerOfflineError,
    enqueue_task_only,
    submit_generation_batch,
    wait_for_task,
)
from lib.generation.generation_result import (
    GenerationAction,
    GenerationBatchResult,
    GenerationProblem,
    GenerationProblemCode,
    GenerationSelectionMode,
    GenerationTargetState,
    encode_generation_problem,
    enqueue_problem,
    migration_problem,
    problem_from_task_failure,
)
from lib.generation.task_failure_encoding import is_provider_refusal
from lib.generation.video_request_facts import VideoRequestFactsError, VideoRequestFactsFailure
from lib.i18n import _ as i18n_message
from lib.infra.async_thread import run_sync_transaction
from lib.infra.async_thread import run_sync_transaction as _run_sync_transaction
from lib.infra.content_digest import prefixed, prefixed_canonical_json_digest, unprefixed
from lib.infra.data_root_layout import DataRootLayout
from lib.infra.path_safety import safe_join
from lib.infra.schema_guards import is_str
from lib.project.asset_merge import (
    MERGEABLE_ASSET_TYPES,
    AssetMergeEpisodeImpact,
    AssetMergeNotFoundError,
    AssetMergeRejectedError,
    AssetMergeReport,
)
from lib.project.asset_types import ASSET_SPECS
from lib.project.project_manager import ProjectManager, is_reference_video_project
from lib.project.project_migration_failure import (
    MigrationFailureRecord,
    ProjectMigrationError,
    load_migration_failure,
)
from lib.project.project_migration_guard import project_migration_failure
from lib.project.project_migrations import migrate_project_with_verdict
from lib.project.source_revision import SourceScope, compute_source_revision
from lib.script.script_batch_edit import (
    ScriptBatchEditCommand,
    ScriptBatchEditLocation,
    ScriptBatchEditor,
    ScriptBatchEditProblem,
    ScriptBatchEditResult,
    script_revision,
)
from lib.script.script_editor import (
    ScriptEditError,
    insert_segment,
    move_segment,
    patch_field,
    remove_segment,
    resolve_items,
    split_segment,
)
from lib.script.script_review import (
    ScriptPlanRebuildCompletionError,
    complete_stale_script_plan_rebuild,
    script_plan_kind,
)
from lib.script.source_loader import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    OnConflict,
    SourceDecodeError,
    SourceLoader,
    UnsupportedFormatError,
)
from lib.speech.character_voice import VALID_CHARACTER_VOICE_BINDINGS
from lib.speech.narration_config import (
    POST_PRODUCTION,
    NarrationConfigError,
    NarrationDelivery,
    validate_project_narration_config,
)
from lib.speech.speech_composition import SpeechProblemCode
from lib.workflow.operation_admission import admit_plan_episodes, whole_source_present
from lib.workflow.workflow_plan import (
    EPISODE_PLANNING_NEXT_SLOT,
    EPISODE_PLANNING_SLOT,
    EPISODE_PLANNING_SLOTS,
    TEXT_DRAFT_REPAIR_TASK_TYPE,
    WorkflowPlan,
    WorkflowPlanRequest,
    draft_repair_resource_id,
)
from lib.workflow.workflow_state import WorkflowRequestError, planning_docs
from server.draft_repair import DraftRepair
from server.draft_workflow import (
    EPISODE_ID_DESCRIPTION,
    DiscardDraftRequest,
    DraftContext,
    DraftDocType,
    DraftLocator,
    DraftWorkflow,
    DraftWorkflowError,
    PatchDraftRequest,
    PositiveEpisode,
    PromoteDraftRequest,
)
from server.services.admission.prompt_preview import ItemPromptPreview, ScriptItemNotFound, preview_item_prompts
from server.services.admission.video_batch_admission import generation_action_for
from server.services.project.episode_id_records import recorded_episode_ids_on
from server.services.project.narration_settings import NarrationSettingsInput, new_project_narration_fields
from server.services.project.workflow_planner import WorkflowPlanner
from server.services.tasks.episode_activity import episode_has_active_tasks
from server.services.tasks.video_caps import (
    annotate_reference_unit_tiers,
    capability_request_facts,
    duration_constraints_payload,
)
from server.text_generation import (
    MAX_INSTRUCTIONS_LEN,
    SCOPE_REMOVED_MESSAGE,
    AdScriptRejectedError,
    OperationNotAdmittedError,
    PromptAuthoringInputError,
    PromptOverwriteRequiredError,
    ScriptOverwriteRequiredError,
    TextGenerationError,
    TextGenerationRequest,
    TextGenerationResult,
    UnexpectedTextGenerationError,
    VideoRequestFactsUnavailableError,
    generate_drama_script_plan,
    generate_narration_script_plan,
    generate_reference_script_plan,
    prompt_authoring_preflight,
    require_admitted,
    script_plan_preflight,
)
from server.text_generation import (
    confirm_script_review as confirm_script_review_handler,
)
from server.text_generation import (
    generate_episode_script as generate_episode_script_handler,
)


@dataclass(frozen=True, slots=True)
class ToolRequest[RequestT]:
    value: RequestT


@dataclass(frozen=True, slots=True)
class ProjectScope:
    project_name: str
    data_root: Path


type BatchWaiter = Callable[..., Awaitable[tuple[list[BatchTaskResult], list[BatchTaskResult]]]]


@dataclass(frozen=True, slots=True)
class CallerContext:
    """Caller identity and host.

    ``source`` determines blocking versus immediate return for long tasks: ``embedded`` enqueues through ``batch_waiter`` and waits for batch termination; ``mcp`` and Web ``webui`` immediately return batch handles after submission and need no waiter."""

    user_id: str
    source: Literal["embedded", "mcp", "webui"]
    batch_waiter: BatchWaiter | None = None

    def waiting_with(self, **options: Any) -> CallerContext:
        """Bind extra waiter options (such as ``on_enqueued`` / ``stop_on_failure``); return unchanged when there is no waiter."""
        if self.batch_waiter is None:
            return self
        return replace(self, batch_waiter=functools.partial(self.batch_waiter, **options))


@dataclass(frozen=True, slots=True)
class Services:
    projects: ProjectManager
    workflow_planner: WorkflowPlanner
    capabilities: ConfigResolver
    queue: GenerationQueue = field(default_factory=get_generation_queue)
    #: Session factory for database records such as provider-material associations.
    sessions: async_sessionmaker[AsyncSession] = field(default_factory=lambda: async_session_factory)

    @classmethod
    def defaults(cls, projects: ProjectManager) -> Services:
        """Production default collaborators: this project manager's workflow planner, database-reading config resolver, global generation queue and session factory."""
        return cls(
            projects=projects,
            workflow_planner=WorkflowPlanner(projects),
            capabilities=ConfigResolver(async_session_factory),
        )


@dataclass(frozen=True, slots=True)
class ToolProblem:
    code: str
    detail: str
    action: str | None = None
    params: dict[str, Any] | None = None

    def model_dump(self, *, mode: str = "python") -> dict[str, Any]:
        del mode
        payload: dict[str, Any] = {"code": self.code, "detail": self.detail}
        if self.action is not None:
            payload["action"] = self.action
        if self.params is not None:
            payload["params"] = self.params
        return payload


@dataclass(frozen=True, slots=True)
class ToolOutcome[ResultT]:
    value: ResultT | None = None
    problem: ToolProblem | None = None


class GenerationBatchToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    batch_id: str = Field(
        min_length=1, description="Generation batch ID from generation_batch.batch_id returned by a generation tool"
    )


@dataclass(frozen=True, slots=True)
class MediaGenerationSubmission:
    batch: GenerationBatchReadModel
    successes: list[BatchTaskResult] | None = None
    failures: list[BatchTaskResult] | None = None


async def submit_media_generation(
    *,
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
    operation: str,
    preflight: GenerationBatchResult,
    pending_ids: list[str],
    specs: list[TaskSpec],
    states: dict[str, GenerationTargetState] | None = None,
    admission: dict[str, dict[str, Any]] | None = None,
    dependencies: Mapping[str, str] | None = None,
) -> MediaGenerationSubmission:
    """Submit a media-generation batch: remote callers immediately receive a handle; embedded callers await batch termination through ``caller.batch_waiter``.

    ``dependencies`` records within-batch order: a unit waits for another unit's task to succeed (its ``TaskSpec`` points there via ``dependency_resource_id``); if the prerequisite fails, do not submit it to the provider."""
    requested, blocked = build_generation_batch_admission(
        preflight=preflight,
        pending_ids=pending_ids,
        states=states,
        admission=admission,
        dependencies=dependencies,
    )
    if caller.source != "embedded":
        batch, _enqueued, _enqueue_failures = await submit_generation_batch(
            project_name=scope.project_name,
            operation=operation,
            requested=requested,
            blocked=blocked,
            specs=specs,
            source=caller.source,
            user_id=caller.user_id,
            queue=services.queue,
        )
        return MediaGenerationSubmission(batch=batch)
    batch_id = await services.queue.create_generation_batch(
        project_name=scope.project_name,
        operation=operation,
        requested=requested,
        blocked=blocked,
        source=caller.source,
        user_id=caller.user_id,
    )
    try:
        waiter = caller.batch_waiter
        if waiter is None:
            raise ValueError("embedded media generation requires a batch waiter")
        if specs:
            successes, failures = await waiter(
                project_name=scope.project_name,
                specs=specs,
                batch_id=batch_id,
                queue=services.queue,
                user_id=caller.user_id,
            )
        else:
            successes, failures = [], []
        settled = await services.queue.get_generation_batch(
            project_name=scope.project_name,
            batch_id=batch_id,
            user_id=caller.user_id,
        )
        return MediaGenerationSubmission(batch=settled, successes=successes, failures=failures)
    except BaseException as failure:
        await cleanup_fresh_generation_batch(
            services.queue,
            project_name=scope.project_name,
            batch_id=batch_id,
            user_id=caller.user_id,
            failure=failure,
        )
        raise


def _batch_currency_resolver(projects: ProjectManager, project_name: str) -> ArtifactCurrencyResolver | None:
    try:
        project = projects.load_project(project_name)
        return active_artifact_currency_resolver(projects.get_project_path(project_name), project)
    except ProjectMigrationError:
        return None


async def get_generation_batch(
    request: ToolRequest[GenerationBatchToolRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    try:
        resolver = await asyncio.to_thread(_batch_currency_resolver, services.projects, scope.project_name)
        result = await services.queue.get_generation_batch(
            project_name=scope.project_name,
            batch_id=request.value.batch_id,
            user_id=_caller.user_id,
            resolver=resolver,
        )
    except GenerationBatchNotFound as exc:
        return ToolOutcome(problem=ToolProblem("generation_batch_not_found", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_generation_batch 失败: {exc}"))
    return ToolOutcome(value=result)


async def cancel_generation_batch(
    request: ToolRequest[GenerationBatchToolRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    try:
        result = await services.queue.cancel_generation_batch(
            project_name=scope.project_name,
            batch_id=request.value.batch_id,
            user_id=_caller.user_id,
        )
    except GenerationBatchNotFound as exc:
        return ToolOutcome(problem=ToolProblem("generation_batch_not_found", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"cancel_generation_batch 失败: {exc}"))
    return ToolOutcome(value=result)


class PatchUpdateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["update"]
    id: str = Field(min_length=1, description="ID of the entry to update (segment_id / scene_id / shot_id / unit_id)")
    fields: dict[str, Any] = Field(
        min_length=1,
        description="Map field paths to new values; use dotted paths for nested fields, such as image_prompt.scene",
    )


class PatchInsertOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["insert"]
    after_id: str | None = Field(
        default=None,
        min_length=1,
        description="Insert the new entry after this ID; omit or use null to insert first, including the first entry in an empty script",
    )
    item: dict[str, Any] = Field(
        description="Complete new entry content; the system assigns an ID above the highest sequence number ever used in this episode and never reuses deleted IDs"
    )


class PatchRemoveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["remove"]
    id: str = Field(min_length=1, description="ID of the entry to remove")


class PatchMoveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["move"]
    id: str = Field(min_length=1, description="ID of the entry to move; its content and generated media move with it")
    after_id: str | None = Field(
        default=None,
        min_length=1,
        description="Move after this ID; omit or use null to move first",
    )


class PatchSplitOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["split"]
    id: str = Field(
        min_length=1,
        description="ID of the entry to split; the first part keeps this ID and other parts receive IDs under the new-entry rules",
    )
    parts: list[dict[str, Any]] = Field(
        min_length=2, description="Complete contents of the split parts in order; at least two parts"
    )


PatchEpisodeScriptOperation = Annotated[
    PatchUpdateOperation | PatchInsertOperation | PatchMoveOperation | PatchRemoveOperation | PatchSplitOperation,
    Field(discriminator="op"),
]


class PatchEpisodeScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(
        min_length=1,
        description="Script filename without directories, such as episode_1.json; one file per episode, with one call per episode for multi-episode edits",
    )
    base_revision: str = Field(
        pattern=r"^(?:sha256-v1:)?[0-9a-f]{64}$",
        description="Current revision returned by get_episode_script; sha256-v1: prefixed and bare hexadecimal forms are equivalent",
    )
    operations: list[PatchEpisodeScriptOperation] = Field(
        min_length=1,
        description="Ordered edit operations, committed atomically as one batch: update / insert / move / remove / split",
    )

    @field_validator("base_revision")
    @classmethod
    def _normalize_revision(cls, value: str) -> str:
        return prefixed(unprefixed(value))

    @field_validator("script")
    @classmethod
    def _validate_script(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in (".", ".."):
            raise ValueError(f"script 必须是纯文件名，禁止路径分隔符: {value!r}")
        return value


_SPEECH_PROBLEM_CODES = frozenset(code.value for code in SpeechProblemCode)
_REGENERATION_FIELDS = frozenset({"image_prompt", "video_prompt"})


class ScriptSpeechProblem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    unit_id: str | None
    locations: tuple[ScriptBatchEditLocation, ...] = ()
    reason: str
    action: str


class ScriptSpeechAdmission(BaseModel):
    """Speech admission for the identified unit when speech composition rejects a script edit; same shape as ``SpeechAdmission.to_dict()``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: Literal[False] = False
    unit_id: str | None
    mode: None = None
    problems: tuple[ScriptSpeechProblem, ...]


class ScriptPatchResult(ScriptBatchEditResult):
    """``patch_episode_script`` result: batch script-edit results plus Agent follow-up guidance.

    - ``regeneration_required_ids``: successfully submitted entries whose image_prompt / video_prompt changed; immediately regenerate the corresponding images / videos.
    - ``speech_admission``: all speech problems for the identified unit when the first problem concerns speech composition.
    Unbound mention hints continue using ``warnings``."""

    regeneration_required_ids: tuple[str, ...] = ()
    speech_admission: ScriptSpeechAdmission | None = None


def _regeneration_required_ids(operations: list[PatchEpisodeScriptOperation]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            operation.id
            for operation in operations
            if isinstance(operation, PatchUpdateOperation)
            and any(field.split(".", 1)[0] in _REGENERATION_FIELDS for field in operation.fields)
        )
    )


def _speech_admission(result: ScriptBatchEditResult) -> ScriptSpeechAdmission | None:
    if not result.problems or result.problems[0].code not in _SPEECH_PROBLEM_CODES:
        return None
    unit_id = result.problems[0].unit_id
    return ScriptSpeechAdmission(
        unit_id=unit_id,
        problems=tuple(
            ScriptSpeechProblem(
                code=problem.code,
                unit_id=problem.unit_id,
                locations=problem.locations,
                reason=problem.reason,
                action=problem.next_action,
            )
            for problem in result.problems
            if problem.unit_id == unit_id
        ),
    )


def _script_patch_result(request: PatchEpisodeScriptRequest, result: ScriptBatchEditResult) -> ScriptPatchResult:
    return ScriptPatchResult(
        **dict(result),
        regeneration_required_ids=_regeneration_required_ids(request.operations) if result.success else (),
        speech_admission=_speech_admission(result),
    )


def _prompt_overwrite_problem(exc: PromptOverwriteRequiredError) -> ToolProblem:
    return ToolProblem("prompt_overwrite_required", str(exc), params={"prompt_overwrite": exc.overwrite})


def truncation_problem(exc: TextOutputTruncatedError) -> ToolProblem:
    """Truncated text-model output: all text tasks share one problem code; register maximum output length (custom models) or choose another text model."""
    return ToolProblem(
        "text_output_truncated",
        str(exc),
        action=GenerationAction.CONFIGURE_PROVIDER,
        params={"provider_id": exc.provider_id or exc.provider, "model": exc.model, "custom_model": exc.custom_model},
    )


def model_not_in_catalog_problem(exc: ModelNotInCatalogError) -> ToolProblem:
    """Referenced built-in model is absent from the provider catalog: retain stable task-failure code and parameters; reselect a model through provider configuration."""
    return ToolProblem(
        exc.code,
        str(exc),
        action=GenerationAction.CONFIGURE_PROVIDER,
        params=dict(exc.params),
    )


def video_facts_problem(failure: VideoRequestFactsFailure, detail: str) -> ToolProblem:
    """Unresolved video-request facts: forward problem codes and parameters unchanged; use batch admission's recovery-guidance mapping (usually provider configuration)."""
    return ToolProblem(
        failure.code,
        detail,
        action=generation_action_for(failure.action),
        params=failure.parameters(),
    )


def _not_admitted_problem(exc: OperationNotAdmittedError) -> ToolProblem:
    """Rejected admission: ``params.reason`` matches the reason code for the same operation in production-status ``operations``."""
    return ToolProblem(
        "operation_not_admitted",
        str(exc),
        action=GenerationAction.FIX_INPUT,
        params={"operation": exc.operation, "reason": exc.reason.value if exc.reason is not None else None},
    )


def _script_overwrite_problem(exc: ScriptOverwriteRequiredError) -> ToolProblem:
    return ToolProblem("script_overwrite_required", str(exc), params={"script_overwrite": exc.overwrite})


async def _run_text_generation(
    operation: str,
    call: Awaitable[TextGenerationResult],
) -> ToolOutcome[TextGenerationResult]:
    try:
        return ToolOutcome(value=await call)
    except ScriptOverwriteRequiredError as exc:
        return ToolOutcome(problem=_script_overwrite_problem(exc))
    except AdScriptRejectedError as exc:
        return ToolOutcome(
            problem=ToolProblem(
                "ad_script_rejected",
                str(exc),
                action=GenerationAction.FIX_INPUT,
                params={"details": "；".join(exc.problems)},
            )
        )
    except PromptOverwriteRequiredError as exc:
        return ToolOutcome(problem=_prompt_overwrite_problem(exc))
    except OperationNotAdmittedError as exc:
        return ToolOutcome(problem=_not_admitted_problem(exc))
    except TextOutputTruncatedError as exc:
        return ToolOutcome(problem=truncation_problem(exc))
    except VideoRequestFactsUnavailableError as exc:
        return ToolOutcome(problem=video_facts_problem(exc.failure, str(exc)))
    except PromptAuthoringInputError as exc:
        return ToolOutcome(
            problem=ToolProblem(exc.code, str(exc), action=GenerationAction.FIX_INPUT, params=dict(exc.params))
        )
    except UnexpectedTextGenerationError as exc:
        return ToolOutcome(problem=_unexpected_text_failure_problem(str(exc), exc))
    except TextGenerationError as exc:
        return ToolOutcome(problem=ToolProblem("generation_refused", str(exc)))
    except ModelNotInCatalogError as exc:
        return ToolOutcome(problem=model_not_in_catalog_problem(exc))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected_text_failure_problem(f"{operation} 失败: {exc}", exc))


def _unexpected_text_failure_problem(detail: str, cause: BaseException) -> ToolProblem:
    """Unexpected generation exception: actual provider content/input rejection maps to rejection (edit input); otherwise retry as task failure, matching image tasks."""
    if is_provider_refusal(cause):
        return ToolProblem("generation_refused", detail)
    return ToolProblem(GenerationProblemCode.TASK_FAILED.value, detail, action=GenerationAction.RETRY)


_TEXT_EPISODE_SCRIPT = "text_episode_script"
_TEXT_DRAMA_SCRIPT_PLAN = "text_drama_script_plan"
_TEXT_NARRATION_SCRIPT_PLAN = "text_narration_script_plan"
_TEXT_REFERENCE_SCRIPT_PLAN = "text_reference_script_plan"
_TEXT_EPISODE_PLAN = "text_episode_plan"
#: Text tasks submitted and awaited in-process by the embedded host: workers reuse the submitter's data root and services.
#: Memory only, excluded from task payloads. Unregistered tasks (MCP submissions or tasks surviving a restart) resolve current configuration.
_TEXT_TASK_SERVICES: dict[str, tuple[ProjectScope, Services]] = {}


def _queued_generation_problem(problem: ToolProblem) -> GenerationProblem:
    try:
        action = GenerationAction(problem.action) if problem.action is not None else GenerationAction.RETRY
    except ValueError:
        action = GenerationAction.RETRY
    if problem.code == "generation_refused" and problem.action is None:
        action = GenerationAction.FIX_INPUT
    return GenerationProblem(code=problem.code, detail=problem.detail, action=action, params=problem.params or {})


async def _submit_text_task(
    *,
    task_type: str,
    operation: str,
    unit_id: str,
    payload: dict[str, Any],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
    conflict_resource_ids: Sequence[str] = (),
) -> ToolOutcome[Any]:
    """Submit one text task. ``conflict_resource_ids`` are other slots mutually exclusive with ``unit_id``: any in-flight task there conflicts.
    An in-flight task on ``unit_id`` conflicts only if request facts differ; merge into it when facts match."""
    active = await services.queue.get_active_tasks_for_resources(
        project_name=scope.project_name,
        task_type=task_type,
        resource_ids=list(dict.fromkeys([unit_id, *conflict_resource_ids])),
        user_id=caller.user_id,
    )
    conflicting = next(
        (
            task
            for task in active
            if task.get("resource_id") != unit_id
            or text_task_request_facts(task.get("payload")) != text_task_request_facts(payload)
        ),
        None,
    )
    if conflicting is not None:
        return ToolOutcome(
            problem=ToolProblem(
                "generation_active_task_conflict",
                "generation_active_task_conflict",
                action=GenerationAction.WAIT_FOR_TASK,
                params={"task_id": conflicting["task_id"], "status": conflicting["status"]},
            )
        )
    snapshot = GenerationBatchRequestSnapshot(
        selection=GenerationSelectionMode.EXPLICIT,
        requested=[GenerationBatchRequestedItem(unit_id=unit_id)],
    )
    batch_id = await services.queue.create_generation_batch(
        project_name=scope.project_name,
        operation=operation,
        requested=snapshot,
        blocked=[],
        source=caller.source,
        user_id=caller.user_id,
    )
    try:
        enqueue = await enqueue_task_only(
            project_name=scope.project_name,
            task_type=task_type,
            media_type="text",
            resource_id=unit_id,
            payload=payload,
            source=caller.source,
            user_id=caller.user_id,
            batch_id=batch_id,
            batch_unit_id=unit_id,
            queue=services.queue,
        )
        registered_services = caller.source == "embedded" and not enqueue.get("deduped")
        if registered_services:
            _TEXT_TASK_SERVICES[enqueue["task_id"]] = (scope, services)
        try:
            batch = await services.queue.get_generation_batch(
                project_name=scope.project_name, batch_id=batch_id, user_id=caller.user_id
            )
            if caller.source != "embedded":
                return ToolOutcome(value=batch)
            task = await wait_for_task(enqueue["task_id"], queue=services.queue)
        finally:
            if registered_services:
                _TEXT_TASK_SERVICES.pop(enqueue["task_id"], None)
        if task["status"] == "cancelled":
            problem = problem_from_task_failure(task.get("error_message"), cancelled=True)
            return ToolOutcome(problem=ToolProblem(**problem.model_dump(mode="json")))
        if task["status"] == "failed":
            problem = problem_from_task_failure(task.get("error_message"))
            return ToolOutcome(problem=ToolProblem(**problem.model_dump(mode="json")))
        result = task.get("result") or {}
        if task_type == _TEXT_EPISODE_PLAN:
            return ToolOutcome(value=PlanEpisodesResult.model_validate(result))
        if task_type == TEXT_DRAFT_REPAIR_TASK_TYPE:
            return ToolOutcome(value=DraftRepairResult.model_validate(result))
        return ToolOutcome(value=TextGenerationResult(**result))
    except BaseException as exc:
        await cleanup_fresh_generation_batch(
            services.queue,
            project_name=scope.project_name,
            batch_id=batch_id,
            user_id=caller.user_id,
            failure=exc,
        )
        if isinstance(exc, WorkerOfflineError):
            return ToolOutcome(problem=ToolProblem(**enqueue_problem(str(exc)).model_dump(mode="json")))
        if isinstance(exc, ActiveTaskRequestConflict):
            return ToolOutcome(
                problem=ToolProblem(
                    "generation_active_task_conflict",
                    "generation_active_task_conflict",
                    action=GenerationAction.WAIT_FOR_TASK,
                    params={"task_id": exc.existing_task_id},
                )
            )
        raise


async def _execute_text_handler(
    operation: str,
    handler: Callable[..., Awaitable[TextGenerationResult]],
    request: TextGenerationRequest,
    scope: ProjectScope,
    services: Services,
) -> ToolOutcome[TextGenerationResult]:
    return await _run_text_generation(
        operation,
        handler(
            request,
            project_name=scope.project_name,
            projects=services.projects,
            config_resolver=services.capabilities,
        ),
    )


_TextInstructions = Annotated[
    str,
    Field(
        max_length=MAX_INSTRUCTIONS_LEN,
        description=(
            "Optional verbatim additional instructions for this generation, injected unchanged into the Additional instructions section at the end of the prompt; "
            f"the body expresses how strictly to follow them. Omitted/blank means absent; maximum {MAX_INSTRUCTIONS_LEN} characters"
        ),
    ),
]
_DRY_RUN_DESCRIPTION = "Return the prompt only; do not call the model"


class GenerateEpisodeScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: PositiveEpisode = Field(description=EPISODE_ID_DESCRIPTION)
    instructions: _TextInstructions | SkipJsonSchema[None] = None
    entry_ids: list[Annotated[str, Field(min_length=1)]] | SkipJsonSchema[None] = Field(
        default=None,
        description="Authoring scope (storyboard / unit IDs); omit for all pending entries. Naming entries only limits scope and does not authorize overwriting existing content",
    )
    rewrite: bool = Field(
        default=False,
        description="Explicitly rewrite every visual layer within scope; when omitted, fill gaps only, preserving existing image/video prompts and supplying only the missing one",
    )
    regenerate: bool = Field(
        default=False,
        description=(
            "Advertisement/short film only: regenerate the entire script, replacing the existing formal script; the result directly becomes formal. Cannot combine with entry_ids / rewrite. If a formal script exists, first return the script_overwrite loss list"
        ),
    )
    overwrite_revision: str | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "Supply this token only after presenting the loss list to the user and obtaining overwrite consent: prompt_overwrite.revision for prompt rewriting, script_overwrite.revision for full regeneration. sha256-v1: prefixed and bare hexadecimal forms are equivalent. Not required when existing content is not overwritten"
        ),
    )
    dry_run: bool = Field(default=False, description=_DRY_RUN_DESCRIPTION)

    @model_validator(mode="before")
    @classmethod
    def _reject_retired_scope(cls, data: Any) -> Any:
        if isinstance(data, dict) and "scope" in data:
            raise ValueError(SCOPE_REMOVED_MESSAGE)
        return data

    @model_validator(mode="after")
    def _regenerate_is_whole_script(self) -> Self:
        if self.regenerate and (self.entry_ids or self.rewrite):
            raise ValueError("regenerate 整份重做全部条目，不与 entry_ids / rewrite 同用")
        return self

    def text_request(self) -> TextGenerationRequest:
        return TextGenerationRequest(
            episode=self.episode_id,
            instructions=self.instructions,
            entry_ids=tuple(self.entry_ids or ()),
            rewrite=self.rewrite,
            overwrite_revision=self.overwrite_revision,
            dry_run=self.dry_run,
            regenerate=self.regenerate,
        )


class GenerateScriptPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: PositiveEpisode = Field(description=EPISODE_ID_DESCRIPTION)
    source: str | SkipJsonSchema[None] = Field(
        default=None,
        description="Optional project-relative source path; defaults to this episode's derived source/episode_N.txt",
    )
    instructions: _TextInstructions | SkipJsonSchema[None] = None
    dry_run: bool = Field(default=False, description=_DRY_RUN_DESCRIPTION)

    def text_request(self) -> TextGenerationRequest:
        return TextGenerationRequest(
            episode=self.episode_id, source=self.source, instructions=self.instructions, dry_run=self.dry_run
        )


async def generate_episode_script(
    request: ToolRequest[GenerateEpisodeScriptRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    text_request = request.value.text_request()
    if text_request.dry_run:
        return await _execute_text_handler(
            "generate_episode_script", generate_episode_script_handler, text_request, scope, services
        )
    try:
        await asyncio.to_thread(
            lambda: prompt_authoring_preflight(
                services.projects.get_project_path(scope.project_name),
                text_request.episode,
                entry_ids=text_request.entry_ids,
                rewrite=text_request.rewrite,
                overwrite_revision=text_request.overwrite_revision,
                regenerate=text_request.regenerate,
            )
        )
    except ScriptOverwriteRequiredError as exc:
        return ToolOutcome(problem=_script_overwrite_problem(exc))
    except PromptOverwriteRequiredError as exc:
        return ToolOutcome(problem=_prompt_overwrite_problem(exc))
    except OperationNotAdmittedError as exc:
        return ToolOutcome(problem=_not_admitted_problem(exc))
    except TextGenerationError as exc:
        return ToolOutcome(problem=ToolProblem("generation_refused", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"generate_episode_script 失败: {exc}"))
    return await _submit_text_task(
        task_type=_TEXT_EPISODE_SCRIPT,
        operation="generate_episode_script",
        unit_id=f"episode-{text_request.episode}",
        payload=text_request.to_payload(),
        scope=scope,
        caller=_caller,
        services=services,
    )


async def generate_script_plan(
    request: ToolRequest[GenerateScriptPlanRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    text_request = request.value.text_request()
    try:
        project = await asyncio.to_thread(services.projects.load_project, scope.project_name)
        content_mode = project.get("content_mode", "narration")
        if content_mode not in {"narration", "drama", "ad"}:
            raise TextGenerationError(f"不支持的创作类型: {content_mode}")
        await asyncio.to_thread(
            lambda: script_plan_preflight(
                services.projects.get_project_path(scope.project_name),
                project,
                text_request.episode,
                text_request.source,
            )
        )
        if is_reference_video_project(project):
            handler, task_type = generate_reference_script_plan, _TEXT_REFERENCE_SCRIPT_PLAN
        elif content_mode == "narration":
            handler, task_type = generate_narration_script_plan, _TEXT_NARRATION_SCRIPT_PLAN
        else:
            handler, task_type = generate_drama_script_plan, _TEXT_DRAMA_SCRIPT_PLAN
    except OperationNotAdmittedError as exc:
        return ToolOutcome(problem=_not_admitted_problem(exc))
    except TextGenerationError as exc:
        return ToolOutcome(problem=ToolProblem("generation_refused", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"generate_script_plan 失败: {exc}"))
    if text_request.dry_run:
        return await _execute_text_handler("generate_script_plan", handler, text_request, scope, services)
    return await _submit_text_task(
        task_type=task_type,
        operation="generate_script_plan",
        unit_id=f"episode-{text_request.episode}",
        payload=text_request.to_payload(),
        scope=scope,
        caller=_caller,
        services=services,
    )


class ConfirmScriptReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: PositiveEpisode = Field(description=EPISODE_ID_DESCRIPTION)
    overwrite_revision: str | SkipJsonSchema[None] = Field(
        default=None,
        description="Token from script_overwrite.revision, supplied only after presenting the loss list to the user and obtaining overwrite consent; sha256-v1: prefixed and bare hexadecimal forms are equivalent. Not required if no formal script exists",
    )


async def confirm_script_review(
    request: ToolRequest[ConfirmScriptReviewRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[TextGenerationResult]:
    return await _run_text_generation(
        "confirm_script_review",
        confirm_script_review_handler(
            request.value.episode_id,
            overwrite_revision=request.value.overwrite_revision,
            project_name=scope.project_name,
            projects=services.projects,
            config_resolver=services.capabilities,
        ),
    )


class NoArguments(BaseModel):
    """Tool request without arguments."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(
        description="Project-relative source path under source/, such as source/episode_1.txt; obtain it from list_source_files"
    )


class EpisodeScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(description="Script filename without directories, such as episode_1.json")


class ScriptPlanContentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: PositiveEpisode = Field(description=EPISODE_ID_DESCRIPTION)


class ProjectFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(
        description="Project-relative business file path, such as project.json or scripts/episode_1.json; obtain it from list_project_files"
    )


class ProjectContent(BaseModel):
    revision: str
    project: dict[str, Any]


class EpisodeScriptContent(BaseModel):
    revision: str
    script_filename: str
    script: dict[str, Any]


class ProjectFileEntry(BaseModel):
    path: str
    size: int
    etag: str


class SourceFilesContent(BaseModel):
    revision: str
    files: list[ProjectFileEntry]


class SourceTextContent(BaseModel):
    revision: str
    etag: str
    path: str
    text: str


class ScriptPlanContent(BaseModel):
    revision: str
    etag: str
    episode: int
    path: str
    content: Any


class ProjectFilesContent(BaseModel):
    revision: str
    files: list[ProjectFileEntry]


class ProjectFileContent(BaseModel):
    revision: str
    etag: str
    path: str
    content: Any


_EPISODE_DIR_RE = re.compile(r"episode_[1-9][0-9]*\Z")
BUSINESS_FILE_MAX_BYTES = 50 * 1024 * 1024
_SCRIPT_PLAN_BUSINESS_FILENAMES = frozenset(
    {
        *SCRIPT_PLAN_FILENAMES.values(),
        *(name for names in SCRIPT_PLAN_LEGACY_FILENAMES.values() for name in names),
        REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
        REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
        DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
        NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
        REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
        REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    }
)


def _business_path_parts(value: str) -> tuple[str, ...]:
    if not is_str(value) or not value or "\\" in value or "\x00" in value:
        raise ValueError("path 必须是项目内业务文件的 POSIX 相对路径")
    pure = PurePosixPath(value)
    parts = pure.parts
    if pure.is_absolute() or ".." in parts or any(part.startswith(".") for part in parts):
        raise ValueError("path 不在业务文件白名单内")
    allowed = (
        parts == ("project.json",)
        or (len(parts) == 2 and parts[0] == "source" and pure.suffix.lower() in {".txt", ".md"})
        or (len(parts) == 2 and parts[0] == "scripts" and pure.suffix.lower() == ".json")
        or (
            len(parts) == 3
            and parts[0] == "drafts"
            and _EPISODE_DIR_RE.fullmatch(parts[1]) is not None
            and parts[2] in _SCRIPT_PLAN_BUSINESS_FILENAMES
        )
    )
    if not allowed:
        raise ValueError("path 不在业务文件白名单内")
    return parts


def _resolve_business_file(project_dir: Path, relative: str) -> Path:
    parts = _business_path_parts(relative)
    lexical = project_dir
    for part in parts:
        lexical /= part
        if lexical.is_symlink():
            raise ValueError("symbolic links are not allowed")
    return safe_join(project_dir, *parts, require_file=True)


class _BusinessFileTooLargeError(ValueError):
    pass


def _read_business_file(project_dir: Path, relative: str) -> tuple[bytes, Path]:
    parts = _business_path_parts(relative)
    path = project_dir.joinpath(*parts)
    if os.open not in os.supports_dir_fd:
        resolved = _resolve_business_file(project_dir, relative)
        with resolved.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            current = os.lstat(resolved)
            if not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(current.st_mode):
                raise ValueError("path 必须指向普通文件")
            if (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError("path 在安全校验后发生变化")
            raw = handle.read(BUSINESS_FILE_MAX_BYTES + 1)
    else:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW  # noqa: TID251 -- Executed only in the POSIX branch supporting dir_fd, where the constants necessarily exist
        directory_fd = os.open(project_dir, directory_flags)
        try:
            for part in parts[:-1]:
                child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory_fd)  # noqa: TID251 -- Executed only in the POSIX branch supporting dir_fd, where the constants necessarily exist
            try:
                if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                    raise ValueError("path 必须指向普通文件")
                with os.fdopen(file_fd, "rb", closefd=False) as handle:
                    raw = handle.read(BUSINESS_FILE_MAX_BYTES + 1)
            finally:
                os.close(file_fd)
        except FileNotFoundError:
            raise
        except OSError as exc:
            raise ValueError("path 必须指向无 symlink 的普通文件") from exc
        finally:
            os.close(directory_fd)
    if len(raw) > BUSINESS_FILE_MAX_BYTES:
        raise _BusinessFileTooLargeError(f"文件超过 {BUSINESS_FILE_MAX_BYTES} 字节读取上限")
    return raw, path


def _decode_business_file(project_dir: Path, relative: str) -> tuple[Any, str, str, int]:
    raw, path = _read_business_file(project_dir, relative)
    etag = prefixed(hashlib.sha256(raw).hexdigest())
    text = raw.decode("utf-8")
    if path.suffix.lower() == ".json":
        content = json.loads(text)
        revision = prefixed_canonical_json_digest(content)
    else:
        content = text
        revision = etag
    return content, revision, etag, len(raw)


def _business_file_entries(
    project_dir: Path,
    *,
    source_only: bool = False,
) -> list[ProjectFileEntry]:
    candidates = [] if source_only else [project_dir / "project.json"]
    for dirname in ("source",) if source_only else ("source", "scripts"):
        directory = project_dir / dirname
        if directory.is_dir() and not directory.is_symlink():
            candidates.extend(directory.iterdir())
    drafts = project_dir / "drafts"
    if not source_only and drafts.is_dir() and not drafts.is_symlink():
        for episode_dir in drafts.iterdir():
            if episode_dir.is_dir() and not episode_dir.is_symlink() and _EPISODE_DIR_RE.fullmatch(episode_dir.name):
                candidates.extend(episode_dir.iterdir())

    entries: list[ProjectFileEntry] = []
    for candidate in candidates:
        try:
            relative = candidate.relative_to(project_dir).as_posix()
            raw, _path = _read_business_file(project_dir, relative)
            etag = prefixed(hashlib.sha256(raw).hexdigest())
            size = len(raw)
        except (FileNotFoundError, OSError, TypeError, ValueError):
            continue
        entries.append(ProjectFileEntry(path=relative, size=size, etag=etag))
    return sorted(entries, key=lambda entry: entry.path)


def _file_problem(name: str, exc: BaseException) -> ToolProblem:
    if isinstance(exc, FileNotFoundError):
        return ToolProblem("file_not_found", f"{name} 文件不存在")
    if isinstance(exc, (json.JSONDecodeError, UnicodeError)):
        return ToolProblem("invalid_content", f"{name} 文件不是有效的 UTF-8 JSON/文本")
    if isinstance(exc, _BusinessFileTooLargeError):
        return ToolProblem("file_too_large", str(exc))
    if isinstance(exc, (TypeError, ValueError)):
        return ToolProblem("unsafe_path", str(exc))
    return ToolProblem("internal_error", f"{name} 失败: {exc}")


def _get_project_content_sync(project_name: str, projects: ProjectManager) -> ProjectContent:
    project = projects.load_project(project_name)
    return ProjectContent(revision=prefixed_canonical_json_digest(project), project=project)


async def get_project_content(
    _request: ToolRequest[NoArguments],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ProjectContent]:
    try:
        content = await asyncio.to_thread(_get_project_content_sync, scope.project_name, services.projects)
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("project_not_found", f"项目未找到或缺 project.json: {exc}"))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_project_content 失败: {exc}"))
    return ToolOutcome(value=content)


async def get_episode_script(
    request: ToolRequest[EpisodeScriptRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[EpisodeScriptContent]:
    """Read one episode script and issue a revision; migration-failed projects receive the complete migration problem without a revision."""
    filename = request.value.script
    if not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        return ToolOutcome(problem=ToolProblem("invalid_request", "script 必须是纯文件名"))
    try:
        failure = await asyncio.to_thread(project_migration_failure, scope.project_name, services.projects)
        if failure is not None:
            return ToolOutcome(problem=_migration_tool_problem(failure))
        script = await asyncio.to_thread(services.projects.load_script_readonly, scope.project_name, filename)
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("file_not_found", str(exc)))
    except (TypeError, ValueError) as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_episode_script 失败: {exc}"))
    return ToolOutcome(
        value=EpisodeScriptContent(revision=script_revision(script), script_filename=filename, script=script)
    )


class PromptPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(min_length=1, description="Script filename without directories, such as episode_1.json")
    item_id: str = Field(
        min_length=1, description="Storyboard entry ID (segment_id / scene_id / shot_id / unit_id), such as E1S01"
    )


async def get_prompt_preview(
    request: ToolRequest[PromptPreviewRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ItemPromptPreview]:
    """Render the final prompt text currently sent to the image / video model for one entry.

    Share execution's rendering outlet so the result is verbatim the prompt actually sent for this generation. Read only: no provider requests, costs or manifest writes. ``unavailable`` is a stable machine-readable reason code forwarded unchanged by both hosts; the REST route renders finished UI wording in the request language."""
    filename = request.value.script
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        return ToolOutcome(problem=ToolProblem("invalid_request", "script 必须是纯文件名"))
    try:
        if problem := await migration_gate(scope, services):
            return ToolOutcome(problem=problem)
        preview = await preview_item_prompts(
            scope.project_name, filename, request.value.item_id, projects=services.projects
        )
    except ScriptItemNotFound:
        return ToolOutcome(problem=ToolProblem("item_not_found", f"剧本中不存在分镜 {request.value.item_id}"))
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("file_not_found", str(exc)))
    except (TypeError, ValueError) as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_prompt_preview 失败: {exc}"))
    return ToolOutcome(value=preview)


async def list_source_files(
    _request: ToolRequest[NoArguments],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[SourceFilesContent]:
    try:
        files = await asyncio.to_thread(
            lambda: _business_file_entries(services.projects.get_project_path(scope.project_name), source_only=True)
        )
        revision = prefixed_canonical_json_digest([entry.model_dump() for entry in files])
        return ToolOutcome(value=SourceFilesContent(revision=revision, files=files))
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("list_source_files", exc))


async def get_source_text(
    request: ToolRequest[SourceTextRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[SourceTextContent]:
    path = request.value.path
    try:
        project_dir = await asyncio.to_thread(services.projects.get_project_path, scope.project_name)
        if not path.startswith("source/"):
            raise ValueError("path 必须指向 source/ 下的文本文件")
        content, revision, etag, _size = await asyncio.to_thread(_decode_business_file, project_dir, path)
        if not isinstance(content, str):
            raise ValueError("source 文件必须是文本")
        return ToolOutcome(value=SourceTextContent(revision=revision, etag=etag, path=path, text=content))
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("get_source_text", exc))


def _get_script_plan_content_sync(
    project_name: str, episode: int, projects: ProjectManager
) -> ScriptPlanContent | None:
    project = projects.load_project(project_name)
    kind = script_plan_kind(project)
    if kind is None:
        return None
    names = (
        (REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME, REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME)
        if kind == "reference_video"
        else (SCRIPT_PLAN_FILENAMES[kind], *SCRIPT_PLAN_LEGACY_FILENAMES.get(kind, ()))
    )
    project_dir = projects.get_project_path(project_name)
    for name in names:
        relative = f"drafts/episode_{episode}/{name}"
        try:
            content, revision, etag, _size = _decode_business_file(project_dir, relative)
        except FileNotFoundError:
            continue
        return ScriptPlanContent(revision=revision, etag=etag, episode=episode, path=relative, content=content)
    raise FileNotFoundError


async def get_script_plan_content(
    request: ToolRequest[ScriptPlanContentRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ScriptPlanContent]:
    episode = request.value.episode_id
    try:
        result = await asyncio.to_thread(_get_script_plan_content_sync, scope.project_name, episode, services.projects)
        if result is None:
            return ToolOutcome(problem=ToolProblem("script_plan_not_applicable", "当前项目没有 script_plan 中间态"))
        return ToolOutcome(value=result)
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("get_script_plan_content", exc))


async def list_project_files(
    _request: ToolRequest[NoArguments],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ProjectFilesContent]:
    try:
        files = await asyncio.to_thread(
            lambda: _business_file_entries(services.projects.get_project_path(scope.project_name))
        )
        revision = prefixed_canonical_json_digest([entry.model_dump() for entry in files])
        return ToolOutcome(value=ProjectFilesContent(revision=revision, files=files))
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("list_project_files", exc))


async def read_project_file(
    request: ToolRequest[ProjectFileRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ProjectFileContent]:
    path = request.value.path
    try:
        content, revision, etag, _size = await asyncio.to_thread(
            lambda: _decode_business_file(services.projects.get_project_path(scope.project_name), path)
        )
        return ToolOutcome(value=ProjectFileContent(revision=revision, etag=etag, path=path, content=content))
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("read_project_file", exc))


def _draft_workflow(scope: ProjectScope, services: Services) -> DraftWorkflow:
    return DraftWorkflow(
        DraftContext(
            project_name=scope.project_name,
            data_root=scope.data_root,
            pm=services.projects,
            config_resolver=services.capabilities,
        )
    )


async def _run_draft(
    call: Awaitable[dict[str, Any]], *, notice_as_problem: bool = False
) -> ToolOutcome[dict[str, Any]]:
    """When ``notice_as_problem`` is true, draft errors with wording keys use that key as problem code and indicate editing input (for task-failure reasons)."""
    try:
        return ToolOutcome(value=await call)
    except DraftWorkflowError as exc:
        if exc.failure is not None:
            return ToolOutcome(problem=video_facts_problem(exc.failure, exc.detail))
        if notice_as_problem and exc.notice_key is not None:
            return ToolOutcome(
                problem=ToolProblem(
                    exc.notice_key, exc.detail, action=GenerationAction.FIX_INPUT, params=dict(exc.notice_params)
                )
            )
        return ToolOutcome(problem=ToolProblem(exc.code, exc.detail))
    except TextOutputTruncatedError as exc:
        return ToolOutcome(problem=truncation_problem(exc))
    except ModelNotInCatalogError as exc:
        return ToolOutcome(problem=model_not_in_catalog_problem(exc))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", str(exc)))


async def open_draft(
    request: ToolRequest[DraftLocator],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    locator = request.value
    return await _run_draft(_draft_workflow(scope, services).open(locator.episode_id, locator.doc_type, locator.source))


async def patch_draft(
    request: ToolRequest[PatchDraftRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    patch = request.value
    return await _run_draft(
        _draft_workflow(scope, services).patch(
            patch.episode_id,
            patch.doc_type,
            patch.content,
            patch.base_revision,
            accept_formal_revision=patch.accept_formal_revision,
            accepts_formal_revision="accept_formal_revision" in patch.model_fields_set,
            source=patch.source,
            updates_source="source" in patch.model_fields_set,
        )
    )


async def promote_draft(
    request: ToolRequest[PromoteDraftRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    promotion = request.value
    return await _run_draft(
        _draft_workflow(scope, services).promote(
            promotion.episode_id,
            promotion.doc_type,
            promotion.base_revision,
        )
    )


async def discard_draft(
    request: ToolRequest[DiscardDraftRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    discard = request.value
    return await _run_draft(
        _draft_workflow(scope, services).discard(discard.episode_id, discard.doc_type, discard.base_revision)
    )


class RepairDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: PositiveEpisode = Field(description=EPISODE_ID_DESCRIPTION)
    doc_type: DraftDocType = Field(
        description="Document corresponding to the draft awaiting repair; same values as open_draft"
    )
    base_revision: str = Field(
        description="Revision obtained when reading the draft; sha256-v1: prefixed and bare hexadecimal forms are equivalent. Check it before writing repairs to verify the draft has not changed"
    )
    instructions: _TextInstructions | SkipJsonSchema[None] = None


class DraftRepairResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    message: str
    episode_id: int
    doc_type: str
    adopted: bool = Field(
        description="All violations have been cleared and the draft has been adopted as formal content"
    )
    violation_count: int = Field(description="Remaining draft violations when not adopted; 0 when adopted")


async def repair_draft(
    request: ToolRequest[RepairDraftRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    """AI repair for a repair draft: after preflight, submit as a queued text task; reassess normally afterward and adopt when violations clear."""
    value = request.value
    try:
        await _draft_repair(scope, services).check(value.episode_id, value.doc_type, value.base_revision)
    except DraftWorkflowError as exc:
        return ToolOutcome(problem=ToolProblem(exc.code, exc.detail))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("repair_draft", exc))
    return await _submit_text_task(
        task_type=TEXT_DRAFT_REPAIR_TASK_TYPE,
        operation="repair_draft",
        unit_id=draft_repair_resource_id(value.episode_id, value.doc_type),
        payload=value.model_dump(mode="json"),
        scope=scope,
        caller=caller,
        services=services,
    )


#: Draft-command error code → task-failure wording key, matching draft REST errors. Failures before writing the draft already map to
#: ``draft_repair_failed``; unlisted errors arise during reassessment/promotion after the body write, so map to incomplete saving.
_DRAFT_REPAIR_FAILURE_KEYS: dict[str, str] = {
    "draft_not_found": "draft_not_found",
    "invalid_request": "draft_doc_type_not_applicable",
    "doc_type_not_applicable": "draft_doc_type_not_applicable",
    "revision_conflict": "draft_revision_conflict",
    "formal_revision_conflict": "draft_formal_revision_conflict",
    "draft_agent_owned": "draft_agent_owned",
    "script_plan_confirmed": "script_review_script_plan_confirmed",
    "draft_repair_failed": "draft_repair_failed",
}


def _draft_repair(scope: ProjectScope, services: Services) -> DraftRepair:
    return DraftRepair(_draft_workflow(scope, services).ctx)


async def _execute_draft_repair(
    request: RepairDraftRequest, scope: ProjectScope, services: Services
) -> ToolOutcome[DraftRepairResult]:
    outcome = await _run_draft(
        _draft_repair(scope, services).repair(
            request.episode_id, request.doc_type, request.base_revision, request.instructions
        ),
        notice_as_problem=True,
    )
    if outcome.problem is not None and outcome.problem.action is not None:
        # Forward problems with recovery guidance unchanged (truncated output, video configuration, formal units failing promotion preflight): the problem code is the wording key; show the actual failure reason.
        return ToolOutcome(problem=outcome.problem)
    if outcome.problem is not None:
        # Localize task-failure reasons by problem code: use the draft command's error wording key; Agent-facing detail is diagnostic only.
        key = _DRAFT_REPAIR_FAILURE_KEYS.get(outcome.problem.code, "draft_save_failed")
        params = {"episode": request.episode_id} if key == "draft_not_found" else None
        return ToolOutcome(problem=ToolProblem(key, outcome.problem.detail, params=params))
    saved = outcome.value or {}
    adopted = bool(saved.get("adopted"))
    draft = saved.get("draft")
    violations = draft.get("violations") if isinstance(draft, dict) else None
    count = 0 if adopted else len(violations) if isinstance(violations, list) else 0
    message = (
        f"✅ AI 修复后违约已清零，集（id={request.episode_id}）{request.doc_type} 草稿已采用为正式内容"
        if adopted
        else f"AI 修复已写回集（id={request.episode_id}）{request.doc_type} 草稿，仍有 {count} 条违约待处理"
    )
    return ToolOutcome(
        value=DraftRepairResult(
            message=message,
            episode_id=request.episode_id,
            doc_type=request.doc_type,
            adopted=adopted,
            violation_count=count,
        )
    )


class CreateProjectToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(
        description="Unique project identifier containing only letters, digits and hyphens; subsequent tools address the project by this identifier"
    )
    title: str = Field(default="", description="User-visible project title")
    content_mode: ContentMode = Field(
        default="narration",
        description="Content mode: narration for narrated storytelling/commentary, drama for episodic drama, ad for advertisements/short films",
    )
    generation_mode: Literal["storyboard", "reference_video"] = Field(
        default="storyboard",
        description="Generation mode: storyboard generates storyboard images before video; reference_video generates video directly from reference images",
    )
    grid_storyboard: bool = Field(
        default=False, description="Whether to use grid storyboards; unsupported for advertisement/short film projects"
    )
    aspect_ratio: str = Field(default="9:16", description="Aspect ratio, such as 9:16 or 16:9")
    default_duration: int | None = Field(
        default=None,
        gt=0,
        description="Default shot duration in seconds; omitted values are not stored and are determined by video-model capabilities. Unavailable for advertisement/short film projects",
    )
    target_duration: int | None = Field(
        default=None,
        gt=0,
        description="Target finished-video duration in seconds; advertisement/short film projects only",
    )
    brief: str | None = Field(
        default=None,
        description="Creative brief (selling points, audience, etc.); advertisement/short film projects only",
    )
    narration_delivery: NarrationDelivery = Field(
        default=POST_PRODUCTION,
        description=(
            "Narration delivery: post_production supplies voiceover text with subtitles for the creator to record later; use_tts has ArcReel generate TTS narration. Users can change this in project settings after creation"
        ),
    )
    audio_backend: str | SkipJsonSchema[None] = Field(
        default=None,
        description="TTS model in provider/model form; used only for use_tts, defaulting to the global audio model when omitted",
    )
    narration_voice: str | SkipJsonSchema[None] = Field(
        default=None,
        description="Narration voice ID; used only for use_tts, defaulting to the global voice when omitted",
    )
    narration_speed: float | None = Field(
        default=None,
        description="Positive speech-rate multiplier; used only for use_tts. Omission uses the global default; null omits speech rate from the provider request",
    )

    @model_validator(mode="after")
    def validate_mode_fields(self) -> CreateProjectToolRequest:
        if self.content_mode == "ad":
            if self.default_duration is not None:
                raise ValueError("广告/短片项目不持有 default_duration")
            if self.grid_storyboard:
                raise ValueError("广告/短片项目不支持宫格分镜")
        elif self.target_duration is not None or self.brief is not None:
            raise ValueError("target_duration 与 brief 仅广告/短片项目可用")
        return self


class UploadSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str = Field(description="Filename with .txt or .md extension, without directories or a leading dot")
    content: str = Field(description="Text content of the source file")
    on_conflict: OnConflict = Field(
        default="fail",
        description="Conflict handling for an existing filename under source/: fail rejects, replace overwrites, rename automatically renames before writing; not applicable when role=episode",
    )
    role: Literal["whole_source", "episode"] = Field(
        default="whole_source",
        description=(
            "whole_source registers a whole-source file appended to the file list for episode planning to split; episode registers an episode with its own source at the end of playback order, assigns a new episode ID and proceeds directly to per-episode script planning"
        ),
    )
    source_kind: SourceKind | None = Field(
        default=None,
        description=(
            "Source type, effective only for drama projects and ignored for other content modes: novel (default) is adapted by AI; screenplay is the user's finished screenplay, retaining the author's episode divisions, dialogue and voiceover verbatim. Replacing a registered whole-source file with the same name preserves its original type"
        ),
    )
    revision: str | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "For on_conflict=replace on a registered whole-source file affecting split episodes: revision of the affected-episode list returned by the preceding call. Write only after the user confirms that list and this revision is returned unchanged"
        ),
    )


async def list_projects(
    _request: ToolRequest[NoArguments],
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[list[dict[str, Any]]]:
    def _list() -> list[dict[str, Any]]:
        result = []
        for name in sorted(services.projects.list_projects()):
            try:
                project = services.projects.load_project(name)
            except (FileNotFoundError, ValueError):
                continue
            result.append(
                {
                    "name": name,
                    "title": project.get("title", ""),
                    "content_mode": project.get("content_mode"),
                    "generation_mode": project.get("generation_mode"),
                }
            )
        return result

    try:
        return ToolOutcome(value=await asyncio.to_thread(_list))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"list_projects 失败: {exc}"))


_NARRATION_CONFIG_PROBLEMS = {
    "narration_tts_model_required": "use_tts 需要 TTS 模型：传 audio_backend（provider/model），或先让用户配置音频供应商",
    "narration_tts_model_invalid": "audio_backend 不是可用的 TTS 模型，需为音频供应商下的 provider/model",
    "narration_tts_voice_required": "use_tts 需要非空的 narration_voice",
    "narration_tts_speed_invalid": "narration_speed 必须是正的有限数值或 null",
}


async def create_project(
    request: ToolRequest[CreateProjectToolRequest],
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    value = request.value
    try:
        narration = await new_project_narration_fields(
            NarrationSettingsInput(
                delivery=value.narration_delivery,
                audio_backend=value.audio_backend,
                narration_voice=value.narration_voice,
                narration_speed=value.narration_speed,
                provided=frozenset(value.model_fields_set & {"audio_backend", "narration_voice", "narration_speed"}),
            ),
            resolver=services.capabilities,
        )
    except NarrationConfigError as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", _NARRATION_CONFIG_PROBLEMS[exc.code]))

    def _create() -> dict[str, Any]:
        name = services.projects.normalize_project_name(value.name)
        services.projects.create_project(name, content_mode=value.content_mode, publish=False)
        try:
            project = services.projects.create_project_metadata(
                name,
                value.title,
                content_mode=value.content_mode,
                aspect_ratio=value.aspect_ratio,
                default_duration=value.default_duration,
                extras={
                    "generation_mode": value.generation_mode,
                    "grid_storyboard": value.grid_storyboard,
                },
                target_duration=value.target_duration,
                brief=value.brief,
                narration=narration,
            )
        except Exception:
            services.projects.delete_project_directory(name)
            raise
        return {"name": name, "project": project}

    try:
        return ToolOutcome(value=await _run_sync_transaction(_create))
    except FileExistsError as exc:
        return ToolOutcome(problem=ToolProblem("project_exists", str(exc)))
    except ValueError as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"create_project 失败: {exc}"))


class SourceReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    old_text: str = Field(
        min_length=1, description="Source passage to replace; must match verbatim exactly once in the current source"
    )
    new_text: str = Field(description="Replacement text; an empty string deletes the passage")


class EditSourceTextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    filename: str | SkipJsonSchema[None] = Field(
        default=None,
        description="Whole-source filename to edit (a registered filename under source/, such as novel.txt); mutually exclusive with episode_id",
    )
    episode_id: int | SkipJsonSchema[None] = Field(
        default=None,
        gt=0,
        description="Episode ID whose source to edit; only episodes with their own source or no source are accepted. For split episodes, edit the whole-source file instead. Mutually exclusive with filename",
    )
    replacements: list[SourceReplacement] | SkipJsonSchema[None] = Field(
        default=None,
        min_length=1,
        description="Passage edits applied sequentially to the current source; mutually exclusive with text",
    )
    text: str | SkipJsonSchema[None] = Field(
        default=None, description="Complete rewritten source text; mutually exclusive with replacements"
    )
    revision: str | SkipJsonSchema[None] = Field(
        default=None,
        description="When editing a whole-source file affects split episodes: revision of the affected-episode list returned by the preceding call. Write only after user confirmation and return this revision unchanged",
    )

    @model_validator(mode="after")
    def _one_target_one_change(self) -> EditSourceTextRequest:
        if (self.filename is None) == (self.episode_id is None):
            raise ValueError("filename 与 episode_id 须且只能给一个")
        if (self.replacements is None) == (self.text is None):
            raise ValueError("replacements 与 text 须且只能给一个")
        return self


class SourceChangeResult(BaseModel):
    message: str
    #: True means nothing was written; await user confirmation of affected episodes, then call again with ``revision``.
    confirmation_required: bool
    #: Affected episode IDs grouped by category; None when editing an episode with its own source.
    impact: dict[str, list[int]] | None = None
    revision: str | None = None


def _zh(key: str, **kwargs: Any) -> str:
    return i18n_message(key, "zh", **kwargs)


def _source_change_problem(exc: SourceFileChangeError) -> ToolProblem:
    return ToolProblem(exc.code, f"❌ {_zh(f'source_file_change_{exc.code}')}")


async def _run_source_file_change(
    scope: ProjectScope,
    services: Services,
    revision: str | None,
    command: Callable[[bool], SourceFileChangeOutcome],
) -> SourceFileChangeOutcome | ToolProblem:
    """Run a whole-source file-change command (``command(dry_run)``). Before execution with confirmed ``revision``, do not write while episodes to remove or retire still have queued or running tasks."""
    if revision is not None:
        preview = await run_sync_transaction(command, True)
        if preview.revision == revision:
            for episode in (*preview.impact.retired, *preview.impact.removed):
                if await episode_has_active_tasks(services.queue, scope.project_name, episode):
                    return ToolProblem(
                        "source_file_change_tasks_active", f"❌ {_zh('source_file_change_tasks_active')}"
                    )
    return await _run_sync_transaction(command, False)


def _source_change_result(
    outcome: SourceFileChangeOutcome, project_before: Mapping[str, Any], rel: str
) -> SourceChangeResult:
    impact = outcome.impact.to_dict()
    text = render_source_file_impact_text(impact, project_before, _zh)
    if not outcome.applied:
        message = (
            f"⚠️ 改动 {rel} 会波及以下集，尚未写入：\n{text}\n"
            f'请把清单如实告知用户；用户确认后带 revision="{outcome.revision}" 原样重新调用。'
        )
        return SourceChangeResult(message=message, confirmation_required=True, impact=impact, revision=outcome.revision)
    message = f"✅ 已写入 {rel}。" + (f"\n分集账本已随之更新：\n{text}" if text else "")
    return SourceChangeResult(message=message, confirmation_required=False, impact=impact, revision=outcome.revision)


async def upload_source(
    request: ToolRequest[UploadSourceRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    value = request.value

    def _validate() -> None:
        if Path(value.filename).name != value.filename or "\\" in value.filename or value.filename.startswith("."):
            raise ValueError("filename 必须是不含路径的非隐藏文件名")
        suffix = Path(value.filename).suffix.lower()
        if suffix not in {".txt", ".md"}:
            raise UnsupportedFormatError(ext=suffix)
        if not services.projects.project_exists(scope.project_name):
            raise FileNotFoundError(f"项目 '{scope.project_name}' 缺少 project.json")
        if value.role == "whole_source" and is_derived_episode_name(f"{Path(value.filename).stem}.txt"):
            raise ValueError(
                f"文件名 {value.filename} 与集文件 episode_N.txt 同名，整本源文的文件须改名后上传；"
                "逐集原文用 role=episode 上传"
            )

    @contextlib.contextmanager
    def _temporary_source() -> Generator[Path]:
        source_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=Path(value.filename).suffix, delete=False) as source:
                source_path = Path(source.name)
                source.write(value.content.encode("utf-8"))
                source.flush()
            yield source_path
        finally:
            if source_path is not None:
                source_path.unlink(missing_ok=True)  # hard-delete: temp -- Temporary source file uploaded by the Agent

    def _upload_episode() -> dict[str, Any]:
        _validate()
        with _temporary_source() as source_path:
            extracted = SourceLoader.extract(source_path, original_filename=value.filename)
            project_dir = services.projects.get_project_path(scope.project_name)
            with services.projects.locked_source_registration(scope.project_name) as (_dir, project, undo):
                episode = add_own_source_episode(
                    project_dir, project, extracted.text, undo=undo, source_kind=value.source_kind
                )
                described = describe_episode_for_agent(project, episode)
        return {
            "episode_id": episode,
            "episode": described,
            "path": episode_source_relpath(episode),
            "original_filename": value.filename,
            "used_encoding": extracted.used_encoding,
            "chapter_count": extracted.chapter_count,
        }

    def _insert_whole_source(_dry_run: bool) -> tuple[SourceFileChangeOutcome, Any]:
        _validate()
        loaded: dict[str, Any] = {}
        with _temporary_source() as source_path:

            def _write(source_dir: Path, undo: contextlib.ExitStack) -> str:
                if value.on_conflict == "replace":
                    # Move replaced files and original uploads into project trash; restore their original locations if the transaction fails.
                    trash_source_file(source_dir.parent, f"source/{Path(value.filename).stem}.txt", undo)
                result = SourceLoader.load(
                    source_path, source_dir, original_filename=value.filename, on_conflict=value.on_conflict
                )
                # hard-delete: rollback -- Undo this write after source-file write failure
                undo.callback(result.normalized_path.unlink, missing_ok=True)
                if result.raw_path is not None:
                    # hard-delete: rollback -- Undo this write after source-file write failure
                    undo.callback(result.raw_path.unlink, missing_ok=True)
                loaded["result"] = result
                return f"source/{result.normalized_path.name}"

            outcome, _rel = insert_whole_source_file(
                services.projects, scope.project_name, _write, index=None, source_kind=value.source_kind
            )
        return outcome, loaded.get("result")

    def _replacement_text() -> str:
        _validate()
        with _temporary_source() as source_path:
            return SourceLoader.extract(source_path, original_filename=value.filename).text

    try:
        if value.role == "episode":
            return ToolOutcome(value={**await _run_sync_transaction(_upload_episode), "confirmation_required": False})
        normalized_name = f"{Path(value.filename).stem}.txt"
        rel = f"source/{normalized_name}"
        project_before = await asyncio.to_thread(services.projects.load_project, scope.project_name)
        if value.on_conflict == "replace" and rel in whole_source_files(project_before):
            text = await asyncio.to_thread(_replacement_text)
            changed = await _run_source_file_change(
                scope,
                services,
                value.revision,
                lambda dry_run: replace_whole_source_file(
                    services.projects,
                    scope.project_name,
                    normalized_name,
                    text,
                    source_kind=value.source_kind,
                    revision=value.revision,
                    dry_run=dry_run,
                ),
            )
            if isinstance(changed, ToolProblem):
                return ToolOutcome(problem=changed)
            result = _source_change_result(changed, project_before, rel)
            return ToolOutcome(value={**result.model_dump(), "filename": normalized_name, "path": rel})
        _outcome, loaded = await _run_sync_transaction(_insert_whole_source, False)
        return ToolOutcome(
            value={
                "confirmation_required": False,
                "filename": loaded.normalized_path.name,
                "path": f"source/{loaded.normalized_path.name}",
                "original_filename": loaded.original_filename,
                "original_kept": loaded.raw_path is not None,
                "used_encoding": loaded.used_encoding,
                "chapter_count": loaded.chapter_count,
            }
        )
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("project_not_found", str(exc)))
    except SourceFileChangeError as exc:
        return ToolOutcome(problem=_source_change_problem(exc))
    except ValueError as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except UnsupportedFormatError as exc:
        return ToolOutcome(problem=ToolProblem("unsupported_format", str(exc)))
    except FileSizeExceededError as exc:
        return ToolOutcome(problem=ToolProblem("source_too_large", str(exc)))
    except (SourceDecodeError, CorruptFileError) as exc:
        return ToolOutcome(problem=ToolProblem("invalid_source", str(exc)))
    except ConflictError as exc:
        return ToolOutcome(problem=ToolProblem("source_conflict", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"upload_source 失败: {exc}"))


def _apply_replacements(current: str, replacements: Sequence[SourceReplacement]) -> str:
    text = current
    for item in replacements:
        count = text.count(item.old_text)
        if count != 1:
            where = "没有找到" if count == 0 else f"出现了 {count} 次"
            raise ValueError(f"片段「{item.old_text}」在当前原文里{where}，须恰好出现一次；请带上更多上下文")
        text = text.replace(item.old_text, item.new_text, 1)
    return text


async def edit_source_text(
    request: ToolRequest[EditSourceTextRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[SourceChangeResult]:
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    value = request.value
    project_dir = await asyncio.to_thread(services.projects.get_project_path, scope.project_name)

    def _new_text(path: Path) -> str:
        if value.text is not None:
            return value.text
        try:
            current = normalize_source_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            raise ValueError(f"读不到当前原文：{exc}") from exc
        return _apply_replacements(current, value.replacements or ())

    try:
        project_before = await asyncio.to_thread(services.projects.load_project, scope.project_name)
        if value.episode_id is not None:
            episode = value.episode_id
            path = episode_source_path(project_dir, episode)
            text = await asyncio.to_thread(_new_text, path)
            await _run_sync_transaction(set_episode_source_text, services.projects, scope.project_name, episode, text)
            name = describe_episode_for_agent(project_before, episode)
            return ToolOutcome(
                value=SourceChangeResult(
                    message=f"✅ 已写入 {name} 的原文 {episode_source_relpath(episode)}。", confirmation_required=False
                )
            )
        filename = value.filename or ""
        if Path(filename).name != filename or "\\" in filename:
            raise ValueError("filename 必须是 source/ 下的纯文件名")
        rel = f"source/{filename}"
        if rel not in whole_source_files(project_before):
            raise SourceFileChangeError("source_file_not_found", f"整本源文里没有这个文件：{filename}")
        text = await asyncio.to_thread(_new_text, project_dir / rel)
        changed = await _run_source_file_change(
            scope,
            services,
            value.revision,
            lambda dry_run: edit_whole_source_file(
                services.projects, scope.project_name, filename, text, revision=value.revision, dry_run=dry_run
            ),
        )
        if isinstance(changed, ToolProblem):
            return ToolOutcome(problem=changed)
        return ToolOutcome(value=_source_change_result(changed, project_before, rel))
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("project_not_found", str(exc)))
    except SourceFileChangeError as exc:
        return ToolOutcome(problem=_source_change_problem(exc))
    except EpisodeSourceError as exc:
        return ToolOutcome(problem=ToolProblem(exc.code, f"❌ 修改集原文失败：{exc}"))
    except ValueError as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("edit_source_text", exc))


async def get_workflow_plan(
    request: ToolRequest[WorkflowPlanRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[WorkflowPlan]:
    try:
        plan = await services.workflow_planner.get_plan(
            scope.project_name,
            request.value,
            user_id=_caller.user_id,
            queue=services.queue,
            config_resolver=services.capabilities,
        )
    except WorkflowRequestError as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_workflow_plan 失败: {exc}"))
    return ToolOutcome(value=plan)


async def get_video_capabilities(
    _request: ToolRequest[NoArguments],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    try:
        project = await asyncio.to_thread(services.projects.load_project, scope.project_name)
        payload = await services.capabilities.video_capabilities_for_project(project)
        payload["duration_constraints"] = duration_constraints_payload(
            await capability_request_facts(
                project,
                generation_type=video_bucket_for_generation_mode(caps_generation_mode(project)),
                config_resolver=services.capabilities,
            )
        )
        await annotate_reference_unit_tiers(
            payload,
            project,
            config_resolver=services.capabilities,
            projects=services.projects,
            project_name=scope.project_name,
        )
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("project_not_found", f"项目未找到或缺 project.json: {exc}"))
    except VideoRequestFactsError as exc:
        failure = exc.failure
        return ToolOutcome(
            problem=ToolProblem(
                failure.code,
                f"无法解析视频模型能力: {failure.summary()}",
                action=failure.action,
                params=failure.parameters(),
            )
        )
    except ModelNotInCatalogError as exc:
        return ToolOutcome(problem=model_not_in_catalog_problem(exc))
    except ValueError as exc:
        return ToolOutcome(problem=ToolProblem("capabilities_unresolved", f"无法解析视频模型能力: {exc}"))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_video_capabilities 失败: {exc}"))
    return ToolOutcome(value=payload)


def _script_edit_failure(
    request: PatchEpisodeScriptRequest,
    current: dict[str, Any],
    *,
    code: str,
    reason: str,
    next_action: str,
    operation_index: int | None = None,
    unit_id: str | None = None,
    location: tuple[str | int, ...] = (),
) -> ScriptBatchEditResult:
    revision = script_revision(current)
    episode = current.get("episode")
    return ScriptBatchEditResult(
        success=False,
        script=request.script,
        episode=episode if isinstance(episode, int) and not isinstance(episode, bool) else None,
        before_revision=revision,
        revision=revision,
        problems=(
            ScriptBatchEditProblem(
                code=code,
                operation_index=operation_index,
                unit_id=unit_id,
                locations=(ScriptBatchEditLocation(path=location),) if location else (),
                reason=reason,
                next_action=next_action,
            ),
        ),
    )


def _project_patch_operations(
    script: dict[str, Any],
    operations: list[PatchEpisodeScriptOperation],
) -> tuple[list[dict[str, Any]], list[int], frozenset[int]]:
    """Project public structural ops onto the existing transactional command."""
    preview = copy.deepcopy(script)
    projected: list[dict[str, Any]] = []
    source_indexes: list[int] = []
    fresh_insert_indexes: set[int] = set()

    def append(operation: dict[str, Any], source_index: int, *, fresh_insert: bool = False) -> None:
        if fresh_insert:
            fresh_insert_indexes.add(len(projected))
        projected.append(operation)
        source_indexes.append(source_index)

    for index, operation in enumerate(operations):

        def apply(
            action: Callable[[], Any],
            *,
            location: tuple[str | int, ...],
            unit_id: str | None = None,
            operation_index: int = index,
        ) -> Any:
            try:
                return action()
            except ScriptEditError as exc:
                exc.params.update(operation_index=operation_index, location=location, unit_id=unit_id)
                raise

        if isinstance(operation, PatchUpdateOperation):
            item_id = operation.id
            for field, value in operation.fields.items():
                apply(
                    lambda field=field, value=value, item_id=item_id: patch_field(preview, item_id, field, value),
                    location=("fields", *field.split(".")),
                    unit_id=item_id,
                )
            append(operation.model_dump(mode="python"), index)
            continue

        if isinstance(operation, PatchInsertOperation):
            insert_after_id = operation.after_id
            new_item = operation.item
            apply(
                lambda insert_after_id=insert_after_id, new_item=new_item: insert_segment(
                    preview, insert_after_id, new_item
                ),
                location=("after_id",),
                unit_id=insert_after_id,
            )
            items, id_field, _kind = resolve_items(preview)
            anchor = (
                -1
                if insert_after_id is None
                else next(i for i, item in enumerate(items) if str(item.get(id_field)) == insert_after_id)
            )
            append(
                {"op": "insert_after", "after_id": insert_after_id, "item": copy.deepcopy(items[anchor + 1])},
                index,
                fresh_insert=True,
            )
            continue

        if isinstance(operation, PatchMoveOperation):
            item_id = operation.id
            move_after_id = operation.after_id
            items, id_field, _kind = resolve_items(preview)
            if not any(str(item.get(id_field)) == item_id for item in items):
                raise ScriptEditError(
                    f"未找到 id={item_id!r} 的分镜", operation_index=index, location=("id",), unit_id=item_id
                )
            apply(
                lambda item_id=item_id, move_after_id=move_after_id: move_segment(preview, item_id, move_after_id),
                location=("after_id",),
                unit_id=item_id,
            )
            append({"op": "move_after", "id": item_id, "after_id": move_after_id}, index)
            continue

        if isinstance(operation, PatchRemoveOperation):
            item_id = operation.id
            apply(lambda item_id=item_id: remove_segment(preview, item_id), location=("id",), unit_id=item_id)
            append(operation.model_dump(mode="python"), index)
            continue

        item_id = operation.id
        parts = operation.parts
        items, id_field, _kind = resolve_items(preview)
        original_index = next(
            (i for i, item in enumerate(items) if str(item.get(id_field)) == item_id),
            None,
        )
        if original_index is None:
            raise ScriptEditError(
                f"未找到 id={item_id!r} 的分镜",
                operation_index=index,
                location=("id",),
                unit_id=item_id,
            )
        previous_id = str(items[original_index - 1].get(id_field)) if original_index else None
        apply(
            lambda item_id=item_id, parts=parts: split_segment(preview, item_id, parts),
            location=("parts",),
            unit_id=item_id,
        )
        items, id_field, _kind = resolve_items(preview)
        anchor = next(i for i, item in enumerate(items) if str(item.get(id_field)) == item_id)
        generated = items[anchor : anchor + len(parts)]
        append({"op": "remove", "id": item_id}, index)
        split_after_id = previous_id
        for part_index, item in enumerate(generated):
            append(
                {"op": "insert_after", "after_id": split_after_id, "item": copy.deepcopy(item)},
                index,
                fresh_insert=part_index > 0,
            )
            split_after_id = str(item[id_field])

    return projected, source_indexes, frozenset(fresh_insert_indexes)


def _remap_operation_indexes(result: ScriptBatchEditResult, source_indexes: list[int]) -> ScriptBatchEditResult:
    if not result.problems:
        return result
    remapped: list[ScriptBatchEditProblem] = []
    for problem in result.problems:
        internal_index = problem.operation_index
        public_index = (
            source_indexes[internal_index]
            if internal_index is not None and 0 <= internal_index < len(source_indexes)
            else internal_index
        )
        locations: list[ScriptBatchEditLocation] = []
        for location in problem.locations:
            path = location.path
            if len(path) >= 2 and path[0] == "operations" and isinstance(path[1], int):
                path = (path[0], public_index if public_index is not None else path[1], *path[2:])
            locations.append(location.model_copy(update={"path": path}))
        remapped.append(problem.model_copy(update={"operation_index": public_index, "locations": tuple(locations)}))
    return result.model_copy(update={"problems": tuple(remapped)})


def _patch_episode_script_sync(
    request: ToolRequest[PatchEpisodeScriptRequest],
    scope: ProjectScope,
    services: Services,
) -> ToolOutcome[ScriptBatchEditResult]:
    try:
        current = services.projects.load_script(scope.project_name, request.value.script)
    except FileNotFoundError:
        return ToolOutcome(problem=ToolProblem("script_not_found", f"剧本不存在: {request.value.script}"))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"patch_episode_script 失败: {exc}"))

    if request.value.base_revision != script_revision(current):
        return ToolOutcome(
            value=_script_edit_failure(
                request.value,
                current,
                code="revision_conflict",
                reason="revision_mismatch",
                next_action="refresh_script",
            )
        )

    try:
        projected, source_indexes, fresh_insert_indexes = _project_patch_operations(current, request.value.operations)
    except ScriptEditError as exc:
        latest = services.projects.load_script(scope.project_name, request.value.script)
        if request.value.base_revision != script_revision(latest):
            return ToolOutcome(
                value=_script_edit_failure(
                    request.value,
                    latest,
                    code="revision_conflict",
                    reason="revision_mismatch",
                    next_action="refresh_script",
                )
            )
        raw_operation_index = exc.params.get("operation_index")
        operation_index = raw_operation_index if isinstance(raw_operation_index, int) else None
        raw_location = exc.params.get("location")
        operation_location = raw_location if isinstance(raw_location, tuple) else ()
        raw_unit_id = exc.params.get("unit_id")
        unit_id = raw_unit_id if isinstance(raw_unit_id, str) else None
        return ToolOutcome(
            value=_script_edit_failure(
                request.value,
                current,
                code="operation_invalid",
                reason="operation_invalid",
                next_action="fix_operation",
                operation_index=operation_index,
                unit_id=unit_id,
                location=("operations", operation_index, *operation_location) if operation_index is not None else (),
            )
        )

    command = ScriptBatchEditCommand.model_validate(
        {
            "script": request.value.script,
            "expected_revision": request.value.base_revision,
            "operations": projected,
        }
    )
    result = ScriptBatchEditor(services.projects).execute(
        scope.project_name,
        command,
        fresh_insert_indexes=fresh_insert_indexes,
    )
    return ToolOutcome(value=_remap_operation_indexes(result, source_indexes))


async def patch_episode_script(
    request: ToolRequest[PatchEpisodeScriptRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ScriptPatchResult]:
    outcome = await _run_sync_transaction(_patch_episode_script_sync, request, scope, services)
    if outcome.value is None:
        return ToolOutcome(problem=outcome.problem)
    return ToolOutcome(value=_script_patch_result(request.value, outcome.value))


ASSET_TABLES = tuple(spec.bucket_key for spec in ASSET_SPECS.values())
MERGEABLE_ASSET_TABLES = tuple(ASSET_SPECS[asset_type].bucket_key for asset_type in MERGEABLE_ASSET_TYPES)
PROJECT_SETTINGS = (
    EPISODE_TARGET_UNITS_FIELD,
    EPISODE_TARGET_DURATION_FIELD,
    "source_language",
    "brief",
    "narration_voice",
    "narration_speed",
    "character_voice_binding",
)
PROJECT_OVERVIEW_FIELDS = ("synopsis", "genre", "theme", "world_setting")
EPISODE_META_FIELDS = ("title",)

_SOURCE_LANGUAGE_VALUES = ("zh", "en", "vi", "ru")
_POSITIVE_INT_SETTINGS = (EPISODE_TARGET_UNITS_FIELD,)


class ToolMessage(BaseModel):
    message: str


class PlanEpisodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    instructions: str | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "Optional verbatim additional episode-planning instructions (for example, split along chapter boundaries), injected unchanged into the planning prompt's Additional instructions section; "
            f"the body expresses how strictly to follow them; state hard constraints explicitly. Repeat them in every batch call. Omitted/blank means absent; maximum {MAX_INSTRUCTIONS_LEN} characters"
        ),
    )

    @field_validator("instructions")
    @classmethod
    def _validate_instructions(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) > MAX_INSTRUCTIONS_LEN:
            raise ValueError(f"instructions 过长（{len(value)} 字符，上限 {MAX_INSTRUCTIONS_LEN}），请精简后重试")
        return value.strip() or None


class PlanEpisodesResult(ToolMessage):
    episodes: list[dict[str, Any]]
    cursor: dict[str, Any] | None
    source_exhausted: bool
    total_planned: int
    ledger_stats: dict[str, Any] | None


class ResetEpisodePlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: PositiveEpisode | SkipJsonSchema[None] = Field(
        default=None,
        description=(
            "Split episode ID from which to reset; omit or use the first split episode in playback order for a full reset. Other split episodes trigger a partial reset preserving earlier episodes in playback order"
        ),
    )
    confirm_consumed: bool = Field(
        default=False,
        strict=True,
        description="Set true only after explaining the affected consumed episodes to the user and obtaining confirmation. Omit on the first call so the tool first returns the affected list",
    )


class ResetEpisodePlanningResult(ToolMessage):
    confirmation_required: bool
    removed_episodes: list[int] = Field(default_factory=list)
    deleted_files: list[str] = Field(default_factory=list)
    archived_files: list[str] | list[tuple[str, str]] = Field(default_factory=list)
    consumed_episodes: list[int] = Field(default_factory=list)
    #: Episodes converted to sourceless and marked stale (confirmation lists episodes that will be converted).
    retired_episodes: list[int] = Field(default_factory=list)


class PatchProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str | None = Field(
        default=None,
        description=f"(Asset upsert branch, supplied with entries) Asset table; one of {list(ASSET_TABLES)}",
        json_schema_extra={"enum": [*ASSET_TABLES, None]},
    )
    entries: dict[str, Any] | None = Field(
        default=None,
        description=(
            "(Asset upsert branch) A nonempty { name: { description, voice_style, ... } } map; insert missing names or merge changed fields into existing names. Character entries may include derivatives: { derivative_name: { description } } for alternative appearances. Merge by derivative name: update same-name descriptions, add new names and preserve unmentioned derivatives. Describe changes relative to the base character"
        ),
    )
    settings: dict[str, Any] | None = Field(
        default=None,
        description=f"(Settings write branch) Top-level field map; keys must be in {list(PROJECT_SETTINGS)}; null clears the field",
    )
    overview: dict[str, Any] | None = Field(
        default=None,
        description=(
            f"(Project overview branch) Overview field map; keys must be in {list(PROJECT_OVERVIEW_FIELDS)}. Update only supplied fields; "
            "create the overview if absent"
        ),
    )

    @model_validator(mode="after")
    def _validate_shape(self) -> PatchProjectRequest:
        has_upsert = self.table is not None or self.entries is not None
        branches = (has_upsert, self.settings is not None, self.overview is not None)
        if sum(branches) > 1:
            raise ValueError("table/entries、settings、overview 三选一,不能同时给出多个")
        if not any(branches):
            raise ValueError("必须提供 table+entries(资产 upsert)、settings(顶层字段)或 overview(项目概述)之一")
        if has_upsert:
            if self.table is None or self.entries is None:
                raise ValueError("资产 upsert 分支必须同时提供 table 和 entries")
            if self.table not in ASSET_TABLES:
                raise ValueError(f"table 必须是 {list(ASSET_TABLES)} 之一")
            if not self.entries:
                raise ValueError("entries 必须是非空 { 名称: 字段对象 } 映射")
        if self.settings is not None and not self.settings:
            raise ValueError("settings 必须是非空 { 字段名: 值 } 映射")
        if self.overview is not None and not self.overview:
            raise ValueError("overview 必须是非空 { 字段名: 值 } 映射")
        return self


class PatchProjectResult(ToolMessage):
    operation: Literal["assets", "settings", "overview"]
    changes: dict[str, Any]


class PatchEpisodeMetaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(description="Script filename without directories, such as episode_1.json")
    field: Literal["title"] = Field(
        description=f"Top-level script field to edit; allowed fields: {list(EPISODE_META_FIELDS)}"
    )
    value: str = Field(
        description="New value; title must be a nonempty string and leading/trailing whitespace is trimmed"
    )

    @field_validator("script")
    @classmethod
    def _validate_script(cls, value: str) -> str:
        if not value or "/" in value or "\\" in value or value in (".", ".."):
            raise ValueError(f"script 必须是纯文件名，禁止路径分隔符: {value!r}")
        return value

    @field_validator("value")
    @classmethod
    def _validate_value(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title 必须是非空字符串")
        return value.strip()


class PatchEpisodeMetaResult(ToolMessage):
    script: str
    field: str
    value: str


class RenameAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str = Field(
        description=f"Asset table; one of {list(ASSET_TABLES)}", json_schema_extra={"enum": list(ASSET_TABLES)}
    )
    old_name: str = Field(description="Existing asset name")
    new_name: str = Field(
        description="New asset name; reject the entire operation if it conflicts with another name in the same table after NFC normalization"
    )

    @field_validator("table")
    @classmethod
    def _validate_table(cls, value: str) -> str:
        if value not in ASSET_TABLES:
            raise ValueError(f"table 必须是 {list(ASSET_TABLES)} 之一")
        return value


class RenameAssetResult(ToolMessage):
    table: str
    old_name: str
    new_name: str
    episodes: int
    references: int
    files: int


class MergeAssetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    table: str = Field(
        description=f"Asset table; one of {list(MERGEABLE_ASSET_TABLES)}",
        json_schema_extra={"enum": list(MERGEABLE_ASSET_TABLES)},
    )
    source: str = Field(description="Merge source: removed from the asset table after merging")
    target: str = Field(description="Merge target: another asset in the same table, kept unchanged")
    as_derivative: bool = Field(
        default=False,
        description="characters only: true merges source as a derivative of target, using the source name and description; the derivative sheet awaits generation",
    )
    dry_run: bool = Field(
        default=False, description="When true, return only per-episode impacts without making changes"
    )

    @field_validator("table")
    @classmethod
    def _validate_table(cls, value: str) -> str:
        if value not in MERGEABLE_ASSET_TABLES:
            raise ValueError(f"table 必须是 {list(MERGEABLE_ASSET_TABLES)} 之一")
        return value


class MergeAssetEpisodeImpact(BaseModel):
    episode_id: int
    script_plan: int
    script: int
    draft: int
    prompt_text: int
    speaker: int
    storyboards: int
    videos: int


class MergeAssetResult(ToolMessage):
    table: str
    source: str
    target: str
    as_derivative: bool
    dry_run: bool
    references: int
    aliases_added: list[str]
    derivative_created: str | None
    derivatives_moved: list[str]
    derivatives_folded: list[str]
    episodes: list[MergeAssetEpisodeImpact]


class RetryProjectMigrationResult(ToolMessage):
    workflow_plan: WorkflowPlan


class CompleteScriptPlanRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    episode_id: int = Field(strict=True, ge=1, description=EPISODE_ID_DESCRIPTION)
    expected_stale_script_plan_revision: str | None = Field(
        description=(
            "Rebuild baseline copied unchanged from the production plan's next_action.args.expected_stale_script_plan_revision (may be null, which must also be supplied explicitly; sha256-v1: prefixed and bare hexadecimal forms are equivalent); reject if it differs from the project record"
        )
    )


class CompleteScriptPlanRebuildResult(BaseModel):
    episode_id: int
    script_plan_revision: str


def _unexpected(name: str, exc: BaseException) -> ToolProblem:
    return ToolProblem("internal_error", f"{name} 失败: {exc}")


def _migration_tool_problem(failure: MigrationFailureRecord) -> ToolProblem:
    payload = migration_problem(failure).model_dump(mode="json")
    return ToolProblem(
        code=str(payload["code"]),
        detail=str(payload["detail"]),
        action=str(payload["action"]),
        params=payload.get("params"),
    )


async def migration_gate(scope: ProjectScope, services: Services) -> ToolProblem | None:
    failure = await asyncio.to_thread(project_migration_failure, scope.project_name, services.projects)
    return _migration_tool_problem(failure) if failure is not None else None


def _ledger_stats_payload(stats: LedgerStats | None) -> dict[str, Any] | None:
    if stats is None:
        return None
    volume = stats.target_volume
    return {
        "total_episodes": stats.total_episodes,
        "smallest": stats.smallest,
        "median_units": stats.median_units,
        "target_units": volume.units if volume is not None else None,
        "target_units_source": volume.source if volume is not None else None,
        "target_seconds": volume.seconds if volume is not None else None,
    }


def _render_ledger_stats(stats: LedgerStats, project: Mapping[str, Any]) -> list[str]:
    lines = [f"累计总集数：{stats.total_episodes}"]
    if stats.smallest:
        smallest = "、".join(
            f"{describe_episode_for_agent(project, num)}（约 {units}）" for num, units in stats.smallest
        )
        lines.append(f"体量最小的几集：{smallest}")
    if stats.median_units is not None:
        lines.append(f"全账本体量中位数：约 {stats.median_units}")
    if (volume := stats.target_volume) is not None:
        # Distinguish converted values from explicit settings: when the main Agent checks volume discrepancies, derived values add speech-rate estimation
        # and must not be treated as user-specified hard requirements.
        derived = f"（按单集目标时长 {volume.seconds} 秒折算）" if volume.source == "duration" else ""
        lines.append(f"每集目标体量设置：约 {volume.units}{derived}")
    lines.append("若用户给过总集数、按章节对齐等结构性偏好，请对照以上分布核实，有偏差须向用户明确说明。")
    return lines


def _format_plan(result: PlanResult, project: Mapping[str, Any], *, gap: bool = False) -> str:
    # When planning an unsplit gap, ``source_exhausted`` means only that this range is planned; later parts of the whole source may remain unplanned.
    done = "这段未切分的原文已全部规划完毕" if gap else "源文已全部规划完毕"
    if not result.episodes and result.source_exhausted:
        lines = [f"{done}，没有可规划的新内容。"]
        if result.ledger_stats is not None:
            lines += _render_ledger_stats(result.ledger_stats, project)
        return "\n".join(lines)
    lines = [f"✅ 已规划 {len(result.episodes)} 集："]
    for episode in result.episodes:
        status_note = "（stale，需重做下游产物）" if episode.ledger_status == "stale" else ""
        lines.append(
            f"- {describe_episode_for_agent(project, episode.episode)}{status_note}"
            f"｜体量约 {episode.reading_units}｜钩子：{episode.hook}"
        )
        lines.append(f"  首句：{episode.first_sentence}")
        lines.append(f"  尾句：{episode.last_sentence}")
    if result.source_exhausted:
        lines.append(f"{done}。")
    elif result.cursor:
        lines.append(f"下一批规划起点：{result.cursor.get('source_file')} 偏移 {result.cursor.get('offset')}")
    if result.ledger_stats is not None:
        lines += _render_ledger_stats(result.ledger_stats, project)
    else:
        lines.append(f"累计已规划 {result.total_planned} 集。")
    lines.append(
        "请把以上摘要（含每集首句与尾句原文）展示给用户做批级审阅；需要调整时先调用 reset_episode_planning 退回到"
        "最早受影响的集，再带 instructions 重新调用本工具。"
    )
    return "\n".join(lines)


#: Running windows (task IDs) for which stop was requested. A stop may arrive before the next window is enqueued: this window was just claimed, or contains the ending
#: and must await the model's batch response to decide whether another window is needed. These windows enqueue no successor. Worker and stop request share the service process.
_STOPPED_PLANNING_WINDOWS: set[str] = set()


@dataclass(frozen=True, slots=True)
class _PlanningChain:
    """Currently executing window in Web's planning chain: if source remains after this batch, enqueue the next window in another slot dependent on this one's success. If this window lacks the ending, enqueue before requesting the model, allowing stop to cancel it; a stopped window never enqueues a successor that was not already queued."""

    task: Mapping[str, Any]
    services: Services

    async def queue_next_window(self) -> None:
        if str(self.task["task_id"]) in _STOPPED_PLANNING_WINDOWS:
            return
        slot = (
            EPISODE_PLANNING_NEXT_SLOT
            if self.task.get("resource_id") == EPISODE_PLANNING_SLOT
            else EPISODE_PLANNING_SLOT
        )
        # The executing worker enqueues the next window, so the worker must be online.
        await self.services.queue.enqueue_task(
            project_name=str(self.task["project_name"]),
            task_type=_TEXT_EPISODE_PLAN,
            media_type="text",
            resource_id=slot,
            payload=dict(self.task.get("payload") or {}),
            source=str(self.task.get("source") or "webui"),
            user_id=str(self.task.get("user_id") or DEFAULT_USER_ID),
            dependency_task_id=str(self.task["task_id"]),
        )


async def _execute_plan_episodes(
    request: ToolRequest[PlanEpisodesRequest],
    scope: ProjectScope,
    services: Services,
    *,
    planner_cls: type[EpisodePlanner] = EpisodePlanner,
    chain: _PlanningChain | None = None,
    gap: tuple[str, int] | None = None,
) -> ToolOutcome[Any]:
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    try:
        planner = await planner_cls.create(
            await asyncio.to_thread(services.projects.get_project_path, scope.project_name)
        )
        if chain is None:
            result = await planner.plan(instructions=request.value.instructions, gap=gap)
        else:
            result = await planner.plan(
                instructions=request.value.instructions, on_more_to_plan=chain.queue_next_window, gap=gap
            )
    except TextOutputTruncatedError as exc:
        return ToolOutcome(problem=truncation_problem(exc))
    except ModelNotInCatalogError as exc:
        return ToolOutcome(problem=model_not_in_catalog_problem(exc))
    except NoCutPointError as exc:
        return ToolOutcome(
            problem=ToolProblem(
                "episode_planning_no_cut_point",
                str(exc),
                action=GenerationAction.FIX_INPUT,
                params={"source_file": exc.source_file, "offset": exc.offset},
            )
        )
    except Exception as exc:
        # Unexpected exceptions such as provider-call failures share planning's failure code. UI shows generic failure in the user's language; detail retains the reason for the Agent.
        return ToolOutcome(problem=ToolProblem("episode_planning_failed", str(exc) or type(exc).__name__))
    finally:
        if chain is not None:
            _STOPPED_PLANNING_WINDOWS.discard(str(chain.task["task_id"]))
    project = await asyncio.to_thread(services.projects.load_project, scope.project_name)
    value = PlanEpisodesResult(
        message=_format_plan(result, project, gap=gap is not None),
        episodes=[
            {
                "episode_id": episode.episode,
                "position": episode_position(project, episode.episode),
                "title": episode.title,
                "hook": episode.hook,
                "reading_units": episode.reading_units,
                "ledger_status": episode.ledger_status,
                "first_sentence": episode.first_sentence,
                "last_sentence": episode.last_sentence,
            }
            for episode in result.episodes
        ],
        cursor=result.cursor,
        source_exhausted=result.source_exhausted,
        total_planned=result.total_planned,
        ledger_stats=_ledger_stats_payload(result.ledger_stats),
    )
    return ToolOutcome(value=value)


def _plan_episodes_preflight(projects: ProjectManager, project_name: str) -> None:
    """AI episode-planning admission: whole source exists; read the same source and call the same predicate as production status."""
    project = projects.load_project(project_name)
    source = compute_source_revision(projects.get_project_path(project_name), project, SourceScope(kind="all"))
    require_admitted(
        "plan_episodes",
        admit_plan_episodes(
            project.get("content_mode"),
            whole_source=whole_source_present(planning_docs(project, source)),
            replan_pending=replan_candidate(project) is not None,
        ),
    )


async def _plan_episodes_gate(scope: ProjectScope, services: Services) -> ToolProblem | None:
    if problem := await migration_gate(scope, services):
        return problem
    try:
        await asyncio.to_thread(_plan_episodes_preflight, services.projects, scope.project_name)
    except OperationNotAdmittedError as exc:
        return _not_admitted_problem(exc)
    return None


async def plan_episodes(
    request: ToolRequest[PlanEpisodesRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
    *,
    planner_cls: type[EpisodePlanner] = EpisodePlanner,
) -> ToolOutcome[Any]:
    """Plan one batch: read a window from the planning start and submit episodes containing complete story arcs."""
    if problem := await _plan_episodes_gate(scope, services):
        return ToolOutcome(problem=problem)
    if planner_cls is not EpisodePlanner:
        return await _execute_plan_episodes(request, scope, services, planner_cls=planner_cls)
    return await _submit_text_task(
        task_type=_TEXT_EPISODE_PLAN,
        operation="plan_episodes",
        unit_id=EPISODE_PLANNING_SLOT,
        payload=request.value.model_dump(mode="json"),
        scope=scope,
        caller=caller,
        services=services,
        conflict_resource_ids=EPISODE_PLANNING_SLOTS,
    )


async def start_episode_planning(
    request: ToolRequest[PlanEpisodesRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
    *,
    gap: tuple[str, int] | None = None,
) -> ToolOutcome[Any]:
    """AI episode planning: process windows from the planning start to the whole source's ending, each a queued text task.

    Completed windows commit independently. After stopping (:func:`stop_episode_planning`) or window failure, retain extracted episodes; another call resumes at the ledger-derived planning start. Additional instructions travel in every window's task payload, never into the project.

    ``gap`` is an unsplit range's endpoint ``(源文件, 偏移)``: planning that range processes windows only to its ending. Insert new episodes by source position without replacing any episodes."""
    if problem := await _plan_episodes_gate(scope, services):
        return ToolOutcome(problem=problem)
    payload: dict[str, Any] = {**request.value.model_dump(mode="json"), "continue_to_end": True}
    if gap is not None:
        payload["gap"] = {"source_file": gap[0], "end": gap[1]}
    return await _submit_text_task(
        task_type=_TEXT_EPISODE_PLAN,
        operation="plan_episodes",
        unit_id=EPISODE_PLANNING_SLOT,
        payload=payload,
        scope=scope,
        caller=caller,
        services=services,
        conflict_resource_ids=EPISODE_PLANNING_SLOTS,
    )


class ReplanWindowResult(ToolMessage):
    #: Episodes appended in this batch, total candidate episodes, and whether the candidate now covers the whole source's ending.
    planned: int
    total: int
    complete: bool


def _replan_refused(exc: ReplanError) -> ToolProblem:
    return ToolProblem("episode_replan_refused", f"❌ 重新规划未能执行：{exc}", params={"reason": exc.code})


async def _active_planning_windows(
    scope: ProjectScope, caller: CallerContext, services: Services
) -> list[dict[str, Any]]:
    return await services.queue.get_active_tasks_for_resources(
        project_name=scope.project_name,
        task_type=_TEXT_EPISODE_PLAN,
        resource_ids=list(EPISODE_PLANNING_SLOTS),
        user_id=caller.user_id,
    )


async def episode_planning_active(scope: ProjectScope, caller: CallerContext, services: Services) -> bool:
    """This project has queued or running episode planning, including candidate generation for replanning."""
    return bool(await _active_planning_windows(scope, caller, services))


async def start_episode_replan(
    request: ToolRequest[PlanEpisodesRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
    *,
    episode: int,
    dry_run: bool = False,
) -> ToolOutcome[Any]:
    """Replan starting from this episode: register a candidate and generate windows to the whole source's ending, each a queued text task.

    Persist the candidate without changing the episode ledger; additional instructions travel in window payloads. ``dry_run`` returns only the replan scope (:class:`lib.episode.episode_replan.ReplanScope`), without registering a candidate. Reject if a candidate already exists or episode planning is in progress."""
    if problem := await _plan_episodes_gate(scope, services):
        return ToolOutcome(problem=problem)
    project_path = await asyncio.to_thread(services.projects.get_project_path, scope.project_name)
    try:
        replan = await asyncio.to_thread(
            lambda: replan_scope(project_path, services.projects.load_project(scope.project_name), episode)
        )
    except ReplanError as exc:
        return ToolOutcome(problem=_replan_refused(exc))
    if dry_run:
        return ToolOutcome(value=replan)
    if active := await _active_planning_windows(scope, caller, services):
        return ToolOutcome(
            problem=ToolProblem(
                "generation_active_task_conflict",
                "generation_active_task_conflict",
                action=GenerationAction.WAIT_FOR_TASK,
                params={"task_id": active[0]["task_id"], "status": active[0]["status"]},
            )
        )
    try:
        candidate_id = await _run_sync_transaction(
            create_replan_candidate, project_path, episode=episode, instructions=request.value.instructions
        )
    except ReplanError as exc:
        return ToolOutcome(problem=_replan_refused(exc))
    outcome = await _submit_replan_window(candidate_id, request.value.instructions, scope, caller, services)
    if outcome.problem is not None:
        # First window failed to enqueue: the candidate is still empty; remove it rather than leaving an empty candidate that blocks planning.
        with contextlib.suppress(ReplanError):
            await _run_sync_transaction(discard_replan_candidate, project_path, candidate_id)
    return outcome


async def continue_episode_replan(
    candidate_id: str, scope: ProjectScope, caller: CallerContext, services: Services
) -> ToolOutcome[Any]:
    """Resume a stopped candidate from its ending through windows to the whole source's ending, reusing its original additional instructions.

    Reject absent, ending-covered or outdated candidates, and ongoing episode planning. The candidate itself blocks ordinary episode-planning admission, so do not use that admission here."""
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    if active := await _active_planning_windows(scope, caller, services):
        return ToolOutcome(
            problem=ToolProblem(
                "generation_active_task_conflict",
                "generation_active_task_conflict",
                action=GenerationAction.WAIT_FOR_TASK,
                params={"task_id": active[0]["task_id"], "status": active[0]["status"]},
            )
        )
    project_path = await asyncio.to_thread(services.projects.get_project_path, scope.project_name)
    try:
        instructions = await _run_sync_transaction(resume_replan_candidate, project_path, candidate_id)
    except ReplanError as exc:
        return ToolOutcome(problem=_replan_refused(exc))
    return await _submit_replan_window(candidate_id, instructions, scope, caller, services)


async def _submit_replan_window(
    candidate_id: str, instructions: str | None, scope: ProjectScope, caller: CallerContext, services: Services
) -> ToolOutcome[Any]:
    payload: dict[str, Any] = {
        **PlanEpisodesRequest(instructions=instructions).model_dump(mode="json"),
        "continue_to_end": True,
        "replan": candidate_id,
    }
    return await _submit_text_task(
        task_type=_TEXT_EPISODE_PLAN,
        operation="plan_episodes",
        unit_id=EPISODE_PLANNING_SLOT,
        payload=payload,
        scope=scope,
        caller=caller,
        services=services,
        conflict_resource_ids=EPISODE_PLANNING_SLOTS,
    )


async def _execute_replan_window(
    candidate_id: str,
    instructions: object,
    scope: ProjectScope,
    services: Services,
    *,
    planner_cls: type[EpisodePlanner],
    chain: _PlanningChain,
) -> ToolOutcome[Any]:
    """One replanning window: take a window from the candidate's ending, append resulting episodes and enqueue another window if unplanned source remains.

    On missing cut points or errors, record interruption reasons on the candidate and retain the generated portion."""
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    outcome = await _draft_replan_window(
        candidate_id, instructions, scope, services, planner_cls=planner_cls, chain=chain
    )
    if outcome.problem is not None:
        reason = "no_cut_point" if outcome.problem.code == "episode_planning_no_cut_point" else "failed"
        await _run_sync_transaction(
            lambda: record_replan_interruption(
                services.projects.get_project_path(scope.project_name), candidate_id, reason
            )
        )
    return outcome


async def _draft_replan_window(
    candidate_id: str,
    instructions: object,
    scope: ProjectScope,
    services: Services,
    *,
    planner_cls: type[EpisodePlanner],
    chain: _PlanningChain,
) -> ToolOutcome[Any]:
    planning_instructions = instructions if isinstance(instructions, str) else None
    try:
        planner = await planner_cls.create(
            await asyncio.to_thread(services.projects.get_project_path, scope.project_name)
        )
        result: CandidatePlanResult = await planner.plan_candidate(
            candidate_id, planning_instructions, on_more_to_plan=chain.queue_next_window
        )
    except TextOutputTruncatedError as exc:
        return ToolOutcome(problem=truncation_problem(exc))
    except ModelNotInCatalogError as exc:
        return ToolOutcome(problem=model_not_in_catalog_problem(exc))
    except NoCutPointError as exc:
        return ToolOutcome(
            problem=ToolProblem(
                "episode_planning_no_cut_point",
                str(exc),
                action=GenerationAction.FIX_INPUT,
                params={"source_file": exc.source_file, "offset": exc.offset},
            )
        )
    except Exception as exc:
        # Unexpected exceptions such as provider-call failures share planning's failure code. UI shows generic failure in the user's language; detail retains the reason for the Agent.
        return ToolOutcome(problem=ToolProblem("episode_planning_failed", str(exc) or type(exc).__name__))
    finally:
        _STOPPED_PLANNING_WINDOWS.discard(str(chain.task["task_id"]))
    done = "，已覆盖到整本源文结尾" if result.source_exhausted else ""
    return ToolOutcome(
        value=ReplanWindowResult(
            message=f"新的分集方案追加了 {len(result.episodes)} 集，共 {result.total} 集{done}。",
            planned=len(result.episodes),
            total=result.total,
            complete=result.source_exhausted,
        )
    )


def _planning_gap(raw: object) -> tuple[str, int] | None:
    """Gap endpoint in a task payload; absent or malformed values mean whole-source planning."""
    if not isinstance(raw, Mapping):
        return None
    source_file, end = raw.get("source_file"), raw.get("end")
    if isinstance(source_file, str) and isinstance(end, int) and not isinstance(end, bool):
        return source_file, end
    return None


@dataclass(frozen=True, slots=True)
class StopEpisodePlanningResult:
    #: Cancelled queued windows.
    cancelled: list[str]
    #: Windows still running: running windows cannot be cancelled and finish and commit normally.
    running: list[str]


async def stop_episode_planning(
    scope: ProjectScope, caller: CallerContext, services: Services
) -> StopEpisodePlanningResult:
    """Stop episode planning: cancel queued windows; the running window finishes normally and retains its extracted episodes but enqueues no successor."""
    active = await services.queue.get_active_tasks_for_resources(
        project_name=scope.project_name,
        task_type=_TEXT_EPISODE_PLAN,
        resource_ids=list(EPISODE_PLANNING_SLOTS),
        user_id=caller.user_id,
    )
    cancelled: list[str] = []
    running: list[str] = []
    for task in active:
        task_id = str(task["task_id"])
        if task.get("status") == "queued":
            try:
                await services.queue.cancel_task(task_id)
            except TaskNotCancellableError:
                pass
            else:
                cancelled.append(task_id)
                continue
        running.append(task_id)
        _STOPPED_PLANNING_WINDOWS.add(task_id)
    return StopEpisodePlanningResult(cancelled=cancelled, running=running)


def _text_result_payload(value: TextGenerationResult) -> dict[str, Any]:
    """Text receipt in task results: write ``warnings``, ``new_assets`` and ``no_pending_authoring`` only when nonempty; readers render by ``result.warnings``."""
    payload: dict[str, Any] = {"message": value.message}
    if value.warnings:
        payload["warnings"] = list(value.warnings)
    if value.new_assets:
        payload["new_assets"] = list(value.new_assets)
    if value.no_pending_authoring:
        payload["no_pending_authoring"] = True
    return payload


async def execute_queued_text_task(
    task: dict[str, Any],
    *,
    planner_cls: type[EpisodePlanner] = EpisodePlanner,
    services: Services | None = None,
) -> dict[str, Any]:
    """Execute one durable text task through the same host-independent handlers.

    Absent ``services`` resolve current configuration (global generation queue); tasks registered by the embedded host reuse the submitter's collaborators."""
    payload = task.get("payload") or {}
    registered = _TEXT_TASK_SERVICES.pop(str(task["task_id"]), None)
    if registered is not None:
        scope, services = registered
    else:
        scope = ProjectScope(project_name=str(task["project_name"]), data_root=DataRootLayout.current().root)
        services = services or Services.defaults(await asyncio.to_thread(ProjectManager, scope.data_root))
    task_type = task["task_type"]
    if task_type == TEXT_DRAFT_REPAIR_TASK_TYPE:
        outcome = await _execute_draft_repair(RepairDraftRequest.model_validate(payload), scope, services)
    elif task_type == _TEXT_EPISODE_PLAN and isinstance(payload.get("replan"), str):
        outcome = await _execute_replan_window(
            str(payload["replan"]),
            payload.get("instructions"),
            scope,
            services,
            planner_cls=planner_cls,
            chain=_PlanningChain(task=task, services=services),
        )
    elif task_type == _TEXT_EPISODE_PLAN:
        outcome = await _execute_plan_episodes(
            ToolRequest(PlanEpisodesRequest(instructions=payload.get("instructions"))),
            scope,
            services,
            planner_cls=planner_cls,
            chain=_PlanningChain(task=task, services=services) if payload.get("continue_to_end") else None,
            gap=_planning_gap(payload.get("gap")),
        )
    else:
        request = TextGenerationRequest(
            episode=payload["episode"],
            source=payload.get("source"),
            instructions=payload.get("instructions"),
            dry_run=bool(payload.get("dry_run")),
            entry_ids=tuple(payload.get("entry_ids") or ()),
            rewrite=bool(payload.get("rewrite")),
            overwrite_revision=payload.get("overwrite_revision"),
            regenerate=bool(payload.get("regenerate")),
        )
        handlers = {
            _TEXT_EPISODE_SCRIPT: ("generate_episode_script", generate_episode_script_handler),
            _TEXT_DRAMA_SCRIPT_PLAN: ("generate_script_plan", generate_drama_script_plan),
            _TEXT_NARRATION_SCRIPT_PLAN: ("generate_script_plan", generate_narration_script_plan),
            _TEXT_REFERENCE_SCRIPT_PLAN: ("generate_script_plan", generate_reference_script_plan),
        }
        try:
            operation, handler = handlers[task_type]
        except KeyError as exc:
            raise ValueError(f"unsupported text task_type: {task_type}") from exc
        outcome = await _execute_text_handler(operation, handler, request, scope, services)
    if outcome.problem is not None:
        raise RuntimeError(encode_generation_problem(_queued_generation_problem(outcome.problem)))
    value = outcome.value
    if isinstance(value, TextGenerationResult):
        return _text_result_payload(value)
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise RuntimeError("text task returned no result")


async def reset_episode_planning(
    request: ToolRequest[ResetEpisodePlanningRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
    *,
    resetter: Callable[..., Any] = reset_episode_planning_service,
) -> ToolOutcome[ResetEpisodePlanningResult]:
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    value = request.value
    project_before = await asyncio.to_thread(services.projects.load_project, scope.project_name)
    try:
        result = await _run_sync_transaction(
            lambda: resetter(
                services.projects.get_project_path(scope.project_name),
                episode_id=value.episode_id,
                confirm_consumed=value.confirm_consumed,
            )
        )
    except (EpisodeResetError, FileNotFoundError) as exc:
        return ToolOutcome(problem=ToolProblem("episode_reset_failed", f"❌ 分集规划重置失败：{exc}"))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("reset_episode_planning", exc))

    def _episodes(ids: list[int]) -> str:
        return "、".join(describe_episode_for_agent(project_before, episode_id) for episode_id in ids)

    partial = value.episode_id is not None and value.episode_id != first_cut_episode_id(project_before)
    start = describe_episode_for_agent(project_before, value.episode_id) if value.episode_id is not None else ""
    retired_note = "转为无原文的集并标 stale（原文已重新规划），移到播出顺序末尾，剧本、媒体等产物与产物登记都保留"
    if isinstance(result, ResetConfirmationRequired):
        scope_note = (
            f"从 {start} 起的切出集退回未规划，播出顺序中它之前的集保留不动"
            if partial
            else "全部切出集退回未规划，接续规划从整本源文开头读起"
        )
        lines = [f"⚠️ 本次重置波及已有产物的集，尚未执行任何改动。确认后{scope_note}："]
        if result.retired_episodes:
            lines.append(f"- {_episodes(result.retired_episodes)}：{retired_note}")
        if result.removed_episodes:
            lines.append(f"- {_episodes(result.removed_episodes)}：还没有产物，移出账本")
        if result.deleted_files:
            lines.append(f"- 删除可按原文范围重造的集文件：{'、'.join(result.deleted_files)}")
        if result.archived_files:
            lines.append(f"- 没有原文范围记录的集文件改名留底：{'、'.join(result.archived_files)}")
        lines.append("请把以上清单如实告知用户；用户确认后带 confirm_consumed=true 重新调用。")
        return ToolOutcome(
            value=ResetEpisodePlanningResult(
                message="\n".join(lines),
                confirmation_required=True,
                archived_files=result.archived_files,
                consumed_episodes=result.consumed_episodes,
                removed_episodes=result.removed_episodes,
                deleted_files=result.deleted_files,
                retired_episodes=result.retired_episodes,
            )
        )

    count = len(result.removed_episodes) + len(result.retired_episodes)
    if partial:
        lines = [
            f"✅ 已部分重置分集规划：从 {start} 起的 {count} 个切出集退回未规划，"
            "接续规划从保留段最后一个切出集的结尾读起。"
        ]
    else:
        lines = [f"✅ 已全量重置分集规划：{count} 个切出集退回未规划，下次规划从整本源文开头读起。"]
    if result.retired_episodes:
        lines.append(f"{_episodes(result.retired_episodes)} {retired_note}。")
    if result.removed_episodes:
        lines.append(f"{_episodes(result.removed_episodes)} 还没有产物，已移出账本。")
    if result.deleted_files:
        lines.append(f"已删除可重造的派生集文件 {len(result.deleted_files)} 个。")
    if result.archived_files:
        archived = "、".join(f"{src} → {dst}" for src, dst in result.archived_files)
        lines.append(f"无原文范围记录的集文件已改名留底（内容保留）：{archived}")
    lines.append(
        "请调用 plan_episodes 继续规划；新规划的集分配新的集 ID，不复用被清除的集 ID。"
        if partial
        else "自带原文与无原文的集保留不动，请调用 plan_episodes 从头重新规划；新规划的集分配新的集 ID，不复用被清除的集 ID。"
    )
    return ToolOutcome(
        value=ResetEpisodePlanningResult(
            message="\n".join(lines),
            confirmation_required=False,
            removed_episodes=result.removed_episodes,
            deleted_files=result.deleted_files,
            archived_files=result.archived_files,
            consumed_episodes=result.consumed_episodes,
            retired_episodes=result.retired_episodes,
        )
    )


def _coerce_numeric_string(value: str, parser: Callable[[str], int | float], message: str) -> int | float:
    try:
        return parser(value.strip())
    except ValueError:
        raise ValueError(message) from None


def _coerce_setting_value(key: str, value: Any) -> Any:
    if key in _POSITIVE_INT_SETTINGS:
        if value is None:
            return None
        if isinstance(value, str):
            value = _coerce_numeric_string(value, int, f"{key} 必须是正整数或 null,收到 {value!r}")
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} 必须是正整数或 null,收到 {value!r}")
        return value
    if key == EPISODE_TARGET_DURATION_FIELD:
        if value is None:
            return None
        message = (
            f"{EPISODE_TARGET_DURATION_FIELD} 必须是 {MIN_EPISODE_TARGET_DURATION}-"
            f"{MAX_EPISODE_TARGET_DURATION} 秒的整数或 null,收到 {value!r}"
        )
        if isinstance(value, str):
            value = _coerce_numeric_string(value, int, message)
        if not is_valid_episode_target_duration(value):
            raise ValueError(message)
        return value
    if key == "source_language":
        if value is not None and (not isinstance(value, str) or value not in _SOURCE_LANGUAGE_VALUES):
            raise ValueError(f"source_language 必须是 {list(_SOURCE_LANGUAGE_VALUES)} 之一或 null,收到 {value!r}")
        return value
    if key == "brief":
        if value is not None and not isinstance(value, str):
            raise ValueError(f"brief 必须是字符串或 null,收到 {value!r}")
        return value
    if key == "character_voice_binding":
        if value is not None and (not isinstance(value, str) or value not in VALID_CHARACTER_VOICE_BINDINGS):
            raise ValueError(
                f"character_voice_binding 必须是 {sorted(VALID_CHARACTER_VOICE_BINDINGS)} 之一或 null,收到 {value!r}"
            )
        return value
    if key == "narration_voice":
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"narration_voice 必须是非空字符串或 null,收到 {value!r}")
        return value
    if key == "narration_speed":
        if value is None:
            return None
        if isinstance(value, str):
            value = _coerce_numeric_string(value, float, f"narration_speed 必须是正的有限数值或 null,收到 {value!r}")
        is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
        try:
            is_valid = is_number and math.isfinite(value) and value > 0
        except OverflowError:
            is_valid = False
        if not is_valid:
            raise ValueError(f"narration_speed 必须是正的有限数值或 null,收到 {value!r}")
        return value
    raise ValueError(f"settings 字段 {key!r} 缺类型校验")


def _format_settings(changes: dict[str, tuple[str, Any]]) -> str:
    set_items = [(key, value) for key, (operation, value) in changes.items() if operation == "set"]
    cleared = [key for key, (operation, _) in changes.items() if operation == "clear"]
    unchanged = [key for key, (operation, _) in changes.items() if operation == "noop"]
    parts = []
    if set_items:
        parts.append("已更新 " + ", ".join(f"{key}={value}" for key, value in set_items))
    if cleared:
        parts.append("已清除 " + ", ".join(cleared))
    if unchanged:
        parts.append("无变更 " + ", ".join(unchanged))
    icon = "ℹ️" if not set_items and not cleared else "✅"
    return f"{icon} settings: {'; '.join(parts) if parts else '无变更'}"


def _format_overview(changes: dict[str, str]) -> str:
    updated = [key for key, operation in changes.items() if operation == "set"]
    unchanged = [key for key, operation in changes.items() if operation == "noop"]
    parts = []
    if updated:
        parts.append("已更新 " + ", ".join(updated))
    if unchanged:
        parts.append("无变更 " + ", ".join(unchanged))
    return f"{'ℹ️' if not updated else '✅'} overview: {'; '.join(parts) if parts else '无变更'}"


def _format_upsert(table: str, result: dict[str, Any]) -> str:
    added = sorted(result.get("added") or [])
    merged = sorted(result.get("merged") or [])
    noop = sorted(result.get("noop") or [])
    dropped_fields = result.get("dropped_fields") or {}
    dropped_legacy = result.get("dropped_legacy") or {}
    parts = []
    if added:
        parts.append(f"新增 {len(added)} 个: {', '.join(added)}")
    if merged:
        parts.append(f"合并改字段 {len(merged)} 个: {', '.join(merged)}")
    if noop:
        parts.append(f"无可写字段已跳过 {len(noop)} 个: {', '.join(noop)}")
    lines = [
        f"{'ℹ️' if not added and not merged else '✅'} {table}: {'; '.join(parts) if parts else '无变更（所有条目均无可写字段）'}"
    ]
    if dropped_fields:
        detail = "; ".join(f"{name}: {', '.join(fields)}" for name, fields in sorted(dropped_fields.items()))
        lines += [
            f"⚠️  以下字段不在 Agent 可编辑范围,已忽略 → {detail}",
            "   说明: reference_image 由用户上传/系统管理;",
            "   character_sheet / scene_sheet / prop_sheet 由资产生成流水线回写,不可手动设置。",
        ]
    if dropped_legacy:
        detail = "; ".join(f"{name}: {', '.join(fields)}" for name, fields in sorted(dropped_legacy.items()))
        lines.append(f"ℹ️  以下历史字段已废弃,本次未持久化 → {detail}")
    return "\n".join(lines)


def _patch_project_sync(
    request: ToolRequest[PatchProjectRequest],
    scope: ProjectScope,
    services: Services,
) -> ToolOutcome[PatchProjectResult]:
    value = request.value
    try:
        if value.overview is not None:
            overview_patch = value.overview
            for key, field_value in overview_patch.items():
                if key not in PROJECT_OVERVIEW_FIELDS:
                    raise ValueError(f"overview 字段 {key!r} 不在白名单 {list(PROJECT_OVERVIEW_FIELDS)} 内")
                if not isinstance(field_value, str):
                    raise ValueError(f"overview 字段 {key!r} 的值必须是字符串,收到 {field_value!r}")
            changes: dict[str, str] = {}

            def mutate_overview(project_data: dict[str, Any]) -> None:
                overview = project_data.get("overview")
                if not isinstance(overview, dict):
                    overview = {}
                    project_data["overview"] = overview
                for key, field_value in overview_patch.items():
                    changes[key] = "noop" if overview.get(key) == field_value else "set"
                    overview[key] = field_value

            services.projects.update_project(scope.project_name, mutate_overview)
            return ToolOutcome(
                value=PatchProjectResult(message=_format_overview(changes), operation="overview", changes=changes)
            )
        if value.settings is not None:
            coerced = {}
            for key, field_value in value.settings.items():
                if key not in PROJECT_SETTINGS:
                    raise ValueError(f"settings 字段 {key!r} 不在白名单 {list(PROJECT_SETTINGS)} 内")
                coerced[key] = _coerce_setting_value(key, field_value)
            diagnostics: dict[str, tuple[str, Any]] = {}

            def mutate_settings(project_data: dict[str, Any]) -> None:
                if "brief" in coerced and project_data.get("content_mode") != "ad":
                    raise ValueError("brief 仅广告/短片项目（content_mode=ad）可用")
                if EPISODE_TARGET_DURATION_FIELD in coerced and project_data.get("content_mode") == "ad":
                    raise ValueError(
                        f"{EPISODE_TARGET_DURATION_FIELD} 不适用广告/短片项目（整集体量按 target_duration 预算规划）"
                    )
                for key, field_value in coerced.items():
                    current = project_data.get(key)
                    if field_value is None:
                        diagnostics[key] = ("clear", None) if key in project_data else ("noop", None)
                        project_data.pop(key, None)
                    elif current == field_value:
                        diagnostics[key] = ("noop", current)
                    else:
                        diagnostics[key] = ("set", field_value)
                        project_data[key] = field_value
                try:
                    validate_project_narration_config(project_data)
                except NarrationConfigError as exc:
                    raise ValueError(
                        f"项目的旁白交付方式是 TTS 配音，TTS 快照必须完整，本次修改被拒绝（{exc.code}）"
                    ) from exc

            services.projects.update_project(scope.project_name, mutate_settings)
            return ToolOutcome(
                value=PatchProjectResult(
                    message=_format_settings(diagnostics), operation="settings", changes=diagnostics
                )
            )
        assert value.table is not None
        assert value.entries is not None
        changes = services.projects.upsert_assets(scope.project_name, value.table, value.entries)
        return ToolOutcome(
            value=PatchProjectResult(message=_format_upsert(value.table, changes), operation="assets", changes=changes)
        )
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("patch_project", exc))


async def patch_project(
    request: ToolRequest[PatchProjectRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[PatchProjectResult]:
    return await _run_sync_transaction(_patch_project_sync, request, scope, services)


def _patch_episode_meta_sync(
    request: ToolRequest[PatchEpisodeMetaRequest],
    scope: ProjectScope,
    services: Services,
) -> ToolOutcome[PatchEpisodeMetaResult]:
    value = request.value
    try:
        with services.projects.locked_script(scope.project_name, value.script) as script:
            script[value.field] = value.value
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("patch_episode_meta", exc))
    return ToolOutcome(
        value=PatchEpisodeMetaResult(
            message=f"✅ 已更新分集{value.field}为「{value.value}」",
            script=value.script,
            field=value.field,
            value=value.value,
        )
    )


async def patch_episode_meta(
    request: ToolRequest[PatchEpisodeMetaRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[PatchEpisodeMetaResult]:
    return await _run_sync_transaction(_patch_episode_meta_sync, request, scope, services)


def _merge_episode_line(project: Mapping[str, Any], impact: AssetMergeEpisodeImpact) -> str:
    counts = (
        ("脚本规划", impact.script_plan),
        ("正式脚本", impact.script),
        ("草稿", impact.draft),
        ("提示词正文", impact.prompt_text),
        ("说话人", impact.speaker),
    )
    references = "、".join(f"{label} {count} 处" for label, count in counts if count) or "无引用改写"
    return (
        f"- {describe_episode_for_agent(project, impact.episode)}：{references}；"
        f"过期分镜图 {impact.storyboards} 张、视频 {impact.videos} 段"
    )


def _merge_asset_message(project: Mapping[str, Any], report: AssetMergeReport) -> str:
    how = f"并为 {report.target!r} 的衍生" if report.as_derivative else f"并入 {report.target!r}"
    head = (
        f"预览：把 {report.table} 资产 {report.source!r} {how}，将改写 {report.references} 处引用。"
        if report.dry_run
        else f"已把 {report.table} 资产 {report.source!r} {how}，改写 {report.references} 处引用。"
    )
    lines = [head, *(_merge_episode_line(project, impact) for impact in report.episodes)]
    if report.aliases_added:
        lines.append("追加为保留方别名：" + "、".join(report.aliases_added))
    if report.derivative_created is not None:
        lines.append(f"新建衍生 {report.derivative_created!r}，资产图待生成")
    if report.derivatives_moved:
        lines.append("迁到保留方名下的衍生：" + "、".join(report.derivatives_moved))
    if report.derivatives_folded:
        lines.append("与保留方已有衍生同名、并入已有衍生：" + "、".join(report.derivatives_folded))
    lines.append(
        "被并方的描述、资产图及版本历史、声音设置、原图与参考音频不并入保留方，将移入项目回收站。"
        if report.dry_run
        else "被并方的描述、资产图及版本历史、声音设置、原图与参考音频已移入项目回收站。"
    )
    return "\n".join(lines)


async def merge_asset(
    request: ToolRequest[MergeAssetRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[MergeAssetResult]:
    value = request.value

    def _merge() -> tuple[AssetMergeReport, dict[str, Any]]:
        report = services.projects.merge_asset(
            scope.project_name,
            value.table,
            value.source,
            value.target,
            as_derivative=value.as_derivative,
            dry_run=value.dry_run,
        )
        return report, services.projects.load_project(scope.project_name)

    try:
        report, project = await _run_sync_transaction(_merge)
    except AssetMergeNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", f"{value.table} 中不存在名为 {exc.name!r} 的资产"))
    except AssetMergeRejectedError as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("merge_asset", exc))
    return ToolOutcome(
        value=MergeAssetResult(
            message=_merge_asset_message(project, report),
            table=value.table,
            source=report.source,
            target=report.target,
            as_derivative=report.as_derivative,
            dry_run=report.dry_run,
            references=report.references,
            aliases_added=list(report.aliases_added),
            derivative_created=report.derivative_created,
            derivatives_moved=list(report.derivatives_moved),
            derivatives_folded=list(report.derivatives_folded),
            episodes=[
                MergeAssetEpisodeImpact(
                    episode_id=impact.episode,
                    script_plan=impact.script_plan,
                    script=impact.script,
                    draft=impact.draft,
                    prompt_text=impact.prompt_text,
                    speaker=impact.speaker,
                    storyboards=impact.storyboards,
                    videos=impact.videos,
                )
                for impact in report.episodes
            ],
        )
    )


async def rename_asset(
    request: ToolRequest[RenameAssetRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[RenameAssetResult]:
    value = request.value
    try:
        report = await _run_sync_transaction(
            services.projects.rename_asset,
            scope.project_name,
            value.table,
            value.old_name,
            value.new_name,
        )
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("rename_asset", exc))
    message = (
        f"已把 {value.table} 资产 {report.old_name!r} 重命名为 {report.new_name!r}:"
        f"更新 {report.episodes} 集共 {report.references} 处引用,迁移 {report.files} 个关联文件。"
    )
    return ToolOutcome(
        value=RenameAssetResult(
            message=message,
            table=value.table,
            old_name=report.old_name,
            new_name=report.new_name,
            episodes=report.episodes,
            references=report.references,
            files=report.files,
        )
    )


async def retry_project_migration(
    _request: ToolRequest[NoArguments],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[RetryProjectMigrationResult]:
    project_dir = await asyncio.to_thread(services.projects.get_project_path, scope.project_name)
    try:
        failure = await _run_sync_transaction(
            migrate_project_with_verdict,
            project_dir,
            recorded_episode_ids=recorded_episode_ids_on(asyncio.get_running_loop()),
        )
        if failure is not None:
            return ToolOutcome(problem=_migration_tool_problem(failure))
        plan = await services.workflow_planner.get_plan(
            scope.project_name,
            WorkflowPlanRequest(),
            user_id=_caller.user_id,
            queue=services.queue,
            config_resolver=services.capabilities,
        )
    except Exception as exc:
        try:
            residual = await asyncio.to_thread(load_migration_failure, project_dir)
        except (FileNotFoundError, ValueError, OSError):
            residual = None
        return ToolOutcome(
            problem=_migration_tool_problem(residual) if residual else _unexpected("retry_project_migration", exc)
        )
    return ToolOutcome(
        value=RetryProjectMigrationResult(
            message="✅ 数据升级已完成，项目解除阻断。当前制作计划：\n" + plan.model_dump_json(),
            workflow_plan=plan,
        )
    )


async def complete_script_plan_rebuild(
    request: ToolRequest[CompleteScriptPlanRebuildRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
    *,
    run_sync: Callable[..., Awaitable[Any]] = asyncio.to_thread,
    complete: Callable[..., Any] = complete_stale_script_plan_rebuild,
) -> ToolOutcome[CompleteScriptPlanRebuildResult]:
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    value = request.value
    try:
        revision = await run_sync(
            complete,
            services.projects,
            scope.project_name,
            value.episode_id,
            value.expected_stale_script_plan_revision,
        )
    except ScriptPlanRebuildCompletionError as exc:
        return ToolOutcome(problem=ToolProblem(exc.code, str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("complete_script_plan_rebuild", exc))
    return ToolOutcome(
        value=CompleteScriptPlanRebuildResult(episode_id=value.episode_id, script_plan_revision=prefixed(revision))
    )


__all__ = [
    "ASSET_TABLES",
    "EPISODE_META_FIELDS",
    "MERGEABLE_ASSET_TABLES",
    "PROJECT_OVERVIEW_FIELDS",
    "PROJECT_SETTINGS",
    "CallerContext",
    "CompleteScriptPlanRebuildRequest",
    "ConfirmScriptReviewRequest",
    "CreateProjectToolRequest",
    "DiscardDraftRequest",
    "DraftLocator",
    "EditSourceTextRequest",
    "EpisodeScriptContent",
    "EpisodeScriptRequest",
    "GenerateEpisodeScriptRequest",
    "GenerateScriptPlanRequest",
    "GenerationBatchToolRequest",
    "MergeAssetRequest",
    "NoArguments",
    "PatchDraftRequest",
    "PatchEpisodeMetaRequest",
    "PatchEpisodeScriptRequest",
    "PatchProjectRequest",
    "PlanEpisodesRequest",
    "ProjectContent",
    "ProjectFileContent",
    "ProjectFileEntry",
    "ProjectFileRequest",
    "ProjectFilesContent",
    "ProjectScope",
    "PromoteDraftRequest",
    "PromptPreviewRequest",
    "RenameAssetRequest",
    "ResetEpisodePlanningRequest",
    "ScriptPatchResult",
    "ScriptPlanContent",
    "ScriptPlanContentRequest",
    "ScriptSpeechAdmission",
    "ScriptSpeechProblem",
    "Services",
    "SourceChangeResult",
    "SourceFilesContent",
    "SourceReplacement",
    "SourceTextContent",
    "SourceTextRequest",
    "TextGenerationError",
    "TextGenerationRequest",
    "TextGenerationResult",
    "ToolOutcome",
    "ToolProblem",
    "ToolRequest",
    "UploadSourceRequest",
    "cancel_generation_batch",
    "complete_script_plan_rebuild",
    "confirm_script_review",
    "create_project",
    "discard_draft",
    "edit_source_text",
    "generate_episode_script",
    "generate_script_plan",
    "get_episode_script",
    "get_generation_batch",
    "get_project_content",
    "get_prompt_preview",
    "get_script_plan_content",
    "get_source_text",
    "get_video_capabilities",
    "get_workflow_plan",
    "list_project_files",
    "list_projects",
    "list_source_files",
    "merge_asset",
    "open_draft",
    "patch_draft",
    "patch_episode_meta",
    "patch_episode_script",
    "patch_project",
    "plan_episodes",
    "promote_draft",
    "read_project_file",
    "rename_asset",
    "reset_episode_planning",
    "retry_project_migration",
    "upload_source",
]
