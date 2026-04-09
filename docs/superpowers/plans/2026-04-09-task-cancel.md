# 任务取消功能实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为排队中的生成任务提供手动取消能力，支持单任务取消（含依赖级联）和批量取消所有排队任务。

**Architecture:** 新增 `cancelled` 终态，从 DB 层到 API 层到前端 UI 全链路实现。后端在 TaskRepository 新增取消和预览方法，通过 GenerationQueue 透传到 API 路由。前端在 TaskHud 组件中添加取消按钮和确认弹窗。Client 层的 `wait_for_task` 检测 `cancelled` 状态抛出 `TaskCancelledError`。

**Tech Stack:** Python / SQLAlchemy / FastAPI / Alembic / React / TypeScript / Zustand

---

## 文件结构

| 操作 | 文件 | 职责 |
|------|------|------|
| Modify | `lib/db/models/task.py` | Task 模型新增 `cancelled_by` 字段 |
| Create | `alembic/versions/xxxx_add_cancelled_by_to_tasks.py` | 数据库迁移 |
| Modify | `lib/db/repositories/task_repo.py` | 新增 cancel_task / cancel_all_queued / get_cancel_preview / get_cancel_all_preview 方法，更新 get_stats |
| Modify | `lib/generation_queue.py` | 透传取消方法，更新 TERMINAL_TASK_STATUSES |
| Modify | `lib/generation_queue_client.py` | 新增 TaskCancelledError，wait_for_task 检测 cancelled |
| Modify | `lib/generation_worker.py` | _process_task 中处理被取消任务 |
| Modify | `server/routers/tasks.py` | 新增 4 个取消相关 API 端点 |
| Modify | `frontend/src/types/task.ts` | TaskStatus 新增 cancelled，TaskItem 新增 cancelled_by |
| Modify | `frontend/src/api.ts` | 新增 cancelPreview / cancelTask / cancelAllPreview / cancelAllQueued 方法 |
| Modify | `frontend/src/components/task-hud/TaskHud.tsx` | 取消按钮、确认弹窗、cancelled 状态展示 |
| Modify | `frontend/src/stores/tasks-store.ts` | stats 新增 cancelled 计数 |
| Modify | `tests/test_task_repo.py` | 取消功能测试 |
| Modify | `tests/test_generation_queue.py` | Queue 层取消测试 |
| Modify | `tests/test_generation_queue_client.py` | TaskCancelledError 测试 |
| Create | `tests/test_task_cancel_router.py` | 取消 API 端点测试 |

---

### Task 1: 数据模型与迁移

**Files:**
- Modify: `lib/db/models/task.py:13-49`
- Create: `alembic/versions/xxxx_add_cancelled_by_to_tasks.py`
- Test: `tests/test_task_repo.py`

- [ ] **Step 1: 写 Task 模型新增字段的测试**

在 `tests/test_task_repo.py` 的 `TestTaskRepository` 类中新增测试：

```python
async def test_cancel_single_queued_task(self, db_session):
    repo = TaskRepository(db_session)

    task = await repo.enqueue(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        payload={},
        script_file="ep1.json",
    )

    result = await repo.cancel_task(task["task_id"])
    assert len(result["cancelled"]) == 1
    assert result["cancelled"][0]["task_id"] == task["task_id"]
    assert result["cancelled"][0]["cancelled_by"] == "user"
    assert result["skipped_running"] == []

    cancelled = await repo.get(task["task_id"])
    assert cancelled["status"] == "cancelled"
    assert cancelled["cancelled_by"] == "user"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_task_repo.py::TestTaskRepository::test_cancel_single_queued_task -v`
Expected: FAIL — `cancelled_by` 字段不存在 / `cancel_task` 方法不存在

- [ ] **Step 3: Task 模型新增 cancelled_by 字段**

在 `lib/db/models/task.py` 的 Task 类中，在 `dependency_index` 之后添加：

```python
cancelled_by: Mapped[str | None] = mapped_column(String)
```

同时在 `lib/db/repositories/task_repo.py` 的 `_task_to_dict` 函数中添加字段映射，在 `"dependency_index"` 之后：

```python
"cancelled_by": row.cancelled_by,
```

- [ ] **Step 4: 生成 Alembic 迁移**

Run: `uv run alembic revision --autogenerate -m "add cancelled_by to tasks"`

- [ ] **Step 5: 执行迁移**

Run: `uv run alembic upgrade head`

- [ ] **Step 6: Commit**

```bash
git add lib/db/models/task.py alembic/versions/*cancelled_by* lib/db/repositories/task_repo.py tests/test_task_repo.py
git commit -m "feat: Task 模型新增 cancelled_by 字段"
```

---

### Task 2: TaskRepository 取消方法

**Files:**
- Modify: `lib/db/repositories/task_repo.py:267-356` (在 `_cascade_failed_dependents` 后新增)
- Modify: `lib/db/repositories/task_repo.py:459-477` (更新 `get_stats`)
- Test: `tests/test_task_repo.py`

- [ ] **Step 1: 写取消级联测试**

在 `tests/test_task_repo.py` 的 `TestTaskRepository` 类中新增：

```python
async def test_cancel_task_cascades_to_dependents(self, db_session):
    repo = TaskRepository(db_session)

    first = await repo.enqueue(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        payload={},
        script_file="ep1.json",
    )
    second = await repo.enqueue(
        project_name="demo",
        task_type="video",
        media_type="video",
        resource_id="E1S01",
        payload={},
        script_file="ep1.json",
        dependency_task_id=first["task_id"],
    )

    result = await repo.cancel_task(first["task_id"])
    assert len(result["cancelled"]) == 2
    assert result["cancelled"][0]["task_id"] == first["task_id"]
    assert result["cancelled"][0]["cancelled_by"] == "user"
    assert result["cancelled"][1]["task_id"] == second["task_id"]
    assert result["cancelled"][1]["cancelled_by"] == "cascade"

    dep_task = await repo.get(second["task_id"])
    assert dep_task["status"] == "cancelled"
    assert dep_task["cancelled_by"] == "cascade"

async def test_cancel_running_task_rejected(self, db_session):
    repo = TaskRepository(db_session)

    task = await repo.enqueue(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        payload={},
        script_file="ep1.json",
    )
    await repo.claim_next("image")

    with pytest.raises(ValueError, match="只有排队中的任务可以取消"):
        await repo.cancel_task(task["task_id"])

async def test_cancel_preview(self, db_session):
    repo = TaskRepository(db_session)

    first = await repo.enqueue(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        payload={},
        script_file="ep1.json",
    )
    second = await repo.enqueue(
        project_name="demo",
        task_type="video",
        media_type="video",
        resource_id="E1S01",
        payload={},
        script_file="ep1.json",
        dependency_task_id=first["task_id"],
    )

    preview = await repo.get_cancel_preview(first["task_id"])
    assert preview["task"]["task_id"] == first["task_id"]
    assert len(preview["cascaded"]) == 1
    assert preview["cascaded"][0]["task_id"] == second["task_id"]

async def test_cancel_all_queued(self, db_session):
    repo = TaskRepository(db_session)

    await repo.enqueue(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        payload={},
        script_file="ep1.json",
    )
    t2 = await repo.enqueue(
        project_name="demo",
        task_type="video",
        media_type="video",
        resource_id="E1S02",
        payload={},
        script_file="ep1.json",
    )
    # Claim one task so it becomes running
    await repo.claim_next("image")

    result = await repo.cancel_all_queued("demo")
    assert result["cancelled_count"] == 1  # only the queued video task
    assert result["skipped_running_count"] == 1

    task = await repo.get(t2["task_id"])
    assert task["status"] == "cancelled"

async def test_get_stats_includes_cancelled(self, db_session):
    repo = TaskRepository(db_session)

    task = await repo.enqueue(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        payload={},
        script_file="ep1.json",
    )
    await repo.cancel_task(task["task_id"])

    stats = await repo.get_stats()
    assert stats["cancelled"] == 1
    assert stats["queued"] == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_task_repo.py::TestTaskRepository::test_cancel_task_cascades_to_dependents tests/test_task_repo.py::TestTaskRepository::test_cancel_running_task_rejected tests/test_task_repo.py::TestTaskRepository::test_cancel_preview tests/test_task_repo.py::TestTaskRepository::test_cancel_all_queued tests/test_task_repo.py::TestTaskRepository::test_get_stats_includes_cancelled -v`
Expected: FAIL — 方法不存在

- [ ] **Step 3: 实现 get_cancel_preview**

在 `lib/db/repositories/task_repo.py` 的 `_cascade_failed_dependents` 方法之后添加：

```python
async def get_cancel_preview(self, task_id: str) -> dict[str, Any]:
    """预览取消某个任务的影响范围，返回任务本身和会被级联取消的依赖任务列表。"""
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
```

- [ ] **Step 4: 实现 cancel_task**

在 `get_cancel_preview` 之后添加：

```python
async def cancel_task(self, task_id: str) -> dict[str, Any]:
    """取消一个 queued 任务，级联取消其所有 queued 依赖任务。

    Returns: {"cancelled": [...], "skipped_running": [...]}
    """
    result = await self.session.execute(select(Task).where(Task.task_id == task_id))
    task = result.scalar_one_or_none()
    if not task:
        raise ValueError(f"任务 '{task_id}' 不存在")
    if task.status != "queued":
        raise ValueError("只有排队中的任务可以取消")

    cancelled = []
    skipped_running = []

    # 取消主任务
    task_dict = await self._mark_cancelled(task_id, cancelled_by="user")
    if task_dict:
        cancelled.append(task_dict)
    else:
        # 竞态：已经变成 running
        refreshed = await self.session.execute(select(Task).where(Task.task_id == task_id))
        t = refreshed.scalar_one_or_none()
        if t and t.status == "running":
            skipped_running.append(_task_to_dict(t))

    # 级联取消依赖任务
    await self._cascade_cancel_dependents(task_id, cancelled, skipped_running)

    await self.session.commit()
    return {"cancelled": cancelled, "skipped_running": skipped_running}

async def _mark_cancelled(self, task_id: str, *, cancelled_by: str) -> dict[str, Any] | None:
    """将一个 queued 任务标记为 cancelled。仅当状态仍为 queued 时生效。"""
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
        select(Task.task_id, Task.status)
        .where(Task.dependency_task_id == task_id)
        .order_by(Task.queued_at.asc())
    )
    for row in result.all():
        dep_id, dep_status = row[0], row[1]
        if dep_status == "queued":
            task_data = await self._mark_cancelled(dep_id, cancelled_by="cascade")
            if task_data:
                cancelled.append(task_data)
                await self._cascade_cancel_dependents(dep_id, cancelled, skipped_running)
            else:
                refreshed = await self.session.execute(select(Task).where(Task.task_id == dep_id))
                t = refreshed.scalar_one_or_none()
                if t and t.status == "running":
                    skipped_running.append(_task_to_dict(t))
        elif dep_status == "running":
            refreshed = await self.session.execute(select(Task).where(Task.task_id == dep_id))
            t = refreshed.scalar_one_or_none()
            if t:
                skipped_running.append(_task_to_dict(t))
```

- [ ] **Step 5: 实现 cancel_all_queued 和 get_cancel_all_preview**

在 `cancel_task` 之后添加：

```python
async def get_cancel_all_preview(self, project_name: str) -> int:
    """返回项目中当前 queued 状态的任务数量。"""
    result = await self.session.execute(
        select(func.count())
        .select_from(Task)
        .where(Task.project_name == project_name, Task.status == "queued")
    )
    return result.scalar_one()

async def cancel_all_queued(self, project_name: str) -> dict[str, Any]:
    """取消项目中所有 queued 任务。

    Returns: {"cancelled_count": N, "skipped_running_count": M}
    """
    # 先统计 running 数量（用于计算 skipped）
    running_result = await self.session.execute(
        select(func.count())
        .select_from(Task)
        .where(Task.project_name == project_name, Task.status == "running")
    )
    running_count = running_result.scalar_one()

    # 收集要取消的任务 ID 列表（用于写事件）
    queued_result = await self.session.execute(
        select(Task)
        .where(Task.project_name == project_name, Task.status == "queued")
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

    # 为每个取消的任务写入事件
    for task in queued_tasks:
        await self.session.flush()
        res = await self.session.execute(select(Task).where(Task.task_id == task.task_id))
        updated_task = res.scalar_one()
        task_data = _task_to_dict(updated_task)
        await self._append_event(
            task_id=task.task_id,
            project_name=project_name,
            event_type="cancelled",
            status="cancelled",
            data=task_data,
        )

    await self.session.commit()
    return {
        "cancelled_count": cancelled_count,
        "skipped_running_count": running_count,
    }
```

- [ ] **Step 6: 更新 get_stats 包含 cancelled**

修改 `lib/db/repositories/task_repo.py:469` 的 stats 默认字典：

```python
stats = {"queued": 0, "running": 0, "succeeded": 0, "failed": 0, "cancelled": 0, "total": 0}
```

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_task_repo.py -v`
Expected: ALL PASS

- [ ] **Step 8: Commit**

```bash
git add lib/db/repositories/task_repo.py tests/test_task_repo.py
git commit -m "feat: TaskRepository 新增取消/预览/批量取消方法"
```

---

### Task 3: GenerationQueue 透传

**Files:**
- Modify: `lib/generation_queue.py:1-230`
- Test: `tests/test_generation_queue.py`

- [ ] **Step 1: 写 GenerationQueue 取消测试**

在 `tests/test_generation_queue.py` 中新增：

```python
async def test_cancel_task(session_factory):
    queue = GenerationQueue(session_factory=session_factory)

    result = await queue.enqueue_task(
        project_name="demo",
        task_type="storyboard",
        media_type="image",
        resource_id="E1S01",
        payload={},
        script_file="ep1.json",
    )

    cancel_result = await queue.cancel_task(result["task_id"])
    assert len(cancel_result["cancelled"]) == 1
    assert cancel_result["cancelled"][0]["status"] == "cancelled"

async def test_cancel_all_queued(session_factory):
    queue = GenerationQueue(session_factory=session_factory)

    await queue.enqueue_task(
        project_name="demo", task_type="storyboard", media_type="image",
        resource_id="E1S01", payload={}, script_file="ep1.json",
    )
    await queue.enqueue_task(
        project_name="demo", task_type="video", media_type="video",
        resource_id="E1S02", payload={}, script_file="ep1.json",
    )

    result = await queue.cancel_all_queued("demo")
    assert result["cancelled_count"] == 2

    stats = await queue.get_task_stats(project_name="demo")
    assert stats["cancelled"] == 2
    assert stats["queued"] == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_generation_queue.py::test_cancel_task tests/test_generation_queue.py::test_cancel_all_queued -v`
Expected: FAIL — 方法不存在

- [ ] **Step 3: 更新 TERMINAL_TASK_STATUSES 并添加透传方法**

在 `lib/generation_queue.py` 中：

1. 更新常量（第 20 行）：

```python
TERMINAL_TASK_STATUSES = ("succeeded", "failed", "cancelled")
```

2. 在 `mark_task_failed` 方法之后添加：

```python
async def cancel_task(self, task_id: str) -> dict[str, Any]:
    async with self._session_factory() as session:
        repo = TaskRepository(session)
        result = await repo.cancel_task(task_id)
    cancelled_count = len(result.get("cancelled", []))
    if cancelled_count > 0:
        logger.info("任务取消 task_id=%s 共取消 %d 个", task_id, cancelled_count)
    return result

async def get_cancel_preview(self, task_id: str) -> dict[str, Any]:
    async with self._session_factory() as session:
        repo = TaskRepository(session)
        return await repo.get_cancel_preview(task_id)

async def cancel_all_queued(self, project_name: str) -> dict[str, Any]:
    async with self._session_factory() as session:
        repo = TaskRepository(session)
        result = await repo.cancel_all_queued(project_name)
    if result["cancelled_count"] > 0:
        logger.info("批量取消 project=%s 共取消 %d 个", project_name, result["cancelled_count"])
    return result

async def get_cancel_all_preview(self, project_name: str) -> int:
    async with self._session_factory() as session:
        repo = TaskRepository(session)
        return await repo.get_cancel_all_preview(project_name)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_generation_queue.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add lib/generation_queue.py tests/test_generation_queue.py
git commit -m "feat: GenerationQueue 透传取消方法，更新 TERMINAL_TASK_STATUSES"
```

---

### Task 4: Client 层 TaskCancelledError

**Files:**
- Modify: `lib/generation_queue_client.py:24-101`
- Test: `tests/test_generation_queue_client.py`

- [ ] **Step 1: 写 TaskCancelledError 测试**

在 `tests/test_generation_queue_client.py` 中新增：

```python
async def test_wait_for_task_cancelled(session_factory, worker_lease):
    """wait_for_task 检测到 cancelled 状态时返回任务（不抛异常）。"""
    queue = GenerationQueue(session_factory=session_factory)
    result = await queue.enqueue_task(
        project_name="demo", task_type="storyboard", media_type="image",
        resource_id="E1S01", payload={}, script_file="ep1.json",
    )

    await queue.cancel_task(result["task_id"])

    task = await wait_for_task(result["task_id"], poll_interval=0.05, timeout_seconds=2)
    assert task["status"] == "cancelled"

async def test_enqueue_and_wait_cancelled(session_factory, worker_lease):
    """enqueue_and_wait 在任务被取消时抛出 TaskCancelledError。"""
    queue = GenerationQueue(session_factory=session_factory)

    # 先入队
    enqueue_result = await enqueue_task_only(
        project_name="demo", task_type="storyboard", media_type="image",
        resource_id="E1S01", payload={}, script_file="ep1.json",
    )

    # 取消
    await queue.cancel_task(enqueue_result["task_id"])

    # wait 应该抛出 TaskCancelledError
    with pytest.raises(TaskCancelledError):
        await enqueue_and_wait(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S01", payload={}, script_file="ep1.json",
            wait_timeout_seconds=2,
        )
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_generation_queue_client.py::test_wait_for_task_cancelled tests/test_generation_queue_client.py::test_enqueue_and_wait_cancelled -v`
Expected: FAIL — TaskCancelledError 不存在

- [ ] **Step 3: 实现 TaskCancelledError 和更新 wait_for_task**

在 `lib/generation_queue_client.py` 中：

1. 在 `TaskFailedError` 之后（第 30 行之后）添加：

```python
class TaskCancelledError(RuntimeError):
    """Raised when queued task is cancelled by user."""
```

2. 更新 `wait_for_task` 函数中的状态检查（第 85-87 行），将：

```python
status = task.get("status")
if status in ("succeeded", "failed"):
    return task
```

改为：

```python
status = task.get("status")
if status in ("succeeded", "failed", "cancelled"):
    return task
```

3. 更新 `enqueue_and_wait` 函数中的失败检查（第 142-144 行），在 `if task.get("status") == "failed":` 之后添加 cancelled 检查：

```python
if task.get("status") == "cancelled":
    raise TaskCancelledError(f"task '{enqueue_result['task_id']}' was cancelled")
```

4. 更新 `_task_result_from_finished` 函数（第 265-279 行），在 failed 分支后添加 cancelled 处理：

```python
def _task_result_from_finished(task: dict[str, Any], resource_id: str, task_id: str) -> BatchTaskResult:
    """Build a BatchTaskResult from a finished task dict."""
    if task.get("status") == "failed":
        return BatchTaskResult(
            resource_id=resource_id,
            task_id=task_id,
            status="failed",
            error=task.get("error_message") or "task failed",
        )
    if task.get("status") == "cancelled":
        return BatchTaskResult(
            resource_id=resource_id,
            task_id=task_id,
            status="cancelled",
            error="task cancelled",
        )
    return BatchTaskResult(
        resource_id=resource_id,
        task_id=task_id,
        status="succeeded",
        result=task.get("result") or {},
    )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_generation_queue_client.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add lib/generation_queue_client.py tests/test_generation_queue_client.py
git commit -m "feat: 新增 TaskCancelledError，wait_for_task 支持 cancelled 状态"
```

---

### Task 5: Worker 处理被取消任务

**Files:**
- Modify: `lib/generation_worker.py` (`_process_task` 方法)

- [ ] **Step 1: 修改 _process_task 检测已取消任务**

在 `lib/generation_worker.py` 的 `_process_task` 方法中，在调用 `execute_generation_task(task)` 之前，添加状态检查；在调用 `mark_task_succeeded` 之前，也添加状态检查。找到 `_process_task` 方法，在 `execute_generation_task` 调用返回后、`mark_task_succeeded` 之前添加：

```python
# 执行完成后检查任务是否已被取消（竞态保护已在 cancel_task 中通过 WHERE status='queued' 处理，
# running 任务不会被取消，此处无需额外检查）
```

实际上，因为设计约束是 `running` 任务不可取消，Worker 的 `_process_task` 无需修改——`cancel_task` 的 `WHERE status = 'queued'` 已经保证了不会取消 running 任务。此步骤标记为无需修改。

- [ ] **Step 2: 运行现有 worker 测试确认不影响**

Run: `uv run python -m pytest tests/ -k "worker" -v`
Expected: ALL PASS（如果有 worker 相关测试）

- [ ] **Step 3: Commit（如有修改）**

如果步骤 1 确认无需修改，跳过此 commit。

---

### Task 6: API 路由端点

**Files:**
- Modify: `server/routers/tasks.py:1-165`
- Create: `tests/test_task_cancel_router.py`

- [ ] **Step 1: 写 API 端点测试**

创建 `tests/test_task_cancel_router.py`：

```python
"""Tests for task cancel API endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from lib.db.base import Base
from lib.generation_queue import GenerationQueue
from server.routers.tasks import get_task_queue

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def queue(db_session_factory):
    return GenerationQueue(session_factory=db_session_factory)

@pytest.fixture
def app(queue):
    from fastapi import FastAPI
    from server.routers import tasks as tasks_router
    from server.auth import get_current_user

    app = FastAPI()
    app.include_router(tasks_router.router, prefix="/api/v1")

    # Override auth and queue dependencies
    app.dependency_overrides[get_current_user] = lambda: type("U", (), {"id": "default"})()
    tasks_router.get_task_queue = lambda: queue  # type: ignore

    return app

@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

class TestCancelPreview:
    async def test_cancel_preview_queued_task(self, client, queue):
        result = await queue.enqueue_task(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S01", payload={}, script_file="ep1.json",
        )

        resp = await client.get(f"/api/v1/tasks/{result['task_id']}/cancel-preview")
        assert resp.status_code == 200
        data = resp.json()
        assert data["task"]["task_id"] == result["task_id"]
        assert isinstance(data["cascaded"], list)

    async def test_cancel_preview_running_task_400(self, client, queue):
        result = await queue.enqueue_task(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S01", payload={}, script_file="ep1.json",
        )
        await queue.claim_next_task("image")

        resp = await client.get(f"/api/v1/tasks/{result['task_id']}/cancel-preview")
        assert resp.status_code == 400

class TestCancelTask:
    async def test_cancel_queued_task(self, client, queue):
        result = await queue.enqueue_task(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S01", payload={}, script_file="ep1.json",
        )

        resp = await client.post(f"/api/v1/tasks/{result['task_id']}/cancel")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["cancelled"]) == 1

    async def test_cancel_nonexistent_task_400(self, client):
        resp = await client.post("/api/v1/tasks/nonexistent/cancel")
        assert resp.status_code == 400

class TestCancelAllQueued:
    async def test_cancel_all_preview(self, client, queue):
        await queue.enqueue_task(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S01", payload={}, script_file="ep1.json",
        )
        await queue.enqueue_task(
            project_name="demo", task_type="video", media_type="video",
            resource_id="E1S02", payload={}, script_file="ep1.json",
        )

        resp = await client.get("/api/v1/projects/demo/tasks/cancel-all-preview")
        assert resp.status_code == 200
        assert resp.json()["queued_count"] == 2

    async def test_cancel_all(self, client, queue):
        await queue.enqueue_task(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S01", payload={}, script_file="ep1.json",
        )

        resp = await client.post("/api/v1/projects/demo/tasks/cancel-all")
        assert resp.status_code == 200
        assert resp.json()["cancelled_count"] == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_task_cancel_router.py -v`
Expected: FAIL — 端点不存在

- [ ] **Step 3: 实现 API 端点**

在 `server/routers/tasks.py` 中，在 `get_task` 端点之前添加：

```python
@router.get("/tasks/{task_id}/cancel-preview")
async def cancel_preview(task_id: str, _user: CurrentUser):
    queue = get_task_queue()
    try:
        preview = await queue.get_cancel_preview(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return preview


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(task_id: str, _user: CurrentUser):
    queue = get_task_queue()
    try:
        result = await queue.cancel_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return result


@router.get("/projects/{project_name}/tasks/cancel-all-preview")
async def cancel_all_preview(project_name: str, _user: CurrentUser):
    queue = get_task_queue()
    queued_count = await queue.get_cancel_all_preview(project_name)
    return {"queued_count": queued_count}


@router.post("/projects/{project_name}/tasks/cancel-all")
async def cancel_all_queued(project_name: str, _user: CurrentUser):
    queue = get_task_queue()
    result = await queue.cancel_all_queued(project_name)
    return result
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_task_cancel_router.py -v`
Expected: ALL PASS

- [ ] **Step 5: 运行全量后端测试**

Run: `uv run python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add server/routers/tasks.py tests/test_task_cancel_router.py
git commit -m "feat: 新增任务取消 API 端点（preview + cancel + cancel-all）"
```

---

### Task 7: 前端类型与 API 客户端

**Files:**
- Modify: `frontend/src/types/task.ts`
- Modify: `frontend/src/api.ts`
- Modify: `frontend/src/stores/tasks-store.ts`

- [ ] **Step 1: 更新 TaskStatus 和 TaskItem 类型**

在 `frontend/src/types/task.ts` 中：

1. 更新 TaskStatus（第 9 行）：

```typescript
export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled";
```

2. TaskItem 新增 cancelled_by 字段（在 `error_message` 之后）：

```typescript
cancelled_by: "user" | "cascade" | null;
```

3. TaskStats 新增 cancelled 计数（在 `failed` 之后）：

```typescript
export interface TaskStats {
  queued: number;
  running: number;
  succeeded: number;
  failed: number;
  cancelled: number;
  total: number;
}
```

- [ ] **Step 2: 更新 tasks-store 默认值**

在 `frontend/src/stores/tasks-store.ts` 中更新 defaultStats（第 16-18 行）：

```typescript
const defaultStats: TaskStats = {
  queued: 0, running: 0, succeeded: 0, failed: 0, cancelled: 0, total: 0,
};
```

- [ ] **Step 3: 新增 API 方法**

在 `frontend/src/api.ts` 中，在 `getTaskStats` 方法之后添加：

```typescript
  // ==================== 任务取消 API ====================

  static async cancelPreview(
    taskId: string
  ): Promise<{ task: { task_id: string; task_type: string; resource_id: string }; cascaded: { task_id: string; task_type: string; resource_id: string }[] }> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/cancel-preview`);
  }

  static async cancelTask(
    taskId: string
  ): Promise<{ cancelled: TaskItem[]; skipped_running: TaskItem[] }> {
    return this.request(`/tasks/${encodeURIComponent(taskId)}/cancel`, {
      method: "POST",
    });
  }

  static async cancelAllPreview(
    projectName: string
  ): Promise<{ queued_count: number }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/tasks/cancel-all-preview`
    );
  }

  static async cancelAllQueued(
    projectName: string
  ): Promise<{ cancelled_count: number; skipped_running_count: number }> {
    return this.request(
      `/projects/${encodeURIComponent(projectName)}/tasks/cancel-all`,
      { method: "POST" }
    );
  }
```

- [ ] **Step 4: 运行前端类型检查**

Run: `cd frontend && pnpm build`
Expected: 编译通过（可能有其他组件引用 TaskStats 需要适配）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types/task.ts frontend/src/api.ts frontend/src/stores/tasks-store.ts
git commit -m "feat: 前端类型新增 cancelled 状态，API 新增取消方法"
```

---

### Task 8: TaskHud 取消交互

**Files:**
- Modify: `frontend/src/components/task-hud/TaskHud.tsx`

- [ ] **Step 1: TaskStatusIcon 新增 cancelled 状态**

在 `TaskHud.tsx` 的 `TaskStatusIcon` 函数中（第 16-27 行），在 `case "failed"` 之后添加：

```typescript
case "cancelled":
  return <X className="h-3.5 w-3.5 text-gray-400" />;
```

- [ ] **Step 2: TaskRow 新增 cancelled 状态展示和取消按钮**

在 `TaskRow` 组件的 `statusLabel` 和 `statusColor` 中添加 cancelled：

```typescript
const statusLabel: Record<TaskItem["status"], string> = {
  running: "生成中...",
  queued: "排队中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

const statusColor: Record<TaskItem["status"], string> = {
  running: "text-indigo-400",
  queued: "text-gray-500",
  succeeded: "text-emerald-400",
  failed: "text-red-400",
  cancelled: "text-gray-400",
};
```

更新 `rowBg` 逻辑，在 failed 分支后添加 cancelled 的背景（使用和 succeeded 淡出后相同的无背景）：

```typescript
const rowBg =
  task.status === "failed"
    ? "bg-red-500/10"
    : task.status === "succeeded" && !isFading
      ? "bg-emerald-500/10"
      : "";
```

（cancelled 不需要特殊背景，保持空即可。）

在 TaskRow 的 `<span>` 状态文本后面、`{hasError && ...}` 之前，为 queued 任务添加取消按钮：

```tsx
{task.status === "queued" && onCancel && (
  <button
    onClick={(e) => {
      e.stopPropagation();
      onCancel(task.task_id);
    }}
    className="ml-1 rounded px-1 py-0.5 text-xs text-gray-500 hover:bg-gray-700 hover:text-gray-300"
    title="取消任务"
  >
    取消
  </button>
)}
```

TaskRow 的 props 需要添加 `onCancel`：

```typescript
function TaskRow({
  task,
  isFading,
  expandedErrorId,
  onToggleError,
  onCancel,
}: {
  task: TaskItem;
  isFading: boolean;
  expandedErrorId: string | null;
  onToggleError: (taskId: string) => void;
  onCancel?: (taskId: string) => void;
}) {
```

cancelled_by 为 cascade 的任务显示小标签：

```tsx
{task.status === "cancelled" && task.cancelled_by === "cascade" && (
  <span className="ml-1 text-xs text-gray-600">级联</span>
)}
```

- [ ] **Step 3: ChannelSection 处理 cancelled 任务**

在 `ChannelSection` 组件中，更新 recent 过滤逻辑（第 217-220 行）包含 cancelled：

```typescript
const recent = tasks
  .filter((t) => t.status === "succeeded" || t.status === "failed" || t.status === "cancelled")
  .filter((t) => !hiddenIds.has(t.task_id))
  .slice(0, 5);
```

cancelled 任务也应该在一段时间后自动淡出，更新 succeeded 淡出逻辑（第 181 行）包含 cancelled：

```typescript
const autoFadeTasks = tasks.filter(
  (t) =>
    (t.status === "succeeded" || t.status === "cancelled") &&
    !fadingIds.has(t.task_id) &&
    !hiddenIds.has(t.task_id),
);
```

在使用 `succeededTasks` 的地方替换为 `autoFadeTasks`。

- [ ] **Step 4: TaskHud 新增取消确认弹窗和批量取消按钮**

在 `TaskHud` 组件中添加取消逻辑。在组件内部添加状态和处理函数：

```tsx
import { useState } from "react";
import { API } from "@/api";

// 在 TaskHud 组件内部：
const [cancelConfirm, setCancelConfirm] = useState<{
  taskId?: string;
  preview?: { task: { task_id: string; task_type: string; resource_id: string }; cascaded: { task_id: string; task_type: string; resource_id: string }[] };
  allCount?: number;
  projectName?: string;
} | null>(null);

const handleCancelSingle = async (taskId: string) => {
  try {
    const preview = await API.cancelPreview(taskId);
    setCancelConfirm({ taskId, preview });
  } catch {
    // 任务已不是 queued 状态
  }
};

const handleCancelAll = async () => {
  // 从 tasks 中获取项目名（取第一个 queued 任务的 project_name）
  const queuedTask = tasks.find((t) => t.status === "queued");
  if (!queuedTask) return;
  const projectName = queuedTask.project_name;
  try {
    const { queued_count } = await API.cancelAllPreview(projectName);
    setCancelConfirm({ allCount: queued_count, projectName });
  } catch {
    // 无排队任务
  }
};

const confirmCancel = async () => {
  if (!cancelConfirm) return;
  try {
    if (cancelConfirm.taskId) {
      await API.cancelTask(cancelConfirm.taskId);
    } else if (cancelConfirm.projectName) {
      await API.cancelAllQueued(cancelConfirm.projectName);
    }
  } finally {
    setCancelConfirm(null);
  }
};
```

在统计栏区域添加"取消所有"按钮（仅当 stats.queued > 0 时显示）：

```tsx
{stats.queued > 0 && (
  <button
    onClick={handleCancelAll}
    className="ml-auto text-xs text-gray-500 hover:text-red-400"
  >
    全部取消
  </button>
)}
```

在统计栏中添加 cancelled 计数（如果有的话）：

```tsx
{stats.cancelled > 0 && (
  <span>
    取消{" "}
    <strong className="text-gray-400">{stats.cancelled}</strong>
  </span>
)}
```

在面板底部添加确认弹窗：

```tsx
{cancelConfirm && (
  <div className="border-t border-gray-800 px-3 py-2">
    <p className="text-xs text-gray-300">
      {cancelConfirm.preview
        ? cancelConfirm.preview.cascaded.length > 0
          ? `取消此任务将同时取消 ${cancelConfirm.preview.cascaded.length} 个依赖任务`
          : "确定取消此任务？"
        : `确定取消所有 ${cancelConfirm.allCount} 个排队中的任务？`}
    </p>
    {cancelConfirm.preview && cancelConfirm.preview.cascaded.length > 0 && (
      <ul className="mt-1 max-h-20 overflow-y-auto text-xs text-gray-500">
        {cancelConfirm.preview.cascaded.map((t) => (
          <li key={t.task_id}>
            {t.task_type} / {t.resource_id}
          </li>
        ))}
      </ul>
    )}
    <div className="mt-2 flex gap-2">
      <button
        onClick={confirmCancel}
        className="rounded bg-red-600/80 px-2 py-0.5 text-xs text-white hover:bg-red-600"
      >
        确认取消
      </button>
      <button
        onClick={() => setCancelConfirm(null)}
        className="rounded px-2 py-0.5 text-xs text-gray-400 hover:bg-gray-700"
      >
        取消
      </button>
    </div>
  </div>
)}
```

- [ ] **Step 5: 将 onCancel 传递给 TaskRow**

在 `ChannelSection` 中传递 `onCancel` prop 到 `TaskRow`：

ChannelSection 需要接收 `onCancel` prop：

```typescript
function ChannelSection({
  title,
  icon: Icon,
  tasks,
  onCancel,
}: {
  title: string;
  icon: React.ComponentType<{ className?: string }>;
  tasks: TaskItem[];
  onCancel?: (taskId: string) => void;
}) {
```

在 TaskRow 调用处传递：

```tsx
<TaskRow
  key={task.task_id}
  task={task}
  isFading={fadingIds.has(task.task_id)}
  expandedErrorId={expandedErrorId}
  onToggleError={toggleError}
  onCancel={onCancel}
/>
```

在 TaskHud 中使用 ChannelSection 时传递 `onCancel={handleCancelSingle}`：

```tsx
<ChannelSection title="图片通道" icon={Image} tasks={imageTasks} onCancel={handleCancelSingle} />
<ChannelSection title="视频通道" icon={Video} tasks={videoTasks} onCancel={handleCancelSingle} />
```

- [ ] **Step 6: 运行前端构建验证**

Run: `cd frontend && pnpm build`
Expected: 编译通过

- [ ] **Step 7: Commit**

```bash
git add frontend/src/components/task-hud/TaskHud.tsx
git commit -m "feat: TaskHud 新增取消按钮、确认弹窗、cancelled 状态展示"
```

---

### Task 9: Lint、格式化和全量验证

**Files:** 全量

- [ ] **Step 1: 后端 lint + format**

Run: `uv run ruff check . && uv run ruff format .`
Expected: 无错误或仅格式修复

- [ ] **Step 2: 后端全量测试**

Run: `uv run python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 3: 前端构建（含类型检查）**

Run: `cd frontend && pnpm build`
Expected: 编译通过

- [ ] **Step 4: 修复任何问题并 Commit**

```bash
git add -A
git commit -m "chore: lint 和格式修复"
```
