# Alembic Best Practices Fix Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Alembic configuration defects, unify 11 String timestamp columns to DateTime(timezone=True), add foreign key constraints, and optimize the PostgreSQL connection pool.

**Architecture:** A single migration script handles all schema changes (column types, foreign keys, server_default); ORM models and repository layer are adapted in sync; `*_to_dict()` 显式 `.isoformat()` 确保 JSON 序列化安全。

**Tech Stack:** SQLAlchemy 2.0 async, Alembic (batch mode), aiosqlite, asyncpg, Pydantic

**Spec:** `docs/superpowers/specs/2026-03-17-alembic-best-practices-design.md`

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `alembic/env.py` | Alembic environment configuration | Modify |
| `alembic.ini` | Alembic INI configuration | Modify |
| `lib/db/engine.py` | Database engine factory | Modify |
| `alembic/versions/*_unify_timestamps_and_add_fk.py` | Migration script | Create |
| `lib/db/models/task.py` | Task/TaskEvent/WorkerLease models | Modify |
| `lib/db/models/api_call.py` | ApiCall model | Modify |
| `lib/db/models/session.py` | AgentSession model | Modify |
| `lib/db/repositories/task_repo.py` | Task repo | Modify |
| `lib/db/repositories/usage_repo.py` | Usage repo | Modify |
| `lib/db/repositories/session_repo.py` | Session repo | Modify |
| `server/agent_runtime/models.py` | SessionMeta Pydantic model | Modify |
| `server/auth.py` | API Key 过期检查 | Modify |
| `tests/factories.py` | 测试工厂 | Modify |
| `tests/conftest.py` | 共享 fixture | No change (创建 in-memory DB via `Base.metadata.create_all`，自动适配新 schema) |
| `tests/fakes.py` | 共享 fake 对象 | No change (无时间戳引用) |

---

## Chunk 1: Alembic 基础设施

### Task 1: alembic/env.py — 添加 render_as_batch=True

**Files:**
- Modify: `alembic/env.py:32-49`

- [ ] **Step 1: 修改 `run_migrations_offline()`**

在 `context.configure()` 中添加 `render_as_batch=True`：

```python
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DB connection required)."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()
```

- [ ] **Step 2: 修改 `do_run_migrations()`**

```python
def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()
```

- [ ] **Step 3: 运行测试验证无回归**

Run: `python -m pytest tests/test_db_engine.py tests/test_db_models.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add alembic/env.py
git commit -m "fix(alembic): add render_as_batch=True for SQLite compatibility"
```

### Task 2: alembic.ini — 启用 ruff post_write_hook

**Files:**
- Modify: `alembic.ini:93-114`

- [ ] **Step 1: 取消注释 ruff hook 配置**

将第 93-114 行的注释块替换为：

```ini
[post_write_hooks]
# Lint & fix newly generated migration files with ruff
hooks = ruff
ruff.type = exec
ruff.executable = ruff
ruff.options = check --fix REVISION_SCRIPT_FILENAME
```

- [ ] **Step 2: Commit**

```bash
git add alembic.ini
git commit -m "chore(alembic): enable ruff post_write_hook for migration files"
```

### Task 3: lib/db/engine.py — PostgreSQL 连接池参数

**Files:**
- Modify: `lib/db/engine.py:43-56`

- [ ] **Step 1: 添加连接池参数**

在 `_create_engine()` 中，`create_async_engine()` 调用之前构建 kwargs：

```python
def _create_engine():
    url = get_database_url()
    _is_sqlite = url.startswith("sqlite")

    connect_args = {}
    kwargs = {}
    if _is_sqlite:
        connect_args["timeout"] = 30
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_recycle=3600)

    engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
        **kwargs,
    )

    if _is_sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine
```

- [ ] **Step 2: 运行测试验证**

Run: `python -m pytest tests/test_db_engine.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add lib/db/engine.py
git commit -m "perf(db): add PostgreSQL connection pool params"
```

---

## Chunk 2: ORM 模型 + Pydantic 模型改动

### Task 4: lib/db/models/task.py — String → DateTime

**Files:**
- Modify: `lib/db/models/task.py`

- [ ] **Step 1: 更新导入和 Task 模型**

替换导入行和All timestamp fields:

```python
"""Task queue ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base


class Task(Base):
    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    task_type: Mapped[str] = mapped_column(String, nullable=False)
    media_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    script_file: Mapped[Optional[str]] = mapped_column(String)
    payload_json: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False)
    result_json: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String, nullable=False, server_default="webui")
    dependency_task_id: Mapped[Optional[str]] = mapped_column(String)
    dependency_group: Mapped[Optional[str]] = mapped_column(String)
    dependency_index: Mapped[Optional[int]] = mapped_column(Integer)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_tasks_status_queued_at", "status", "queued_at"),
        Index("idx_tasks_project_updated_at", "project_name", "updated_at"),
        Index("idx_tasks_dependency_task_id", "dependency_task_id"),
        Index(
            "idx_tasks_dedupe_active",
            "project_name",
            "task_type",
            "resource_id",
            text("COALESCE(script_file, '')"),
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
```

- [ ] **Step 2: 更新 TaskEvent 模型（+ 外键）**

```python
class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False
    )
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    data_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_task_events_project_id", "project_name", "id"),
    )
```

- [ ] **Step 3: 更新 WorkerLease 模型**

```python
class WorkerLease(Base):
    __tablename__ = "worker_lease"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    lease_until: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

### Task 5: lib/db/models/api_call.py — String → DateTime + server_default

**Files:**
- Modify: `lib/db/models/api_call.py`

- [ ] **Step 1: 更新导入和模型**

```python
"""API call usage tracking ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base


class ApiCall(Base):
    __tablename__ = "api_calls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    call_type: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[Optional[str]] = mapped_column(Text)
    resolution: Mapped[Optional[str]] = mapped_column(String)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    aspect_ratio: Mapped[Optional[str]] = mapped_column(String)
    generate_audio: Mapped[Optional[bool]] = mapped_column(Boolean, server_default=sa.true_())
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    output_path: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, server_default="0.0")
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_api_calls_project_name", "project_name"),
        Index("idx_api_calls_call_type", "call_type"),
        Index("idx_api_calls_status", "status"),
        Index("idx_api_calls_started_at", "started_at"),
    )
```

### Task 6: lib/db/models/session.py — String → DateTime

**Files:**
- Modify: `lib/db/models/session.py`

- [ ] **Step 1: 更新导入和模型**

```python
"""Agent session ORM model."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    sdk_session_id: Mapped[Optional[str]] = mapped_column(String)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, server_default="")
    status: Mapped[str] = mapped_column(String, server_default="idle")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index("idx_agent_sessions_project", "project_name", "updated_at"),
        Index("idx_agent_sessions_status", "status"),
    )
```

### Task 7: server/agent_runtime/models.py — SessionMeta 时间戳类型

**Files:**
- Modify: `server/agent_runtime/models.py:1-18`

- [ ] **Step 1: 更新 SessionMeta**

```python
"""Agent runtime data models."""

from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

SessionStatus = Literal["idle", "running", "completed", "error", "interrupted"]


class SessionMeta(BaseModel):
    """Session metadata stored in database."""
    id: str
    sdk_session_id: Optional[str] = None
    project_name: str
    title: str = ""
    status: SessionStatus = "idle"
    created_at: datetime
    updated_at: datetime
```

- [ ] **Step 2: 运行模型测试**

Run: `python -m pytest tests/test_db_models.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add lib/db/models/task.py lib/db/models/api_call.py lib/db/models/session.py server/agent_runtime/models.py
git commit -m "refactor(models): unify timestamp columns from String to DateTime(timezone=True)"
```

---

## Chunk 3: Repository 层改动

### Task 8: lib/db/repositories/task_repo.py

**Files:**
- Modify: `lib/db/repositories/task_repo.py`

- [ ] **Step 1: 更新 `_utc_now_iso()` → `_utc_now()` 和 `_task_to_dict()` / `_event_to_dict()`**

替换文件头部的 helper 函数（第 1-72 行）：

```python
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


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Optional[str], default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _dt_to_iso(val: Optional[datetime]) -> Optional[str]:
    """Convert datetime to ISO string for JSON serialization."""
    return val.isoformat() if val else None


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
        "queued_at": _dt_to_iso(row.queued_at),
        "started_at": _dt_to_iso(row.started_at),
        "finished_at": _dt_to_iso(row.finished_at),
        "updated_at": _dt_to_iso(row.updated_at),
    }


def _event_to_dict(row: TaskEvent) -> dict[str, Any]:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "project_name": row.project_name,
        "event_type": row.event_type,
        "status": row.status,
        "data": _json_loads(row.data_json, {}),
        "created_at": _dt_to_iso(row.created_at),
    }
```

- [ ] **Step 2: 更新 TaskRepository 中所有 `_utc_now_iso()` → `_utc_now()`**

全文替换：所有 `_utc_now_iso()` 调用改为 `_utc_now()`。

涉及的方法和行号（原始行号）：
- `_append_event` (L88): `now = _utc_now()`
- `enqueue` (L115): `now = _utc_now()`
- `claim_next` (L178): `now = _utc_now()`
- `mark_succeeded` (L239): `now = _utc_now()`
- `_mark_failed_internal` (L309): `now = _utc_now()`
- `requeue_running` (L370): `now = _utc_now()`
- `requeue_running` events (L406): `event_now = _utc_now()`
- `acquire_or_renew_lease` (L549): `updated_at = _utc_now()`

- [ ] **Step 2b: 更新 `get_worker_lease()` 中的 `updated_at` 序列化**

`get_worker_lease()` (L603-618) 返回的 dict 中 `"updated_at": row.updated_at` 需要改为：

```python
        return {
            "name": row.name,
            "owner_id": row.owner_id,
            "lease_until": row.lease_until,
            "updated_at": _dt_to_iso(row.updated_at),
            "is_online": row.lease_until > time.time(),
        }
```

- [ ] **Step 3: 运行 task_repo 测试**

Run: `python -m pytest tests/test_task_repo.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add lib/db/repositories/task_repo.py
git commit -m "refactor(task_repo): use datetime objects instead of ISO strings"
```

### Task 9: lib/db/repositories/usage_repo.py

**Files:**
- Modify: `lib/db/repositories/usage_repo.py`

- [ ] **Step 1: 替换 helper 函数和 `_row_to_dict()`**

删除 `_iso_millis()` 和 `_utc_now_iso()`，替换为：

```python
"""Async repository for API call usage tracking."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.cost_calculator import cost_calculator
from lib.db.models.api_call import ApiCall


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_iso(val: Optional[datetime]) -> Optional[str]:
    """Convert datetime to ISO string for JSON serialization."""
    return val.isoformat() if val else None


def _row_to_dict(row: ApiCall) -> dict[str, Any]:
    return {
        "id": row.id,
        "project_name": row.project_name,
        "call_type": row.call_type,
        "model": row.model,
        "prompt": row.prompt,
        "resolution": row.resolution,
        "duration_seconds": row.duration_seconds,
        "aspect_ratio": row.aspect_ratio,
        "generate_audio": row.generate_audio,
        "status": row.status,
        "error_message": row.error_message,
        "output_path": row.output_path,
        "started_at": _dt_to_iso(row.started_at),
        "finished_at": _dt_to_iso(row.finished_at),
        "duration_ms": row.duration_ms,
        "retry_count": row.retry_count,
        "cost_usd": row.cost_usd,
        "created_at": _dt_to_iso(row.created_at),
    }
```

- [ ] **Step 2: 更新 `start_call` 和 `finish_call`**

`start_call` (L65): `now = _utc_now()`

`finish_call` — 删除 fromisoformat 解析，直接用 datetime 减法：

```python
    async def finish_call(
        self,
        call_id: int,
        *,
        status: str,
        output_path: Optional[str] = None,
        error_message: Optional[str] = None,
        retry_count: int = 0,
    ) -> None:
        finished_at = _utc_now()

        result = await self.session.execute(
            select(ApiCall).where(ApiCall.id == call_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return

        # Calculate duration — both are now datetime objects
        try:
            duration_ms = int((finished_at - row.started_at).total_seconds() * 1000)
        except (ValueError, TypeError):
            duration_ms = 0

        # Calculate cost (failed = 0)
        cost_usd = 0.0
        if status == "success":
            if row.call_type == "image":
                cost_usd = cost_calculator.calculate_image_cost(
                    row.resolution or "1K", model=row.model
                )
            elif row.call_type == "video":
                cost_usd = cost_calculator.calculate_video_cost(
                    duration_seconds=row.duration_seconds or 8,
                    resolution=row.resolution or "1080p",
                    generate_audio=bool(row.generate_audio),
                    model=row.model,
                )

        error_truncated = error_message[:500] if error_message else None

        await self.session.execute(
            update(ApiCall)
            .where(ApiCall.id == call_id)
            .values(
                status=status,
                finished_at=finished_at,
                duration_ms=duration_ms,
                retry_count=retry_count,
                cost_usd=cost_usd,
                output_path=output_path,
                error_message=error_truncated,
            )
        )
        await self.session.commit()
```

- [ ] **Step 3: 更新 `get_stats` 和 `get_calls` 的过滤条件**

`get_stats` 中的 `_base_filters()`：

```python
        def _base_filters():
            filters = []
            if project_name:
                filters.append(ApiCall.project_name == project_name)
            if start_date:
                start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
                filters.append(ApiCall.started_at >= start)
            if end_date:
                end_exclusive = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
                filters.append(ApiCall.started_at < end_exclusive)
            return filters
```

`get_calls` 中同理：

```python
        if start_date:
            start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)
            filters.append(ApiCall.started_at >= start)
        if end_date:
            end_exclusive = datetime(end_date.year, end_date.month, end_date.day, tzinfo=timezone.utc) + timedelta(days=1)
            filters.append(ApiCall.started_at < end_exclusive)
```

- [ ] **Step 4: 运行 usage_repo 测试**

Run: `python -m pytest tests/test_usage_repo.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add lib/db/repositories/usage_repo.py
git commit -m "refactor(usage_repo): use datetime objects instead of ISO strings"
```

### Task 10: lib/db/repositories/session_repo.py

**Files:**
- Modify: `lib/db/repositories/session_repo.py`

- [ ] **Step 1: 替换 helper 和 `_row_to_dict()`**

```python
"""Async repository for agent sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete as sa_delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.session import AgentSession


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _dt_to_iso(val: Optional[datetime]) -> Optional[str]:
    """Convert datetime to ISO string for JSON serialization."""
    return val.isoformat() if val else None


def _row_to_dict(row: AgentSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "sdk_session_id": row.sdk_session_id,
        "project_name": row.project_name,
        "title": row.title or "",
        "status": row.status,
        "created_at": _dt_to_iso(row.created_at),
        "updated_at": _dt_to_iso(row.updated_at),
    }
```

- [ ] **Step 2: 更新所有 `_utc_now_iso()` → `_utc_now()`**

全文替换：`_utc_now_iso()` → `_utc_now()`。涉及：
- `create` (L36)
- `update_status` (L77)
- `update_sdk_session_id` (L87)
- `update_title` (L97)
- `interrupt_running` (L114)

- [ ] **Step 3: 运行 session_repo 测试**

Run: `python -m pytest tests/test_session_repo.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add lib/db/repositories/session_repo.py
git commit -m "refactor(session_repo): use datetime objects instead of ISO strings"
```

---

## Chunk 4: 周边代码适配 + 测试 fixture

### Task 11: server/auth.py — 简化过期检查

**Files:**
- Modify: `server/auth.py:298-318`

- [ ] **Step 1: 简化 `_verify_api_key` 中的过期检查**

`ApiKey.expires_at` 已经是 DateTime 类型（ApiKey 模型本来就用 DateTime）。当前代码有 `isinstance(expires_at, str)` 分支兼容旧式 ISO 字符串。由于 ApiKey 模型不在此次改动范围内（它本来就是 DateTime），这段代码实际上已经能正确处理。不过可以安全地移除 `isinstance(expires_at, str)` 分支，因为从数据库读出的 `expires_at` 始终是 datetime 对象。

将 `_verify_api_key` 中第 298-318 行替换为（保留 try/except 防御性处理）：

```python
    # 检查过期
    expires_at = row.get("expires_at")
    expires_at_monotonic: Optional[float] = None
    if expires_at:
        from datetime import datetime, timezone
        try:
            exp_dt = expires_at
            if exp_dt.tzinfo is None:
                exp_dt = exp_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= exp_dt:
                _set_api_key_cache(key_hash, None)
                return None
            # 将过期时刻转换为 monotonic 时间戳，供缓存 TTL 上界计算
            remaining_secs = (exp_dt - datetime.now(timezone.utc)).total_seconds()
            expires_at_monotonic = time.monotonic() + remaining_secs
        except (ValueError, TypeError):
            logger.warning("API Key expires_at 值格式无法解析，忽略过期检查: %r", expires_at)
```

- [ ] **Step 2: 运行 auth 测试**

Run: `python -m pytest tests/test_auth.py tests/test_auth_api_key.py -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add server/auth.py
git commit -m "refactor(auth): remove string parsing path for ApiKey expires_at"
```

### Task 12: tests/factories.py — 适配 datetime

**Files:**
- Modify: `tests/factories.py:1-23`

- [ ] **Step 1: 更新 `make_session_meta`**

```python
"""Test data factories — reduce boilerplate when constructing common objects."""

from __future__ import annotations

from datetime import datetime, timezone

from server.agent_runtime.models import SessionMeta


def make_session_meta(**overrides) -> SessionMeta:
    """Build a SessionMeta with sensible defaults.

    Any keyword argument overrides the corresponding default field.
    """
    defaults = dict(
        id="session-1",
        sdk_session_id="sdk-1",
        project_name="demo",
        title="demo",
        status="running",
        created_at=datetime(2026, 2, 9, 8, 0, 0, tzinfo=timezone.utc),
        updated_at=datetime(2026, 2, 9, 8, 0, 0, tzinfo=timezone.utc),
    )
    defaults.update(overrides)
    return SessionMeta(**defaults)
```

- [ ] **Step 2: 运行使用 factories 的测试**

Run: `python -m pytest tests/ -k "session_meta or factories" -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add tests/factories.py
git commit -m "test: adapt factories to use datetime objects for SessionMeta"
```

---

## Chunk 5: Alembic 迁移脚本

### Task 13: 创建迁移脚本

**Files:**
- Create: `alembic/versions/*_unify_timestamps_and_add_fk.py`

- [ ] **Step 1: 生成迁移文件**

Run: `uv run alembic revision --autogenerate -m "unify_timestamps_and_add_fk"`

这会自动检测 ORM 模型与数据库 schema 的差异并生成迁移骨架。

- [ ] **Step 2: 手动调整生成的迁移文件**

Alembic autogenerate 不能自动处理数据转换和 PostgreSQL USING 子句。需要手动编辑生成的迁移文件，确保：

**upgrade() 函数：**

1. 清理孤立 task_events（在 FK 添加前）：
```python
    # Clean up orphaned task_events before adding FK constraint
    op.execute(sa.text(
        "DELETE FROM task_events WHERE task_id NOT IN (SELECT task_id FROM tasks)"
    ))
```

2. 对每个表的列类型变更，autogenerate 应已生成 `batch_alter_table` 操作（因为 render_as_batch=True）。检查所有 11 列都包含在内。

3. 对 `task_events.task_id` 添加外键：autogenerate 应已生成。

4. 对 `api_calls.generate_audio` 的 `server_default` 变更：autogenerate 可能检测不到，如果没有则手动添加：
```python
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default=sa.true_())
```

5. 对于 PostgreSQL，由于 batch mode 不适用（PostgreSQL 用原生 ALTER），需要添加 dialect-specific 逻辑或依赖 batch_alter_table 的自动跳过。实际上 `render_as_batch=True` 在 PostgreSQL 上不会走重建表路径，而是生成原生 ALTER。但数据转换需要 USING 子句。如果 autogenerate 没有生成 USING 子句，需要手动添加条件判断：

```python
    from alembic import context

    # 在 upgrade() 开头检测方言
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"
```

然后在每个列类型变更处添加 PG-specific USING：

```python
    if is_pg:
        # PostgreSQL: use native ALTER with USING for data conversion
        for table, columns in TIMESTAMP_COLUMNS.items():
            for col in columns:
                op.execute(sa.text(
                    f'ALTER TABLE {table} ALTER COLUMN {col} '
                    f'TYPE TIMESTAMP WITH TIME ZONE '
                    f"USING CASE WHEN {col} IS NOT NULL AND {col} != '' "
                    f'THEN {col}::timestamptz END'
                ))
    else:
        # SQLite: batch mode handles table rebuild
        # (autogenerated batch_alter_table operations below)
        ...
```

**downgrade() 函数：**

```python
def downgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # Remove FK from task_events
    with op.batch_alter_table("task_events") as batch_op:
        batch_op.drop_constraint("fk_task_events_task_id", type_="foreignkey")

    # Revert server_default
    with op.batch_alter_table("api_calls") as batch_op:
        batch_op.alter_column("generate_audio", server_default="1")

    # Revert DateTime → String for all timestamp columns
    if is_pg:
        for table, columns in TIMESTAMP_COLUMNS.items():
            for col in columns:
                op.execute(sa.text(
                    f"ALTER TABLE {table} ALTER COLUMN {col} "
                    f"TYPE VARCHAR "
                    f"USING to_char({col} AT TIME ZONE 'UTC', "
                    f"'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"')"
                ))
    else:
        # SQLite: batch mode rebuild
        for table, columns in TIMESTAMP_COLUMNS.items():
            with op.batch_alter_table(table) as batch_op:
                for col in columns:
                    nullable = (table, col) in NULLABLE_COLUMNS
                    batch_op.alter_column(
                        col,
                        type_=sa.String(),
                        existing_type=sa.DateTime(timezone=True),
                        nullable=nullable,
                    )
```

其中常量定义：

```python
TIMESTAMP_COLUMNS = {
    "tasks": ["queued_at", "started_at", "finished_at", "updated_at"],
    "task_events": ["created_at"],
    "worker_lease": ["updated_at"],
    "api_calls": ["started_at", "finished_at", "created_at"],
    "agent_sessions": ["created_at", "updated_at"],
}

# Per-table nullable mapping — avoids incorrectly marking task_events.created_at as nullable
NULLABLE_COLUMNS = {
    ("tasks", "started_at"),
    ("tasks", "finished_at"),
    ("api_calls", "finished_at"),
    ("api_calls", "created_at"),
}
```

- [ ] **Step 3: 验证迁移——升级**

Run: `uv run alembic upgrade head`
Expected: 成功，无报错

- [ ] **Step 4: 验证迁移——降级**

Run: `uv run alembic downgrade -1`
Expected: 成功，无报错

- [ ] **Step 5: 验证迁移——重新升级**

Run: `uv run alembic upgrade head`
Expected: 成功，无报错

- [ ] **Step 6: Commit**

```bash
git add alembic/versions/
git commit -m "feat(db): add migration to unify timestamps and add FK constraint"
```

---

## Chunk 6: 全量验证

### Task 14: 运行全部测试

**Files:** (none — verification only)

- [ ] **Step 1: 运行完整测试套件**

Run: `python -m pytest -v`
Expected: all PASS

- [ ] **Step 2: 排查失败（如有）**

如果有测试失败，检查是否因为：
1. 测试中构造了 ISO 字符串类型的时间戳传给了期望 datetime 的字段
2. 测试断言检查了时间戳的具体字符串格式（如 `Z` 结尾）
3. `_row_to_dict` / `_task_to_dict` 的返回值格式变化导致断言失败

根据具体报错修复。

- [ ] **Step 3: 验证前端时间戳解析兼容性**

检查前端代码：确认所有时间戳解析使用 `new Date(isoStr)` 模式（已在设计阶段确认——全部使用 `new Date()`，兼容 `+00:00` 后缀）。

Run: 无需代码改动，已确认兼容。

- [ ] **Step 4: 最终 Commit（如有额外修复）**

```bash
git add -A
git commit -m "fix: address test failures from timestamp unification"
```
