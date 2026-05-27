"""Async repository for generation task queue."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.task import Task, TaskEvent, WorkerLease
from lib.db.repositories.base import BaseRepository, rowcount

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = ("queued", "running", "cancelling")


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
        "provider_id": row.provider_id,
        "provider_job_id": row.provider_job_id,
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
        provider_id: str | None = None,
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
            provider_id=provider_id,
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
    async def claim_next(
        self,
        media_type: str,
        *,
        pool_full_providers: frozenset[str] | None = None,
    ) -> dict[str, Any] | None:
        """领取下一个 queued 任务。

        ``pool_full_providers`` 为本 cycle 已知池满的 provider_id 集合（黑名单语义）：
        - ``None`` / 空集合 —— 不做 provider 过滤
        - 非空集合 —— 排除这些 provider 的任务。``provider_id IS NULL`` 的老数据和
          ``provider_id`` 不在已知池集合里的任务（例如自定义 provider 被删除）都不会
          被排除，worker claim 后由 ``_extract_provider`` 派生 provider 再校验。

        采用黑名单语义而非白名单（早期实现）的原因：白名单会把"已知但未在当前
        ``_pools`` 里的 provider"任务永久过滤掉（例如自定义 provider 被禁用 / 删除），
        导致静默堆积。黑名单只排除已知池满，未知 provider 任务正常 claim 走 worker
        二次解析（解析失败走 mark_failed 兜底，不会无声卡死）。
        """
        now = utc_now()

        params: dict[str, Any] = {"media_type": media_type}
        provider_filter = ""
        if pool_full_providers:
            # SQLite/PG 都支持 expanding bindparam：list 形式 + NOT IN (:providers)
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

        await self._append_event(
            task_id=target_task_id,
            project_name=running_task.project_name,
            event_type="running",
            status="running",
            data=task_data,
        )
        await self.session.commit()
        return task_data

    async def mark_succeeded(self, task_id: str, result: dict[str, Any] | None = None) -> int:
        """SQL `WHERE status='running'` 守卫；返回受影响行数。

        rows=0 表示外部已把 DB 翻成 cancelling/cancelled/failed 等非 running 终/中间态，
        worker finally 应据此走 0-rows-cancelled 协议（ADR 0006）。
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
            # 不 commit；外部已写过其他终态，不要触发 event
            return 0

        await self.session.flush()
        res = await self.session.execute(select(Task).where(Task.task_id == task_id))
        done_task = res.scalar_one()
        task_data = _task_to_dict(done_task)
        await self._append_event(
            task_id=task_id,
            project_name=done_task.project_name,
            event_type="succeeded",
            status="succeeded",
            data=task_data,
        )
        await self.session.commit()
        return affected

    async def mark_failed(self, task_id: str, error_message: str) -> int:
        """SQL `WHERE status='running'` 守卫；返回受影响行数。

        rows=0 表示外部已把 DB 翻成 cancelling/cancelled/succeeded 等非 running 状态，
        worker finally 走 0-rows-cancelled 协议。级联失败（依赖 task）走独立路径。
        """
        affected = await self._mark_failed_running(task_id=task_id, error_message=error_message)
        if affected == 0:
            return 0

        await self._cascade_failed_queued(task_id=task_id, error_message=error_message)
        await self.session.commit()
        return affected

    async def _mark_failed_running(self, *, task_id: str, error_message: str) -> int:
        """单点：将 running task 标 failed；返回受影响行数。不 commit。"""
        now = utc_now()
        update_result = await self.session.execute(
            update(Task)
            .where(Task.task_id == task_id, Task.status == "running")
            .values(
                status="failed",
                error_message=error_message[:2000],
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
        task_data = _task_to_dict(failed_task)
        await self._append_event(
            task_id=task_id,
            project_name=failed_task.project_name,
            event_type="failed",
            status="failed",
            data=task_data,
        )
        return affected

    async def _mark_failed_queued_dep(self, *, task_id: str, error_message: str) -> int:
        """级联专用：将 queued 依赖 task 标 failed；返回受影响行数。不 commit。"""
        now = utc_now()
        update_result = await self.session.execute(
            update(Task)
            .where(Task.task_id == task_id, Task.status == "queued")
            .values(
                status="failed",
                error_message=error_message[:2000],
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
        task_data = _task_to_dict(failed_task)
        await self._append_event(
            task_id=task_id,
            project_name=failed_task.project_name,
            event_type="failed",
            status="failed",
            data=task_data,
        )
        return affected

    async def _cascade_failed_queued(self, *, task_id: str, error_message: str) -> int:
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
            affected = await self._mark_failed_queued_dep(task_id=dep_id, error_message=blocked_message)
            if affected == 0:
                continue
            cascaded += affected
            cascaded += await self._cascade_failed_queued(task_id=dep_id, error_message=blocked_message)
        return cascaded

    async def get_cancel_preview(self, task_id: str) -> dict[str, Any]:
        """预览取消某个任务的影响范围。

        现在 queued / running / cancelling 都允许取消（ADR 0006），preview 只列「队列中的下游」
        以避免吓人：running / cancelling 下游运行期数量不稳定，由 cancel 操作实际触发后再
        通过 SSE 反映。终态 task 调用方应在前端避免触发。
        """
        result = await self.session.execute(select(Task).where(Task.task_id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError(f"任务 '{task_id}' 不存在")

        task_summary = {
            "task_id": task.task_id,
            "task_type": task.task_type,
            "resource_id": task.resource_id,
            "status": task.status,
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
        """按状态分发取消（ADR 0006）：

        - ``queued`` → ``mark_cancelled('user')`` 直接终态
        - ``running`` → ``mark_cancelling()`` 中间态，等待 worker finally 兜底
        - ``cancelling`` → 幂等（视为已取消，不重复发信号）
        - 终态（succeeded/failed/cancelled）→ skipped_terminal

        Repository 只更新 DB，不持有 worker callback。``cancelling`` 列表交由
        上层（GenerationQueue）拿到后同步分发 in-process cancel 信号。
        """
        result = await self.session.execute(select(Task).where(Task.task_id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            raise ValueError(f"任务 '{task_id}' 不存在")

        cancelled: list[dict[str, Any]] = []
        cancelling: list[str] = []
        skipped_terminal: list[dict[str, Any]] = []

        await self._dispatch_cancel(
            task, cancelled_by="user", cancelled=cancelled, cancelling=cancelling, skipped_terminal=skipped_terminal
        )
        await self._cascade_cancel_dependents(task_id, cancelled, cancelling, skipped_terminal)

        await self.session.commit()
        return {
            "cancelled": cancelled,
            "cancelling": cancelling,
            "skipped_terminal": skipped_terminal,
        }

    async def _dispatch_cancel(
        self,
        task: Task,
        *,
        cancelled_by: str,
        cancelled: list[dict[str, Any]],
        cancelling: list[str],
        skipped_terminal: list[dict[str, Any]],
    ) -> None:
        """根据 task.status 分发到对应的 DB 状态转移。

        cancelled_by 在 queued 和 running 两条路径都用同一个值，统一归因。
        running 路径写入 cancelling 中间态时即记录 cancelled_by，worker finally
        通过 COALESCE 保留这个值。
        """
        status = task.status
        if status == "queued":
            data = await self._mark_cancelled(task.task_id, cancelled_by=cancelled_by)
            if data:
                cancelled.append(data)
        elif status == "running":
            affected = await self._mark_cancelling(task.task_id, cancelled_by=cancelled_by)
            if affected > 0:
                cancelling.append(task.task_id)
            else:
                # 竞态：UPDATE 失败说明 status 已变；刷新分发到对应桶
                await self.session.refresh(task)
                if task.status == "cancelling":
                    cancelling.append(task.task_id)
                elif task.status in ("succeeded", "failed", "cancelled"):
                    # worker 已抢先落终态，让 API 响应体里有迹可循（避免前端 spinner 转死）
                    skipped_terminal.append(_task_to_dict(task))
                # 其他状态（queued —— 理论上不会出现）忽略
        elif status == "cancelling":
            # 幂等：已发起取消，不重复加 cancelling 信号
            pass
        else:
            # succeeded / failed / cancelled —— 终态
            skipped_terminal.append(_task_to_dict(task))

    async def _mark_cancelled(self, task_id: str, *, cancelled_by: str) -> dict[str, Any] | None:
        """将 queued / cancelling / running 任务标记为 cancelled（终态）。

        WHERE 守卫 ``status IN ('queued','cancelling','running')`` 承担三条路径：
        1. cancel API 直接取消 queued；
        2. worker finally 兜底从 cancelling 落地；
        3. 进程级 cancel（SIGTERM / wait_for 超时 / asyncio.Task.cancel 直接打到 running）
           ——这条以前漏掉，会把任务永久卡在 running，每次重启都被 orphan handler 当成
           需要 resume 的任务重新拉起来。
        终态（succeeded/failed/cancelled）仍然由 IN 子句排除，保持幂等。

        cancelled_by 用 COALESCE 写入：上游 _mark_cancelling 已写过的（cascade 等）保留，
        没写过的（直接 running→cancelled 兜底）用 caller 提供的值兜底，避免级联归因丢失。
        """
        now = utc_now()
        stmt = (
            update(Task)
            .where(Task.task_id == task_id, Task.status.in_(("queued", "cancelling", "running")))
            .values(
                status="cancelled",
                cancelled_by=func.coalesce(Task.cancelled_by, cancelled_by),
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
        await self._append_event(
            task_id=task_id,
            project_name=cancelled_task.project_name,
            event_type="cancelled",
            status="cancelled",
            data=task_data,
        )
        return task_data

    async def _mark_cancelling(self, task_id: str, *, cancelled_by: str = "user") -> int:
        """将 running task 标 cancelling（中间态，ADR 0006）；返回受影响行数。

        cancelled_by 在这里就写入，worker finally 通过 COALESCE 兜底而非覆盖，
        让级联归因 ('cascade') 一路穿透到最终 cancelled 终态。
        """
        now = utc_now()
        stmt = (
            update(Task)
            .where(Task.task_id == task_id, Task.status == "running")
            .values(status="cancelling", cancelled_by=cancelled_by, updated_at=now)
        )
        result = await self.session.execute(stmt)
        affected = rowcount(result)
        if affected == 0:
            return 0

        await self.session.flush()
        res = await self.session.execute(select(Task).where(Task.task_id == task_id))
        cancelling_task = res.scalar_one()
        task_data = _task_to_dict(cancelling_task)
        await self._append_event(
            task_id=task_id,
            project_name=cancelling_task.project_name,
            event_type="cancelling",
            status="cancelling",
            data=task_data,
        )
        return affected

    async def _cascade_cancel_dependents(
        self,
        task_id: str,
        cancelled: list[dict[str, Any]],
        cancelling: list[str],
        skipped_terminal: list[dict[str, Any]],
    ) -> None:
        """递归级联取消下游：queued → cancelled('cascade')；running → cancelling；其他幂等/跳过。"""
        result = await self.session.execute(
            select(Task).where(Task.dependency_task_id == task_id).order_by(Task.queued_at.asc())
        )
        for dep_task in result.scalars().all():
            before_cancelled = len(cancelled)
            before_cancelling = len(cancelling)
            await self._dispatch_cancel(
                dep_task,
                cancelled_by="cascade",
                cancelled=cancelled,
                cancelling=cancelling,
                skipped_terminal=skipped_terminal,
            )
            # 仅在 queued → cancelled 这条路径递归向下（cancelling 下游运行期不递归
            # 以避免预先级联未确定的依赖；worker finally 落地 cancelled 后由依赖检查路径处理）
            if len(cancelled) > before_cancelled and len(cancelling) == before_cancelling:
                await self._cascade_cancel_dependents(dep_task.task_id, cancelled, cancelling, skipped_terminal)

    async def persist_provider_job_id(self, task_id: str, job_id: str) -> None:
        """单独事务持久化 provider_job_id；不带 WHERE 状态守卫（worker 内调用，确定是 running）。

        失败抛异常，由 worker finally 兜底 mark_failed（ADR 0007 fail-fast：未持久化的
        submit 视为整笔失败，避免「幽灵任务」继续在 provider 端跑而 DB 已忘）。
        """
        now = utc_now()
        await self.session.execute(
            update(Task).where(Task.task_id == task_id).values(provider_job_id=job_id, updated_at=now)
        )
        await self.session.commit()

    async def list_orphan_tasks_on_start(self) -> list[dict[str, Any]]:
        """返回 running + cancelling 状态任务用于重启自愈（ADR 0007）。"""
        result = await self.session.execute(
            select(Task).where(Task.status.in_(("running", "cancelling"))).order_by(Task.updated_at.asc())
        )
        return [_task_to_dict(t) for t in result.scalars().all()]

    async def finalize_cancelled(self, task_id: str, *, cancelled_by: str = "user") -> int:
        """Worker finally 0-rows-cancelled 协议入口：把 queued/cancelling/running task 落 cancelled。

        SQL 守卫 ``status IN ('queued','cancelling','running')`` 接住三条路径：
        - cancel API 取消的 queued 任务；
        - mark_succeeded/mark_failed 返回 0 rows（外部已抢先翻 cancelling）后兜底；
        - SIGTERM / 进程外 cancel 直接打到 running，没有走过 cancel API 的也能落地。

        独立 commit，返回受影响行数。
        """
        data = await self._mark_cancelled(task_id, cancelled_by=cancelled_by)
        await self.session.commit()
        return 1 if data is not None else 0

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
        cancelled_count = rowcount(result)

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
        """救援扳手：批量把 running 任务回队（保留供 ops 手动执行）。

        Worker 启动期不再自动调，改走 ``list_orphan_tasks_on_start`` + ``resume_video``（ADR 0007）。
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

        stats = {
            "queued": 0,
            "running": 0,
            "cancelling": 0,
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
