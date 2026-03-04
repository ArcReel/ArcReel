# SQLAlchemy Async ORM Migration — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace 3 independent SQLite databases with hand-written SQL with a unified SQLAlchemy Async ORM layer, supporting PostgreSQL as production backend.

**Architecture:** New `lib/db/` package with ORM models, async repositories, and engine configuration. Existing modules (`GenerationQueue`, `UsageTracker`, `SessionMetaStore`) are rewritten to use repositories. Alembic manages schema migrations. A one-time script migrates old SQLite data.

**Tech Stack:** SQLAlchemy 2.0+ (async), aiosqlite, asyncpg, Alembic

**Design Doc:** `docs/plans/2026-03-04-sqlalchemy-orm-migration-design.md`

---

### Task 1: Install Dependencies

**Files:**
- Modify: `pyproject.toml` (via `uv add`)

**Step 1: Add dependencies**

```bash
uv add "sqlalchemy[asyncio]" aiosqlite asyncpg alembic
```

**Step 2: Verify installation**

```bash
uv run python -c "import sqlalchemy; print(sqlalchemy.__version__); from sqlalchemy.ext.asyncio import create_async_engine; print('async OK')"
uv run python -c "import aiosqlite; print('aiosqlite OK')"
uv run python -c "import alembic; print('alembic OK')"
```

Expected: version numbers and "OK" messages.

**Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "build: add sqlalchemy, aiosqlite, asyncpg, alembic dependencies"
```

---

### Task 2: Create ORM Base and Engine Configuration

**Files:**
- Create: `lib/db/__init__.py`
- Create: `lib/db/base.py`
- Create: `lib/db/engine.py`
- Modify: `.env.example`

**Step 1: Write test for engine configuration**

Create `tests/test_db_engine.py`:

```python
"""Tests for lib.db.engine configuration."""

import os
import pytest
from unittest.mock import patch

from lib.db.engine import get_database_url, is_sqlite_backend


class TestGetDatabaseUrl:
    def test_default_returns_sqlite(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            url = get_database_url()
            assert url.startswith("sqlite+aiosqlite:///")
            assert ".arcreel.db" in url

    def test_env_override(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+asyncpg://localhost/test"}):
            url = get_database_url()
            assert url == "postgresql+asyncpg://localhost/test"


class TestIsSqliteBackend:
    def test_sqlite(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATABASE_URL", None)
            assert is_sqlite_backend() is True

    def test_postgresql(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+asyncpg://localhost/test"}):
            assert is_sqlite_backend() is False
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_db_engine.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'lib.db'`

**Step 3: Implement `lib/db/base.py`**

```python
"""SQLAlchemy declarative base."""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass
```

**Step 4: Implement `lib/db/engine.py`**

```python
"""Async engine and session factory configuration."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def get_database_url() -> str:
    """Resolve DATABASE_URL from environment or default to SQLite."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "projects" / ".arcreel.db"
    return f"sqlite+aiosqlite:///{db_path}"


def is_sqlite_backend() -> bool:
    """Check whether the configured backend is SQLite."""
    return get_database_url().startswith("sqlite")


def _create_engine():
    url = get_database_url()
    _is_sqlite = url.startswith("sqlite")

    connect_args = {}
    if _is_sqlite:
        connect_args["timeout"] = 30

    engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
    )

    if _is_sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.close()

    return engine


async_engine = _create_engine()

async_session_factory = async_sessionmaker(
    async_engine,
    expire_on_commit=False,
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends generator for per-request AsyncSession."""
    async with async_session_factory() as session:
        yield session
```

**Step 5: Implement `lib/db/__init__.py`**

```python
"""Database package — ORM models, engine, and session factory."""

from lib.db.engine import (
    async_engine,
    async_session_factory,
    get_async_session,
    get_database_url,
    is_sqlite_backend,
)
from lib.db.base import Base


async def init_db() -> None:
    """Create all tables (development convenience). Production uses Alembic."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose engine connections on shutdown."""
    await async_engine.dispose()


__all__ = [
    "Base",
    "async_engine",
    "async_session_factory",
    "close_db",
    "get_async_session",
    "get_database_url",
    "init_db",
    "is_sqlite_backend",
]
```

**Step 6: Update `.env.example`**

Add the following block (after existing GEMINI config):

```
# 数据库配置（默认使用 SQLite）
# SQLite（开发/单机）: sqlite+aiosqlite:///./projects/.arcreel.db
# PostgreSQL（生产）:  postgresql+asyncpg://user:pass@host:5432/arcreel
# DATABASE_URL=sqlite+aiosqlite:///./projects/.arcreel.db
```

**Step 7: Run tests**

```bash
python -m pytest tests/test_db_engine.py -v
```

Expected: PASS

**Step 8: Commit**

```bash
git add lib/db/ tests/test_db_engine.py .env.example
git commit -m "feat(db): add SQLAlchemy async engine configuration and base"
```

---

### Task 3: Create ORM Models

**Files:**
- Create: `lib/db/models/__init__.py`
- Create: `lib/db/models/task.py`
- Create: `lib/db/models/api_call.py`
- Create: `lib/db/models/session.py`

**Step 1: Write test for model table creation**

Create `tests/test_db_models.py`:

```python
"""Tests for ORM model definitions — verify tables can be created."""

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.db.models import Task, TaskEvent, WorkerLease, ApiCall, AgentSession


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestModelsCreateTables:
    async def test_all_tables_exist(self, engine):
        async with engine.connect() as conn:
            table_names = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_table_names()
            )
        assert "tasks" in table_names
        assert "task_events" in table_names
        assert "worker_lease" in table_names
        assert "api_calls" in table_names
        assert "agent_sessions" in table_names

    async def test_task_round_trip(self, session):
        task = Task(
            task_id="abc123",
            project_name="demo",
            task_type="storyboard",
            media_type="image",
            resource_id="E1S01",
            status="queued",
            queued_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        session.add(task)
        await session.commit()

        from sqlalchemy import select
        result = await session.execute(select(Task).where(Task.task_id == "abc123"))
        loaded = result.scalar_one()
        assert loaded.project_name == "demo"
        assert loaded.status == "queued"

    async def test_agent_session_round_trip(self, session):
        s = AgentSession(
            id="sess123",
            project_name="demo",
            status="idle",
            created_at="2026-01-01T00:00:00Z",
            updated_at="2026-01-01T00:00:00Z",
        )
        session.add(s)
        await session.commit()

        from sqlalchemy import select
        result = await session.execute(select(AgentSession).where(AgentSession.id == "sess123"))
        loaded = result.scalar_one()
        assert loaded.project_name == "demo"
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_db_models.py -v
```

Expected: FAIL — `ImportError: cannot import name 'Task' from 'lib.db.models'`

**Step 3: Implement `lib/db/models/task.py`**

```python
"""Task queue ORM models."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Float, Index, Integer, String, Text, text
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
    queued_at: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[Optional[str]] = mapped_column(String)
    finished_at: Mapped[Optional[str]] = mapped_column(String)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("idx_tasks_status_queued_at", "status", "queued_at"),
        Index("idx_tasks_project_updated_at", "project_name", "updated_at"),
        Index("idx_tasks_dependency_task_id", "dependency_task_id"),
    )


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    data_json: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("idx_task_events_project_id", "project_name", "id"),
    )


class WorkerLease(Base):
    __tablename__ = "worker_lease"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    lease_until: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
```

**Step 4: Implement `lib/db/models/api_call.py`**

```python
"""API call usage tracking ORM model."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
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
    generate_audio: Mapped[Optional[bool]] = mapped_column(Boolean, server_default="1")
    status: Mapped[str] = mapped_column(String, nullable=False, server_default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    output_path: Mapped[Optional[str]] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(String, nullable=False)
    finished_at: Mapped[Optional[str]] = mapped_column(String)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    retry_count: Mapped[int] = mapped_column(Integer, server_default="0")
    cost_usd: Mapped[float] = mapped_column(Float, server_default="0.0")
    created_at: Mapped[Optional[str]] = mapped_column(String)

    __table_args__ = (
        Index("idx_api_calls_project_name", "project_name"),
        Index("idx_api_calls_call_type", "call_type"),
        Index("idx_api_calls_status", "status"),
        Index("idx_api_calls_started_at", "started_at"),
    )
```

**Step 5: Implement `lib/db/models/session.py`**

```python
"""Agent session ORM model."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base


class AgentSession(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    sdk_session_id: Mapped[Optional[str]] = mapped_column(String)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, server_default="")
    status: Mapped[str] = mapped_column(String, server_default="idle")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("idx_agent_sessions_project", "project_name", "updated_at"),
        Index("idx_agent_sessions_status", "status"),
    )
```

**Step 6: Implement `lib/db/models/__init__.py`**

```python
"""ORM model exports."""

from lib.db.models.task import Task, TaskEvent, WorkerLease
from lib.db.models.api_call import ApiCall
from lib.db.models.session import AgentSession

__all__ = ["Task", "TaskEvent", "WorkerLease", "ApiCall", "AgentSession"]
```

**Step 7: Run tests**

```bash
python -m pytest tests/test_db_models.py -v
```

Expected: PASS

**Step 8: Commit**

```bash
git add lib/db/models/ tests/test_db_models.py
git commit -m "feat(db): add SQLAlchemy ORM models for tasks, usage, sessions"
```

---

### Task 4: Create Repositories — SessionRepository

Start with the simplest repository to establish the pattern.

**Files:**
- Create: `lib/db/repositories/__init__.py`
- Create: `lib/db/repositories/session_repo.py`
- Create: `tests/test_session_repo.py`

**Step 1: Write the test**

```python
"""Tests for SessionRepository."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.db.models import AgentSession
from lib.db.repositories.session_repo import SessionRepository


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestSessionRepository:
    async def test_create_and_get(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "Test Session")
        assert created["project_name"] == "demo"
        assert created["status"] == "idle"
        assert created["title"] == "Test Session"

        fetched = await repo.get(created["id"])
        assert fetched is not None
        assert fetched["id"] == created["id"]

    async def test_list_with_filters(self, db_session):
        repo = SessionRepository(db_session)
        await repo.create("project_a", "Session A1")
        await repo.create("project_a", "Session A2")
        await repo.create("project_b", "Session B1")

        results = await repo.list(project_name="project_a")
        assert len(results) == 2

        results = await repo.list(project_name="project_b")
        assert len(results) == 1

    async def test_update_status(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "Test")
        assert await repo.update_status(created["id"], "running")

        fetched = await repo.get(created["id"])
        assert fetched["status"] == "running"

    async def test_update_title(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "Original")
        assert await repo.update_title(created["id"], "Renamed")

        fetched = await repo.get(created["id"])
        assert fetched["title"] == "Renamed"

    async def test_update_sdk_session_id(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "Test")
        assert await repo.update_sdk_session_id(created["id"], "sdk-abc")

        fetched = await repo.get(created["id"])
        assert fetched["sdk_session_id"] == "sdk-abc"

    async def test_delete(self, db_session):
        repo = SessionRepository(db_session)
        created = await repo.create("demo", "Test")
        assert await repo.delete(created["id"])
        assert await repo.get(created["id"]) is None

    async def test_delete_nonexistent(self, db_session):
        repo = SessionRepository(db_session)
        assert not await repo.delete("nonexistent")

    async def test_interrupt_running(self, db_session):
        repo = SessionRepository(db_session)
        s1 = await repo.create("demo", "Running")
        s2 = await repo.create("demo", "Completed")
        s3 = await repo.create("demo", "Idle")

        await repo.update_status(s1["id"], "running")
        await repo.update_status(s2["id"], "completed")

        count = await repo.interrupt_running()
        assert count == 1

        assert (await repo.get(s1["id"]))["status"] == "interrupted"
        assert (await repo.get(s2["id"]))["status"] == "completed"
        assert (await repo.get(s3["id"]))["status"] == "idle"
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_session_repo.py -v
```

Expected: FAIL — `ModuleNotFoundError`

**Step 3: Implement `lib/db/repositories/session_repo.py`**

```python
"""Async repository for agent sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db.models.session import AgentSession


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_dict(row: AgentSession) -> dict[str, Any]:
    return {
        "id": row.id,
        "sdk_session_id": row.sdk_session_id,
        "project_name": row.project_name,
        "title": row.title or "",
        "status": row.status,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


class SessionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, project_name: str, title: str = "") -> dict[str, Any]:
        now = _utc_now_iso()
        row = AgentSession(
            id=uuid.uuid4().hex,
            project_name=project_name,
            title=title,
            status="idle",
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        await self.session.commit()
        await self.session.refresh(row)
        return _row_to_dict(row)

    async def get(self, session_id: str) -> Optional[dict[str, Any]]:
        result = await self.session.execute(
            select(AgentSession).where(AgentSession.id == session_id)
        )
        row = result.scalar_one_or_none()
        return _row_to_dict(row) if row else None

    async def list(
        self,
        *,
        project_name: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        stmt = select(AgentSession)
        if project_name:
            stmt = stmt.where(AgentSession.project_name == project_name)
        if status:
            stmt = stmt.where(AgentSession.status == status)
        stmt = stmt.order_by(AgentSession.updated_at.desc())
        stmt = stmt.limit(max(1, limit)).offset(max(0, offset))

        result = await self.session.execute(stmt)
        return [_row_to_dict(row) for row in result.scalars().all()]

    async def update_status(self, session_id: str, status: str) -> bool:
        now = _utc_now_iso()
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(status=status, updated_at=now)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def update_sdk_session_id(self, session_id: str, sdk_session_id: str) -> bool:
        now = _utc_now_iso()
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(sdk_session_id=sdk_session_id, updated_at=now)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def update_title(self, session_id: str, title: str) -> bool:
        now = _utc_now_iso()
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.id == session_id)
            .values(title=title.strip(), updated_at=now)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def delete(self, session_id: str) -> bool:
        from sqlalchemy import delete as sa_delete
        result = await self.session.execute(
            sa_delete(AgentSession).where(AgentSession.id == session_id)
        )
        await self.session.commit()
        return result.rowcount > 0

    async def interrupt_running(self) -> int:
        now = _utc_now_iso()
        result = await self.session.execute(
            update(AgentSession)
            .where(AgentSession.status == "running")
            .values(status="interrupted", updated_at=now)
        )
        await self.session.commit()
        return result.rowcount
```

**Step 4: Implement `lib/db/repositories/__init__.py`**

```python
"""Repository exports."""

from lib.db.repositories.session_repo import SessionRepository

__all__ = ["SessionRepository"]
```

**Step 5: Run tests**

```bash
python -m pytest tests/test_session_repo.py -v
```

Expected: PASS

**Step 6: Commit**

```bash
git add lib/db/repositories/ tests/test_session_repo.py
git commit -m "feat(db): add SessionRepository with full CRUD operations"
```

---

### Task 5: Create Repositories — UsageRepository

**Files:**
- Create: `lib/db/repositories/usage_repo.py`
- Create: `tests/test_usage_repo.py`
- Modify: `lib/db/repositories/__init__.py`

**Step 1: Write the test**

```python
"""Tests for UsageRepository."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.db.repositories.usage_repo import UsageRepository


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestUsageRepository:
    async def test_start_and_finish_call(self, db_session):
        repo = UsageRepository(db_session)
        call_id = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="gemini-3.1-flash-image-preview",
            prompt="test prompt",
            resolution="1K",
        )
        assert call_id > 0

        await repo.finish_call(
            call_id,
            status="success",
            output_path="storyboards/test.png",
            retry_count=0,
        )

        calls = await repo.get_calls(project_name="demo")
        assert calls["total"] == 1
        assert calls["items"][0]["status"] == "success"

    async def test_get_stats(self, db_session):
        repo = UsageRepository(db_session)
        call1 = await repo.start_call(
            project_name="demo",
            call_type="image",
            model="test-model",
        )
        await repo.finish_call(call1, status="success")

        call2 = await repo.start_call(
            project_name="demo",
            call_type="video",
            model="test-model",
            duration_seconds=8,
        )
        await repo.finish_call(call2, status="failed", error_message="timeout")

        stats = await repo.get_stats(project_name="demo")
        assert stats["image_count"] == 1
        assert stats["video_count"] == 1
        assert stats["failed_count"] == 1
        assert stats["total_count"] == 2

    async def test_get_projects_list(self, db_session):
        repo = UsageRepository(db_session)
        await repo.start_call(project_name="project_a", call_type="image", model="m")
        await repo.start_call(project_name="project_b", call_type="video", model="m")

        projects = await repo.get_projects_list()
        assert set(projects) == {"project_a", "project_b"}

    async def test_pagination(self, db_session):
        repo = UsageRepository(db_session)
        for i in range(5):
            await repo.start_call(project_name="demo", call_type="image", model="m")

        page1 = await repo.get_calls(page=1, page_size=2)
        assert len(page1["items"]) == 2
        assert page1["total"] == 5

        page2 = await repo.get_calls(page=2, page_size=2)
        assert len(page2["items"]) == 2
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_usage_repo.py -v
```

**Step 3: Implement `lib/db/repositories/usage_repo.py`**

This repository mirrors `UsageTracker` with all its methods. The cost calculation is delegated to `cost_calculator` as before.

Key methods: `start_call`, `finish_call`, `get_stats`, `get_calls`, `get_projects_list`.

**Step 4: Update `lib/db/repositories/__init__.py`**

Add `UsageRepository` export.

**Step 5: Run tests and commit**

```bash
python -m pytest tests/test_usage_repo.py -v
git add lib/db/repositories/usage_repo.py tests/test_usage_repo.py lib/db/repositories/__init__.py
git commit -m "feat(db): add UsageRepository for API call tracking"
```

---

### Task 6: Create Repositories — TaskRepository

The most complex repository, containing queue operations with transactional semantics.

**Files:**
- Create: `lib/db/repositories/task_repo.py`
- Create: `tests/test_task_repo.py`
- Modify: `lib/db/repositories/__init__.py`

**Step 1: Write the test**

Tests should mirror the existing `test_generation_queue.py` tests: enqueue/dedup, claim, succeed, fail with cascade, requeue, events, and worker lease operations.

```python
"""Tests for TaskRepository."""

import time
import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from lib.db.repositories.task_repo import TaskRepository


@pytest.fixture
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def db_session(engine):
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session


class TestTaskRepository:
    async def test_enqueue_dedupe_claim_succeed(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S01", payload={"prompt": "test"}, script_file="ep1.json",
        )
        assert not first["deduped"]

        deduped = await repo.enqueue(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S01", payload={"prompt": "test2"}, script_file="ep1.json",
        )
        assert deduped["deduped"]
        assert deduped["task_id"] == first["task_id"]

        running = await repo.claim_next("image")
        assert running is not None
        assert running["status"] == "running"

        done = await repo.mark_succeeded(first["task_id"], {"file": "test.png"})
        assert done["status"] == "succeeded"

    async def test_event_sequence(self, db_session):
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo", task_type="video", media_type="video",
            resource_id="E1S01", payload={}, script_file="ep1.json",
        )
        await repo.claim_next("video")
        await repo.mark_failed(task["task_id"], "mock error")

        events = await repo.get_events_since(last_event_id=0)
        assert len(events) >= 3
        types = [e["event_type"] for e in events]
        assert types == ["queued", "running", "failed"]

    async def test_dependency_cascade_failure(self, db_session):
        repo = TaskRepository(db_session)

        first = await repo.enqueue(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S01", payload={}, script_file="ep1.json",
        )
        second = await repo.enqueue(
            project_name="demo", task_type="storyboard", media_type="image",
            resource_id="E1S02", payload={}, script_file="ep1.json",
            dependency_task_id=first["task_id"],
        )

        await repo.claim_next("image")
        await repo.mark_failed(first["task_id"], "boom")

        dep_task = await repo.get(second["task_id"])
        assert dep_task["status"] == "failed"
        assert "blocked by failed dependency" in dep_task["error_message"]

    async def test_requeue_running_tasks(self, db_session):
        repo = TaskRepository(db_session)

        task = await repo.enqueue(
            project_name="demo", task_type="video", media_type="video",
            resource_id="E1S01", payload={}, script_file="ep1.json",
        )
        await repo.claim_next("video")
        count = await repo.requeue_running()
        assert count == 1

        queued = await repo.get(task["task_id"])
        assert queued["status"] == "queued"

    async def test_worker_lease(self, db_session):
        repo = TaskRepository(db_session)

        assert await repo.acquire_or_renew_lease(name="default", owner_id="a", ttl=2)
        assert not await repo.acquire_or_renew_lease(name="default", owner_id="b", ttl=2)
        assert await repo.is_worker_online(name="default")

        await repo.release_lease(name="default", owner_id="a")
        assert not await repo.is_worker_online(name="default")

    async def test_list_tasks_with_filters(self, db_session):
        repo = TaskRepository(db_session)

        await repo.enqueue(project_name="demo", task_type="storyboard", media_type="image",
                           resource_id="E1S01", payload={}, script_file="ep1.json")
        await repo.enqueue(project_name="other", task_type="video", media_type="video",
                           resource_id="E1S02", payload={}, script_file="ep2.json")

        result = await repo.list_tasks(project_name="demo")
        assert result["total"] == 1

        result = await repo.list_tasks()
        assert result["total"] == 2

    async def test_get_stats(self, db_session):
        repo = TaskRepository(db_session)

        await repo.enqueue(project_name="demo", task_type="storyboard", media_type="image",
                           resource_id="E1S01", payload={}, script_file="ep1.json")
        stats = await repo.get_stats()
        assert stats["queued"] == 1
        assert stats["total"] == 1
```

**Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_task_repo.py -v
```

**Step 3: Implement `lib/db/repositories/task_repo.py`**

This is the largest repository. Key implementation details:

- **Enqueue**: INSERT with dedup via unique constraint catch (IntegrityError)
- **Claim**: SELECT + UPDATE within a transaction (use `session.begin_nested()` for SQLite)
- **Mark succeeded/failed**: UPDATE + event insert within transaction
- **Cascade failure**: Recursive query for dependent tasks
- **Worker lease**: SELECT + INSERT/UPDATE with optimistic locking
- All methods use `await self.session.execute(...)` + `await self.session.commit()`

**Step 4: Update `lib/db/repositories/__init__.py`**

Add `TaskRepository` export.

**Step 5: Run tests and commit**

```bash
python -m pytest tests/test_task_repo.py -v
git add lib/db/repositories/task_repo.py tests/test_task_repo.py lib/db/repositories/__init__.py
git commit -m "feat(db): add TaskRepository with queue operations and worker lease"
```

---

### Task 7: Rewrite `GenerationQueue` to Use TaskRepository

Replace the hand-written SQLite code in `GenerationQueue` with async calls through `TaskRepository`.

**Files:**
- Modify: `lib/generation_queue.py`
- Modify: `tests/test_generation_queue.py`
- Modify: `tests/conftest.py`

**Step 1: Rewrite `GenerationQueue`**

The class becomes an async wrapper around `TaskRepository`:

- All public methods become `async def`
- Constructor creates an in-process `AsyncEngine` (for backward compat with skill scripts that don't run the web server)
- Module-level singleton `get_generation_queue()` returns an async-ready instance

Key changes:
- `_connect()` context manager → removed
- `_init_db()` → `async _init_db()` using `Base.metadata.create_all`
- Each method internally creates an `AsyncSession`, instantiates `TaskRepository`, and delegates
- `get_generation_queue()` creates engine from `DATABASE_URL` or defaults to the old SQLite path

**Step 2: Update all tests in `test_generation_queue.py`**

Convert all test methods to `async def`, use `await` for all queue method calls.

**Step 3: Update `tests/conftest.py`**

The `generation_queue` fixture should now provide an async-compatible queue instance backed by an in-memory SQLite.

**Step 4: Run all tests**

```bash
python -m pytest tests/test_generation_queue.py -v
```

Expected: PASS (same behavior, now async)

**Step 5: Commit**

```bash
git add lib/generation_queue.py tests/test_generation_queue.py tests/conftest.py
git commit -m "refactor(db): rewrite GenerationQueue to use TaskRepository (async)"
```

---

### Task 8: Rewrite `UsageTracker` to Use UsageRepository

**Files:**
- Modify: `lib/usage_tracker.py`
- Modify: `lib/media_generator.py` (update calls to async)
- Modify: `server/routers/usage.py`

**Step 1: Rewrite `UsageTracker`**

Convert to async wrapper around `UsageRepository`. Methods become `async def`.

**Step 2: Update `media_generator.py`**

Change `self.usage_tracker.start_call(...)` → `await self.usage_tracker.start_call(...)` and same for `finish_call`.

**Step 3: Update `server/routers/usage.py`**

Change from creating `UsageTracker(db_path)` per request to using `Depends(get_async_session)` + `UsageRepository(session)`.

**Step 4: Run tests**

```bash
python -m pytest -v
```

**Step 5: Commit**

```bash
git add lib/usage_tracker.py lib/media_generator.py server/routers/usage.py
git commit -m "refactor(db): rewrite UsageTracker to use UsageRepository (async)"
```

---

### Task 9: Rewrite `SessionMetaStore` to Use SessionRepository

**Files:**
- Modify: `server/agent_runtime/session_store.py`
- Modify: `server/agent_runtime/service.py`
- Modify: `server/agent_runtime/session_manager.py`
- Modify: `tests/test_session_meta_store.py`
- Modify: `tests/conftest.py`

**Step 1: Rewrite `SessionMetaStore`**

Convert to async wrapper. Each method takes an `AsyncSession` or creates one internally.

**Step 2: Update `SessionManager`**

All `self.meta_store.*` calls become `await self.meta_store.*`.

**Step 3: Update `AssistantService`**

All sync calls like `self.meta_store.list(...)`, `self.meta_store.get(...)` become async.
`list_sessions()` and `get_session()` must become `async def`.

**Step 4: Update `server/routers/assistant.py`**

Add `await` to newly-async service methods.

**Step 5: Update tests**

Convert `test_session_meta_store.py` tests to async.

**Step 6: Run tests**

```bash
python -m pytest tests/test_session_meta_store.py -v
python -m pytest -v
```

**Step 7: Commit**

```bash
git add server/agent_runtime/ tests/test_session_meta_store.py tests/conftest.py
git commit -m "refactor(db): rewrite SessionMetaStore to use SessionRepository (async)"
```

---

### Task 10: Update `GenerationWorker` for Async Queue

**Files:**
- Modify: `lib/generation_worker.py`

**Step 1: Update worker to `await` queue operations**

The `_run_loop` method already runs in an async context. Change all `self.queue.*()` calls to `await self.queue.*()`.

Before:
```python
self._owns_lease = self.queue.acquire_or_renew_worker_lease(...)
task = self.queue.claim_next_task(media_type="image")
self.queue.mark_task_succeeded(task_id, result)
```

After:
```python
self._owns_lease = await self.queue.acquire_or_renew_worker_lease(...)
task = await self.queue.claim_next_task(media_type="image")
await self.queue.mark_task_succeeded(task_id, result)
```

**Step 2: Run tests**

```bash
python -m pytest -v
```

**Step 3: Commit**

```bash
git add lib/generation_worker.py
git commit -m "refactor(db): update GenerationWorker to await async queue methods"
```

---

### Task 11: Update `server/routers/tasks.py` for Async Queue

**Files:**
- Modify: `server/routers/tasks.py`

**Step 1: Update all route handlers to `await` queue calls**

Before:
```python
stats = queue.get_task_stats(project_name=project_name)
```

After:
```python
stats = await queue.get_task_stats(project_name=project_name)
```

Update all endpoints: `get_task_stats`, `list_tasks`, `list_project_tasks`, `stream_tasks`, `get_task`.

The SSE `stream_tasks` endpoint needs special attention — the polling loop should `await` both `queue.get_events_since()` and `queue.get_task_stats()`.

**Step 2: Run tests**

```bash
python -m pytest -v
```

**Step 3: Commit**

```bash
git add server/routers/tasks.py
git commit -m "refactor(db): update tasks router to await async queue methods"
```

---

### Task 12: Update `generation_queue_client.py` for Skill Scripts

**Files:**
- Modify: `lib/generation_queue_client.py`

**Step 1: Update to use `asyncio.run()` wrapper**

Skill scripts are synchronous Python. The client functions should wrap async queue calls with `asyncio.run()` or use a persistent event loop:

```python
import asyncio

def _run_async(coro):
    """Run an async coroutine from sync code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # If already in event loop (unlikely for skills), use nest_asyncio or thread
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
```

The `wait_for_task()` function still uses `time.sleep()` for polling but delegates individual calls through `_run_async()`.

**Step 2: Run tests**

```bash
python -m pytest -v
```

**Step 3: Commit**

```bash
git add lib/generation_queue_client.py
git commit -m "refactor(db): update generation_queue_client sync wrapper for async queue"
```

---

### Task 13: Integrate with FastAPI Lifespan

**Files:**
- Modify: `server/app.py`

**Step 1: Add `init_db()` and `close_db()` to lifespan**

```python
from lib.db import init_db, close_db

@asynccontextmanager
async def lifespan(app: FastAPI):
    # DB 初始化
    await init_db()

    # 原有启动逻辑...
    ensure_auth_password()
    worker = create_generation_worker()
    # ...

    yield

    # 原有关闭逻辑...
    # ...

    # DB 关闭
    await close_db()
```

**Step 2: Run the app and test manually**

```bash
uv run uvicorn server.app:app --reload --port 8080
```

Verify the app starts without errors.

**Step 3: Run all tests**

```bash
python -m pytest -v
```

**Step 4: Commit**

```bash
git add server/app.py
git commit -m "feat(db): integrate init_db/close_db into FastAPI lifespan"
```

---

### Task 14: Set Up Alembic

**Files:**
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/script.py.mako`
- Create: `alembic/versions/001_initial_schema.py`

**Step 1: Initialize Alembic**

```bash
uv run alembic init alembic
```

**Step 2: Configure `alembic.ini`**

Set `sqlalchemy.url` to empty (will be overridden by env.py).

**Step 3: Configure `alembic/env.py`**

- Import `Base.metadata` from `lib.db.base`
- Import `get_database_url` from `lib.db.engine`
- Set the URL dynamically in `run_migrations_online()`
- Import all models so metadata includes all tables

**Step 4: Generate initial migration**

```bash
uv run alembic revision --autogenerate -m "initial schema"
```

**Step 5: Verify migration**

```bash
uv run alembic upgrade head
```

**Step 6: Commit**

```bash
git add alembic/ alembic.ini
git commit -m "feat(db): add Alembic migration framework with initial schema"
```

---

### Task 15: Create Data Migration Script

**Files:**
- Create: `scripts/migrate_sqlite_to_orm.py`

**Step 1: Implement migration script**

The script:
1. Reads old `.db` files: `projects/.task_queue.db`, `projects/.api_usage.db`, `projects/.agent_data/sessions.db`
2. Uses `sqlite3` to read old data (sync)
3. Uses `AsyncSession` to write to new DB
4. Renames old files to `.bak` on success
5. Prints statistics

```bash
python scripts/migrate_sqlite_to_orm.py
```

**Step 2: Test with existing data (if available)**

If old `.db` files exist, run the migration and verify data integrity.

**Step 3: Commit**

```bash
git add scripts/migrate_sqlite_to_orm.py
git commit -m "feat(db): add data migration script from old SQLite to new ORM"
```

---

### Task 16: Clean Up Old SQLite Code and Run Full Test Suite

**Files:**
- Remove old `_init_db()` / `CREATE TABLE` code from `GenerationQueue`, `UsageTracker`, `SessionMetaStore`
- Remove `_connect()` context managers
- Remove `_ensure_task_columns()` migration code
- Update `.gitignore` if needed (ignore `.arcreel.db`)

**Step 1: Remove dead code**

Ensure all hand-written SQL and `sqlite3` imports are removed from the rewritten modules.

**Step 2: Run full test suite**

```bash
python -m pytest -v --cov
```

Expected: All tests PASS, no regressions.

**Step 3: Start the app and verify manually**

```bash
uv run uvicorn server.app:app --reload --port 8080
```

Test key flows:
- Task queue SSE stream
- Usage stats API
- Assistant sessions

**Step 4: Commit**

```bash
git add -u
git commit -m "refactor(db): remove legacy sqlite3 code, complete ORM migration"
```

---

### Task 17: Update CLAUDE.md Documentation

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Update environment section**

Add `DATABASE_URL` to environment requirements and explain the two backends.

**Step 2: Update architecture notes**

Mention `lib/db/` package and Alembic.

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md with SQLAlchemy ORM migration notes"
```
