"""Async repository for generation task queue."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Collection
from typing import Any

from sqlalchemy import ColumnElement, func, select, text, update
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.backends.providers import CallStatus
from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.api_call import ApiCall
from lib.db.models.task import BatchTask, GenerationBatch, Task, WorkerLease
from lib.db.repositories.base import BaseRepository, rowcount
from lib.generation.task_failure import bound_reason, collapse_cascade_reason, encode_failure, parse_failure
from lib.generation.task_terminal_events import TERMINAL_TASK_STATUSES

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = ("queued", "running")

# Maximum persisted error_message length. Raw slicing can cut structured reasons inside JSON, preventing render_failure
# from parsing them and exposing machine-code fragments instead of localized copy; both persistence paths use bound_reason.
# Cascade encoding also trims reason to budget before encoding (_encode_bounded_cascade_failure), ensuring the result
# needs no further truncation and deep dependency chains do not grow near-exponentially.
_MAX_ERROR_MESSAGE_LEN = 2000


def _active_dedupe_clauses(
    *,
    project_name: str,
    user_id: str,
    task_type: str,
    script_file: str | None,
    resource_type: str | None,
) -> list[ColumnElement[bool]]:
    """Matching conditions for the part of the ``idx_tasks_dedupe_active`` dedupe key independent of ``resource_id``.

    Shared by ``enqueue``'s ``IntegrityError`` lookup and :meth:`TaskRepository.get_active_tasks_for_resources`,
    defining deduplication in one place. Normalize ``script_file`` / ``resource_type`` to empty strings,
    matching ``COALESCE`` in the index expression.
    """
    return [
        Task.project_name == project_name,
        Task.user_id == user_id,
        Task.task_type == task_type,
        func.coalesce(Task.script_file, "") == (script_file or ""),
        func.coalesce(Task.resource_type, "") == (resource_type or ""),
        Task.status.in_(ACTIVE_TASK_STATUSES),
    ]


def _encode_bounded_cascade_failure(*, dependency_task_id: str, reason: str) -> str:
    # Collapse upstream cascade strings to their root cause first: repeated wrapping grows near-exponentially, and deep-chain
    # trimming can cut inner JSON envelopes into plain-text fragments inside localized copy. Collapsing makes length depth-independent.
    reason = collapse_cascade_reason(reason)
    encoded = encode_failure("cascade_blocked_dependency", dependency_task_id=dependency_task_id, reason=reason)
    if len(encoded) <= _MAX_ERROR_MESSAGE_LEN:
        return encoded

    overhead = len(encode_failure("cascade_blocked_dependency", dependency_task_id=dependency_task_id, reason=""))
    budget = max(_MAX_ERROR_MESSAGE_LEN - overhead, 0)
    # JSON escaping of quotes/backslashes in reason may make a budget-character reason exceed the encoded budget; always use
    # bound_reason to preserve structured validity rather than raw-slicing an already trimmed result and cutting its JSON tail.
    # If rare iterations remain oversized, slightly exceeding _MAX_ERROR_MESSAGE_LEN is preferable to persisting broken JSON.
    for _ in range(5):
        reason = bound_reason(reason, budget)
        encoded = encode_failure("cascade_blocked_dependency", dependency_task_id=dependency_task_id, reason=reason)
        overflow = len(encoded) - _MAX_ERROR_MESSAGE_LEN
        if overflow <= 0 or budget <= 0:
            break
        budget = max(budget - overflow, 0)
    return encoded


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _task_to_dict(row: Task) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "batch_id": row.batch_id,
        "project_name": row.project_name,
        "task_type": row.task_type,
        "media_type": row.media_type,
        "resource_id": row.resource_id,
        "resource_type": row.resource_type,
        "script_file": row.script_file,
        "payload": _json_loads(row.payload_json, {}),
        "status": row.status,
        "result": _json_loads(row.result_json, {}),
        "error_message": row.error_message,
        "source": row.source,
        "dependency_task_id": row.dependency_task_id,
        "dependency_group": row.dependency_group,
        "dependency_index": row.dependency_index,
        "cancelled_by": row.cancelled_by,
        "provider_id": row.provider_id,
        "provider_job_id": row.provider_job_id,
        "provider_endpoint": row.provider_endpoint,
        "submitted_base_url": row.submitted_base_url,
        "execution_checkpoint_json": row.execution_checkpoint_json,
        "queued_at": dt_to_iso(row.queued_at),
        "started_at": dt_to_iso(row.started_at),
        "finished_at": dt_to_iso(row.finished_at),
        "updated_at": dt_to_iso(row.updated_at),
        "user_id": row.user_id,
    }


class TaskNotCancellableError(Exception):
    """The task has started and cannot be cancelled: only queued tasks allow cancellation; running tasks finish normally."""

    def __init__(self, task_id: str) -> None:
        super().__init__(f"任务 '{task_id}' 正在执行，不可取消")
        self.task_id = task_id


class TaskRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        # Terminal task transitions persisted in this session, for the upper layer (GenerationQueue) to publish project events after commit.
        # Collect here instead of recording separately in each terminal method: all terminal transitions, including cascading failure,
        # cascading cancellation and bulk queue cancellation, pass through _record_terminal_event, so one hook covers all paths.
        self.terminal_events: list[dict[str, Any]] = []

    def _record_terminal_event(
        self,
        *,
        task_id: str,
        project_name: str,
        status: str,
        task_type: str | None = None,
    ) -> None:
        """Nonterminal calls are no-ops: the status guard covers future nonterminal call sites, preventing events without corresponding actions."""
        if status in TERMINAL_TASK_STATUSES:
            self.terminal_events.append(
                {
                    "task_id": task_id,
                    "project_name": project_name,
                    "status": status,
                    "task_type": task_type,
                }
            )

    async def _require_batch_units(
        self,
        *,
        project_name: str,
        batch_id: str,
        unit_ids: tuple[str, ...],
        user_id: str,
    ) -> None:
        result = await self.session.execute(
            select(GenerationBatch).where(
                GenerationBatch.batch_id == batch_id,
                GenerationBatch.project_name == project_name,
                GenerationBatch.user_id == user_id,
            )
        )
        batch = result.scalar_one_or_none()
        requested = _json_loads(batch.requested_json, {}) if batch is not None else {}
        requested_ids = {item.get("unit_id") for item in requested.get("requested", []) if isinstance(item, dict)}
        blocked_ids = {
            item["item"].get("unit_id")
            for item in (_json_loads(batch.blocked_json, []) if batch is not None else [])
            if isinstance(item, dict) and isinstance(item.get("item"), dict)
        }
        invalid = next(
            (unit_id for unit_id in unit_ids if unit_id not in requested_ids or unit_id in blocked_ids), None
        )
        if batch is None or invalid is not None:
            unit_id = invalid or unit_ids[0]
            raise ValueError(f"batch '{batch_id}' does not own unit '{unit_id}' in project '{project_name}'")

    async def enqueue(
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
        dedupe_guard: Callable[[dict[str, Any], str], None] | None = None,
    ) -> dict[str, Any]:
        unit_ids = tuple(dict.fromkeys((*(batch_unit_ids or ()), *((batch_unit_id,) if batch_unit_id else ()))))
        if unit_ids and batch_id is None:
            raise ValueError("batch_id and at least one batch unit id must be provided together")
        if batch_id is not None:
            if not unit_ids:
                raise ValueError("batch_id and at least one batch unit id must be provided together")
            await self._require_batch_units(
                project_name=project_name,
                batch_id=batch_id,
                unit_ids=unit_ids,
                user_id=user_id,
            )
        now = utc_now()

        task_id = uuid.uuid4().hex
        task = Task(
            task_id=task_id,
            batch_id=batch_id,
            project_name=project_name,
            task_type=task_type,
            media_type=media_type,
            resource_id=resource_id,
            script_file=script_file,
            resource_type=resource_type,
            payload_json=_json_dumps(payload or {}),
            status="queued",
            source=source,
            dependency_task_id=dependency_task_id,
            dependency_group=dependency_group,
            dependency_index=dependency_index,
            provider_id=provider_id,
            queued_at=now,
            updated_at=now,
            user_id=user_id,
        )
        self.session.add(task)
        deduped = False
        existing_task_id: str | None = None
        existing_payload: dict[str, Any] | None = None
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            # Unique partial index violation: an active task already exists
            result = await self.session.execute(
                select(Task)
                .where(
                    *_active_dedupe_clauses(
                        project_name=project_name,
                        user_id=user_id,
                        task_type=task_type,
                        script_file=script_file,
                        resource_type=resource_type,
                    ),
                    Task.resource_id == resource_id,
                )
                .order_by(Task.queued_at.desc())
                .limit(1)
            )
            existing = result.scalar_one_or_none()
            if existing:
                task_id = existing.task_id
                deduped = True
                existing_task_id = existing.task_id
                loaded_payload: dict[str, Any] = _json_loads(existing.payload_json, {})
                existing_payload = loaded_payload
                if dedupe_guard is not None:
                    dedupe_guard(loaded_payload, existing.task_id)
                status = existing.status
            else:
                raise
        else:
            status = "queued"

        if batch_id is not None:
            self.session.add_all(
                BatchTask(batch_id=batch_id, task_id=task_id, unit_id=unit_id, deduped=deduped) for unit_id in unit_ids
            )
            await self.session.flush()

        await self.session.commit()

        return {
            "task_id": task_id,
            "status": status,
            "deduped": deduped,
            "existing_task_id": existing_task_id,
            "_existing_payload": existing_payload,
        }

    async def create_batch(
        self,
        *,
        batch_id: str,
        project_name: str,
        operation: str,
        requested: dict[str, Any],
        blocked: list[dict[str, Any]],
        source: str,
        user_id: str,
    ) -> None:
        self.session.add(
            GenerationBatch(
                batch_id=batch_id,
                project_name=project_name,
                operation=operation,
                requested_json=_json_dumps(requested),
                blocked_json=_json_dumps(blocked),
                source=source,
                user_id=user_id,
                created_at=utc_now(),
            )
        )
        await self.session.commit()

    async def attach_batch_task(
        self,
        *,
        project_name: str,
        batch_id: str,
        task_id: str,
        unit_id: str,
        deduped: bool,
        user_id: str,
    ) -> None:
        await self._require_batch_units(
            project_name=project_name,
            batch_id=batch_id,
            unit_ids=(unit_id,),
            user_id=user_id,
        )
        task_result = await self.session.execute(
            select(Task).where(
                Task.task_id == task_id,
                Task.project_name == project_name,
                Task.user_id == user_id,
            )
        )
        task = task_result.scalar_one_or_none()
        if task is None:
            raise ValueError(f"batch '{batch_id}' does not own unit '{unit_id}' in project '{project_name}'")
        self.session.add(BatchTask(batch_id=batch_id, task_id=task_id, unit_id=unit_id, deduped=deduped))
        await self.session.commit()

    async def get_batch(
        self, *, project_name: str, batch_id: str, user_id: str = DEFAULT_USER_ID
    ) -> dict[str, Any] | None:
        result = await self.session.execute(
            select(GenerationBatch).where(
                GenerationBatch.batch_id == batch_id,
                GenerationBatch.project_name == project_name,
                GenerationBatch.user_id == user_id,
            )
        )
        batch = result.scalar_one_or_none()
        if batch is None:
            return None

        rows = await self.session.execute(
            select(BatchTask, Task)
            .join(Task, Task.task_id == BatchTask.task_id)
            .where(
                BatchTask.batch_id == batch_id,
                Task.project_name == batch.project_name,
                Task.user_id == batch.user_id,
            )
        )
        memberships = []
        for membership, task in rows.all():
            item = _task_to_dict(task)
            item.update(unit_id=membership.unit_id, deduped=membership.deduped)
            memberships.append(item)

        task_types = {item["task_type"] for item in memberships}
        depth: dict[str, int] = {}
        if task_types:
            depth_rows = await self.session.execute(
                select(Task.task_type, func.count())
                .where(
                    Task.project_name == batch.project_name,
                    Task.user_id == batch.user_id,
                    Task.task_type.in_(task_types),
                    Task.status == "queued",
                )
                .group_by(Task.task_type)
            )
            depth = {str(task_type): int(count) for task_type, count in depth_rows.all()}
        return {
            "batch_id": batch.batch_id,
            "project_name": batch.project_name,
            "operation": batch.operation,
            "requested": json.loads(batch.requested_json),
            "blocked": json.loads(batch.blocked_json),
            "source": batch.source,
            "user_id": batch.user_id,
            "created_at": dt_to_iso(batch.created_at),
            "memberships": memberships,
            "queue_depth": depth,
        }

    async def delete_fresh_batch(self, *, project_name: str, batch_id: str, user_id: str) -> int:
        scope = (
            GenerationBatch.batch_id == batch_id,
            GenerationBatch.project_name == project_name,
            GenerationBatch.user_id == user_id,
        )
        if self.session.get_bind().dialect.name == "postgresql":
            # Serialize with membership FK checks before the fresh-snapshot delete.
            await self.session.execute(select(GenerationBatch.batch_id).where(*scope).with_for_update())
        result = await self.session.execute(
            sa_delete(GenerationBatch).where(
                *scope,
                ~select(Task.task_id).where(Task.batch_id == GenerationBatch.batch_id).exists(),
                ~select(BatchTask.task_id).where(BatchTask.batch_id == GenerationBatch.batch_id).exists(),
            )
        )
        await self.session.commit()
        return rowcount(result)

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
        """Find active tasks matching the ``idx_tasks_dedupe_active`` dedupe key.

        Share :func:`_active_dedupe_clauses` with :meth:`enqueue`'s ``IntegrityError`` branch, allowing
        callers to detect and reject conflicts before actual enqueueing instead of letting ``enqueue``'s
        atomic deduplication silently fall back to existing tasks. Order results by enqueue time so
        caller-generated copy remains stable across implementations.
        """
        if not resource_ids:
            return []
        result = await self.session.execute(
            select(Task)
            .where(
                *_active_dedupe_clauses(
                    project_name=project_name,
                    user_id=user_id,
                    task_type=task_type,
                    script_file=script_file,
                    resource_type=resource_type,
                ),
                Task.resource_id.in_(resource_ids),
            )
            .order_by(Task.queued_at, Task.task_id)
        )
        return [_task_to_dict(t) for t in result.scalars().all()]

    # NOTE: In multi-user mode, override this method to add user_id filtering
    async def claim_next(
        self,
        media_type: str,
        *,
        pool_full_providers: frozenset[str] | None = None,
    ) -> dict[str, Any] | None:
        """Claim the next queued task.

        ``pool_full_providers`` contains provider_id values whose pools are known full this cycle (denylist semantics):
        - ``None`` / empty set: no provider filtering.
        - Nonempty set: exclude these providers' tasks by their recorded enqueue-time ``provider_id``,
          consistently across all task types. Neither legacy rows with ``provider_id IS NULL`` nor tasks
          whose ``provider_id`` is absent from known pools, such as deleted custom providers, are excluded;
          workers check capacity after claiming them.

        Recorded providers may no longer match project settings. Filtering by the record cannot permanently
        strand tasks: the denylist contains only providers currently full, and running tasks eventually free
        slots. Tasks are then claimed normally; execution checks the current provider and, if it differs,
        updates the record and requeues the task in place.

        Denylist semantics replace the early allowlist because an allowlist permanently excludes tasks for
        known providers absent from current ``_pools``, such as disabled/deleted custom providers, causing
        silent accumulation. Excluding only known-full pools lets workers claim unknown-provider tasks
        and resolve them again; resolution failures fall back to mark_failed instead of silently hanging.
        """
        now = utc_now()

        params: dict[str, Any] = {"media_type": media_type}
        provider_filter = ""
        if pool_full_providers:
            # SQLite/PG both support expanding bindparam: list form + NOT IN (:providers)
            provider_filter = "AND (tasks.provider_id IS NULL OR tasks.provider_id NOT IN :providers)"
            params["providers"] = tuple(pool_full_providers)

        # Use raw SQL for the dependency join (clearer than ORM for self-join)
        raw_stmt = text(f"""
            SELECT tasks.task_id
            FROM tasks
            LEFT JOIN tasks AS dependency
              ON dependency.task_id = tasks.dependency_task_id
            WHERE tasks.status = 'queued'
              AND tasks.media_type = :media_type
              {provider_filter}
              AND (
                tasks.dependency_task_id IS NULL
                OR dependency.status = 'succeeded'
              )
            ORDER BY tasks.queued_at ASC
            LIMIT 1
        """)
        if "providers" in params:
            raw_stmt = raw_stmt.bindparams(sa_bindparam("providers", expanding=True))

        result = await self.session.execute(raw_stmt, params)
        row = result.first()
        if not row:
            return None

        target_task_id = row[0]

        # Update to running atomically; check rowcount to guard against concurrent claims
        update_result = await self.session.execute(
            update(Task)
            .where(Task.task_id == target_task_id, Task.status == "queued")
            .values(
                status="running",
                started_at=now,
                updated_at=now,
            )
        )
        if rowcount(update_result) == 0:
            # Another worker claimed this task between our SELECT and UPDATE
            await self.session.rollback()
            return None
        await self.session.flush()

        # Reload task
        result = await self.session.execute(select(Task).where(Task.task_id == target_task_id))
        running_task = result.scalar_one()
        task_data = _task_to_dict(running_task)
        await self.session.commit()
        return task_data

    async def mark_succeeded(self, task_id: str, result: dict[str, Any] | None = None) -> int:
        """Guard with SQL `WHERE status='running'`; return the affected row count.

        rows=0 means another path has already moved the task out of running into a terminal state;
        the write has no effect and the caller only logs it.
        """
        now = utc_now()

        update_result = await self.session.execute(
            update(Task)
            .where(Task.task_id == task_id, Task.status == "running")
            .values(
                status="succeeded",
                result_json=_json_dumps(result or {}),
                error_message=None,
                finished_at=now,
                updated_at=now,
            )
        )
        affected = rowcount(update_result)
        if affected == 0:
            # Do not commit or emit events: another path already wrote a terminal state
            return 0

        await self.session.flush()
        res = await self.session.execute(select(Task).where(Task.task_id == task_id))
        done_task = res.scalar_one()
        self._record_terminal_event(
            task_id=task_id,
            project_name=done_task.project_name,
            status="succeeded",
            task_type=done_task.task_type,
        )
        await self.session.commit()
        return affected

    async def mark_failed(self, task_id: str, error_message: str) -> int:
        """Guard with SQL `WHERE status='running'`; return the affected row count.

        rows=0 means another path has already moved the task out of running into a terminal state;
        the write has no effect and the caller only logs it. Cascading failures of dependent tasks use a separate path.
        """
        affected = await self._mark_failed_running(task_id=task_id, error_message=error_message)
        if affected == 0:
            return 0

        await self._cascade_failed_queued(task_id=task_id, error_message=error_message)
        await self.session.commit()
        return affected

    async def retry_artifact_download(self, task_id: str) -> dict[str, Any]:
        """Restore a recoverable download-failure task to running in place and reopen its original ApiCall for resume settlement.

        Require the ``provider_id`` needed for dispatch as part of eligibility: this method commits as soon
        as the status becomes running. Rejection during subsequent dispatch has no rollback point and
        would permanently leave the task running without a worker.
        """
        result = await self.session.execute(self._scope_query(select(Task).where(Task.task_id == task_id), Task))
        task = result.scalar_one_or_none()
        parsed = parse_failure(task.error_message or "") if task is not None else None
        if (
            task is None
            or task.status != "failed"
            or parsed is None
            or parsed[0] != "artifact_download_failed"
            or not task.provider_job_id
            or not task.provider_id
        ):
            raise ValueError(f"task is not eligible for artifact download retry: {task_id}")
        # For artifact_download_failed tasks, the call may be failed (initial download exhausted and already settled)
        # or pending (resume download exhausted, between settlement and task status update). Accept both and restore
        # pending in place, reusing the same call without a new billing row. Clear the previous download failure text
        # and machine code too, or successful resume settlement would change only status, leaving download_failed on a success row.
        # Find the call through ``api_calls.task_id``, the sole source of truth for task/call associations.
        call_row = await self.session.execute(
            select(ApiCall.id)
            .where(
                ApiCall.task_id == task_id,
                ApiCall.status.in_((CallStatus.FAILED, CallStatus.PENDING)),
            )
            .order_by(ApiCall.id.desc())
            .limit(1)
        )
        call_id = call_row.scalars().first()
        if call_id is None:
            raise ValueError(f"task has no settleable api call for artifact download retry: {task_id}")
        call_update = await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id, ApiCall.status.in_((CallStatus.FAILED, CallStatus.PENDING)))
            .values(
                status=CallStatus.PENDING,
                finished_at=None,
                error_message=None,
                error_code=None,
                error_params=None,
            )
        )
        if rowcount(call_update) != 1:
            await self.session.rollback()
            raise ValueError(f"api call is not eligible for artifact download retry: {call_id}")
        now = utc_now()
        task_update = await self.session.execute(
            update(Task)
            .where(Task.task_id == task_id, Task.status == "failed")
            # Refresh started_at too: retry begins a new execution interval; keeping the original submission time would make
            # the panel calculate elapsed time from the previous interval's start.
            .values(status="running", error_message=None, finished_at=None, started_at=now, updated_at=now)
        )
        if rowcount(task_update) != 1:
            await self.session.rollback()
            raise ValueError(f"task is not eligible for artifact download retry: {task_id}")
        await self.session.commit()
        refreshed = await self.session.execute(select(Task).where(Task.task_id == task_id))
        return _task_to_dict(refreshed.scalar_one())

    async def _mark_failed_running(self, *, task_id: str, error_message: str) -> int:
        """Single point for marking a running task failed; return affected row count. Do not commit."""
        now = utc_now()
        update_result = await self.session.execute(
            update(Task)
            .where(Task.task_id == task_id, Task.status == "running")
            .values(
                status="failed",
                error_message=bound_reason(error_message, _MAX_ERROR_MESSAGE_LEN),
                finished_at=now,
                updated_at=now,
            )
        )
        affected = rowcount(update_result)
        if affected == 0:
            return 0

        await self.session.flush()
        res = await self.session.execute(select(Task).where(Task.task_id == task_id))
        failed_task = res.scalar_one()
        self._record_terminal_event(
            task_id=task_id,
            project_name=failed_task.project_name,
            status="failed",
            task_type=failed_task.task_type,
        )
        return affected

    async def _mark_failed_queued_dep(self, *, task_id: str, error_message: str) -> int:
        """Cascade-only path for marking a queued dependent task failed; return affected row count. Do not commit."""
        now = utc_now()
        update_result = await self.session.execute(
            update(Task)
            .where(Task.task_id == task_id, Task.status == "queued")
            .values(
                status="failed",
                error_message=bound_reason(error_message, _MAX_ERROR_MESSAGE_LEN),
                finished_at=now,
                updated_at=now,
            )
        )
        affected = rowcount(update_result)
        if affected == 0:
            return 0

        await self.session.flush()
        res = await self.session.execute(select(Task).where(Task.task_id == task_id))
        failed_task = res.scalar_one()
        self._record_terminal_event(
            task_id=task_id,
            project_name=failed_task.project_name,
            status="failed",
            task_type=failed_task.task_type,
        )
        return affected

    async def _cascade_failed_queued(self, *, task_id: str, error_message: str) -> int:
        """Cascade failures along dependencies. Pass `error_message` unchanged as the root cause throughout the chain,
        without nesting it again at each level. Each level encodes only its immediate blocking `task_id`,
        avoiding near-exponential growth and database truncation corruption from nesting the previous level's
        entire encoded string inside another JSON envelope.
        """
        result = await self.session.execute(
            select(Task.task_id)
            .where(
                Task.dependency_task_id == task_id,
                Task.status == "queued",
            )
            .order_by(Task.queued_at.asc())
        )
        dependent_ids = [row[0] for row in result.all()]

        cascaded = 0
        for dep_id in dependent_ids:
            blocked_message = _encode_bounded_cascade_failure(dependency_task_id=task_id, reason=error_message)
            affected = await self._mark_failed_queued_dep(task_id=dep_id, error_message=blocked_message)
            if affected == 0:
                continue
            cascaded += affected
            cascaded += await self._cascade_failed_queued(task_id=dep_id, error_message=error_message)
        return cascaded

    async def get_cancel_preview(self, task_id: str) -> dict[str, Any]:
        """Preview cancellation of a queued task, listing queued downstream tasks that would also be cancelled.

        Only ``queued`` tasks allow cancellation; running tasks raise ``TaskNotCancellableError``.
        Terminal tasks still return a summary for callers to decide whether to proceed.
        """
        result = await self.session.execute(self._scope_query(select(Task).where(Task.task_id == task_id), Task))
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError(f"任务 '{task_id}' 不存在")
        if task.status == "running":
            raise TaskNotCancellableError(task_id)

        task_summary = {
            "task_id": task.task_id,
            "project_name": task.project_name,
            "task_type": task.task_type,
            "resource_id": task.resource_id,
            "status": task.status,
        }

        cascaded = await self._collect_queued_dependents(task_id)
        return {"task": task_summary, "cascaded": cascaded}

    async def _collect_queued_dependents(self, task_id: str) -> list[dict[str, Any]]:
        """Recursively collect summaries of all queued tasks depending on task_id."""
        result = await self.session.execute(
            select(Task.task_id, Task.task_type, Task.resource_id, Task.project_name)
            .where(
                Task.dependency_task_id == task_id,
                Task.status == "queued",
            )
            .order_by(Task.queued_at.asc())
        )
        dependents = []
        for row in result.all():
            summary = {"task_id": row[0], "task_type": row[1], "resource_id": row[2], "project_name": row[3]}
            dependents.append(summary)
            dependents.extend(await self._collect_queued_dependents(row[0]))
        return dependents

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a queued task and cascade cancellation to its queued downstream dependents.

        - ``queued`` → ``cancelled('user')``; downstream tasks → ``cancelled('cascade')``, including grandchildren.
        - ``running`` → raise ``TaskNotCancellableError``; the task finishes normally.
        - Terminal (succeeded/failed/cancelled) → ``skipped_terminal``.

        Also reject as running when a worker claims the task between reading queued and the UPDATE.
        """
        result = await self.session.execute(self._scope_query(select(Task).where(Task.task_id == task_id), Task))
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError(f"任务 '{task_id}' 不存在")

        cancelled: list[dict[str, Any]] = []
        skipped_terminal: list[dict[str, Any]] = []
        if task.status in TERMINAL_TASK_STATUSES:
            skipped_terminal.append(_task_to_dict(task))
            return {"cancelled": cancelled, "skipped_terminal": skipped_terminal}

        if await self._mark_cancelled(task.task_id, cancelled_by="user", cancelled=cancelled) is None:
            await self.session.rollback()
            await self.session.refresh(task)
            if task.status in TERMINAL_TASK_STATUSES:
                return {"cancelled": [], "skipped_terminal": [_task_to_dict(task)]}
            raise TaskNotCancellableError(task_id)

        await self.session.commit()
        return {"cancelled": cancelled, "skipped_terminal": skipped_terminal}

    async def _mark_cancelled(
        self,
        task_id: str,
        *,
        cancelled_by: str,
        cancelled: list[dict[str, Any]],
        from_statuses: tuple[str, ...] = ("queued",),
    ) -> dict[str, Any] | None:
        """Move a task in ``from_statuses`` to terminal cancelled, then cancel queued downstream dependents.

        User cancellation transitions only from ``queued``; process-level interruption additionally allows
        ``running`` through ``finalize_interrupted``. Downstream tasks can only be claimed after upstream
        completion, so before completion they remain queued and cascading need only handle ``queued``.
        Return None if the UPDATE affects zero rows.
        """
        now = utc_now()
        stmt = (
            update(Task)
            .where(Task.task_id == task_id, Task.status.in_(from_statuses))
            .values(
                status="cancelled",
                cancelled_by=cancelled_by,
                finished_at=now,
                updated_at=now,
            )
        )
        result = await self.session.execute(stmt)
        if rowcount(result) == 0:
            return None

        await self.session.flush()
        res = await self.session.execute(select(Task).where(Task.task_id == task_id))
        cancelled_task = res.scalar_one()
        task_data = _task_to_dict(cancelled_task)
        self._record_terminal_event(
            task_id=task_id,
            project_name=cancelled_task.project_name,
            status="cancelled",
            task_type=cancelled_task.task_type,
        )

        # Enqueue the task itself before cascading so parents precede children in the response.
        cancelled.append(task_data)
        dependents = await self.session.execute(
            select(Task.task_id)
            .where(Task.dependency_task_id == task_id, Task.status == "queued")
            .order_by(Task.queued_at.asc())
        )
        for dep_task_id in dependents.scalars().all():
            await self._mark_cancelled(dep_task_id, cancelled_by="cascade", cancelled=cancelled)
        return task_data

    async def persist_provider_job_id(
        self,
        task_id: str,
        job_id: str,
        *,
        endpoint: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """Persist provider_job_id in a separate transaction, without a WHERE status guard (worker calls guarantee running).

        Store endpoint dimensions separately: ``endpoint`` is the protocol identifier for custom providers only,
        written to ``provider_endpoint``; ``base_url`` is the domain actually requested for either provider type,
        written to ``submitted_base_url``. Persist both in the same UPDATE as job_id: the domain must become
        visible with job_id so resume can replay the original domain. Saving the protocol identifier together
        also makes the provider job's protocol traceable. None leaves each corresponding column unchanged:
        clearing would lose protocol identity or replay capability, making preservation safer.

        Raise on failure; worker finally falls back to mark_failed (ADR 0007 fail-fast: an unpersisted
        submit fails the whole operation, preventing ghost tasks still running at the provider after the DB forgets).
        """
        now = utc_now()
        values: dict[str, Any] = {"provider_job_id": job_id, "updated_at": now}
        if endpoint is not None:
            values["provider_endpoint"] = endpoint
        if base_url is not None:
            values["submitted_base_url"] = base_url
        await self.session.execute(update(Task).where(Task.task_id == task_id).values(**values))
        await self.session.commit()

    async def persist_execution_checkpoint(self, task_id: str, checkpoint_json: str, provider_id: str) -> None:
        """Persist the once-only pre-submit checkpoint and actual provider atomically.

        The guarded transition is deliberately narrower than ordinary task metadata updates: only a running
        video task with neither a checkpoint nor a provider job may cross the submit boundary. A
        zero-row update is an execution conflict and must abort before the provider call.
        """
        result = await self.session.execute(
            update(Task)
            .where(
                Task.task_id == task_id,
                Task.task_type.in_(("video", "reference_video")),
                Task.status == "running",
                Task.execution_checkpoint_json.is_(None),
                Task.provider_job_id.is_(None),
            )
            .values(
                execution_checkpoint_json=checkpoint_json,
                provider_id=provider_id,
                updated_at=utc_now(),
            )
        )
        if rowcount(result) != 1:
            await self.session.rollback()
            raise ValueError(f"execution checkpoint persistence guard rejected task: {task_id}")
        await self.session.commit()

    async def persist_execution_provider_id(self, task_id: str, provider_id: str) -> None:
        """Update the enqueued ``task.provider_id`` record, which controls worker full-pool filtering and slot ownership.

        At claim time, persist inferred providers for legacy tasks without a record or whose recorded provider
        no longer supports the media type. At execution entry, if current project settings resolve a different
        provider, persist it before requeueing so the next claim uses the new provider's slot.
        This does not lock the model or establish execution identity. No WHERE status guard, as with ``persist_provider_job_id``.
        """
        now = utc_now()
        await self.session.execute(
            update(Task).where(Task.task_id == task_id).values(provider_id=provider_id, updated_at=now)
        )
        await self.session.commit()

    async def list_orphan_tasks_on_start(self) -> list[dict[str, Any]]:
        """Return running tasks for restart recovery (ADR 0007)."""
        result = await self.session.execute(
            select(Task).where(Task.status == "running").order_by(Task.updated_at.asc())
        )
        return [_task_to_dict(t) for t in result.scalars().all()]

    async def finalize_interrupted(self, task_id: str) -> int:
        """Process-level interruption fallback: mark interrupted queued / running tasks cancelled and return affected row count.

        Workers call this when process-level causes, such as event-loop teardown or shutdown timeout, interrupt
        execution coroutines. Tasks must not remain ``running`` or be revived by restart recovery on every restart.
        This differs from user cancellation, which permits only queued tasks; see ``cancel_task``.
        After this task becomes terminal, cascade cancellation to queued downstream dependents.
        """
        data = await self._mark_cancelled(
            task_id,
            cancelled_by="interrupted",
            cancelled=[],
            from_statuses=("queued", "running"),
        )
        await self.session.commit()
        return 1 if data is not None else 0

    async def get_cancel_all_preview(self, project_name: str, *, exclude_media_types: Collection[str] = ()) -> int:
        """Return the project's current queued task count, excluding media types in ``exclude_media_types``."""
        stmt = (
            select(func.count())
            .select_from(Task)
            .where(
                Task.project_name == project_name, Task.status == "queued", Task.media_type.not_in(exclude_media_types)
            )
        )
        result = await self.session.execute(self._scope_query(stmt, Task))
        return result.scalar_one()

    async def cancel_all_queued(
        self, project_name: str, *, exclude_media_types: Collection[str] = ()
    ) -> dict[str, Any]:
        """Cancel all queued project tasks except media types in ``exclude_media_types``."""
        queued_result = await self.session.execute(
            self._scope_query(
                select(Task).where(
                    Task.project_name == project_name,
                    Task.status == "queued",
                    Task.media_type.not_in(exclude_media_types),
                ),
                Task,
            )
        )
        task_ids = [t.task_id for t in queued_result.scalars().all()]
        if not task_ids:
            return {"cancelled_count": 0, "skipped_running_count": 0}

        # UPDATE cannot use _scope_query (Select only); constrain target rows to the IDs found within scope
        now = utc_now()
        stmt = (
            update(Task)
            .where(Task.task_id.in_(task_ids), Task.status == "queued")
            .values(
                status="cancelled",
                cancelled_by="user",
                finished_at=now,
                updated_at=now,
            )
        )
        result = await self.session.execute(stmt)
        cancelled_count = rowcount(result)

        await self.session.flush()
        refreshed = await self.session.execute(
            select(Task).where(Task.task_id.in_(task_ids), Task.status == "cancelled")
        )
        for updated_task in refreshed.scalars().all():
            self._record_terminal_event(
                task_id=updated_task.task_id,
                project_name=project_name,
                status="cancelled",
                task_type=updated_task.task_type,
            )

        await self.session.commit()
        # Workers may claim some tasks before UPDATE in a race; skipped = expected cancellations - actual cancellations
        skipped = len(task_ids) - cancelled_count
        return {
            "cancelled_count": cancelled_count,
            "skipped_running_count": max(0, skipped),
        }

    async def requeue_running(self, *, limit: int = 1000) -> int:
        """Rescue tool: bulk-requeue running tasks, retained for manual ops use.

        Worker startup no longer calls this automatically, using ``list_orphan_tasks_on_start`` + ``resume_video`` instead (ADR 0007).
        """
        now = utc_now()
        limit = max(1, min(5000, limit))

        # Step 1: collect task_ids to requeue
        id_result = await self.session.execute(
            select(Task.task_id).where(Task.status == "running").order_by(Task.updated_at.asc()).limit(limit)
        )
        task_ids = [row[0] for row in id_result.all()]
        if not task_ids:
            return 0

        # Step 2: batch UPDATE — single round-trip for all tasks
        await self.session.execute(
            update(Task)
            .where(Task.task_id.in_(task_ids), Task.status == "running")
            .values(
                status="queued",
                started_at=None,
                finished_at=None,
                updated_at=now,
                result_json=None,
                error_message=None,
            )
        )
        await self.session.flush()

        # Step 3: reload updated tasks in one SELECT IN
        rows = await self.session.execute(select(Task).where(Task.task_id.in_(task_ids), Task.status == "queued"))
        requeued_tasks = rows.scalars().all()

        await self.session.commit()
        return len(requeued_tasks)

    async def get(self, task_id: str) -> dict[str, Any] | None:
        stmt = select(Task).where(Task.task_id == task_id)
        stmt = self._scope_query(stmt, Task)
        result = await self.session.execute(stmt)
        task = result.scalar_one_or_none()
        return _task_to_dict(task) if task else None

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
        page = max(1, page)
        page_size = max(1, min(500, page_size))
        offset = (page - 1) * page_size

        filters = []
        if project_name:
            filters.append(Task.project_name == project_name)
        if status:
            filters.append(Task.status == status)
        if task_type:
            filters.append(Task.task_type == task_type)
        if source:
            filters.append(Task.source == source)

        count_stmt = select(func.count()).select_from(Task).where(*filters)
        count_stmt = self._scope_query(count_stmt, Task)
        total = (await self.session.execute(count_stmt)).scalar() or 0

        items_stmt = (
            select(Task)
            .where(*filters)
            .order_by(Task.updated_at.desc(), Task.queued_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        items_stmt = self._scope_query(items_stmt, Task)
        result = await self.session.execute(items_stmt)
        items = [_task_to_dict(t) for t in result.scalars().all()]
        await self._attach_running_models(items)

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def _attach_running_models(self, items: list[dict[str, Any]]) -> None:
        """Add the actually called model to running tasks from their latest call record; model is None for other tasks.

        Queued tasks have not sent a call or resolved a model, so usage-record model filters treat them as nonmatches.
        """
        for item in items:
            item["model"] = None
        running = {item["task_id"]: item for item in items if item["status"] == "running"}
        if not running:
            return
        stmt = (
            select(ApiCall.task_id, ApiCall.model)
            .where(ApiCall.task_id.in_(running.keys()))
            .order_by(ApiCall.started_at.asc(), ApiCall.id.asc())
        )
        for task_id, model in (await self.session.execute(stmt)).all():
            running[task_id]["model"] = model

    async def get_stats(self, *, project_name: str | None = None) -> dict[str, int]:
        filters = []
        if project_name:
            filters.append(Task.project_name == project_name)

        # Group by status
        stmt = select(Task.status, func.count().label("cnt")).where(*filters).group_by(Task.status)
        stmt = self._scope_query(stmt, Task)
        result = await self.session.execute(stmt)

        stats = {
            "queued": 0,
            "running": 0,
            "succeeded": 0,
            "failed": 0,
            "cancelled": 0,
            "total": 0,
        }
        total = 0
        for row in result.all():
            s, cnt = row[0], row[1]
            if s in stats:
                stats[s] = cnt
            total += cnt
        stats["total"] = total
        return stats

    # ---- Worker Lease ----

    async def acquire_or_renew_lease(
        self,
        *,
        name: str,
        owner_id: str,
        ttl: float,
    ) -> bool:
        now_epoch = time.time()
        lease_until = now_epoch + max(1.0, float(ttl))
        updated_at = utc_now()

        # Fast path: renew existing lease only when we own it or it's expired.
        update_result = await self.session.execute(
            update(WorkerLease)
            .where(
                WorkerLease.name == name,
                (WorkerLease.owner_id == owner_id) | (WorkerLease.lease_until <= now_epoch),
            )
            .values(
                owner_id=owner_id,
                lease_until=lease_until,
                updated_at=updated_at,
            )
        )
        if rowcount(update_result) > 0:
            await self.session.commit()
            return True

        # Slow path: lease row may not exist yet; try to create it.
        lease = WorkerLease(
            name=name,
            owner_id=owner_id,
            lease_until=lease_until,
            updated_at=updated_at,
        )
        self.session.add(lease)
        try:
            await self.session.commit()
            return True
        except IntegrityError:
            # Another worker won the race to insert; treat as normal contention.
            await self.session.rollback()
            return False

    async def release_lease(self, *, name: str, owner_id: str) -> None:
        await self.session.execute(
            sa_delete(WorkerLease).where(
                WorkerLease.name == name,
                WorkerLease.owner_id == owner_id,
            )
        )
        await self.session.commit()

    async def is_worker_online(self, *, name: str = "default") -> bool:
        now_epoch = time.time()
        result = await self.session.execute(select(WorkerLease.lease_until).where(WorkerLease.name == name))
        row = result.first()
        if not row:
            return False
        return row[0] > now_epoch

    async def get_worker_lease(self, *, name: str = "default") -> dict[str, Any] | None:
        result = await self.session.execute(select(WorkerLease).where(WorkerLease.name == name))
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {
            "name": row.name,
            "owner_id": row.owner_id,
            "lease_until": row.lease_until,
            "updated_at": dt_to_iso(row.updated_at),
            "is_online": row.lease_until > time.time(),
        }
