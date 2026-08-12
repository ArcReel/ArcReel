"""Async repository for generation task queue."""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from sqlalchemy import ColumnElement, func, select, text, update
from sqlalchemy import bindparam as sa_bindparam
from sqlalchemy import delete as sa_delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.base import DEFAULT_USER_ID, dt_to_iso, utc_now
from lib.db.models.task import Task, WorkerLease
from lib.db.repositories.base import BaseRepository, rowcount
from lib.task_failure import bound_reason, collapse_cascade_reason, encode_failure
from lib.task_terminal_events import TERMINAL_TASK_STATUSES

logger = logging.getLogger(__name__)

ACTIVE_TASK_STATUSES = ("queued", "running", "cancelling")

# 落库 error_message 的长度上限。裸切片会把结构化原因切在 JSON 中途，使 render_failure
# 无法解析、用户看到机器码碎片而非本地化文案，因此两条落库路径都经 bound_reason 收窄。
# 级联编码另在编码前按预算裁剪 reason（_encode_bounded_cascade_failure），保证结果本身
# 不需要再被截断，深层依赖链也不会近指数增长。
_MAX_ERROR_MESSAGE_LEN = 2000


def _active_dedupe_clauses(
    *,
    project_name: str,
    task_type: str,
    script_file: str | None,
    resource_type: str | None,
) -> list[ColumnElement[bool]]:
    """``idx_tasks_dedupe_active`` 去重键中与 ``resource_id`` 无关的那部分匹配条件。

    ``enqueue`` 的 ``IntegrityError`` 回查与 :meth:`TaskRepository.get_active_tasks_for_resources`
    共用这一份，去重口径只有一处定义。``script_file`` / ``resource_type`` 以空串归一，与索引
    表达式里的 ``COALESCE`` 对齐。
    """
    return [
        Task.project_name == project_name,
        Task.task_type == task_type,
        func.coalesce(Task.script_file, "") == (script_file or ""),
        func.coalesce(Task.resource_type, "") == (resource_type or ""),
        Task.status.in_(ACTIVE_TASK_STATUSES),
    ]


def _encode_bounded_cascade_failure(*, dependency_task_id: str, reason: str) -> str:
    # 上游原因本身是级联串时先折叠到根本原因：逐层包裹近指数增长，深链上裁剪只能把内层信封
    # 切在 JSON 中途，读侧会把残缺内层当普通文本嵌进本地化文案。折叠后串长与链深无关。
    reason = collapse_cascade_reason(reason)
    encoded = encode_failure("cascade_blocked_dependency", dependency_task_id=dependency_task_id, reason=reason)
    if len(encoded) <= _MAX_ERROR_MESSAGE_LEN:
        return encoded

    overhead = len(encode_failure("cascade_blocked_dependency", dependency_task_id=dependency_task_id, reason=""))
    budget = max(_MAX_ERROR_MESSAGE_LEN - overhead, 0)
    # JSON 转义（reason 中的引号/反斜杠）会让 budget 字符的 reason 编码后略超预算；始终经
    # bound_reason 收窄以保持结构化 reason 合法，不对已裁剪结果做原始字符再截断（那会切断
    # JSON 尾部）。极少数迭代仍超限时接受结果略超 _MAX_ERROR_MESSAGE_LEN，好过写坏 JSON。
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
        "queued_at": dt_to_iso(row.queued_at),
        "started_at": dt_to_iso(row.started_at),
        "finished_at": dt_to_iso(row.finished_at),
        "updated_at": dt_to_iso(row.updated_at),
        "user_id": row.user_id,
    }


class TaskRepository(BaseRepository):
    def __init__(self, session: AsyncSession):
        super().__init__(session)
        # 该 session 内落地的任务终态，供上层（GenerationQueue）在事务提交后发布项目事件。
        # 在此收集而非各终态方法各自记账：所有终态迁移（含级联失败/级联取消、批量取消
        # 队列）都经 _record_terminal_event 收口，一处挂钩即全覆盖。
        self.terminal_events: list[dict[str, Any]] = []

    def _record_terminal_event(
        self,
        *,
        task_id: str,
        project_name: str,
        status: str,
        task_type: str | None = None,
    ) -> None:
        """非终态调用是空操作：状态守卫兜住未来新增的非终态调用点，避免发出无对应动作的事件。"""
        if status in TERMINAL_TASK_STATUSES:
            self.terminal_events.append(
                {
                    "task_id": task_id,
                    "project_name": project_name,
                    "status": status,
                    "task_type": task_type,
                }
            )

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
                # Queue-layer semantic dedupe needs the active request payload from the row that
                # won the unique-index race. The wrapper consumes this private field before
                # returning its public enqueue result.
                return {
                    "task_id": existing.task_id,
                    "status": existing.status,
                    "deduped": True,
                    "existing_task_id": existing.task_id,
                    "_existing_payload": _json_loads(existing.payload_json, {}),
                }
            raise

        await self.session.commit()

        return {
            "task_id": task_id,
            "status": "queued",
            "deduped": False,
            "existing_task_id": None,
        }

    async def get_active_tasks_for_resources(
        self,
        *,
        project_name: str,
        task_type: str,
        resource_ids: list[str],
        script_file: str | None = None,
        resource_type: str | None = None,
    ) -> list[dict[str, Any]]:
        """查询命中 ``idx_tasks_dedupe_active`` 去重键、当前处于活动态的任务。

        match 条件与 :meth:`enqueue` 的 ``IntegrityError`` 分支共用
        :func:`_active_dedupe_clauses`，供调用方在真正入队前探测冲突并拒绝，而不是让
        ``enqueue`` 的原子去重悄悄回退到既有任务。结果按入队时间定序，调用方据此
        组织的文案不随实现漂移。
        """
        if not resource_ids:
            return []
        result = await self.session.execute(
            select(Task)
            .where(
                *_active_dedupe_clauses(
                    project_name=project_name,
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
        exclude_task_ids: frozenset[str] | None = None,
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
            # reference_video 的 provider_id 只是入队时投影；项目配置可能已改变，必须先
            # claim 并按当前 unit 重投影，不能让过期列值在 SQL 层永久挡住任务。
            provider_filter = (
                "AND (tasks.task_type = 'reference_video' "
                "OR tasks.provider_id IS NULL OR tasks.provider_id NOT IN :providers)"
            )
            params["providers"] = tuple(pool_full_providers)
        task_filter = ""
        if exclude_task_ids:
            task_filter = "AND tasks.task_id NOT IN :excluded_task_ids"
            params["excluded_task_ids"] = tuple(exclude_task_ids)

        # Use raw SQL for the dependency join (clearer than ORM for self-join)
        raw_stmt = text(f"""
            SELECT tasks.task_id
            FROM tasks
            LEFT JOIN tasks AS dependency
              ON dependency.task_id = tasks.dependency_task_id
            WHERE tasks.status = 'queued'
              AND tasks.media_type = :media_type
              {provider_filter}
              {task_filter}
              AND (
                tasks.dependency_task_id IS NULL
                OR dependency.status = 'succeeded'
              )
            ORDER BY tasks.queued_at ASC
            LIMIT 1
        """)
        if "providers" in params:
            raw_stmt = raw_stmt.bindparams(sa_bindparam("providers", expanding=True))
        if "excluded_task_ids" in params:
            raw_stmt = raw_stmt.bindparams(sa_bindparam("excluded_task_ids", expanding=True))

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
        self._record_terminal_event(
            task_id=task_id,
            project_name=done_task.project_name,
            status="succeeded",
            task_type=done_task.task_type,
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
        """级联专用：将 queued 依赖 task 标 failed；返回受影响行数。不 commit。"""
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
        """按依赖链级联标失败。`error_message` 沿链条原样传递（根因，不随层数重新嵌套），
        每层只把自己的直接阻塞方 `task_id` 写入编码——避免深层依赖链把上一层的完整编码串
        再嵌套进新一层 JSON 造成的近指数增长与落库截断损坏。
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

        - ``queued`` → ``mark_cancelled('user')`` 直接终态，``_mark_cancelled`` 内部
          ``cascade=True`` 自动级联（含 grandchildren）；
        - ``running`` → ``mark_cancelling()`` 中间态，等待 worker finally 兜底；
          下游级联由该 task 的 ``finalize_cancelled`` 兜底触发，避免在 running 还未
          实际终止时就把下游 queued 永久 cancelled。
        - ``cancelling`` → 幂等（视为已取消，不重复发信号）；
        - 终态（succeeded/failed/cancelled）→ skipped_terminal。

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

        # 顶层 dispatch：若 task 是 queued，_dispatch_cancel → _mark_cancelled(cascade=True)
        # 会自动递归级联下游（含 grandchildren）；若 task 是 running，仅落 cancelling，
        # 下游 cascade 等 finalize_cancelled 时再触发——这样避免「running 父尚未终止，
        # 下游 queued 已永久 cancelled」的语义错误。
        await self._dispatch_cancel(
            task, cancelled_by="user", cancelled=cancelled, cancelling=cancelling, skipped_terminal=skipped_terminal
        )

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

        queued 路径调 ``_mark_cancelled(cascade=True, ...)``——同一组 list 引用向下传，
        `_mark_cancelled` 内部递归触发后 grandchildren 自动累计到顶层响应体。
        """
        status = task.status
        if status == "queued":
            # `_mark_cancelled` 内部会在 cascade 前把自身 task_data append 到 cancelled
            # 列表（语义：先记录自己再级联下游），caller 拿返回值仅作 None 判定。
            await self._mark_cancelled(
                task.task_id,
                cancelled_by=cancelled_by,
                cancelled=cancelled,
                cancelling=cancelling,
                skipped_terminal=skipped_terminal,
                cascade=True,
            )
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

    async def _mark_cancelled(
        self,
        task_id: str,
        *,
        cancelled_by: str,
        cancelled: list[dict[str, Any]] | None = None,
        cancelling: list[str] | None = None,
        skipped_terminal: list[dict[str, Any]] | None = None,
        cascade: bool = True,
    ) -> dict[str, Any] | None:
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

        cascade=True（默认）：UPDATE 成功后内部触发 _cascade_cancel_dependents 向下递归，
        让 cancel_task 顶层响应体能收集到 grandchildren；递归通过 ``cancelled`` / ``cancelling``
        / ``skipped_terminal`` 三个 list 引用一起向下传。caller 不传 list 时用空 list 兜底
        （finalize_cancelled 等不需要响应体的 caller 走这条路径）。
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
        self._record_terminal_event(
            task_id=task_id,
            project_name=cancelled_task.project_name,
            status="cancelled",
            task_type=cancelled_task.task_type,
        )

        # 先把自身入 cancelled 列表，再级联——保证 cancel_task 响应体里父先于子。
        # caller 不传 list（finalize_cancelled）时跳过。
        if cancelled is not None:
            cancelled.append(task_data)

        if cascade:
            await self._cascade_cancel_dependents(
                task_id,
                cancelled if cancelled is not None else [],
                cancelling if cancelling is not None else [],
                skipped_terminal if skipped_terminal is not None else [],
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
        return rowcount(result)

    async def _cascade_cancel_dependents(
        self,
        task_id: str,
        cancelled: list[dict[str, Any]],
        cancelling: list[str],
        skipped_terminal: list[dict[str, Any]],
    ) -> None:
        """级联取消下游 dependents（仅遍历直接下游 + 派发）。

        递归由 ``_mark_cancelled`` 内部驱动：``_dispatch_cancel`` 调
        ``_mark_cancelled(cascade=True, ...)`` 让其在 rows>0 后自动调本函数处理
        grandchildren——即使下游 task 是 running（落 cancelling、不在本帧级联），
        其 worker finally 走 ``finalize_cancelled`` 时仍会触发对它自己下游的级联。
        """
        result = await self.session.execute(
            select(Task).where(Task.dependency_task_id == task_id).order_by(Task.queued_at.asc())
        )
        for dep_task in result.scalars().all():
            await self._dispatch_cancel(
                dep_task,
                cancelled_by="cascade",
                cancelled=cancelled,
                cancelling=cancelling,
                skipped_terminal=skipped_terminal,
            )

    async def persist_provider_job_id(
        self,
        task_id: str,
        job_id: str,
        *,
        endpoint: str | None = None,
        base_url: str | None = None,
    ) -> None:
        """单独事务持久化 provider_job_id；不带 WHERE 状态守卫（worker 内调用，确定是 running）。

        ``endpoint`` 是提交本 job 时实际使用的执行端点，按供应商类型有两种取值：自定义供应商
        传模型行的 endpoint 标识，提交域名随用户配置变化的内置供应商传实际请求域名。
        ``base_url`` 只由自定义供应商传——它的 ``endpoint`` 位已被协议标识占用，实际请求域名
        另落 ``submitted_base_url`` 列。两者都与 job_id 同一次 UPDATE 落地：必须同时可见，否则
        续跑会拿到 job_id 却判不出协议是否已被换掉、也无从回放原域名。None 时不写对应列——保留
        既有值比清空更安全（清空等于放弃比对 / 放弃回放）。

        失败抛异常，由 worker finally 兜底 mark_failed（ADR 0007 fail-fast：未持久化的
        submit 视为整笔失败，避免「幽灵任务」继续在 provider 端跑而 DB 已忘）。
        """
        now = utc_now()
        values: dict[str, Any] = {"provider_job_id": job_id, "updated_at": now}
        if endpoint is not None:
            values["provider_endpoint"] = endpoint
        if base_url is not None:
            values["submitted_base_url"] = base_url
        await self.session.execute(update(Task).where(Task.task_id == task_id).values(**values))
        await self.session.commit()

    async def _merge_payload_field(self, task_id: str, key: str, value: Any, *, raise_if_missing: bool = True) -> None:
        """把单个字段并入 task.payload 并提交；``raise_if_missing`` 决定 task 缺失时的处置。

        Task.payload_json 是 TEXT 列存 JSON 字符串（非 native JSONB），故用 read-modify-write
        模式更新。并发安全前提：写 payload 的路径在同一 task 的执行协程内串行——
        ``persist_effective_duration`` 与 ``persist_execution_identity`` 在向 provider 提交前
        各写一次，``persist_api_call_id`` 由 media_generator 在其后拿到 call_id 时写一次，
        互不并发。引入真正并发写 payload 的路径时需要外层加
        ``SELECT ... FOR UPDATE`` 或单事务串行化。
        """
        # 用 .first() 而非 scalar_one_or_none()：Task.payload_json 允许 NULL（迁移输入可含
        # payload_json IS NULL 行），scalar_one_or_none 会把"行存在但
        # payload_json=NULL"误判成"无行"。Row 解构 row[0] 后 None 由 _json_loads 兜底为 {}。
        result = await self.session.execute(select(Task.payload_json).where(Task.task_id == task_id))
        row = result.first()
        if row is None:
            if raise_if_missing:
                raise ValueError(f"task not found: {task_id}")
            return
        data = _json_loads(row[0], {})
        if not isinstance(data, dict):
            data = {}
        data[key] = value
        update_result = await self.session.execute(
            update(Task).where(Task.task_id == task_id).values(payload_json=_json_dumps(data), updated_at=utc_now())
        )
        if raise_if_missing and rowcount(update_result) == 0:
            raise ValueError(f"task not found: {task_id}")
        await self.session.commit()

    async def persist_api_call_id(self, task_id: str, call_id: int) -> None:
        """将 ApiCall.id 写入 task.payload["api_call_id"]，供 resume 路径精准翻 pending。

        Raises:
            ValueError: task_id 不存在或 UPDATE 命中 0 行——避免静默 commit 让上层
                以为"已持久化"但 payload["api_call_id"] 实际未写入，resume 时只能退回兜底。
        """
        await self._merge_payload_field(task_id, "api_call_id", call_id)

    async def persist_effective_duration(self, task_id: str, duration_seconds: int) -> None:
        """把取档后实际申请的秒数写回 ``task.payload["duration_seconds"]``。

        参考视频执行层按 model 能力取档后申请的秒数可能偏离入队时的剧本原值；
        resume 路径读的正是这个字段（见 ``server.services.resume_executor``），
        不写回会让 resume 时按剧本原值重新申请，与该任务执行时实际申请的秒数不一致。
        task 不存在时静默跳过（不影响该任务的生成结果，仅是 resume 元数据，不必 fail-fast
        阻断执行）。
        """
        await self._merge_payload_field(task_id, "duration_seconds", duration_seconds, raise_if_missing=False)

    async def persist_execution_provider_id(self, task_id: str, provider_id: str) -> None:
        """把 worker 重投影的 provider advisory 写回 ``task.provider_id``。

        未提交的 reference_video 任务在排队期间可以改项目配置；worker 认领后如果发现
        持久化列与当前投影分裂时，回队前刷新该列，让后续 claim 过滤与限流路由使用当前 provider。
        它不锁定 model，也不是执行身份。不带 WHERE 状态守卫，与 ``persist_provider_job_id`` 同理。
        """
        now = utc_now()
        await self.session.execute(
            update(Task).where(Task.task_id == task_id).values(provider_id=provider_id, updated_at=now)
        )
        await self.session.commit()

    async def persist_execution_identity(self, task_id: str, provider_id: str, payload_patch: dict[str, Any]) -> None:
        """把执行期实际解析出的身份写回 ``task.provider_id`` 列并按 ``payload_patch`` 改写 payload。

        ``payload_patch`` 值为 ``None`` 表示删除该键，其余键覆盖写入；列与 payload 在同一次
        提交内落地。task 不存在时 UPDATE 命中 0 行、静默返回，与
        ``persist_execution_provider_id`` 同口径。写 payload 的串行前提见
        ``_merge_payload_field`` docstring。
        """
        result = await self.session.execute(select(Task.payload_json).where(Task.task_id == task_id))
        row = result.first()
        values: dict[str, Any] = {"provider_id": provider_id, "updated_at": utc_now()}
        if row is not None:
            data = _json_loads(row[0], {})
            if not isinstance(data, dict):
                data = {}
            for key, value in payload_patch.items():
                if value is None:
                    data.pop(key, None)
                else:
                    data[key] = value
            values["payload_json"] = _json_dumps(data)
        await self.session.execute(update(Task).where(Task.task_id == task_id).values(**values))
        await self.session.commit()

    async def list_orphan_tasks_on_start(self) -> list[dict[str, Any]]:
        """返回 running + cancelling 状态任务用于重启自愈（ADR 0007）。"""
        result = await self.session.execute(
            select(Task).where(Task.status.in_(("running", "cancelling"))).order_by(Task.updated_at.asc())
        )
        return [_task_to_dict(t) for t in result.scalars().all()]

    async def finalize_cancelled(self, task_id: str, *, cancelled_by: str = "user") -> dict[str, Any]:
        """Worker finally 0-rows-cancelled 协议入口：把 queued/cancelling/running task 落 cancelled。

        SQL 守卫 ``status IN ('queued','cancelling','running')`` 接住三条路径：
        - cancel API 取消的 queued 任务；
        - mark_succeeded/mark_failed 返回 0 rows（外部已抢先翻 cancelling）后兜底；
        - SIGTERM / 进程外 cancel 直接打到 running，没有走过 cancel API 的也能落地。

        ``cascade=True``：本 task 终态落地后，``_mark_cancelled`` 内部触发下游级联——
        覆盖「父 running 还在 cancelling，下游 queued 暂未级联，等父 worker finally
        落 cancelled 时再统一级联下游」这条主路径。

        返回 ``{"rows": int, "cancelling": list[str]}``：cancelling 是级联出来的 running
        下游 task_id 列表——Repository 只返回意图，由上层 GenerationQueue 同步分发
        in-process cancel 信号（Repository 不持 Worker callback）；这样级联打到的
        running 子任务能立刻收到 cancel 而不必等它跑完。
        """
        cancelling: list[str] = []
        data = await self._mark_cancelled(
            task_id,
            cancelled_by=cancelled_by,
            cancelling=cancelling,
            cascade=True,
        )
        await self.session.commit()
        return {"rows": 1 if data is not None else 0, "cancelling": cancelling}

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
                self._record_terminal_event(
                    task_id=updated_task.task_id,
                    project_name=project_name,
                    status="cancelled",
                    task_type=updated_task.task_type,
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
