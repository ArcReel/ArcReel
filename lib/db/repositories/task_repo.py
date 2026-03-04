"""Async repository for generation task queue."""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete as sa_delete, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.task import Task, TaskEvent, WorkerLease

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = ("queued", "running")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _task_to_dict(row: Task) -> dict[str, Any]:
    return {
        "task_id": row.task_id,
        "project_name": row.project_name,
        "task_type": row.task_type,
        "media_type": row.media_type,
        "resource_id": row.resource_id,
        "script_file": row.script_file,
        "payload": _json_loads(row.payload_json, {}),
        "status": row.status,
        "result": _json_loads(row.result_json, {}),
        "error_message": row.error_message,
        "source": row.source,
        "dependency_task_id": row.dependency_task_id,
        "dependency_group": row.dependency_group,
        "dependency_index": row.dependency_index,
        "queued_at": row.queued_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "updated_at": row.updated_at,
    }


def _event_to_dict(row: TaskEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "project_name": row.project_name,
        "event_type": row.event_type,
        "status": row.status,
        "data": _json_loads(row.data_json, {}),
        "created_at": row.created_at,
    }


class TaskRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _append_event(
        self,
        *,
        task_id: str,
        project_name: str,
        event_type: str,
        status: str,
        data: Optional[dict] = None,
    ) -> int:
        now = _utc_now_iso()
        event = TaskEvent(
            task_id=task_id,
            project_name=project_name,
            event_type=event_type,
            status=status,
            data_json=_json_dumps(data or {}),
            created_at=now,
        )
        self.session.add(event)
        await self.session.flush()
        return event.id

    async def enqueue(
        self,
        *,
        project_name: str,
        task_type: str,
        media_type: str,
        resource_id: str,
        payload: Optional[dict[str, Any]] = None,
        script_file: Optional[str] = None,
        source: str = "webui",
        dependency_task_id: Optional[str] = None,
        dependency_group: Optional[str] = None,
        dependency_index: Optional[int] = None,
    ) -> dict[str, Any]:
        now = _utc_now_iso()

        task_id = uuid.uuid4().hex
        task = Task(
            task_id=task_id,
            project_name=project_name,
            task_type=task_type,
            media_type=media_type,
            resource_id=resource_id,
            script_file=script_file,
            payload_json=_json_dumps(payload or {}),
            status="queued",
            source=source,
            dependency_task_id=dependency_task_id,
            dependency_group=dependency_group,
            dependency_index=dependency_index,
            queued_at=now,
            updated_at=now,
        )
        self.session.add(task)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            # Unique partial index violation: an active task already exists
            sf = script_file or ""
            result = await self.session.execute(
                select(Task).where(
                    Task.project_name == project_name,
                    Task.task_type == task_type,
                    Task.resource_id == resource_id,
                    func.coalesce(Task.script_file, "") == sf,
                    Task.status.in_(ACTIVE_TASK_STATUSES),
                ).order_by(Task.queued_at.desc()).limit(1)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return {
                    "task_id": existing.task_id,
                    "status": existing.status,
                    "deduped": True,
                    "existing_task_id": existing.task_id,
                }
            raise

        task_data = _task_to_dict(task)
        await self._append_event(
            task_id=task_id,
            project_name=project_name,
            event_type="queued",
            status="queued",
            data=task_data,
        )
        await self.session.commit()

        return {
            "task_id": task_id,
            "status": "queued",
            "deduped": False,
            "existing_task_id": None,
        }

    async def claim_next(self, media_type: str) -> Optional[dict[str, Any]]:
        now = _utc_now_iso()

        # Use raw SQL for the dependency join (clearer than ORM for self-join)
        raw_stmt = text("""
            SELECT tasks.task_id
            FROM tasks
            LEFT JOIN tasks AS dependency
              ON dependency.task_id = tasks.dependency_task_id
            WHERE tasks.status = 'queued'
              AND tasks.media_type = :media_type
              AND (
                tasks.dependency_task_id IS NULL
                OR dependency.status = 'succeeded'
              )
            ORDER BY tasks.queued_at ASC
            LIMIT 1
        """)

        result = await self.session.execute(raw_stmt, {"media_type": media_type})
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
        if update_result.rowcount == 0:
            # Another worker claimed this task between our SELECT and UPDATE
            await self.session.rollback()
            return None
        await self.session.flush()

        # Reload task
        result = await self.session.execute(
            select(Task).where(Task.task_id == target_task_id)
        )
        running_task = result.scalar_one()
        task_data = _task_to_dict(running_task)

        await self._append_event(
            task_id=target_task_id,
            project_name=running_task.project_name,
            event_type="running",
            status="running",
            data=task_data,
        )
        await self.session.commit()
        return task_data

    async def mark_succeeded(
        self, task_id: str, result: Optional[dict[str, Any]] = None
    ) -> Optional[dict[str, Any]]:
        now = _utc_now_iso()

        await self.session.execute(
            update(Task)
            .where(Task.task_id == task_id)
            .values(
                status="succeeded",
                result_json=_json_dumps(result or {}),
                error_message=None,
                finished_at=now,
                updated_at=now,
            )
        )
        await self.session.flush()

        res = await self.session.execute(
            select(Task).where(Task.task_id == task_id)
        )
        done_task = res.scalar_one_or_none()
        if not done_task:
            return None

        task_data = _task_to_dict(done_task)
        await self._append_event(
            task_id=task_id,
            project_name=done_task.project_name,
            event_type="succeeded",
            status="succeeded",
            data=task_data,
        )
        await self.session.commit()
        return task_data

    async def mark_failed(
        self, task_id: str, error_message: str
    ) -> Optional[dict[str, Any]]:
        failed_task, changed = await self._mark_failed_internal(
            task_id=task_id,
            error_message=error_message,
            allowed_statuses=ACTIVE_TASK_STATUSES,
        )
        if failed_task is None:
            return None

        if changed:
            await self._cascade_failed_dependents(
                task_id=task_id,
                error_message=failed_task.get("error_message") or error_message,
            )

        await self.session.commit()
        return failed_task

    async def _mark_failed_internal(
        self,
        *,
        task_id: str,
        error_message: str,
        allowed_statuses: tuple[str, ...],
    ) -> tuple[Optional[dict[str, Any]], bool]:
        result = await self.session.execute(
            select(Task).where(Task.task_id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            return None, False

        if task.status not in allowed_statuses:
            return _task_to_dict(task), False

        now = _utc_now_iso()
        await self.session.execute(
            update(Task)
            .where(Task.task_id == task_id)
            .values(
                status="failed",
                error_message=error_message[:2000],
                finished_at=now,
                updated_at=now,
            )
        )
        await self.session.flush()

        res = await self.session.execute(
            select(Task).where(Task.task_id == task_id)
        )
        failed_task = res.scalar_one()
        task_data = _task_to_dict(failed_task)
        await self._append_event(
            task_id=task_id,
            project_name=failed_task.project_name,
            event_type="failed",
            status="failed",
            data=task_data,
        )
        return task_data, True

    async def _cascade_failed_dependents(
        self,
        *,
        task_id: str,
        error_message: str,
    ) -> int:
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
            blocked_message = f"blocked by failed dependency {task_id}: {error_message}"
            failed_task, changed = await self._mark_failed_internal(
                task_id=dep_id,
                error_message=blocked_message,
                allowed_statuses=("queued",),
            )
            if not changed or failed_task is None:
                continue
            cascaded += 1
            cascaded += await self._cascade_failed_dependents(
                task_id=dep_id,
                error_message=failed_task.get("error_message") or blocked_message,
            )
        return cascaded

    async def requeue_running(self, *, limit: int = 1000) -> int:
        now = _utc_now_iso()
        limit = max(1, min(5000, limit))

        # Step 1: collect task_ids to requeue
        id_result = await self.session.execute(
            select(Task.task_id)
            .where(Task.status == "running")
            .order_by(Task.updated_at.asc())
            .limit(limit)
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
        rows = await self.session.execute(
            select(Task).where(Task.task_id.in_(task_ids), Task.status == "queued")
        )
        requeued_tasks = rows.scalars().all()

        # Step 4: bulk-insert all requeue events
        event_now = _utc_now_iso()
        events = [
            TaskEvent(
                task_id=t.task_id,
                project_name=t.project_name,
                event_type="requeued",
                status="queued",
                data_json=_json_dumps(_task_to_dict(t)),
                created_at=event_now,
            )
            for t in requeued_tasks
        ]
        self.session.add_all(events)
        await self.session.commit()
        return len(requeued_tasks)

    async def get(self, task_id: str) -> Optional[dict[str, Any]]:
        result = await self.session.execute(
            select(Task).where(Task.task_id == task_id)
        )
        task = result.scalar_one_or_none()
        return _task_to_dict(task) if task else None

    async def list_tasks(
        self,
        *,
        project_name: Optional[str] = None,
        status: Optional[str] = None,
        task_type: Optional[str] = None,
        source: Optional[str] = None,
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
        total = (await self.session.execute(count_stmt)).scalar() or 0

        items_stmt = (
            select(Task)
            .where(*filters)
            .order_by(Task.updated_at.desc(), Task.queued_at.desc())
            .limit(page_size)
            .offset(offset)
        )
        result = await self.session.execute(items_stmt)
        items = [_task_to_dict(t) for t in result.scalars().all()]

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_stats(
        self, *, project_name: Optional[str] = None
    ) -> dict[str, int]:
        filters = []
        if project_name:
            filters.append(Task.project_name == project_name)

        # Group by status
        stmt = (
            select(Task.status, func.count().label("cnt"))
            .where(*filters)
            .group_by(Task.status)
        )
        result = await self.session.execute(stmt)

        stats = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "total": 0}
        total = 0
        for row in result.all():
            s, cnt = row[0], row[1]
            if s in stats:
                stats[s] = cnt
            total += cnt
        stats["total"] = total
        return stats

    async def get_recent_tasks_snapshot(
        self,
        *,
        project_name: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(1000, limit))
        stmt = select(Task)
        if project_name:
            stmt = stmt.where(Task.project_name == project_name)
        stmt = stmt.order_by(Task.updated_at.desc()).limit(limit)

        result = await self.session.execute(stmt)
        return [_task_to_dict(t) for t in result.scalars().all()]

    async def get_events_since(
        self,
        *,
        last_event_id: int,
        project_name: Optional[str] = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(1000, limit))
        stmt = select(TaskEvent).where(TaskEvent.id > last_event_id)
        if project_name:
            stmt = stmt.where(TaskEvent.project_name == project_name)
        stmt = stmt.order_by(TaskEvent.id.asc()).limit(limit)

        result = await self.session.execute(stmt)
        return [_event_to_dict(e) for e in result.scalars().all()]

    async def get_latest_event_id(
        self, *, project_name: Optional[str] = None
    ) -> int:
        stmt = select(func.max(TaskEvent.id))
        if project_name:
            stmt = stmt.where(TaskEvent.project_name == project_name)
        result = await self.session.execute(stmt)
        return result.scalar() or 0

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
        updated_at = _utc_now_iso()

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
        if update_result.rowcount > 0:
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
        result = await self.session.execute(
            select(WorkerLease.lease_until).where(WorkerLease.name == name)
        )
        row = result.first()
        if not row:
            return False
        return row[0] > now_epoch

    async def get_worker_lease(
        self, *, name: str = "default"
    ) -> Optional[dict[str, Any]]:
        result = await self.session.execute(
            select(WorkerLease).where(WorkerLease.name == name)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return {
            "name": row.name,
            "owner_id": row.owner_id,
            "lease_until": row.lease_until,
            "updated_at": row.updated_at,
            "is_online": row.lease_until > time.time(),
        }
