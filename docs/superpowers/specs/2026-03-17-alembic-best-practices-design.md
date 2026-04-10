# Alembic Best Practices Fix Design

## Overview

Performs a comprehensive fix of the project Alembic + Async SQLAlchemy configuration, including: infrastructure config completion, timestamp type unification (String → DateTime), foreign key constraint addition, and PostgreSQL connection pool optimization. Uses a single-migration approach for a one-shot fix.

## Background

Review found 6 issues:

1. **`render_as_batch=True` missing (High priority)** — future ALTER TABLE migrations on SQLite will fail
2. **Mixed timestamp types (Medium priority)** — 5 tables use String, 1 table (ApiKey) uses DateTime, and the format is inconsistent across 3 repos
3. **TaskEvent has no foreign key (Medium priority)** — data integrity has no database-level protection
4. **post_write_hooks not enabled (Low priority)** — migration file formatting is inconsistent
5. **PostgreSQL connection pool not configured (Low priority)** — only affects production performance
6. **`server_default` cross-database compatibility (Low priority)** — Boolean default value syntax is non-standard

## Solution Selection

**Selected Option 1: Single large migration**. Rationale: the project is early-stage (only 2 migration versions), table data volumes are small, and the change logic is singular (String→DateTime); splitting would introduce intermediate inconsistent states.

Data migration strategy: **direct conversion** (not old/new column coexistence), for the same rationale as above.

## Change Scope

### 1. Alembic Infrastructure (3 files)

#### `alembic/env.py`

Add `render_as_batch=True` to both `do_run_migrations()` and `run_migrations_offline()`:

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

The offline mode is the same.

#### `alembic.ini`

Uncomment the ruff post_write_hook:

```ini
[post_write_hooks]
hooks = ruff
ruff.type = exec
ruff.executable = ruff
ruff.options = check --fix REVISION_SCRIPT_FILENAME
```

#### `lib/db/engine.py`

Add connection pool parameters for PostgreSQL:

```python
if not _is_sqlite:
    kwargs.update(pool_size=10, max_overflow=20, pool_recycle=3600)
```

### 2. Database Migration Script (1 new migration file)

New migration `xxxx_unify_timestamps_and_add_fk.py`.

#### Affected Columns (11 columns, 5 tables)

| Table | Column | String → DateTime(timezone=True) |
|---|---|---|
| tasks | queued_at, started_at, finished_at, updated_at | 4 columns |
| task_events | created_at | 1 column |
| worker_lease | updated_at | 1 column |
| api_calls | started_at, finished_at, created_at | 3 columns |
| agent_sessions | created_at, updated_at | 2 columns |

**Unchanged column**: `worker_lease.lease_until` (Float, epoch timestamp semantics, kept as is).

#### SQLite Path

With `render_as_batch=True`, Alembic implements column type changes by reconstructing the table. The process: create new table → `INSERT INTO new SELECT * FROM old` → drop old table → rename. SQLite has no native DateTime type; ISO strings are copied as-is to the new column (still stored as TEXT). aiosqlite + SQLAlchemy automatically performs `fromisoformat` parsing when reading DateTime columns, so runtime behavior is unaffected.

#### PostgreSQL Path

Use a defensive USING clause to handle potentially dirty data:

```sql
ALTER COLUMN col TYPE TIMESTAMP WITH TIME ZONE
USING CASE WHEN col IS NOT NULL AND col != '' THEN col::timestamptz END
```

All three existing ISO formats (`2026-03-17T12:00:00Z`, `...000+00:00`, `...+00:00`) are correctly parsed by PostgreSQL.

#### Foreign Key Constraints

`task_events.task_id` adds `ForeignKey("tasks.task_id", ondelete="CASCADE")`.

**Orphan data cleanup**: During migration upgrade, clean up any potentially orphaned records before adding the foreign key constraint:

```sql
DELETE FROM task_events WHERE task_id NOT IN (SELECT task_id FROM tasks)
```

#### server_default Fix

`api_calls.generate_audio` `server_default="1"` changed to `server_default=sa.true_()`. `sa.true_()` is a cross-backend boolean literal provided by SQLAlchemy (PostgreSQL generates `true`, SQLite generates `1`), avoiding the issue where `sa.text("true")` stores the string "true" on SQLite.

#### downgrade

- Revert DateTime columns back to String
  - PostgreSQL uses `USING to_char(col AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS"Z"')`
  - SQLite uses batch mode to reconstruct the table
  - **Note**: After downgrade, the timestamp format is unified to `YYYY-MM-DDTHH:MM:SSZ` (no milliseconds), which is not fully consistent with the original three formats. This is an acceptable degradation and has been documented.
- Remove task_events foreign key
- Revert `server_default` back to `"1"`

### 3. ORM Model Changes (4 files)

- `lib/db/models/task.py` — Task (4 columns), TaskEvent (1 column + foreign key), WorkerLease (1 column)
- `lib/db/models/api_call.py` — ApiCall (3 columns + server_default)
- `lib/db/models/session.py` — AgentSession (2 columns)
- `lib/db/models/api_key.py` — unchanged (already uses DateTime)

All timestamp fields:
```python
# Before
queued_at: Mapped[str] = mapped_column(String, nullable=False)

# After
queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

TaskEvent new foreign key:
```python
task_id: Mapped[str] = mapped_column(
    String, ForeignKey("tasks.task_id", ondelete="CASCADE"), nullable=False
)
```

### 4. Repository Layer Changes (3 files)

The `_utc_now_iso()` helper in each of the three repos is unified to `_utc_now()`, returning `datetime.now(timezone.utc)`.

#### `lib/db/repositories/task_repo.py` (~12 locations)

- `_utc_now_iso()` → `_utc_now()`, remove strftime
- All write points like `queued_at=now`, `started_at=now` etc. assign datetime objects directly
- **`_task_to_dict()` and `_event_to_dict()`**: datetime fields must explicitly call `.isoformat()` to convert to strings. Reason:
  1. `_task_to_dict()` result is passed to `json.dumps()` (stored in `TaskEvent.data_json`), datetime objects would cause `TypeError: Object of type datetime is not JSON serializable`
  2. `_event_to_dict()` result is passed to the SSE event stream, which also requires strings

#### `lib/db/repositories/usage_repo.py` (~8 locations)

- Remove the `_iso_millis()` helper
- `_utc_now_iso()` → `_utc_now()`
- Remove `datetime.fromisoformat()` parsing (`finish_call` duration_ms calculation uses datetime subtraction directly)
- Filter condition `ApiCall.started_at >= _iso_millis(start)` changed to `ApiCall.started_at >= start` (pass datetime directly)
- **`_row_to_dict()`**: datetime fields explicitly use `.isoformat()` to ensure consistent API response format

#### `lib/db/repositories/session_repo.py` (~6 locations)

- `_utc_now_iso()` → `_utc_now()`
- All write points same as above
- **`_row_to_dict()`**: datetime fields explicitly use `.isoformat()` to ensure consistent API response format

### 5. Pydantic Model Adaptation

#### `server/agent_runtime/models.py` — SessionMeta

`created_at` and `updated_at` changed from `str` to `datetime` type:

```python
# Before
created_at: str
updated_at: str

# After
created_at: datetime
updated_at: datetime
```

Reason: when `session_repo._row_to_dict()` returns a datetime object, Pydantic's `str()` coercion produces `2026-03-17 12:00:00+00:00` (note the space instead of T), which breaks the downstream `_parse_iso_datetime()` parsing. Changing to a datetime type allows Pydantic to handle it correctly.

### 6. Peripheral Code Adaptation

#### `server/auth.py`

Remove `datetime.fromisoformat(expires_at.replace("Z", "+00:00"))` string parsing. `ApiKey.expires_at` is read from the database as a datetime object and can be compared directly.

#### `server/agent_runtime/service.py` — **unchanged**

`_parse_iso_datetime()` is only used to parse the timestamp field in SSE buffer messages (from session_manager's `_utc_now_iso()` and the raw SDK message stream), and does not involve any database fields. Keep unchanged.

#### `server/agent_runtime/session_manager.py` — **unchanged**

`_utc_now_iso()` is only used to construct the `"timestamp"` field value in the SSE buffer (lines 817, 917, 957), and does not involve any database writes. Keep unchanged.

### 7. Parts Not Changed

- `lib/project_manager.py` — `.isoformat()` for JSON file writing unchanged
- `lib/script_generator.py` — same as above
- `server/services/project_archive.py` — same as above
- `server/agent_runtime/service.py` — SSE parsing, does not involve the database
- `server/agent_runtime/session_manager.py` — SSE timestamps, does not involve the database

### 8. Test Adaptation

- `tests/conftest.py`, `tests/factories.py`, `tests/fakes.py` timestamp string fixtures updated to datetime objects
- `tests/test_app_module.py` and other tests: timestamp assertions adapted
- `SessionMeta` related tests adapted to the new datetime type
- Ensure all `pytest` tests pass

## API Compatibility

FastAPI JSON serialization defaults to outputting `datetime` as ISO 8601 strings (`datetime.isoformat()` format, with `+00:00` suffix instead of `Z`).

**Minor format difference**: previously task_repo output `Z` suffix; after the change it is unified to `+00:00` suffix. JavaScript's `new Date()` / `Date.parse()` can correctly parse both formats, so the frontend is very likely unaffected.

**Risk reduction**: Use `.isoformat()` explicitly in `*_to_dict()` for consistent output format control. If `Z` suffix compatibility is required, use `.isoformat().replace("+00:00", "Z")`.

## Verification Plan

1. `python -m pytest` — all tests pass
2. `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head` — round-trip verification
3. Check timestamp format in API responses (confirm JavaScript Date.parse compatibility)
4. Check how the frontend code parses timestamp fields; confirm no hardcoded `Z` suffix matching

## File Checklist

| File | Change Type |
|---|---|
| `alembic/env.py` | Add `render_as_batch=True` |
| `alembic.ini` | Enable ruff hook |
| `lib/db/engine.py` | PG connection pool parameters |
| `alembic/versions/xxxx_unify_timestamps_and_add_fk.py` | New migration |
| `lib/db/models/task.py` | String → DateTime, foreign key |
| `lib/db/models/api_call.py` | String → DateTime, server_default |
| `lib/db/models/session.py` | String → DateTime |
| `lib/db/repositories/task_repo.py` | _utc_now_iso → _utc_now |
| `lib/db/repositories/usage_repo.py` | Same + remove parse logic |
| `lib/db/repositories/session_repo.py` | Same as above |
| `server/auth.py` | Remove string parsing |
| `server/agent_runtime/models.py` | SessionMeta timestamp str → datetime |
| `tests/conftest.py` | fixture adaptation |
| `tests/factories.py` | fixture adaptation |
| `tests/fakes.py` | fixture adaptation |
