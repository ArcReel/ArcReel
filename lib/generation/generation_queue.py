"""
Async generation task queue shared by WebUI and skills.

Wraps TaskRepository with a module-level singleton pattern.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from lib.backends.backend_runtime import install_provider_job_id_store
from lib.catalog.errors import ModelNotInCatalogError
from lib.db import safe_session_factory
from lib.db.base import DEFAULT_USER_ID
from lib.db.repositories.task_repo import TaskNotCancellableError, TaskRepository
from lib.generation.generation_admission import generation_admission_lock
from lib.generation.generation_batch import (
    GenerationBatchBlockedItem,
    GenerationBatchCancelResult,
    GenerationBatchReadModel,
    GenerationBatchRequestSnapshot,
    build_generation_batch_read_model,
    validate_blocked_items,
)
from lib.generation.render_lane import RENDER_MEDIA_TYPE, RENDER_PROVIDER_ID, is_render_task_type
from lib.generation.task_terminal_events import TERMINAL_TASK_STATUSES, emit_task_terminal_events
from lib.infra.async_thread import run_noninterruptible_async, run_sync_transaction
from lib.project.asset_derivatives import DERIVATIVE_TASK_TYPE
from lib.project.project_manager import ProjectManager
from lib.project.project_migration_guard import assert_project_migration_ok

if TYPE_CHECKING:
    from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
    from lib.config.resolver import ConfigResolver, ProviderModel, VideoGenerationType
    from lib.script.reference_video.request_projection import ReferenceUnitRequestProjection

logger = logging.getLogger(__name__)


def _normalized_script_file(script_file: str | None) -> str | None:
    """Shared script lookup for enqueueing and in-flight detection: `scripts/episode_N.json` and `episode_N.json` alias the same script.

    Deduplication keys include script filenames. Web supplies ledger values, Agent bare filenames; without normalization the same unit becomes two tasks.
    """
    return ProjectManager.normalize_script_filename(script_file) if script_file is not None else None


_VIDEO_EXECUTION_IDENTITY_KEYS = frozenset({"video_provider_i2v", "video_provider_r2v"})
_REFERENCE_VIDEO_ENQUEUE_PAYLOAD_KEYS = frozenset({"script_file", "reference_request_options"})


#: Older enqueued text tasks carry enqueue-time data roots in payloads; execution resolves current configuration, and request equality also ignores them.
_LEGACY_TEXT_PAYLOAD_KEYS = frozenset({"projects_root"})


def text_task_request_facts(payload: dict[str, Any] | None) -> dict[str, Any]:
    """The text-task payload portion describing this request, used to determine whether two submissions are the same request."""
    return {key: value for key, value in (payload or {}).items() if key not in _LEGACY_TEXT_PAYLOAD_KEYS}


def _text_request_facts(task_type: str, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if not task_type.startswith("text_"):
        return None
    return text_task_request_facts(payload)


def _render_request_facts(task_type: str, payload: dict[str, Any] | None) -> dict[str, Any] | None:
    """Entire rendering payloads are requests (edit timelines, revisions and versions); different requests for the same artifact identity do not deduplicate."""
    return dict(payload or {}) if is_render_task_type(task_type) else None


class ActiveTaskRequestConflict(RuntimeError):
    """An active video task owns the resource with different request facts."""

    def __init__(self, *, resource_id: str, existing_task_id: str) -> None:
        self.resource_id = resource_id
        self.existing_task_id = existing_task_id
        super().__init__(
            f"resource '{resource_id}' already has active task '{existing_task_id}' with different request options"
        )


class GenerationBatchNotFound(ValueError):
    pass


async def cleanup_fresh_generation_batch(
    queue: GenerationQueue,
    *,
    project_name: str,
    batch_id: str,
    user_id: str,
    failure: BaseException,
) -> None:
    """Settle best-effort cleanup without replacing the submission failure."""

    try:
        await run_noninterruptible_async(
            queue.delete_fresh_generation_batch(
                project_name=project_name,
                batch_id=batch_id,
                user_id=user_id,
            )
        )
    except BaseException as cleanup_failure:
        failure.add_note(f"fresh batch cleanup also failed: {cleanup_failure}")


def _reference_request_facts(task_type: str, payload: dict[str, Any] | None) -> dict[str, object] | None:
    if task_type != "reference_video":
        return None

    from lib.script.reference_video.request_projection import ReferenceRequestOptions

    return ReferenceRequestOptions.from_payload(payload or {}).to_payload()


class DispatchProviderChanged(RuntimeError):
    """Execution projections differ from provider slots claimed by workers; return to the queue for a new claim."""

    def __init__(self, *, claimed_provider_id: str, actual_provider_id: str) -> None:
        self.claimed_provider_id = claimed_provider_id
        self.actual_provider_id = actual_provider_id
        super().__init__(f"dispatch provider changed: {claimed_provider_id} -> {actual_provider_id}")


def ensure_dispatch_provider(claimed_provider_id: str | None, actual_provider_id: str) -> None:
    """Check execution entry: raise :class:`DispatchProviderChanged` if the provider resolved from current project state differs from the claimed slot.

    Callers must check before any provider call. None ``claimed_provider_id`` means direct calls without worker
    claims, so skip checking.
    """
    if claimed_provider_id is not None and actual_provider_id != claimed_provider_id:
        raise DispatchProviderChanged(claimed_provider_id=claimed_provider_id, actual_provider_id=actual_provider_id)


def reference_video_enqueue_payload(
    payload: dict[str, Any] | None,
    *,
    script_file: str | None,
) -> dict[str, Any]:
    """Narrow newly enqueued reference-video payloads to locators and request options.

    Mutable request facts such as prompt, references, style and duration are not task snapshots; workers
    reproject current project/script/unit state when starting. Post-submission execution identities live in dedicated checkpoints,
    outside this enqueue boundary.
    """

    normalized = {key: value for key, value in (payload or {}).items() if key in _REFERENCE_VIDEO_ENQUEUE_PAYLOAD_KEYS}
    if "reference_request_options" in normalized:
        from lib.script.reference_video.request_projection import ReferenceRequestOptions

        normalized["reference_request_options"] = ReferenceRequestOptions.from_payload(
            {"reference_request_options": normalized["reference_request_options"]}
        ).to_payload()
    if script_file is not None:
        normalized["script_file"] = script_file
    return normalized


def without_video_execution_identity(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Remove enqueue-time video identities from payloads so unsubmitted tasks project current configuration."""

    return {key: value for key, value in (payload or {}).items() if key not in _VIDEO_EXECUTION_IDENTITY_KEYS}


without_reference_video_execution_identity = without_video_execution_identity


async def video_bucket_for_queued_task(
    *,
    project: dict | None,
    project_name: str | None,
    task_type: str,
    payload: dict[str, Any] | None,
    resource_id: str | None,
) -> VideoGenerationType | None:
    """Shared video-task bucketing for enqueue derivation and worker rate-limit projections, aligned with execution (docs/adr/0054).

    Image-to-video / grids use i2v; reference video uses shared request projection to route by currently available assets:
    units without reference images use i2v, others r2v. Unreadable scripts / units / capabilities fall back to representative bucket r2v;
    fallback affects only claim-pool filtering and rate-limit slot routing accuracy. Execution still reprojects when processing starts.
    Task types outside this table return None (no bucket).
    """
    from lib.config.resolver import VIDEO_BUCKET_BY_TASK_TYPE

    if task_type != "reference_video":
        return VIDEO_BUCKET_BY_TASK_TYPE.get(task_type)
    fallback = VIDEO_BUCKET_BY_TASK_TYPE["reference_video"]
    projection = await reference_projection_for_queued_task(
        project=project,
        project_name=project_name,
        payload=payload,
        resource_id=resource_id,
    )
    return projection.hydrated_generation_type if projection is not None else fallback


async def reference_projection_for_queued_task(
    *,
    project: dict | None,
    project_name: str | None,
    payload: dict[str, Any] | None,
    resource_id: str | None,
) -> ReferenceUnitRequestProjection | None:
    """Read current units from task locators and build current-state projections shared by claim/rate limiting."""

    from lib.config.resolver import get_project_manager
    from lib.script.reference_video.request_projection import (
        ReferenceRequestOptions,
        project_reference_unit_request,
    )
    from lib.script.reference_video.units import find_reference_unit

    script_file = (payload or {}).get("script_file")
    if project is None or not project_name or not script_file or not resource_id:
        return None
    try:
        pm = get_project_manager()
        script = await run_sync_transaction(pm.load_script, project_name, str(script_file))
        project_path = await asyncio.to_thread(pm.get_project_path, project_name)
        unit = find_reference_unit(script, str(resource_id))
        if unit is None:
            return None
        return await project_reference_unit_request(
            project=project,
            script=script,
            unit=unit,
            project_path=project_path,
            # Claim/rate-limit routing only needs the hydrated visual generation type.
            options=ReferenceRequestOptions(),
            check_asset_links=False,
        )
    except Exception:
        logger.debug("reference_video 当前请求投影失败，限流路由回退代表桶", exc_info=True)
        return None


async def resolve_video_execution_for_queued_task(
    *,
    resolver: ConfigResolver,
    project: dict | None,
    project_name: str | None,
    task_type: str,
    payload: dict[str, Any] | None,
    resource_id: str | None,
) -> tuple[ProviderModel, VideoGenerationType | None]:
    """Resolve current queue-video identities and task-type buckets shared by enqueue advisory and worker rate limiting."""

    projection = (
        await reference_projection_for_queued_task(
            project=project,
            project_name=project_name,
            payload=payload,
            resource_id=resource_id,
        )
        if task_type == "reference_video"
        else None
    )
    if projection is not None and projection.request_facts is not None:
        from lib.config.resolver import ProviderModel

        return ProviderModel(
            projection.provider_id or "", projection.model_id or ""
        ), projection.hydrated_generation_type

    generation_type = await video_bucket_for_queued_task(
        project=project,
        project_name=project_name,
        task_type=task_type,
        payload=payload,
        resource_id=resource_id,
    )
    execution_payload = (
        without_video_execution_identity(payload) if task_type in ("video", "reference_video") else payload
    )
    resolved = await resolver.resolve_video_backend(project, execution_payload or {}, generation_type=generation_type)
    return resolved, generation_type


async def _derive_execution_model_for_enqueue(
    *,
    project_name: str | None,
    payload: dict[str, Any] | None,
    task_type: str,
    media_type: str,
    resource_id: str | None,
) -> tuple[ProviderModel, VideoGenerationType | None] | None:
    """Derive advisory providers and video buckets from project + payload at enqueue time.

    ``provider_id`` persists in task rows; workers use it for full-pool filtering and slot ownership when claiming. Both video-generation modes store
    advisory providers only. Execution checks current project state, rewriting records and requeueing mismatches. This uses the same resolution
    as worker ``_extract_provider``; catalog errors retain their exception's provider, other failures return ``None`` (never force DEFAULT_PROVIDER). Tasks then use the ``provider_id IS NULL``
    fallback, resolving after worker claims, safer than forcing a possibly incorrect provider.
    """
    if media_type == RENDER_MEDIA_TYPE:
        from lib.config.resolver import ProviderModel

        return ProviderModel(RENDER_PROVIDER_ID, ""), None
    is_text = media_type == "text"
    is_video = media_type == "video" or task_type in ("video", "reference_video")
    is_audio = media_type == "audio" or task_type == "tts"
    video_generation_type: VideoGenerationType | None = None
    try:
        # Local import: lib.config resolution imports backend and custom-provider assembly. Enqueue paths avoid paying
        # that module-level import cost.
        from lib.config.resolver import ConfigResolver, get_project_manager
        from lib.db import async_session_factory

        project: dict | None = None
        if project_name:
            project = await asyncio.to_thread(get_project_manager().load_project, project_name)

        resolver = ConfigResolver(async_session_factory)
        if is_text:
            from lib.config.resolver import ProviderModel

            resolved = ProviderModel("text", "")
        elif is_video:
            resolved, video_generation_type = await resolve_video_execution_for_queued_task(
                resolver=resolver,
                project=project,
                project_name=project_name,
                task_type=task_type,
                payload=payload,
                resource_id=resource_id,
            )
        elif is_audio:
            resolved = await resolver.resolve_audio_backend(project, payload or {})
        else:
            # image_edit / derivative asset images necessarily use i2i, known at enqueue time (the sole exception, docs/adr/0001). Resolve through i2i
            # slots; other image-task types are determined at execution, so use t2i as the representative provider.
            generation_type = "i2i" if task_type in I2I_ONLY_TASK_TYPES else "t2i"
            resolved = await resolver.resolve_image_backend(project, payload or {}, generation_type=generation_type)
    except ModelNotInCatalogError as exc:
        from lib.config.resolver import ProviderModel

        return ProviderModel(exc.provider_id, exc.model_id), video_generation_type
    except Exception:
        logger.debug("入队时派生执行身份失败，留 NULL 由 worker 兜底", exc_info=True)
        return None
    if not resolved.provider_id:
        return None
    return resolved, video_generation_type


#: Image task types necessarily using i2i, known at enqueue time (sole exception in ``docs/adr/0001``): image edits use the current image
#: as sole input, derivative asset images the base asset image. Enqueue derivation and worker rate-limit routing therefore resolve exact i2i
#: slots; other image tasks use t2i as representative provider.
I2I_ONLY_TASK_TYPES: frozenset[str] = frozenset({"image_edit", DERIVATIVE_TASK_TYPE})

ACTIVE_TASK_STATUSES = ("queued", "running")
TASK_WORKER_LEASE_TTL_SEC = 10.0
TASK_WORKER_HEARTBEAT_SEC = 3.0
TASK_POLL_INTERVAL_SEC = 1.0

_QUEUE_LOCK = threading.Lock()
_QUEUE_INSTANCE: GenerationQueue | None = None


class GenerationQueue:
    """Async queue manager wrapping TaskRepository."""

    def __init__(
        self,
        *,
        session_factory=None,
        project_manager: ProjectManager | None = None,
    ):
        self._session_factory = session_factory or safe_session_factory
        self._project_manager = project_manager

    @property
    def session_factory(self):
        """Session factory for this queue's persistence; collaborating accounting writes use the same wiring."""
        return self._session_factory

    async def assert_project_migration_ok(self, project_name: str) -> None:
        await asyncio.to_thread(assert_project_migration_ok, project_name, self._project_manager)

    @asynccontextmanager
    async def _task_repo(self) -> AsyncGenerator[TaskRepository]:
        """Open a TaskRepository session, publishing persisted terminal task states to the project event bus on exit.

        Publish after session exit, outside the repo: publishing inside lets frontend readers see uncommitted state.
        If the body raises, the session does not commit or publish; without persisted terminal states there is nothing to announce.

        Every method obtains its repo here, making terminal-event publication structural rather than dependent on remembering hooks:
        new terminal-writing methods automatically publish without omissions.
        """
        async with self._session_factory() as session:
            repo = TaskRepository(session)
            yield repo
        emit_task_terminal_events(repo.terminal_events)

    async def enqueue_task(
        self,
        *,
        project_name: str,
        task_type: str,
        media_type: str,
        resource_id: str,
        payload: dict[str, Any] | None = None,
        script_file: str | None = None,
        resource_type: str | None = None,
        source: str = "webui",
        dependency_task_id: str | None = None,
        dependency_group: str | None = None,
        dependency_index: int | None = None,
        user_id: str = DEFAULT_USER_ID,
        provider_id: str | None = None,
        batch_id: str | None = None,
        batch_unit_id: str | None = None,
        batch_unit_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        # Every Web, Agent and batch generation entry reaches the queue through this
        # method, so a project whose migration failed is refused here once instead of
        # at each caller. Nothing is created and nothing is billed.
        await self.assert_project_migration_ok(project_name)

        script_file = _normalized_script_file(script_file)

        if task_type == "reference_video":
            payload = reference_video_enqueue_payload(payload, script_file=script_file)

        # No caller provider_id: derive at enqueue time so claims use the SQL pool-filter fast path;
        # failed derivation leaves NULL for the IS NULL fallback, resolved by worker _extract_provider after claiming.
        # Successful derivation makes provider_id claim/rate-limit advisory only; video models do not freeze at enqueue time.
        if provider_id is None:
            derived = await _derive_execution_model_for_enqueue(
                project_name=project_name,
                payload=payload,
                task_type=task_type,
                media_type=media_type,
                resource_id=resource_id,
            )
            if derived is not None:
                execution_model, _video_generation_type = derived
                provider_id = execution_model.provider_id
                # Video provider/model is only an advisory claim projection until the worker materializes the
                # current request and persists its pre-submit checkpoint. Enqueue payload never freezes identity.

        requested_facts = _reference_request_facts(task_type, payload)
        text_request_facts = _text_request_facts(task_type, payload)
        render_request_facts = _render_request_facts(task_type, payload)

        def _guard_deduped(existing_payload: dict[str, Any], existing_task_id: str) -> None:
            if requested_facts is not None and _reference_request_facts(task_type, existing_payload) != requested_facts:
                raise ActiveTaskRequestConflict(resource_id=resource_id, existing_task_id=existing_task_id)
            if (
                text_request_facts is not None
                and _text_request_facts(task_type, existing_payload) != text_request_facts
            ):
                raise ActiveTaskRequestConflict(resource_id=resource_id, existing_task_id=existing_task_id)
            if (
                render_request_facts is not None
                and _render_request_facts(task_type, existing_payload) != render_request_facts
            ):
                raise ActiveTaskRequestConflict(resource_id=resource_id, existing_task_id=existing_task_id)

        async def _enqueue() -> dict[str, Any]:
            async with self._task_repo() as repo:
                return await repo.enqueue(
                    project_name=project_name,
                    task_type=task_type,
                    media_type=media_type,
                    resource_id=resource_id,
                    payload=payload,
                    script_file=script_file,
                    resource_type=resource_type,
                    source=source,
                    dependency_task_id=dependency_task_id,
                    dependency_group=dependency_group,
                    dependency_index=dependency_index,
                    user_id=user_id,
                    provider_id=provider_id,
                    batch_id=batch_id,
                    batch_unit_id=batch_unit_id,
                    batch_unit_ids=batch_unit_ids,
                    dedupe_guard=_guard_deduped,
                )

        # Audio restoration observes active TTS/video consumers while holding the
        # same per-unit admission guard.  Keeping the guard here makes every Web,
        # Agent, and batch enqueue entry participate without duplicating checks.
        if task_type in {"tts", "video", "reference_video"} and script_file:
            async with generation_admission_lock(
                project_name=project_name,
                script_file=script_file,
                resource_id=resource_id,
            ):
                result = await _enqueue()
        else:
            result = await _enqueue()
        # The unique index intentionally protects one active task per resource. A matching
        # video task is reusable only when its durable narration request facts also match;
        # current TTS evidence and other mutable generation intent are re-read by the worker.
        result.pop("_existing_payload", None)
        if not result.get("deduped"):
            logger.info("任务入队 task_id=%s type=%s", result["task_id"], task_type)
        else:
            logger.debug("任务去重 task_id=%s", result["task_id"])
        return result

    async def create_generation_batch(
        self,
        *,
        project_name: str,
        operation: str,
        requested: GenerationBatchRequestSnapshot,
        blocked: list[GenerationBatchBlockedItem],
        source: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> str:
        validate_blocked_items(requested, blocked)
        batch_id = uuid.uuid4().hex
        try:
            async with self._task_repo() as repo:
                await repo.create_batch(
                    batch_id=batch_id,
                    project_name=project_name,
                    operation=operation,
                    requested=requested.model_dump(mode="json"),
                    blocked=[item.model_dump(mode="json") for item in blocked],
                    source=source,
                    user_id=user_id,
                )
        except BaseException as failure:
            await cleanup_fresh_generation_batch(
                self,
                project_name=project_name,
                batch_id=batch_id,
                user_id=user_id,
                failure=failure,
            )
            raise
        return batch_id

    async def get_generation_batch(
        self,
        *,
        project_name: str,
        batch_id: str,
        user_id: str = DEFAULT_USER_ID,
        resolver: ArtifactCurrencyResolver | None = None,
    ) -> GenerationBatchReadModel:
        async with self._task_repo() as repo:
            batch = await repo.get_batch(project_name=project_name, batch_id=batch_id, user_id=user_id)
        if batch is None:
            raise GenerationBatchNotFound(f"batch '{batch_id}' does not belong to project '{project_name}'")
        # Read models determine member-artifact currency from manifests/content hashes; disk reads and manifest-lock waits run in worker threads.
        return await asyncio.to_thread(
            build_generation_batch_read_model, batch, batch["memberships"], batch["queue_depth"], resolver
        )

    async def delete_fresh_generation_batch(
        self,
        *,
        project_name: str,
        batch_id: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> int:
        async with self._task_repo() as repo:
            return await repo.delete_fresh_batch(project_name=project_name, batch_id=batch_id, user_id=user_id)

    async def attach_task_to_generation_batch(
        self,
        *,
        project_name: str,
        batch_id: str,
        task_id: str,
        unit_id: str,
        deduped: bool,
        user_id: str = DEFAULT_USER_ID,
    ) -> None:
        async with self._task_repo() as repo:
            await repo.attach_batch_task(
                project_name=project_name,
                batch_id=batch_id,
                task_id=task_id,
                unit_id=unit_id,
                deduped=deduped,
                user_id=user_id,
            )

    async def cancel_generation_batch(
        self,
        *,
        project_name: str,
        batch_id: str,
        user_id: str = DEFAULT_USER_ID,
    ) -> GenerationBatchCancelResult:
        async with self._task_repo() as repo:
            batch = await repo.get_batch(project_name=project_name, batch_id=batch_id, user_id=user_id)
        if batch is None:
            raise GenerationBatchNotFound(f"batch '{batch_id}' does not belong to project '{project_name}'")

        initial = {item["task_id"]: item["status"] for item in batch["memberships"]}
        not_cancellable: set[str] = set()
        for task_id, status in initial.items():
            if status in TERMINAL_TASK_STATUSES:
                continue
            try:
                await self.cancel_task(task_id)
            except TaskNotCancellableError:
                not_cancellable.add(task_id)

        async with self._task_repo() as repo:
            updated = await repo.get_batch(project_name=project_name, batch_id=batch_id, user_id=user_id)
        assert updated is not None
        current = {item["task_id"]: item["status"] for item in updated["memberships"]}
        cancelled: list[str] = []
        skipped_running: list[str] = []
        skipped_terminal: list[str] = []
        for task_id, previous in initial.items():
            if previous in TERMINAL_TASK_STATUSES:
                skipped_terminal.append(task_id)
            elif current[task_id] == "cancelled":
                cancelled.append(task_id)
            elif task_id in not_cancellable:
                skipped_running.append(task_id)
            else:
                skipped_terminal.append(task_id)
        return GenerationBatchCancelResult(
            cancelled=cancelled,
            skipped_running=skipped_running,
            skipped_terminal=skipped_terminal,
        )

    async def get_active_tasks_for_resources(
        self,
        *,
        project_name: str,
        task_type: str,
        resource_ids: list[str],
        script_file: str | None = None,
        resource_type: str | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> list[dict[str, Any]]:
        script_file = _normalized_script_file(script_file)
        async with self._task_repo() as repo:
            return await repo.get_active_tasks_for_resources(
                project_name=project_name,
                task_type=task_type,
                resource_ids=resource_ids,
                script_file=script_file,
                resource_type=resource_type,
                user_id=user_id,
            )

    async def claim_next_task(
        self,
        media_type: str,
        *,
        pool_full_providers: frozenset[str] | None = None,
    ) -> dict[str, Any] | None:
        async with self._task_repo() as repo:
            task = await repo.claim_next(media_type, pool_full_providers=pool_full_providers)
        if task:
            logger.debug("任务被领取 task_id=%s", task["task_id"])
        return task

    async def requeue_running_tasks(self, *, limit: int = 1000) -> int:
        async with self._task_repo() as repo:
            recovered = await repo.requeue_running(limit=limit)
        if recovered > 0:
            logger.warning("回收 %d 个 running 任务", recovered)
        return recovered

    async def list_orphan_tasks_on_start(self) -> list[dict[str, Any]]:
        async with self._task_repo() as repo:
            return await repo.list_orphan_tasks_on_start()

    async def persist_provider_job_id(
        self,
        task_id: str,
        job_id: str,
        *,
        endpoint: str | None = None,
        base_url: str | None = None,
    ) -> None:
        async with self._task_repo() as repo:
            await repo.persist_provider_job_id(task_id, job_id, endpoint=endpoint, base_url=base_url)

    async def persist_execution_checkpoint(self, task_id: str, checkpoint_json: str, provider_id: str) -> None:
        async with self._task_repo() as repo:
            await repo.persist_execution_checkpoint(task_id, checkpoint_json, provider_id)

    async def persist_execution_provider_id(self, task_id: str, provider_id: str) -> None:
        async with self._task_repo() as repo:
            await repo.persist_execution_provider_id(task_id, provider_id)

    async def mark_task_succeeded(self, task_id: str, result: dict[str, Any] | None) -> int:
        """Returns rows_affected (0 = task no longer running; this write has no effect)."""
        async with self._task_repo() as repo:
            affected = await repo.mark_succeeded(task_id, result)
        if affected > 0:
            logger.info("任务成功 task_id=%s", task_id)
        else:
            logger.info("mark_succeeded 0 rows task_id=%s (任务已不在 running)", task_id)
        return affected

    async def mark_task_failed(self, task_id: str, error_message: str) -> int:
        """Returns rows_affected (0 = task no longer running; this write has no effect)."""
        async with self._task_repo() as repo:
            affected = await repo.mark_failed(task_id, error_message)
        if affected > 0:
            logger.warning("任务失败 task_id=%s error=%s", task_id, error_message[:200])
        else:
            logger.info("mark_failed 0 rows task_id=%s (任务已不在 running)", task_id)
        return affected

    async def mark_task_interrupted(self, task_id: str) -> int:
        """Process-interruption fallback: persist interrupted tasks as cancelled and cascade queued downstream tasks; return affected row counts."""
        async with self._task_repo() as repo:
            return await repo.finalize_interrupted(task_id)

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel queued tasks and queued downstream tasks; running tasks raise ``TaskNotCancellableError``."""
        async with self._task_repo() as repo:
            result = await repo.cancel_task(task_id)
        if result["cancelled"]:
            logger.info("任务取消 task_id=%s cancelled=%d", task_id, len(result["cancelled"]))
        return result

    async def get_cancel_preview(self, task_id: str) -> dict[str, Any]:
        async with self._task_repo() as repo:
            return await repo.get_cancel_preview(task_id)

    async def cancel_all_queued(self, project_name: str) -> dict[str, Any]:
        """Cancel queued project generation tasks. Local rendering tasks are absent from usage panels and excluded from batch cancellation."""
        async with self._task_repo() as repo:
            result = await repo.cancel_all_queued(project_name, exclude_media_types=(RENDER_MEDIA_TYPE,))
        if result["cancelled_count"] > 0:
            logger.info("批量取消 project=%s 共取消 %d 个", project_name, result["cancelled_count"])
        return result

    async def get_cancel_all_preview(self, project_name: str) -> int:
        async with self._task_repo() as repo:
            return await repo.get_cancel_all_preview(project_name, exclude_media_types=(RENDER_MEDIA_TYPE,))

    async def get_task(self, task_id: str) -> dict[str, Any] | None:

        async with self._task_repo() as repo:
            return await repo.get(task_id)

    async def retry_artifact_download(self, task_id: str) -> dict[str, Any]:
        async with self._task_repo() as repo:
            return await repo.retry_artifact_download(task_id)

    async def list_tasks(
        self,
        *,
        project_name: str | None = None,
        status: str | None = None,
        task_type: str | None = None,
        source: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:

        async with self._task_repo() as repo:
            return await repo.list_tasks(
                project_name=project_name,
                status=status,
                task_type=task_type,
                source=source,
                page=page,
                page_size=page_size,
            )

    async def get_task_stats(self, project_name: str | None = None) -> dict[str, int]:

        async with self._task_repo() as repo:
            return await repo.get_stats(project_name=project_name)

    async def acquire_or_renew_worker_lease(
        self,
        *,
        name: str,
        owner_id: str,
        ttl_seconds: float,
    ) -> bool:

        async with self._task_repo() as repo:
            return await repo.acquire_or_renew_lease(
                name=name,
                owner_id=owner_id,
                ttl=ttl_seconds,
            )

    async def release_worker_lease(self, *, name: str, owner_id: str) -> None:

        async with self._task_repo() as repo:
            await repo.release_lease(name=name, owner_id=owner_id)

    async def is_worker_online(self, *, name: str = "default") -> bool:

        async with self._task_repo() as repo:
            return await repo.is_worker_online(name=name)

    async def get_worker_lease(self, *, name: str = "default") -> dict[str, Any] | None:

        async with self._task_repo() as repo:
            return await repo.get_worker_lease(name=name)


def get_generation_queue() -> GenerationQueue:
    global _QUEUE_INSTANCE
    if _QUEUE_INSTANCE is not None:
        return _QUEUE_INSTANCE

    with _QUEUE_LOCK:
        if _QUEUE_INSTANCE is None:
            _QUEUE_INSTANCE = GenerationQueue()
        return _QUEUE_INSTANCE


def read_queue_poll_interval() -> float:
    return max(0.1, float(TASK_POLL_INTERVAL_SEC))


# This queue persists provider task IDs after channel submission. Resolve singleton by name so patches to this module's get_generation_queue still work.
install_provider_job_id_store(lambda: get_generation_queue())
