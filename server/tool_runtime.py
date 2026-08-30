"""Host-independent ArcReel tool handlers and their typed call contract."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import stat
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from lib.artifact_activation import ArtifactCurrencyResolver, active_artifact_currency_resolver
from lib.asset_inventory import (
    AssetInventoryError,
    AssetInventoryInvalidRequest,
    AssetInventoryRevisionConflict,
    AssetInventorySourceBlocked,
)
from lib.asset_inventory import (
    complete_asset_inventory as complete_asset_inventory_service,
)
from lib.asset_types import ASSET_SPECS
from lib.async_thread import run_sync_transaction as _run_sync_transaction
from lib.character_voice import VALID_CHARACTER_VOICE_BINDINGS
from lib.config.resolver import ConfigResolver
from lib.content_digest import prefixed, prefixed_canonical_json_digest
from lib.db import async_session_factory
from lib.episode_paths import (
    DRAMA_SCRIPT_PLAN_QUARANTINE_FILENAME,
    NARRATION_SCRIPT_PLAN_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_PROMPT_AUTHORING_QUARANTINE_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_LEGACY_FILENAME,
    REFERENCE_VIDEO_SCRIPT_PLAN_QUARANTINE_FILENAME,
    SCRIPT_PLAN_FILENAMES,
    SCRIPT_PLAN_LEGACY_FILENAMES,
)
from lib.episode_planner import EpisodePlanner, EpisodePlanningError, LedgerStats, PlanResult
from lib.episode_reset import (
    EpisodeResetError,
    ResetConfirmationRequired,
)
from lib.episode_reset import (
    reset_episode_planning as reset_episode_planning_service,
)
from lib.episode_target_duration import (
    EPISODE_TARGET_DURATION_FIELD,
    MAX_EPISODE_TARGET_DURATION,
    MIN_EPISODE_TARGET_DURATION,
    is_valid_episode_target_duration,
)
from lib.formal_write import FormalWriteReceipt, project_metadata_lock
from lib.generation_batch import (
    GenerationBatchReadModel,
    GenerationBatchRequestedItem,
    GenerationBatchRequestSnapshot,
    build_generation_batch_admission,
)
from lib.generation_queue import (
    ActiveTaskRequestConflict,
    CompensableGenerationResult,
    GenerationBatchNotFound,
    GenerationQueue,
    cleanup_fresh_generation_batch,
    get_generation_queue,
)
from lib.generation_queue_client import (
    BatchTaskResult,
    TaskSpec,
    WorkerOfflineError,
    enqueue_task_only,
    submit_generation_batch,
    wait_for_task,
)
from lib.generation_result import (
    GenerationAction,
    GenerationBatchResult,
    GenerationProblem,
    GenerationSelectionMode,
    GenerationTargetState,
    encode_generation_problem,
    enqueue_problem,
    migration_problem,
    problem_from_task_failure,
)
from lib.path_safety import safe_join
from lib.profile_manifest import ContentMode
from lib.project_manager import ProjectManager, SourceKind, is_reference_video_project
from lib.project_migration_failure import (
    MIGRATION_FAILURE_CODE,
    MigrationFailureRecord,
    ProjectMigrationError,
    load_migration_failure,
)
from lib.project_migration_guard import project_migration_failure
from lib.project_migrations import migrate_project_with_verdict
from lib.script_batch_edit import (
    ScriptBatchEditCommand,
    ScriptBatchEditLocation,
    ScriptBatchEditor,
    ScriptBatchEditProblem,
    ScriptBatchEditResult,
    script_revision,
)
from lib.script_editor import (
    ScriptEditError,
    insert_segment,
    patch_field,
    remove_segment,
    resolve_items,
    split_segment,
)
from lib.script_plan_entries import SCOPE_STALE
from lib.script_review import ScriptPlanRebuildCompletionError, complete_stale_script_plan_rebuild, script_plan_kind
from lib.source_loader import (
    ConflictError,
    CorruptFileError,
    FileSizeExceededError,
    OnConflict,
    SourceDecodeError,
    SourceLoader,
    UnsupportedFormatError,
)
from lib.source_revision import SourceScope
from lib.workflow_plan import WorkflowPlan, WorkflowPlanRequest
from lib.workflow_state import WorkflowRequestError
from server.draft_workflow import (
    DiscardDraftRequest,
    DraftContext,
    DraftLocator,
    DraftWorkflow,
    DraftWorkflowError,
    PatchDraftRequest,
    PromoteDraftRequest,
)
from server.services.prompt_preview import ItemPromptPreview, ScriptItemNotFound, preview_item_prompts
from server.services.video_caps import annotate_reference_unit_tiers
from server.services.workflow_planner import WorkflowPlanner
from server.text_generation import (
    CompensableTextGenerationResult,
    TextGenerationError,
    TextGenerationRequest,
    TextGenerationResult,
    _episode_generation_preflight,
    generate_drama_script_plan,
    generate_narration_script_plan,
    generate_reference_script_plan,
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
    projects_root: Path


@dataclass(frozen=True, slots=True)
class CallerContext:
    user_id: str
    source: Literal["embedded", "mcp"]


@dataclass(frozen=True, slots=True)
class Services:
    projects: ProjectManager
    workflow_planner: WorkflowPlanner
    capabilities: ConfigResolver
    queue: GenerationQueue = field(default_factory=get_generation_queue)


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

    batch_id: str = Field(min_length=1)


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
    embedded_waiter: Callable[..., Awaitable[tuple[list[BatchTaskResult], list[BatchTaskResult]]]] | None = None,
) -> MediaGenerationSubmission:
    requested, blocked = build_generation_batch_admission(
        preflight=preflight,
        pending_ids=pending_ids,
        states=states,
        admission=admission,
    )
    if caller.source == "mcp":
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
        if embedded_waiter is None:
            raise ValueError("embedded media generation requires a batch waiter")
        if specs:
            successes, failures = await embedded_waiter(
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


async def get_generation_batch(
    request: ToolRequest[GenerationBatchToolRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    try:
        resolver: ArtifactCurrencyResolver | None = None
        try:
            project = await asyncio.to_thread(services.projects.load_project_readonly, scope.project_name)
            resolver = active_artifact_currency_resolver(
                services.projects.get_project_path(scope.project_name),
                project,
            )
        except ProjectMigrationError:
            pass
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
    id: str = Field(min_length=1)
    fields: dict[str, Any] = Field(min_length=1)


class PatchInsertOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["insert"]
    after_id: str = Field(min_length=1)
    item: dict[str, Any]


class PatchRemoveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["remove"]
    id: str = Field(min_length=1)


class PatchSplitOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    op: Literal["split"]
    id: str = Field(min_length=1)
    parts: list[dict[str, Any]] = Field(min_length=2)


PatchEpisodeScriptOperation = Annotated[
    PatchUpdateOperation | PatchInsertOperation | PatchRemoveOperation | PatchSplitOperation,
    Field(discriminator="op"),
]


class PatchEpisodeScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    script: str = Field(min_length=1)
    base_revision: str = Field(pattern=r"^sha256-v1:[0-9a-f]{64}$")
    operations: list[PatchEpisodeScriptOperation] = Field(min_length=1)


async def _run_text_generation(
    operation: str,
    call: Awaitable[TextGenerationResult],
) -> ToolOutcome[TextGenerationResult]:
    try:
        return ToolOutcome(value=await call)
    except TextGenerationError as exc:
        return ToolOutcome(problem=ToolProblem("generation_refused", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"{operation} 失败: {exc}"))


_TEXT_EPISODE_SCRIPT = "text_episode_script"
_TEXT_DRAMA_SCRIPT_PLAN = "text_drama_script_plan"
_TEXT_NARRATION_SCRIPT_PLAN = "text_narration_script_plan"
_TEXT_REFERENCE_SCRIPT_PLAN = "text_reference_script_plan"
_TEXT_EPISODE_PLAN = "text_episode_plan"
_TEXT_TASK_SERVICES: dict[str, Services] = {}


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
) -> ToolOutcome[Any]:
    active = await services.queue.get_active_tasks_for_resources(
        project_name=scope.project_name,
        task_type=task_type,
        resource_ids=[unit_id],
        user_id=caller.user_id,
    )
    requested_facts = {key: value for key, value in payload.items() if key != "projects_root"}
    if active:
        existing_facts = {
            key: value for key, value in (active[0].get("payload") or {}).items() if key != "projects_root"
        }
        if existing_facts != requested_facts:
            return ToolOutcome(
                problem=ToolProblem(
                    "generation_active_task_conflict",
                    "generation_active_task_conflict",
                    action=GenerationAction.WAIT_FOR_TASK,
                    params={"task_id": active[0]["task_id"], "status": active[0]["status"]},
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
            payload={**payload, "projects_root": str(scope.projects_root)},
            source=caller.source,
            user_id=caller.user_id,
            batch_id=batch_id,
            batch_unit_id=unit_id,
            queue=services.queue,
        )
        registered_services = caller.source == "embedded" and not enqueue.get("deduped")
        if registered_services:
            _TEXT_TASK_SERVICES[enqueue["task_id"]] = services
        try:
            batch = await services.queue.get_generation_batch(
                project_name=scope.project_name, batch_id=batch_id, user_id=caller.user_id
            )
            if caller.source == "mcp":
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


async def generate_episode_script(
    request: ToolRequest[TextGenerationRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    if request.value.dry_run:
        return await _execute_text_handler(
            "generate_episode_script", generate_episode_script_handler, request.value, scope, services
        )
    try:
        await asyncio.to_thread(
            _episode_generation_preflight,
            services.projects.get_project_path(scope.project_name),
            request.value.episode,
            enforce_review_gate=True,
        )
    except TextGenerationError as exc:
        return ToolOutcome(problem=ToolProblem("generation_refused", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"generate_episode_script 失败: {exc}"))
    return await _submit_text_task(
        task_type=_TEXT_EPISODE_SCRIPT,
        operation="generate_episode_script",
        unit_id=f"episode-{request.value.episode}",
        payload=request.value.to_payload(),
        scope=scope,
        caller=_caller,
        services=services,
    )


async def generate_script_plan(
    request: ToolRequest[TextGenerationRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[Any]:
    try:
        project = await asyncio.to_thread(services.projects.load_project_readonly, scope.project_name)
        content_mode = project.get("content_mode", "narration")
        if content_mode == "ad":
            raise TextGenerationError("广告/短片项目无 script_plan，请直接调用 generate_episode_script")
        if is_reference_video_project(project):
            handler, task_type = generate_reference_script_plan, _TEXT_REFERENCE_SCRIPT_PLAN
        elif content_mode == "narration":
            handler, task_type = generate_narration_script_plan, _TEXT_NARRATION_SCRIPT_PLAN
        elif content_mode == "drama":
            handler, task_type = generate_drama_script_plan, _TEXT_DRAMA_SCRIPT_PLAN
        else:
            raise TextGenerationError(f"不支持的创作类型: {content_mode}")
    except TextGenerationError as exc:
        return ToolOutcome(problem=ToolProblem("generation_refused", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", f"generate_script_plan 失败: {exc}"))
    if request.value.dry_run:
        return await _execute_text_handler("generate_script_plan", handler, request.value, scope, services)
    return await _submit_text_task(
        task_type=task_type,
        operation="generate_script_plan",
        unit_id=f"episode-{request.value.episode}",
        payload=request.value.to_payload(),
        scope=scope,
        caller=_caller,
        services=services,
    )


async def confirm_script_review(
    request: ToolRequest[int],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[TextGenerationResult]:
    return await _run_text_generation(
        "confirm_script_review",
        confirm_script_review_handler(
            request.value,
            project_name=scope.project_name,
            projects=services.projects,
            config_resolver=services.capabilities,
        ),
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
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
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
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory_fd = os.open(project_dir, directory_flags)
        try:
            for part in parts[:-1]:
                child_fd = os.open(part, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = child_fd
            file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=directory_fd)
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
    project = projects.load_project_readonly(project_name)
    return ProjectContent(revision=prefixed_canonical_json_digest(project), project=project)


async def get_project_content(
    _request: ToolRequest[None],
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
    request: ToolRequest[str],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[EpisodeScriptContent]:
    filename = request.value
    if not isinstance(filename, str) or not filename or "/" in filename or "\\" in filename or filename in {".", ".."}:
        return ToolOutcome(problem=ToolProblem("invalid_request", "script 必须是纯文件名"))
    try:
        failure = await asyncio.to_thread(project_migration_failure, scope.project_name, services.projects)
        if failure is not None:
            return ToolOutcome(problem=ToolProblem(MIGRATION_FAILURE_CODE, failure.reason))
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

    script: str = Field(min_length=1, description="剧本文件名（纯文件名）")
    item_id: str = Field(min_length=1, description="分镜条目 id")


async def get_prompt_preview(
    request: ToolRequest[PromptPreviewRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ItemPromptPreview]:
    """渲染一个条目当前会送进图像 / 视频模型的最终提示词文本。

    与执行路径共用同一渲染出口，结果逐字等于本次生成实际发出的提示词。只读：不向供应商
    发请求、不产生费用、不写产物清单。``unavailable`` 是稳定的机器可读原因码，两个 host
    原样透传，UI 侧的成品文案由 REST 路由按请求语言渲染。
    """
    filename = request.value.script
    if "/" in filename or "\\" in filename or filename in {".", ".."}:
        return ToolOutcome(problem=ToolProblem("invalid_request", "script 必须是纯文件名"))
    try:
        failure = await asyncio.to_thread(project_migration_failure, scope.project_name, services.projects)
        if failure is not None:
            return ToolOutcome(problem=ToolProblem(MIGRATION_FAILURE_CODE, failure.reason))
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
    _request: ToolRequest[None],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[SourceFilesContent]:
    try:
        project_dir = services.projects.get_project_path(scope.project_name)
        files = await asyncio.to_thread(
            _business_file_entries,
            project_dir,
            source_only=True,
        )
        revision = prefixed_canonical_json_digest([entry.model_dump() for entry in files])
        return ToolOutcome(value=SourceFilesContent(revision=revision, files=files))
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("list_source_files", exc))


async def get_source_text(
    request: ToolRequest[str],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[SourceTextContent]:
    try:
        project_dir = services.projects.get_project_path(scope.project_name)
        if not request.value.startswith("source/"):
            raise ValueError("path 必须指向 source/ 下的文本文件")
        content, revision, etag, _size = await asyncio.to_thread(_decode_business_file, project_dir, request.value)
        if not isinstance(content, str):
            raise ValueError("source 文件必须是文本")
        return ToolOutcome(value=SourceTextContent(revision=revision, etag=etag, path=request.value, text=content))
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("get_source_text", exc))


def _get_script_plan_content_sync(
    project_name: str, episode: int, projects: ProjectManager
) -> ScriptPlanContent | None:
    project = projects.load_project_readonly(project_name)
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
    request: ToolRequest[int],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ScriptPlanContent]:
    episode = request.value
    if not isinstance(episode, int) or isinstance(episode, bool) or episode < 1:
        return ToolOutcome(problem=ToolProblem("invalid_request", "episode 必须是正整数"))
    try:
        result = await asyncio.to_thread(_get_script_plan_content_sync, scope.project_name, episode, services.projects)
        if result is None:
            return ToolOutcome(problem=ToolProblem("script_plan_not_applicable", "当前项目没有 script_plan 中间态"))
        return ToolOutcome(value=result)
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("get_script_plan_content", exc))


async def list_project_files(
    _request: ToolRequest[None],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ProjectFilesContent]:
    try:
        files = await asyncio.to_thread(
            _business_file_entries,
            services.projects.get_project_path(scope.project_name),
        )
        revision = prefixed_canonical_json_digest([entry.model_dump() for entry in files])
        return ToolOutcome(value=ProjectFilesContent(revision=revision, files=files))
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("list_project_files", exc))


async def read_project_file(
    request: ToolRequest[str],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[ProjectFileContent]:
    try:
        project_dir = services.projects.get_project_path(scope.project_name)
        content, revision, etag, _size = await asyncio.to_thread(_decode_business_file, project_dir, request.value)
        return ToolOutcome(value=ProjectFileContent(revision=revision, etag=etag, path=request.value, content=content))
    except Exception as exc:
        return ToolOutcome(problem=_file_problem("read_project_file", exc))


def _draft_workflow(scope: ProjectScope, services: Services) -> DraftWorkflow:
    return DraftWorkflow(
        DraftContext(
            project_name=scope.project_name,
            projects_root=scope.projects_root,
            pm=services.projects,
            config_resolver=services.capabilities,
        )
    )


async def _run_draft(call: Awaitable[dict[str, Any]]) -> ToolOutcome[dict[str, Any]]:
    try:
        return ToolOutcome(value=await call)
    except DraftWorkflowError as exc:
        return ToolOutcome(problem=ToolProblem(exc.code, exc.detail))
    except Exception as exc:
        return ToolOutcome(problem=ToolProblem("internal_error", str(exc)))


async def open_draft(
    request: ToolRequest[DraftLocator],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    locator = request.value
    return await _run_draft(_draft_workflow(scope, services).open(locator.episode, locator.doc_type, locator.source))


async def patch_draft(
    request: ToolRequest[PatchDraftRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    patch = request.value
    return await _run_draft(
        _draft_workflow(scope, services).patch(
            patch.episode,
            patch.doc_type,
            patch.content,
            patch.base_revision,
            accept_formal_revision=patch.accept_formal_revision,
            accepts_formal_revision=patch.accepts_formal_revision,
            source=patch.source,
            updates_source=patch.updates_source,
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
            promotion.episode,
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
        _draft_workflow(scope, services).discard(discard.episode, discard.doc_type, discard.base_revision)
    )


class CreateProjectToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    title: str = ""
    content_mode: ContentMode = "narration"
    source_kind: SourceKind = "novel"
    generation_mode: Literal["storyboard", "reference_video"] = "storyboard"
    grid_storyboard: bool = False
    aspect_ratio: str = "9:16"
    default_duration: int | None = Field(default=None, gt=0)
    target_duration: int | None = Field(default=None, gt=0)
    brief: str | None = None

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
    model_config = ConfigDict(extra="forbid")

    filename: str
    content: str
    on_conflict: OnConflict = "fail"


async def list_projects(
    _request: ToolRequest[None],
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[list[dict[str, Any]]]:
    def _list() -> list[dict[str, Any]]:
        result = []
        for name in sorted(services.projects.list_projects()):
            try:
                project = services.projects.load_project_readonly(name)
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


async def create_project(
    request: ToolRequest[CreateProjectToolRequest],
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    def _create() -> dict[str, Any]:
        value = request.value
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
                source_kind=value.source_kind,
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


async def upload_source(
    request: ToolRequest[UploadSourceRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)

    def _upload() -> dict[str, Any]:
        value = request.value
        if Path(value.filename).name != value.filename or "\\" in value.filename or value.filename.startswith("."):
            raise ValueError("filename 必须是不含路径的非隐藏文件名")
        suffix = Path(value.filename).suffix.lower()
        if suffix not in {".txt", ".md"}:
            raise UnsupportedFormatError(ext=suffix)
        if not services.projects.project_exists(scope.project_name):
            raise FileNotFoundError(f"项目 '{scope.project_name}' 缺少 project.json")
        source_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=Path(value.filename).suffix, delete=False) as source:
                source_path = Path(source.name)
                source.write(value.content.encode("utf-8"))
                source.flush()
            with services.projects.locked_source_mutation(scope.project_name) as source_dir:
                result = SourceLoader.load(
                    source_path,
                    source_dir,
                    original_filename=value.filename,
                    on_conflict=value.on_conflict,
                )
        finally:
            if source_path is not None:
                source_path.unlink(missing_ok=True)
        return {
            "filename": result.normalized_path.name,
            "path": f"source/{result.normalized_path.name}",
            "original_filename": result.original_filename,
            "original_kept": result.raw_path is not None,
            "used_encoding": result.used_encoding,
            "chapter_count": result.chapter_count,
        }

    try:
        return ToolOutcome(value=await _run_sync_transaction(_upload))
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("project_not_found", str(exc)))
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
    _request: ToolRequest[None],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    try:
        project = await asyncio.to_thread(services.projects.load_project_readonly, scope.project_name)
        payload = await services.capabilities.video_capabilities_for_project(project)
        await annotate_reference_unit_tiers(payload, project, config_resolver=services.capabilities)
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("project_not_found", f"项目未找到或缺 project.json: {exc}"))
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
            anchor = next(i for i, item in enumerate(items) if str(item.get(id_field)) == insert_after_id)
            append(
                {"op": "insert_after", "after_id": insert_after_id, "item": copy.deepcopy(items[anchor + 1])},
                index,
                fresh_insert=True,
            )
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
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("script_not_found", str(exc)))
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
) -> ToolOutcome[ScriptBatchEditResult]:
    return await _run_sync_transaction(_patch_episode_script_sync, request, scope, services)


MAX_INSTRUCTIONS_LEN = 4000
ASSET_TABLES = tuple(spec.bucket_key for spec in ASSET_SPECS.values())
PROJECT_SETTINGS = (
    "episode_target_units",
    EPISODE_TARGET_DURATION_FIELD,
    "source_language",
    "brief",
    "planning_window_chars",
    "planning_max_episodes",
    "narration_voice",
    "narration_speed",
    "character_voice_binding",
)
PROJECT_OVERVIEW_FIELDS = ("synopsis", "genre", "theme", "world_setting")
EPISODE_META_FIELDS = ("title",)

_SOURCE_LANGUAGE_VALUES = ("zh", "en", "vi")
_POSITIVE_INT_SETTINGS = ("episode_target_units", "planning_window_chars", "planning_max_episodes")


class ToolMessage(BaseModel):
    message: str


class PlanEpisodesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: str | None = None

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
    model_config = ConfigDict(extra="forbid")

    from_episode: int = Field(strict=True, ge=1)
    confirm_consumed: bool = Field(default=False, strict=True)


class ResetEpisodePlanningResult(ToolMessage):
    confirmation_required: bool
    removed_episodes: list[int] = Field(default_factory=list)
    deleted_files: list[str] = Field(default_factory=list)
    archived_files: list[str] | list[tuple[str, str]] = Field(default_factory=list)
    consumed_episodes: list[int] = Field(default_factory=list)


class PatchProjectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str | None = None
    entries: dict[str, Any] | None = None
    settings: dict[str, Any] | None = None
    overview: dict[str, Any] | None = None

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
    model_config = ConfigDict(extra="forbid")

    script: str
    field: Literal["title"]
    value: str

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
    model_config = ConfigDict(extra="forbid")

    table: str
    old_name: str
    new_name: str

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


class RetryProjectMigrationResult(ToolMessage):
    workflow_plan: WorkflowPlan


class CompleteAssetInventoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: SourceScope
    expected_source_revision: str
    entries: dict[str, Any] | None = None


class CompleteAssetInventoryResult(BaseModel):
    scope: SourceScope
    source_revision: str
    counts: dict[str, int]


class CompleteScriptPlanRebuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode: int = Field(strict=True, ge=1)
    expected_stale_script_plan_revision: str | None


class CompleteScriptPlanRebuildResult(BaseModel):
    episode: int
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
    return {
        "total_episodes": stats.total_episodes,
        "smallest": stats.smallest,
        "median_units": stats.median_units,
        "target_units": stats.target_units,
    }


def _render_ledger_stats(stats: LedgerStats) -> list[str]:
    lines = [f"累计总集数：{stats.total_episodes}"]
    if stats.smallest:
        smallest = "、".join(f"第 {num} 集（约 {units}）" for num, units in stats.smallest)
        lines.append(f"体量最小的几集：{smallest}")
    if stats.median_units is not None:
        lines.append(f"全账本体量中位数：约 {stats.median_units}")
    if stats.target_units is not None:
        lines.append(f"每集目标体量设置：约 {stats.target_units}")
    lines.append("若用户给过总集数、按章节对齐等结构性偏好，请对照以上分布核实，有偏差须向用户明确说明。")
    return lines


def _format_plan(result: PlanResult) -> str:
    if not result.episodes and result.source_exhausted:
        lines = ["源文已全部规划完毕，没有可规划的新内容。"]
        if result.ledger_stats is not None:
            lines += _render_ledger_stats(result.ledger_stats)
        return "\n".join(lines)
    lines = [f"✅ 已规划 {len(result.episodes)} 集："]
    for episode in result.episodes:
        status_note = "（stale，需重做下游产物）" if episode.ledger_status == "stale" else ""
        lines.append(
            f"- 第 {episode.episode} 集《{episode.title}》{status_note}｜体量约 {episode.reading_units}｜钩子：{episode.hook}"
        )
    if result.source_exhausted:
        lines.append("源文已全部规划完毕。")
    elif result.cursor:
        lines.append(f"下一批规划起点：{result.cursor.get('source_file')} 偏移 {result.cursor.get('offset')}")
    if result.ledger_stats is not None:
        lines += _render_ledger_stats(result.ledger_stats)
    else:
        lines.append(f"累计已规划 {result.total_planned} 集。")
    lines.append(
        "请把以上摘要展示给用户做批级审阅；需要调整时先调用 reset_episode_planning 退回到"
        "最早受影响的集，再带 instructions 重新调用本工具。"
    )
    return "\n".join(lines)


async def _execute_plan_episodes(
    request: ToolRequest[PlanEpisodesRequest],
    scope: ProjectScope,
    services: Services,
    *,
    planner_cls: type[EpisodePlanner] = EpisodePlanner,
    cancellation_receipts: list[FormalWriteReceipt] | None = None,
) -> ToolOutcome[Any]:
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    try:
        planner = await planner_cls.create(services.projects.get_project_path(scope.project_name))
        if cancellation_receipts is None:
            result = await planner.plan(instructions=request.value.instructions)
        else:
            result = await planner.plan(
                instructions=request.value.instructions,
                cancellation_receipts=cancellation_receipts,
            )
    except (EpisodePlanningError, FileNotFoundError) as exc:
        return ToolOutcome(problem=ToolProblem("episode_planning_failed", f"❌ 分集规划失败：{exc}"))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("plan_episodes", exc))
    value = PlanEpisodesResult(
        message=_format_plan(result),
        episodes=[
            {
                "episode": episode.episode,
                "title": episode.title,
                "hook": episode.hook,
                "reading_units": episode.reading_units,
                "ledger_status": episode.ledger_status,
            }
            for episode in result.episodes
        ],
        cursor=result.cursor,
        source_exhausted=result.source_exhausted,
        total_planned=result.total_planned,
        ledger_stats=_ledger_stats_payload(result.ledger_stats),
    )
    if cancellation_receipts is None:
        return ToolOutcome(value=value)
    if not cancellation_receipts:
        return ToolOutcome(value=value)
    if len(cancellation_receipts) != 1:
        raise RuntimeError("episode planning commit did not return cancellation state")
    receipt = cancellation_receipts[0]
    project_path = services.projects.get_project_path(scope.project_name)

    def _compensate_cancelled() -> None:
        with project_metadata_lock(project_path):
            receipt.compensate_cancelled()

    return ToolOutcome(
        value=CompensableTextGenerationResult(
            value.message,
            _compensate_cancelled,
            payload=value.model_dump(mode="json"),
        )
    )


async def plan_episodes(
    request: ToolRequest[PlanEpisodesRequest],
    scope: ProjectScope,
    caller: CallerContext,
    services: Services,
    *,
    planner_cls: type[EpisodePlanner] = EpisodePlanner,
) -> ToolOutcome[Any]:
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    if planner_cls is not EpisodePlanner:
        return await _execute_plan_episodes(request, scope, services, planner_cls=planner_cls)
    return await _submit_text_task(
        task_type=_TEXT_EPISODE_PLAN,
        operation="plan_episodes",
        unit_id="episode-planning",
        payload=request.value.model_dump(mode="json"),
        scope=scope,
        caller=caller,
        services=services,
    )


async def execute_queued_text_task(
    task: dict[str, Any], *, planner_cls: type[EpisodePlanner] = EpisodePlanner
) -> dict[str, Any]:
    """Execute one durable text task through the same host-independent handlers."""
    payload = task.get("payload") or {}
    scope = ProjectScope(project_name=str(task["project_name"]), projects_root=Path(payload["projects_root"]))
    services = _TEXT_TASK_SERVICES.pop(str(task["task_id"]), None)
    if services is None:
        projects = ProjectManager(str(payload["projects_root"]))
        services = Services(
            projects=projects,
            workflow_planner=WorkflowPlanner(projects),
            capabilities=ConfigResolver(async_session_factory),
        )
    task_type = task["task_type"]
    if task_type == _TEXT_EPISODE_PLAN:
        cancellation_receipts: list[FormalWriteReceipt] = []
        outcome = await _execute_plan_episodes(
            ToolRequest(PlanEpisodesRequest(instructions=payload.get("instructions"))),
            scope,
            services,
            planner_cls=planner_cls,
            cancellation_receipts=cancellation_receipts if planner_cls is EpisodePlanner else None,
        )
    else:
        request = TextGenerationRequest(
            episode=payload["episode"],
            source=payload.get("source"),
            instructions=payload.get("instructions"),
            dry_run=bool(payload.get("dry_run")),
            scope=payload.get("scope") or SCOPE_STALE,
            entry_ids=tuple(payload.get("entry_ids") or ()),
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
    if isinstance(value, CompensableTextGenerationResult):
        return CompensableGenerationResult(
            value.payload or {"message": value.message},
            cancel_compensation=value.compensate_cancelled,
        )
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
    try:
        result = await _run_sync_transaction(
            resetter,
            services.projects.get_project_path(scope.project_name),
            from_episode=value.from_episode,
            confirm_consumed=value.confirm_consumed,
        )
    except (EpisodeResetError, FileNotFoundError) as exc:
        return ToolOutcome(problem=ToolProblem("episode_reset_failed", f"❌ 分集规划重置失败：{exc}"))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("reset_episode_planning", exc))

    if isinstance(result, ResetConfirmationRequired):
        episodes = "、".join(str(num) for num in result.consumed_episodes)
        aftermath = (
            "账本清空后这些集需要重新规划"
            if value.from_episode == 1
            else f"这些集的账本条目被清除后需要重新规划，第 1..{value.from_episode - 1} 集保留不动"
        )
        lines = [
            f"⚠️ 本次重置会波及已消费集（已有 script_plan/剧本/媒体产物）：第 {episodes} 集。尚未执行任何改动。",
            "请把影响范围告知用户；用户确认后带 confirm_consumed=true 重新调用"
            f"（剧本与媒体产物不会被删除，但{aftermath}）。",
        ]
        if result.archived_files:
            lines.append(f"其中无原文范围记录的集文件会改名留底：{'、'.join(result.archived_files)}")
        return ToolOutcome(
            value=ResetEpisodePlanningResult(
                message="\n".join(lines),
                confirmation_required=True,
                archived_files=result.archived_files,
                consumed_episodes=result.consumed_episodes,
            )
        )

    if value.from_episode == 1:
        lines = [f"✅ 已全量重置分集规划：清空 {len(result.removed_episodes)} 集，planning_cursor 已置空。"]
    else:
        lines = [
            f"✅ 已部分重置分集规划：清空第 {value.from_episode} 集起共 {len(result.removed_episodes)} 集，"
            f"planning_cursor 已退到第 {value.from_episode - 1} 集原文范围末尾。"
        ]
    if result.deleted_files:
        lines.append(f"已删除可重造的派生集文件 {len(result.deleted_files)} 个。")
    if result.archived_files:
        archived = "、".join(f"{src} → {dst}" for src, dst in result.archived_files)
        lines.append(f"无原文范围记录的集文件已改名留底（内容保留）：{archived}")
    if result.consumed_episodes:
        consumed = "、".join(str(num) for num in result.consumed_episodes)
        lines.append(f"第 {consumed} 集的剧本 / 媒体产物仍在磁盘，未删除。")
    lines.append(
        "账本已空，请调用 plan_episodes 从头重新规划（集号从第 1 集起）。"
        if value.from_episode == 1
        else f"请调用 plan_episodes 继续规划（新集号从第 {value.from_episode} 集起）。"
    )
    return ToolOutcome(
        value=ResetEpisodePlanningResult(
            message="\n".join(lines),
            confirmation_required=False,
            removed_episodes=result.removed_episodes,
            deleted_files=result.deleted_files,
            archived_files=result.archived_files,
            consumed_episodes=result.consumed_episodes,
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
    _request: ToolRequest[None],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[RetryProjectMigrationResult]:
    project_dir = services.projects.get_project_path(scope.project_name)
    try:
        failure = await _run_sync_transaction(migrate_project_with_verdict, project_dir)
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
            residual = load_migration_failure(project_dir)
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


async def complete_asset_inventory(
    request: ToolRequest[CompleteAssetInventoryRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
    *,
    run_sync: Callable[..., Awaitable[Any]] = asyncio.to_thread,
    complete: Callable[..., Any] = complete_asset_inventory_service,
) -> ToolOutcome[CompleteAssetInventoryResult]:
    if problem := await migration_gate(scope, services):
        return ToolOutcome(problem=problem)
    value = request.value
    try:
        completed = await run_sync(
            complete,
            services.projects,
            scope.project_name,
            value.scope,
            value.expected_source_revision,
            value.entries,
        )
    except AssetInventoryRevisionConflict as exc:
        return ToolOutcome(
            problem=ToolProblem(
                "source_revision_conflict",
                str(exc),
                params={
                    "expected_source_revision": exc.expected_revision,
                    "actual_source_revision": exc.actual_revision,
                },
            )
        )
    except AssetInventorySourceBlocked as exc:
        return ToolOutcome(
            problem=ToolProblem(
                "source_blocked",
                str(exc),
                params={"blockers": [blocker.model_dump(mode="json") for blocker in exc.blockers]},
            )
        )
    except AssetInventoryInvalidRequest as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except AssetInventoryError as exc:
        return ToolOutcome(problem=ToolProblem("inventory_unavailable", str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("complete_asset_inventory", exc))
    return ToolOutcome(
        value=CompleteAssetInventoryResult(
            scope=completed.scope,
            source_revision=completed.source_revision,
            counts=completed.counts,
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
            value.episode,
            value.expected_stale_script_plan_revision,
        )
    except ScriptPlanRebuildCompletionError as exc:
        return ToolOutcome(problem=ToolProblem(exc.code, str(exc)))
    except Exception as exc:
        return ToolOutcome(problem=_unexpected("complete_script_plan_rebuild", exc))
    return ToolOutcome(value=CompleteScriptPlanRebuildResult(episode=value.episode, script_plan_revision=revision))


__all__ = [
    "ASSET_TABLES",
    "EPISODE_META_FIELDS",
    "MAX_INSTRUCTIONS_LEN",
    "PROJECT_OVERVIEW_FIELDS",
    "PROJECT_SETTINGS",
    "CallerContext",
    "CompleteAssetInventoryRequest",
    "CompleteScriptPlanRebuildRequest",
    "CreateProjectToolRequest",
    "DiscardDraftRequest",
    "DraftLocator",
    "EpisodeScriptContent",
    "GenerationBatchToolRequest",
    "PatchDraftRequest",
    "PatchEpisodeMetaRequest",
    "PatchEpisodeScriptRequest",
    "PatchProjectRequest",
    "PlanEpisodesRequest",
    "ProjectContent",
    "ProjectFileContent",
    "ProjectFileEntry",
    "ProjectFilesContent",
    "ProjectScope",
    "PromoteDraftRequest",
    "PromptPreviewRequest",
    "RenameAssetRequest",
    "ResetEpisodePlanningRequest",
    "ScriptPlanContent",
    "Services",
    "SourceFilesContent",
    "SourceTextContent",
    "TextGenerationError",
    "TextGenerationRequest",
    "TextGenerationResult",
    "ToolOutcome",
    "ToolProblem",
    "ToolRequest",
    "UploadSourceRequest",
    "cancel_generation_batch",
    "complete_asset_inventory",
    "complete_script_plan_rebuild",
    "confirm_script_review",
    "create_project",
    "discard_draft",
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
