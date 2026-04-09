"""Async repository for generation task queue."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.task import Task, TaskEvent, WorkerLease
from lib.db.repositories.base import BaseRepository

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = ("queued", "running")


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
        "cancelled_by": row.cancelled_by,
        "queued_at": dt_to_iso(row.queued_at),
        "started_at": dt_to_iso(row.started_at),
        "finished_at": dt_to_iso(row.finished_at),
        "updated_at": dt_to_iso(row.updated_at),
        "user_id": row.user_id,
    }


def _event_to_dict(row: TaskEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "project_name": row.project_name,
        "event_type": row.event_type,
        "status": row.status,
        "data": _json_loads(row.data_json, {}),
        "created_at": dt_to_iso(row.created_at),
    }


class TaskRepository(BaseRepository):
    async def _append_event(
        self,
        *,
        task_id: str,
        project_name: str,
        event_type: str,
        status: str,
        data: dict | None = None,
    ) -> int:
        now = utc_now()
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
        payload: dict[str, Any] | None = None,
        script_file: str | None = None,
        source: str = "webui",
        dependency_task_id: str | None = None,
        dependency_group: str | None = None,
        dependency_index: int | None = None,
        user_id: str = DEFAULT_USER_ID,
    ) -> dict[str, Any]:
        now = utc_now()

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
            user_id=user_id,
        )
        self.session.add(task)
        try:
            await self.session.flush()
        except IntegrityError:
            await self.session.rollback()
            # Unique partial index violation: an active task already exists
            sf = script_file or ""
            result = await self.session.execute(
                select(Task)
                .where(
                    Task.project_name == project_name,
                    Task.task_type == task_type,
                    Task.resource_id == resource_id,
                    func.coalesce(Task.script_file, "") == sf,
                    Task.status.in_(ACTIVE_TASK_STATUSES),
                )
                .order_by(Task.queued_at.desc())
                .limit(1)
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

    # NOTE: In multi-user mode, override this method to add user_id filtering
    async def claim_next(self, media_type: str) -> dict[str, Any] | None:
        now = utc_now()

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
        result = await self.session.execute(select(Task).where(Task.task_id == target_task_id))
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

    async def mark_succeeded(self, task_id: str, result: dict[str, Any] | None = None) -> dict[str, Any] | None:
        now = utc_now()

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

        res = await self.session.execute(select(Task).where(Task.task_id == task_id))
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

    async def mark_failed(self, task_id: str, error_message: str) -> dict[str, Any] | None:
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
    ) -> tuple[dict[str, Any] | None, bool]:
        result = await self.session.execute(select(Task).where(Task.task_id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            return None, False

        if task.status not in allowed_statuses:
            return _task_to_dict(task), False

        now = utc_now()
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

        res = await self.session.execute(select(Task).where(Task.task_id == task_id))
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

    async def get_cancel_preview(self, task_id: str) -> dict[str, Any]:
        """预览取消某个任务的影响范围。"""
        result = await self.session.execute(select(Task).where(Task.task_id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError(f"任务 '{task_id}' 不存在")
        if task.status != "queued":
            raise ValueError("只有排队中的任务可以取消")

        task_summary = {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "resource_id": task.resource_id,
        }

        cascaded = await self._collect_queued_dependents(task_id)
        return {"task": task_summary, "cascaded": cascaded}

    async def _collect_queued_dependents(self, task_id: str) -> list[dict[str, Any]]:
        """递归收集依赖于 task_id 的所有 queued 任务摘要。"""
        result = await self.session.execute(
            select(Task.task_id, Task.task_type, Task.resource_id)
            .where(
                Task.dependency_task_id == task_id,
                Task.status == "queued",
            )
            .order_by(Task.queued_at.asc())
        )
        dependents = []
        for row in result.all():
            summary = {"task_id": row[0], "task_type": row[1], "resource_id": row[2]}
            dependents.append(summary)
            dependents.extend(await self._collect_queued_dependents(row[0]))
        return dependents

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """取消一个 queued 任务，级联取消其所有 queued 依赖任务。"""
        result = await self.session.execute(select(Task).where(Task.task_id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError(f"任务 '{task_id}' 不存在")
        if task.status != "queued":
            raise ValueError("只有排队中的任务可以取消")

        cancelled = []
        skipped_running = []

        task_dict = await self._mark_cancelled(task_id, cancelled_by="user")
        if task_dict:
            cancelled.append(task_dict)
        else:
            refreshed = await self.session.execute(select(Task).where(Task.task_id == task_id))
            t = refreshed.scalar_one_or_none()
            if t and t.status == "running":
                skipped_running.append(_task_to_dict(t))

        await self._cascade_cancel_dependents(task_id, cancelled, skipped_running)

        await self.session.commit()
        return {"cancelled": cancelled, "skipped_running": skipped_running}

    async def _mark_cancelled(self, task_id: str, *, cancelled_by: str) -> dict[str, Any] | None:
        """将一个 queued 任务标记为 cancelled。"""
        now = utc_now()
        stmt = (
            update(Task)
            .where(Task.task_id == task_id, Task.status == "queued")
            .values(
                status="cancelled",
                cancelled_by=cancelled_by,
                finished_at=now,
                updated_at=now,
            )
        )
        result = await self.session.execute(stmt)
        if result.rowcount == 0:
            return None

        await self.session.flush()
        res = await self.session.execute(select(Task).where(Task.task_id == task_id))
        cancelled_task = res.scalar_one()
        task_data = _task_to_dict(cancelled_task)
        await self._append_event(
            task_id=task_id,
            project_name=cancelled_task.project_name,
            event_type="cancelled",
            status="cancelled",
            data=task_data,
        )
        return task_data

    async def _cascade_cancel_dependents(
        self,
        task_id: str,
        cancelled: list[dict[str, Any]],
        skipped_running: list[dict[str, Any]],
    ) -> None:
        """递归取消依赖于 task_id 的所有 queued 任务。"""
        result = await self.session.execute(
            select(Task).where(Task.dependency_task_id == task_id).order_by(Task.queued_at.asc())
        )
        for dep_task in result.scalars().all():
            if dep_task.status == "queued":
                task_data = await self._mark_cancelled(dep_task.task_id, cancelled_by="cascade")
                if task_data:
                    cancelled.append(task_data)
                    await self._cascade_cancel_dependents(dep_task.task_id, cancelled, skipped_running)
                else:
                    # 竞态：初始查询时为 queued 但 UPDATE 失败，刷新检查实际状态
                    await self.session.refresh(dep_task)
                    if dep_task.status == "running":
                        skipped_running.append(_task_to_dict(dep_task))
            elif dep_task.status == "running":
                skipped_running.append(_task_to_dict(dep_task))

    async def get_cancel_all_preview(self, project_name: str) -> int:
        """返回项目中当前 queued 状态的任务数量。"""
        result = await self.session.execute(
            select(func.count()).select_from(Task).where(Task.project_name == project_name, Task.status == "queued")
        )
        return result.scalar_one()

    async def cancel_all_queued(self, project_name: str) -> dict[str, Any]:
        """取消项目中所有 queued 任务。"""
        queued_result = await self.session.execute(
            select(Task).where(Task.project_name == project_name, Task.status == "queued")
        )
        queued_tasks = list(queued_result.scalars().all())

        now = utc_now()
        stmt = (
            update(Task)
            .where(Task.project_name == project_name, Task.status == "queued")
            .values(
                status="cancelled",
                cancelled_by="user",
                finished_at=now,
                updated_at=now,
            )
        )
        result = await self.session.execute(stmt)
        cancelled_count = result.rowcount

        if queued_tasks:
            await self.session.flush()
            task_ids = [t.task_id for t in queued_tasks]
            refreshed = await self.session.execute(
                select(Task).where(Task.task_id.in_(task_ids), Task.status == "cancelled")
            )
            for updated_task in refreshed.scalars().all():
                task_data = _task_to_dict(updated_task)
                await self._append_event(
                    task_id=updated_task.task_id,
                    project_name=project_name,
                    event_type="cancelled",
                    status="cancelled",
                    data=task_data,
                )

        await self.session.commit()
        # 竞态时部分任务可能在 UPDATE 前被 worker 领走，skipped = 预期取消数 - 实际取消数
        skipped = len(queued_tasks) - cancelled_count
        return {
            "cancelled_count": cancelled_count,
            "skipped_running_count": max(0, skipped),
        }

    async def requeue_running(self, *, limit: int = 1000) -> int:
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

        # Step 4: bulk-insert all requeue events
        event_now = utc_now()
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

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
        }

    async def get_stats(self, *, project_name: str | None = None) -> dict[str, int]:
        filters = []
        if project_name:
            filters.append(Task.project_name == project_name)

        # Group by status
        stmt = select(Task.status, func.count().label("cnt")).where(*filters).group_by(Task.status)
        stmt = self._scope_query(stmt, Task)
        result = await self.session.execute(stmt)

        stats = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0, "total": 0}
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
        project_name: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(1000, limit))
        stmt = select(Task)
        if project_name:
            stmt = stmt.where(Task.project_name == project_name)
        stmt = stmt.order_by(Task.updated_at.desc()).limit(limit)
        stmt = self._scope_query(stmt, Task)

        result = await self.session.execute(stmt)
        return [_task_to_dict(t) for t in result.scalars().all()]

    # NOTE: In multi-user mode, override this method to filter by user via JOIN Task
    async def get_events_since(
        self,
        *,
        last_event_id: int,
        project_name: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(1000, limit))
        stmt = select(TaskEvent).where(TaskEvent.id > last_event_id)
        if project_name:
            stmt = stmt.where(TaskEvent.project_name == project_name)
        stmt = stmt.order_by(TaskEvent.id.asc()).limit(limit)

        result = await self.session.execute(stmt)
        return [_event_to_dict(e) for e in result.scalars().all()]

    # NOTE: In multi-user mode, override this method to filter by user via JOIN Task
    async def get_latest_event_id(self, *, project_name: str | None = None) -> int:
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
