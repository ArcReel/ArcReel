# Multi-User Pre-embedding Refactor Design

> **Date**: 2026-03-24
> **Goal**: Pre-embed interfaces and data models in the open-source edition so that the commercial edition can cleanly extend multi-user functionality through inheritance/override
> **Scope**: Moderate pre-embedding (excludes multi-tenant directory isolation, login flow, and admin backend)

---

## 1. Design Decision Summary

| Decision | Conclusion |
|----------|-----------|
| Refactor scope | Moderate pre-embedding, no multi-tenancy |
| Open-source user experience | Maintain single-user, no changes |
| Project isolation strategy | Flat directory unchanged, visibility controlled via DB `user_id` |
| `get_current_user` return value | Pydantic model (`CurrentUserInfo`) |
| Repository pre-embedding | Template method `_scope_query()`, no-op in open-source edition |
| ORM model user_id | Pre-embedded with default value `"default"` |
| ProjectManager | No changes |

---

## 2. User ORM Model

Add `lib/db/models/user.py`:

```python
class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    role: Mapped[str] = mapped_column(String, nullable=False, server_default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="1")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- Open-source edition retains only fields required for identity
- Commercial edition extends fields via migration (email, hashed_password, display_name, quota_*, last_login_at)
- Migration inserts default user on table creation: `id="default", username="admin", role="admin"`

---

## 3. Model Base Class System (Mixin)

### 3.1 Current Issues

| Inconsistency | Affected Models |
|--------------|----------------|
| `created_at` is NOT NULL in some, Optional in others, absent in others | ApiCall (Optional!), Task (absent), ProviderConfig (absent) |
| Mixed timestamp generation strategies | ProviderConfig/SystemSetting use Python `default`, others rely on manual application-layer assignment |
| `updated_at` present in some, absent in others | ApiKey (absent), TaskEvent (absent) |

### 3.2 Mixin Definitions

Placed in `lib/db/base.py` (unified as single global definition; duplicate `_utc_now()` in various repositories and config.py to be imported from here):

```python
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)

class TimestampMixin:
    """Unified creation/update timestamps."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utc_now, onupdate=_utc_now
    )

class UserOwnedMixin:
    """User ownership marker. Fixed as "default" in open-source edition, commercial edition filters via _scope_query."""
    user_id: Mapped[str] = mapped_column(
        String, ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False, server_default="default", index=True,
    )
```

### 3.3 Mixin Application per Model

| Model | TimestampMixin | UserOwnedMixin | Notes |
|-------|:-:|:-:|-------|
| **Task** | - | ✓ | Retains `queued_at`/`updated_at` (domain-meaningful) |
| **TaskEvent** | - | - | Immutable event, indirectly associated with user via Task FK |
| **ApiCall** | ✓ | ✓ | Fix `created_at` Optional → NOT NULL, add `updated_at` |
| **ApiKey** | ✓ | ✓ | Add `updated_at` |
| **AgentSession** | ✓ | ✓ | Has timestamps already, changed to inherit from Mixin |
| **WorkerLease** | - | - | Infrastructure, not user-related |
| **ProviderConfig** | - | - | System configuration, retains own timestamps |
| **SystemSetting** | - | - | Same as above |

### 3.4 Reasons for Not Applying Mixin

- **Task**: `queued_at` is the domain expression of creation time; replacing it with `created_at` would lose business semantics
- **TaskEvent**: indirectly owned by user via `task_id` FK; adding redundant `user_id` violates normalization
- **WorkerLease**: infrastructure model, no user ownership concept
- **ProviderConfig / SystemSetting**: system-level configuration, no user ownership; already have `_utc_now` implementation, moving into Mixin allows reuse

---

## 4. Repository Base Class

Add `lib/db/repositories/base.py`:

```python
from sqlalchemy import Select

class BaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    def _scope_query(self, stmt: Select, model: type[Base]) -> Select:
        """Query scope limiter. No-op in open-source edition, commercial edition overrides to inject user_id filter."""
        return stmt
```

Four Repositories inherit `BaseRepository`: `TaskRepository`, `UsageRepository`, `SessionRepository`, `ApiKeyRepository`.

### 4.1 Complete List of Query Methods Needing `_scope_query` Insertion

| Repository | Method | Notes |
|---|---|---|
| **TaskRepository** | `list_tasks`, `get`, `get_stats`, `get_recent_tasks_snapshot` | Methods that directly query Task table |
| **TaskRepository** | `get_events_since`, `get_latest_event_id` | Query TaskEvent table, need to filter by user via JOIN Task (TaskEvent has no `user_id` field) |
| **TaskRepository** | `claim_next` | **Special handling**: currently uses raw SQL (relies on self-join), needs refactoring to ORM query to support `_scope_query`, or mark as method that commercial edition must override |
| **UsageRepository** | `get_stats`, `get_stats_grouped_by_provider`, `get_calls`, `get_projects_list` | All read methods |
| **SessionRepository** | `get`, `list` | All read methods |
| **ApiKeyRepository** | `list_all`, `get_by_hash`, `get_by_id` | All read methods |

Example:

```python
async def list_tasks(self, project_name=None, ...):
    stmt = select(Task)
    stmt = self._scope_query(stmt, Task)
    if project_name:
        stmt = stmt.where(Task.project_name == project_name)
    ...
```

### 4.2 `claim_next` Raw SQL Issue

`TaskRepository.claim_next()` uses `text()` raw SQL for dependency self-join; `_scope_query` cannot intercept it. It needs to be refactored as an ORM query to ensure user filtering in the commercial edition works correctly. If the refactoring complexity is too high, mark it as a method the commercial edition must override and note it in the method documentation.

### 4.3 Write Methods Needing `user_id` Parameter

| Repository | Method |
|---|---|
| **TaskRepository** | `enqueue` |
| **UsageRepository** | `start_call` |
| **SessionRepository** | `create` |
| **ApiKeyRepository** | `create` |

These methods add a `user_id: str = "default"` parameter, writing to the corresponding model's `user_id` field.

### 4.4 Commercial Edition Subclass Example

```python
class MultiUserTaskRepository(TaskRepository):
    def __init__(self, session, user_id: str):
        super().__init__(session)
        self._user_id = user_id

    def _scope_query(self, stmt: Select, model: type[Base]) -> Select:
        return stmt.where(model.user_id == self._user_id)
```

---

## 5. Auth Refactoring

### 5.1 CurrentUserInfo Model

Placed in `server/auth.py`:

```python
class CurrentUserInfo(BaseModel):
    id: str
    sub: str
    role: str = "admin"

    model_config = ConfigDict(frozen=True)
```

### 5.2 Synchronized Refactoring of `get_current_user` and `get_current_user_flexible`

Both authentication functions need to be changed to return `CurrentUserInfo`:

```python
async def get_current_user(...) -> CurrentUserInfo:
    payload = await _verify_and_get_payload(token, db)
    sub = payload.get("sub", "")
    return CurrentUserInfo(id="default", sub=sub, role="admin")

async def get_current_user_flexible(...) -> CurrentUserInfo:
    # For SSE endpoints (supports query param token)
    # Also changed to return CurrentUserInfo
    ...
```

`get_current_user_flexible` is used by the following SSE endpoints and must be synchronized:
- `server/routers/assistant.py` — SSE stream
- `server/routers/tasks.py` — SSE stream
- `server/routers/project_events.py` — SSE stream

### 5.3 Type Aliases

```python
CurrentUser = Annotated[CurrentUserInfo, Depends(get_current_user)]
CurrentUserFlexible = Annotated[CurrentUserInfo, Depends(get_current_user_flexible)]
```

### 5.4 Semantic Notes for `id` and `sub`

- `id`: corresponds to `users.id` primary key, used for database associations. Fixed as `"default"` in open-source edition, real user ID in commercial edition
- `sub`: the JWT payload's subject claim, represents login identity (username or `apikey:<name>`). Retained to be compatible with existing logging/audit logic

### 5.5 Impact on Existing Code

- Approximately ~80 occurrences of `current_user` references in about 15 router files need updating (signature type + variable name + attribute access method)
- Router signatures: `current_user: dict` → `user: CurrentUser` (most changes)
- `current_user["sub"]` → `current_user.sub` (only about 2 dict attribute accesses)

---

## 6. Route Layer Refactoring

### 6.1 Passing user_id During Writes

```python
# Before
@router.post("/api/v1/projects/{project_name}/tasks")
async def create_task(project_name: str, ...):
    await task_repo.create_task(project_name=project_name, ...)

# After
@router.post("/api/v1/projects/{project_name}/tasks")
async def create_task(project_name: str, user: CurrentUser, ...):
    await task_repo.create_task(project_name=project_name, user_id=user.id, ...)
```

### 6.2 GenerationQueue Complete Call Chain

`user_id` needs to be passed through the following complete call chain:

```
Route layer (user.id)
  → GenerationQueue.enqueue_task(user_id=...)
    → TaskRepository.enqueue(user_id=...)

Skill scripts (agent runtime)
  → generation_queue_client.enqueue_and_wait(user_id=...)
    → enqueue_task_only(user_id=...)
      → GenerationQueue.enqueue_task(user_id=...)
```

Skill scripts run in agent runtime without HTTP authentication context; `user_id` source strategy: open-source edition defaults to `"default"`, commercial edition carries `user_id` from agent session.

### 6.3 UsageRepository Call Chain

When `MediaGenerator` calls `UsageRepository.start_call()`, it also needs to pass through `user_id`. Source strategy: Task model already has `user_id` field; `GenerationWorker` gets `user_id` from task record when dequeuing, passes it to `MediaGenerator`, which passes it to `start_call()`.

---

## 7. Migration Plan

A single migration file completes all schema changes:

1. Create `users` table
2. Insert default user `(id="default", username="admin", role="admin")`
3. Add `user_id` field to Task, ApiCall, AgentSession, ApiKey (`server_default="default"`, FK → `users.id`, with index)
4. Fix ApiCall.created_at: Optional → NOT NULL (fill existing NULL rows with `started_at` value)
5. Add `updated_at` field to ApiCall
6. Add `updated_at` field to ApiKey
7. AgentSession's `created_at`/`updated_at` migrated to Mixin unified implementation (schema unchanged, code-level only)

**Implementation note**: SQLite doesn't support `ALTER COLUMN`, modifying column nullability (step 4) requires `op.batch_alter_table()` to rebuild the table.

---

## 8. What We're Not Doing

- **Not changing ProjectManager**: flat directory structure remains unchanged
- **Not adding login flow**: open-source edition retains current single-user authentication
- **Not building admin backend**: left for commercial edition
- **Not adding quota system**: left for commercial edition
- **Not changing frontend**: no user-visible functional changes
