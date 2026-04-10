# SQLAlchemy Async ORM Migration Design

**Date**: 2026-03-04
**Issue**: [#48](https://github.com/ArcReel/ArcReel/issues/48)
**Status**: Approved

---

## Background

The current runtime state uses 3 separate SQLite databases with hand-written SQL and no ORM:

| DB | File Path | Source File | Tables |
|---|---|---|---|
| Task Queue | `projects/.task_queue.db` | `lib/generation_queue.py` | 3 (tasks, task_events, worker_lease) |
| API Usage | `projects/.api_usage.db` | `lib/usage_tracker.py` | 1 (api_calls) |
| Agent Sessions | `projects/.agent_data/sessions.db` | `server/agent_runtime/session_store.py` | 1 (sessions) |

**Problems**:
1. Hand-written SQL scattered across modules; table schema changes lack migration management
2. SQLite single-file databases are not suitable for multi-instance deployment and high-concurrency production environments
3. All DB operations are synchronous `sqlite3`, blocking the event loop in async FastAPI routes

## Key Decisions

| Decision | Choice | Reason |
|---|---|---|
| Migration strategy | Hard cutover | Provide a one-time migration script; do not retain old code |
| Async mode | Fully async (AsyncSession) | Matches FastAPI async architecture; avoids blocking event loop |
| Database topology | Merge 3 SQLite files into single DB | Simplifies deployment; only one DATABASE_URL needed |
| Migration management | Alembic; first migration creates all tables | Standard approach; future schema changes are traceable |
| Dependency management | `uv add` | Automatically gets latest version and updates lock file |

## Architecture Design

### Directory Structure

```
lib/db/
├── __init__.py          # Exports init_db, close_db, get_async_session
├── engine.py            # AsyncEngine creation, DATABASE_URL resolution
├── base.py              # DeclarativeBase
├── models/
│   ├── __init__.py      # Exports all models
│   ├── task.py          # Task, TaskEvent, WorkerLease
│   ├── api_call.py      # ApiCall
│   └── session.py       # AgentSession
└── repositories/
    ├── __init__.py
    ├── task_repo.py     # TaskRepository
    ├── usage_repo.py    # UsageRepository
    └── session_repo.py  # SessionRepository

alembic/
├── alembic.ini
├── env.py
└── versions/
    └── 001_initial_schema.py

scripts/
└── migrate_sqlite_to_orm.py  # Old data migration script
```

### Engine Configuration (`lib/db/engine.py`)

- **DATABASE_URL resolution**: read from `DATABASE_URL` environment variable
  - Default: `sqlite+aiosqlite:///./projects/.arcreel.db`
  - PostgreSQL: `postgresql+asyncpg://user:pass@host:5432/arcreel`
- **SQLite-specific config**: set WAL + busy_timeout via `event.listens_for("connect")`
- **AsyncSession factory**: `async_sessionmaker(engine, expire_on_commit=False)`
- **FastAPI Depends**: `get_async_session()` generator injects AsyncSession

```python
# engine.py core logic
def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "projects" / ".arcreel.db"
    return f"sqlite+aiosqlite:///{db_path}"

async_engine = create_async_engine(get_database_url(), echo=False, pool_pre_ping=True)
async_session_factory = async_sessionmaker(async_engine, expire_on_commit=False)

async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
```

### ORM Models

#### Task (`lib/db/models/task.py`)

```python
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
        Index(
            "idx_tasks_dedupe_active",
            "project_name", "task_type", "resource_id",
            text("COALESCE(script_file, '')"),
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )
```

#### TaskEvent (`lib/db/models/task.py`)

```python
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
        Index("idx_task_events_id", "id"),
        Index("idx_task_events_project_id", "project_name", "id"),
    )
```

#### WorkerLease (`lib/db/models/task.py`)

```python
class WorkerLease(Base):
    __tablename__ = "worker_lease"

    name: Mapped[str] = mapped_column(String, primary_key=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    lease_until: Mapped[float] = mapped_column(Float, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)
```

#### ApiCall (`lib/db/models/api_call.py`)

```python
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
    generate_audio: Mapped[bool] = mapped_column(Boolean, server_default="1")
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
        Index("idx_api_calls_created_at", "created_at"),
        Index("idx_api_calls_started_at", "started_at"),
    )
```

#### AgentSession (`lib/db/models/session.py`)

```python
class AgentSession(Base):
    __tablename__ = "agent_sessions"  # Avoid conflict with PostgreSQL reserved words

    id: Mapped[str] = mapped_column(String, primary_key=True)
    sdk_session_id: Mapped[Optional[str]] = mapped_column(String)
    project_name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, server_default="")
    status: Mapped[str] = mapped_column(String, server_default="idle")
    created_at: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[str] = mapped_column(String, nullable=False)

    __table_args__ = (
        Index("idx_agent_sessions_project", "project_name", text("updated_at DESC")),
        Index("idx_agent_sessions_status", "status"),
    )
```

### Repository Layer

#### TaskRepository (`lib/db/repositories/task_repo.py`)

```python
class TaskRepository:
    def __init__(self, session: AsyncSession): ...

    # Queue operations (require transactions)
    async def enqueue(self, *, project_name, task_type, media_type, resource_id, ...) -> dict
    async def claim_next(self, media_type: str) -> Optional[dict]
    async def mark_succeeded(self, task_id: str, result: dict) -> Optional[dict]
    async def mark_failed(self, task_id: str, error: str) -> Optional[dict]
    async def requeue_running(self, limit: int = 1000) -> int

    # Queries (read-only)
    async def get(self, task_id: str) -> Optional[dict]
    async def list_tasks(self, *, project_name=None, status=None, task_type=None, source=None, page=1, page_size=50) -> dict
    async def get_stats(self, project_name=None) -> dict
    async def get_recent_snapshot(self, *, project_name=None, limit=200) -> list

    # Events
    async def get_events_since(self, *, last_event_id: int, project_name=None, limit=200) -> list
    async def get_latest_event_id(self, *, project_name=None) -> int

    # Worker lease
    async def acquire_or_renew_lease(self, *, name, owner_id, ttl) -> bool
    async def release_lease(self, *, name, owner_id) -> None
    async def is_worker_online(self, *, name="default") -> bool
    async def get_worker_lease(self, *, name="default") -> Optional[dict]
```

**Concurrency Control**:
- PostgreSQL: `SELECT ... FOR UPDATE` + transaction
- SQLite: relies on aiosqlite's connection-level write lock + `BEGIN IMMEDIATE` (explicit control via `session.execute(text("BEGIN IMMEDIATE"))`)

#### UsageRepository (`lib/db/repositories/usage_repo.py`)

```python
class UsageRepository:
    def __init__(self, session: AsyncSession): ...

    async def start_call(self, *, project_name, call_type, model, ...) -> int
    async def finish_call(self, call_id: int, *, status, output_path=None, error_message=None, retry_count=0) -> None
    async def get_stats(self, *, project_name=None, start_date=None, end_date=None) -> dict
    async def get_calls(self, *, project_name=None, call_type=None, status=None, start_date=None, end_date=None, page=1, page_size=20) -> dict
    async def get_projects_list(self) -> list[str]
```

#### SessionRepository (`lib/db/repositories/session_repo.py`)

```python
class SessionRepository:
    def __init__(self, session: AsyncSession): ...

    async def create(self, project_name: str, title: str = "") -> SessionMeta
    async def get(self, session_id: str) -> Optional[SessionMeta]
    async def list(self, *, project_name=None, status=None, limit=50, offset=0) -> list[SessionMeta]
    async def update_status(self, session_id: str, status: str) -> bool
    async def update_sdk_session_id(self, session_id: str, sdk_id: str) -> bool
    async def update_title(self, session_id: str, title: str) -> bool
    async def delete(self, session_id: str) -> bool
    async def interrupt_running(self) -> int
```

### Existing Module Refactoring

| Existing Module | Refactoring Approach |
|---|---|
| `GenerationQueue` | Internally uses `TaskRepository`; all methods made async. Global singleton → FastAPI Depends injection |
| `UsageTracker` | Internally uses `UsageRepository`; methods made async. Route uses Depends injection |
| `SessionMetaStore` | Internally uses `SessionRepository`; methods made async |
| `GenerationWorker` | await queue's async methods; no longer blocks event loop |
| `generation_queue_client.py` | Skill scripts interact via HTTP API; no longer directly access the database |

### Alembic Configuration

```
alembic/
├── alembic.ini          # sqlalchemy.url injected dynamically by env.py
├── env.py               # imports Base.metadata, reads connection string from DATABASE_URL
└── versions/
    └── 001_initial_schema.py  # Creates all 5 tables + indexes
```

`env.py` gets the connection string from `lib.db.engine.get_database_url()`.

### Data Migration Script

`scripts/migrate_sqlite_to_orm.py`:

1. Check if old `.db` files exist (`projects/.task_queue.db`, `projects/.api_usage.db`, `projects/.agent_data/sessions.db`)
2. Read old data synchronously with `sqlite3`
3. Write to new database in batches with `AsyncSession` (flush every 500 rows)
4. Rename old files to `.bak` after successful migration
5. Print migration statistics

### Environment Configuration

Add to `.env.example`:
```bash
# Database configuration (defaults to SQLite)
# SQLite (development/single-machine): sqlite+aiosqlite:///./projects/.arcreel.db
# PostgreSQL (production):             postgresql+asyncpg://user:pass@host:5432/arcreel
# DATABASE_URL=sqlite+aiosqlite:///./projects/.arcreel.db
```

### New Dependencies

Install via `uv add`:
- `sqlalchemy[asyncio]`
- `aiosqlite`
- `asyncpg`
- `alembic`

### FastAPI Integration

Update `server/app.py` lifespan:
```python
async def lifespan(app: FastAPI):
    await init_db()       # Ensure tables exist
    # ... existing worker startup logic ...
    yield
    # ... existing shutdown logic ...
    await close_db()
```

## Out of Scope

- Project data (project.json, script JSON, version JSON, media files) remains in file system storage
- No frontend code changes (API interface signatures remain unchanged)
- No changes to Skill script user interfaces
