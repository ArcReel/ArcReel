# SQLAlchemy Async ORM 迁移设计

**日期**：2026-03-04
**Issue**：[#48](https://github.com/ArcReel/ArcReel/issues/48)
**状态**：已批准

---

## 背景

当前运行时状态使用 3 个独立 SQLite DB，手写 SQL，无 ORM：

| DB | 文件路径 | 源文件 | 表数 |
|---|---|---|---|
| 任务队列 | `projects/.task_queue.db` | `lib/generation_queue.py` | 3 (tasks, task_events, worker_lease) |
| API 用量 | `projects/.api_usage.db` | `lib/usage_tracker.py` | 1 (api_calls) |
| Agent 会话 | `projects/.agent_data/sessions.db` | `server/agent_runtime/session_store.py` | 1 (sessions) |

**问题**：
1. 手写 SQL 分散在各模块中，表结构变更缺乏迁移管理
2. SQLite 单文件数据库不适合多实例部署和高并发生产环境
3. 所有 DB 操作都是同步 `sqlite3`，在 async FastAPI 路由中阻塞 event loop

## 关键决策

| 决策项 | 选择 | 理由 |
|---|---|---|
| 迁移策略 | 硬切换 | 提供一次性迁移脚本，不保留旧代码 |
| 异步模式 | 全异步 (AsyncSession) | 契合 FastAPI 异步架构，避免阻塞 event loop |
| 数据库拓扑 | 3 个 SQLite 合并为单 DB | 简化部署，只需一个 DATABASE_URL |
| 迁移管理 | Alembic，首个 migration 建全部表 | 标准做法，后续 schema 变更有迹可循 |
| 依赖管理 | `uv add` | 自动获取最新版本并更新 lock 文件 |

## 架构设计

### 目录结构

```
lib/db/
├── __init__.py          # 导出 init_db, close_db, get_async_session
├── engine.py            # AsyncEngine 创建、DATABASE_URL 解析
├── base.py              # DeclarativeBase
├── models/
│   ├── __init__.py      # 导出所有模型
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
└── migrate_sqlite_to_orm.py  # 旧数据迁移脚本
```

### Engine 配置 (`lib/db/engine.py`)

- **DATABASE_URL 解析**：从环境变量 `DATABASE_URL` 读取
  - 默认值：`sqlite+aiosqlite:///./projects/.arcreel.db`
  - PostgreSQL：`postgresql+asyncpg://user:pass@host:5432/arcreel`
- **SQLite 专用配置**：通过 `event.listens_for("connect")` 设置 WAL + busy_timeout
- **AsyncSession 工厂**：`async_sessionmaker(engine, expire_on_commit=False)`
- **FastAPI Depends**：`get_async_session()` 生成器注入 AsyncSession

```python
# engine.py 核心逻辑
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

### ORM 模型

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
    __tablename__ = "agent_sessions"  # 避免与 PostgreSQL 保留词冲突

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

### Repository 层

#### TaskRepository (`lib/db/repositories/task_repo.py`)

```python
class TaskRepository:
    def __init__(self, session: AsyncSession): ...

    # 队列操作（需要事务）
    async def enqueue(self, *, project_name, task_type, media_type, resource_id, ...) -> dict
    async def claim_next(self, media_type: str) -> Optional[dict]
    async def mark_succeeded(self, task_id: str, result: dict) -> Optional[dict]
    async def mark_failed(self, task_id: str, error: str) -> Optional[dict]
    async def requeue_running(self, limit: int = 1000) -> int

    # 查询（只读）
    async def get(self, task_id: str) -> Optional[dict]
    async def list_tasks(self, *, project_name=None, status=None, task_type=None, source=None, page=1, page_size=50) -> dict
    async def get_stats(self, project_name=None) -> dict
    async def get_recent_snapshot(self, *, project_name=None, limit=200) -> list

    # 事件
    async def get_events_since(self, *, last_event_id: int, project_name=None, limit=200) -> list
    async def get_latest_event_id(self, *, project_name=None) -> int

    # Worker 租约
    async def acquire_or_renew_lease(self, *, name, owner_id, ttl) -> bool
    async def release_lease(self, *, name, owner_id) -> None
    async def is_worker_online(self, *, name="default") -> bool
    async def get_worker_lease(self, *, name="default") -> Optional[dict]
```

**并发控制**：
- PostgreSQL：`SELECT ... FOR UPDATE` + 事务
- SQLite：依赖 aiosqlite 的连接级写锁 + `BEGIN IMMEDIATE`（通过 `session.execute(text("BEGIN IMMEDIATE"))` 显式控制）

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

### 现有模块改造

| 现有模块 | 改造方式 |
|---|---|
| `GenerationQueue` | 内部改用 `TaskRepository`，所有方法 async 化。全局单例 → FastAPI Depends 注入 |
| `UsageTracker` | 内部改用 `UsageRepository`，方法 async 化。路由通过 Depends 注入 |
| `SessionMetaStore` | 内部改用 `SessionRepository`，方法 async 化 |
| `GenerationWorker` | await queue 的 async 方法，不再阻塞 event loop |
| `generation_queue_client.py` | Skill 脚本改为通过 HTTP API 交互，不再直接操作数据库 |

### Alembic 配置

```
alembic/
├── alembic.ini          # sqlalchemy.url 由 env.py 动态注入
├── env.py               # 引入 Base.metadata，从 DATABASE_URL 读取连接字符串
└── versions/
    └── 001_initial_schema.py  # 创建全部 5 张表 + 索引
```

`env.py` 从 `lib.db.engine.get_database_url()` 获取连接字符串。

### 数据迁移脚本

`scripts/migrate_sqlite_to_orm.py`：

1. 检查旧 `.db` 文件是否存在（`projects/.task_queue.db`、`projects/.api_usage.db`、`projects/.agent_data/sessions.db`）
2. 用 `sqlite3` 同步读取旧数据
3. 用 `AsyncSession` 批量写入新数据库（每 500 条 flush）
4. 迁移成功后旧文件重命名为 `.bak`
5. 打印迁移统计

### 环境配置

`.env.example` 新增：
```bash
# 数据库配置（默认使用 SQLite）
# SQLite（开发/单机）: sqlite+aiosqlite:///./projects/.arcreel.db
# PostgreSQL（生产）:  postgresql+asyncpg://user:pass@host:5432/arcreel
# DATABASE_URL=sqlite+aiosqlite:///./projects/.arcreel.db
```

### 新增依赖

通过 `uv add` 安装：
- `sqlalchemy[asyncio]`
- `aiosqlite`
- `asyncpg`
- `alembic`

### FastAPI 集成

`server/app.py` lifespan 更新：
```python
async def lifespan(app: FastAPI):
    await init_db()       # 确保表存在
    # ... 原有 worker 启动逻辑 ...
    yield
    # ... 原有 shutdown 逻辑 ...
    await close_db()
```

## 不在范围内

- 项目数据（project.json、剧本 JSON、版本 JSON、媒体文件）仍保留文件系统存储
- 不修改前端代码（API 接口签名保持不变）
- 不修改 Skill 脚本的用户接口
