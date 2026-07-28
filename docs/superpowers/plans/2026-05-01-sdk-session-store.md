# SDK SessionStore 接入实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用 `claude-agent-sdk` 0.1.65 的 `SessionStore` 协议替换 `sdk_transcript_adapter.py` 对私有 `_internal._read_session_file` 的依赖，并把会话 transcript 镜像到项目数据库（dev SQLite / prod PG）。

**Architecture:** 自定义 `DbSessionStore` 走 `lib/db/`；行级 `agent_session_entries` 表 + 一行/会话 `agent_session_summaries` 表（`fold_session_summary` 维护快路径）；`SessionStoreEntry` 由 SDK 在 ~100ms 节奏批量推送，单事务内取 seq + 部分唯一索引去重 + 行锁 fold summary；本地 jsonl 副本保留作兜底；启动钩子用 SDK 公开 `import_session_to_store(directory=cwd)` 一次性导入历史会话；环境变量 `ARCREEL_SDK_SESSION_STORE=off` 5 秒回滚。

**Tech Stack:** SQLAlchemy 2.x async + Alembic + claude-agent-sdk 0.1.71 + pytest-asyncio + FastAPI lifespan

**Spec:** `docs/superpowers/specs/2026-05-01-sdk-session-store-design.md`

---

## File Structure

### 新增文件

```
lib/agent_session_store/
  __init__.py                 # 导出 DbSessionStore, make_project_key
  models.py                   # AgentSessionEntry / AgentSessionSummary ORM
  store.py                    # DbSessionStore 实现 SDK SessionStore 协议
  import_local.py             # 启动迁移：本地 jsonl → store

alembic/versions/
  <new>_add_session_store_tables.py   # 新增两表

tests/agent_session_store/
  __init__.py
  conftest.py                 # 共享 store fixture
  test_make_project_key.py
  test_models.py
  test_store_append.py
  test_store_load.py
  test_store_optional.py
  test_store_concurrency.py
  test_store_summary.py
  test_conformance.py         # SDK 官方 14 项契约测试
  test_import_local.py

tests/agent_runtime/
  test_session_store_e2e.py
  test_stream_projector_mirror_error.py
```

### 修改文件

```
server/agent_runtime/
  sdk_transcript_adapter.py   # *_from_store helper；删 _internal 引用
  session_manager.py          # 注入 DbSessionStore；环境变量回滚
  service.py                  # *_via_store helper
  stream_projector.py         # 识别 mirror_error system 消息

server/app.py                 # lifespan 加 migrate_local_transcripts_to_store
```

### 责任边界

- `lib/agent_session_store/` 不依赖 `server/`；只依赖 `lib/db/` 与 `claude_agent_sdk`
- `server/agent_runtime/` 通过工厂方法注入 store；测试可替换为内存 store
- ORM models 与 schema 迁移耦合在 `lib/agent_session_store/`，与 `lib/db/models/` 不串，避免循环导入

---

## Task 1：Alembic 迁移：建两表

**Files:**
- Create: `alembic/versions/<auto-hash>_add_session_store_tables.py`

- [ ] **Step 1: 写 ORM models 文件骨架（让 autogenerate 能识别）**

```python
# lib/agent_session_store/models.py
"""SessionStore ORM models — SDK transcript mirror tables."""
from __future__ import annotations

from sqlalchemy import BigInteger, Index, JSON, PrimaryKeyConstraint, String, text
from sqlalchemy.orm import Mapped, mapped_column

from lib.db.base import Base, TimestampMixin, UserOwnedMixin


class AgentSessionEntry(TimestampMixin, UserOwnedMixin, Base):
    __tablename__ = "agent_session_entries"

    project_key: Mapped[str] = mapped_column(String, nullable=False)
    session_id: Mapped[str] = mapped_column(String, nullable=False)
    subpath: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    uuid: Mapped[str | None] = mapped_column(String, nullable=True)
    entry_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    mtime_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)

    __table_args__ = (
        PrimaryKeyConstraint("project_key", "session_id", "subpath", "seq"),
        Index(
            "uq_agent_entries_uuid",
            "project_key", "session_id", "subpath", "uuid",
            unique=True,
            postgresql_where=text("uuid IS NOT NULL"),
            sqlite_where=text("uuid IS NOT NULL"),
        ),
        Index(
            "idx_agent_entries_listing",
            "project_key", "session_id", "mtime_ms",
        ),
    )


class AgentSessionSummary(TimestampMixin, UserOwnedMixin, Base):
    __tablename__ = "agent_session_summaries"

    project_key: Mapped[str] = mapped_column(String, primary_key=True)
    session_id: Mapped[str] = mapped_column(String, primary_key=True)
    mtime_ms: Mapped[int] = mapped_column(BigInteger, nullable=False)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
```

- [ ] **Step 2: 创建 lib/agent_session_store/__init__.py 占位（让 alembic env.py import）**

```python
# lib/agent_session_store/__init__.py
"""Agent SessionStore — SDK transcript mirror to project DB."""
from lib.agent_session_store.models import AgentSessionEntry, AgentSessionSummary

__all__ = ["AgentSessionEntry", "AgentSessionSummary"]
```

- [ ] **Step 3: 把 models import 加入 alembic env.py**

修改 `alembic/env.py`，在已有 `from lib.db.models import *` 附近加：

```python
import lib.agent_session_store.models  # noqa: F401  ensure tables registered
```

（具体行号根据现有 env.py 的 import 区域决定；不要重复 import，不要改 target_metadata 赋值）

- [ ] **Step 4: 生成迁移**

Run: `uv run alembic revision --autogenerate -m "add session store tables"`

Expected: 在 `alembic/versions/` 下生成新文件，内含 `op.create_table('agent_session_entries', ...)` 与 `op.create_table('agent_session_summaries', ...)`，以及部分唯一索引 `uq_agent_entries_uuid`。

打开新生成的迁移文件，**手动核对以下两点**：

1. `uq_agent_entries_uuid` 索引应使用 `sqlite_where=` + `postgresql_where=` 限制 `uuid IS NOT NULL`。如果 autogenerate 没生成 `*_where`，手动添加：

```python
op.create_index(
    "uq_agent_entries_uuid",
    "agent_session_entries",
    ["project_key", "session_id", "subpath", "uuid"],
    unique=True,
    postgresql_where=sa.text("uuid IS NOT NULL"),
    sqlite_where=sa.text("uuid IS NOT NULL"),
)
```

2. `subpath` 列必须有 `server_default=""`，否则旧 SQLite NULL 行为会破坏 PK 唯一性。

- [ ] **Step 5: 应用迁移并验证**

Run: `uv run alembic upgrade head`
Expected: 无报错。

Run: `uv run python -c "from sqlalchemy import inspect; from lib.db.engine import async_session_factory; import asyncio; \
async def main():\
    async with async_session_factory() as s:\
        names = await s.run_sync(lambda c: inspect(c.bind).get_table_names()); \
        print(sorted([n for n in names if 'agent_session' in n]));\
asyncio.run(main())"`

Expected: `['agent_session_entries', 'agent_session_summaries', 'agent_sessions']`

- [ ] **Step 6: Commit**

```bash
git add lib/agent_session_store/__init__.py lib/agent_session_store/models.py alembic/env.py alembic/versions/*_add_session_store_tables.py
git commit -m "feat(session-store): add agent_session_entries / summaries tables"
```

---

## Task 2：ORM models 单测

**Files:**
- Create: `tests/agent_session_store/__init__.py` (empty)
- Create: `tests/agent_session_store/conftest.py`
- Create: `tests/agent_session_store/test_models.py`

- [ ] **Step 1: 写共享 fixture**

```python
# tests/agent_session_store/conftest.py
"""Fixtures for agent_session_store tests."""
from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from lib.db.base import Base


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """In-memory SQLite session factory with all tables created."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # Import model modules to register tables on Base.metadata.
        import lib.agent_session_store.models  # noqa: F401
        import lib.db.models  # noqa: F401  (users / agent_sessions / config etc.)

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()
```

- [ ] **Step 2: 写 model 单测**

```python
# tests/agent_session_store/test_models.py
"""ORM smoke tests for AgentSessionEntry / AgentSessionSummary."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from lib.agent_session_store.models import AgentSessionEntry, AgentSessionSummary


@pytest.mark.asyncio
async def test_entry_can_round_trip(session_factory):
    async with session_factory() as session:
        row = AgentSessionEntry(
            project_key="proj-A",
            session_id="sess-1",
            subpath="",
            seq=0,
            uuid="00000000-0000-0000-0000-000000000001",
            entry_type="user",
            payload={"type": "user", "content": "hi"},
            mtime_ms=1714540000000,
            user_id="default",
        )
        session.add(row)
        await session.commit()

        rows = (await session.execute(select(AgentSessionEntry))).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload == {"type": "user", "content": "hi"}


@pytest.mark.asyncio
async def test_summary_pk_dedup(session_factory):
    async with session_factory() as session:
        s1 = AgentSessionSummary(
            project_key="proj-A",
            session_id="sess-1",
            mtime_ms=1,
            data={"v": 1},
            user_id="default",
        )
        session.add(s1)
        await session.commit()

        # 同 PK 二次插入：必须报 IntegrityError
        from sqlalchemy.exc import IntegrityError
        s2 = AgentSessionSummary(
            project_key="proj-A",
            session_id="sess-1",
            mtime_ms=2,
            data={"v": 2},
            user_id="default",
        )
        session.add(s2)
        with pytest.raises(IntegrityError):
            await session.commit()
```

- [ ] **Step 3: 运行测试**

Run: `uv run python -m pytest tests/agent_session_store/test_models.py -v`
Expected: 2 passed

- [ ] **Step 4: Commit**

```bash
git add tests/agent_session_store/__init__.py tests/agent_session_store/conftest.py tests/agent_session_store/test_models.py
git commit -m "test(session-store): ORM round-trip + summary PK dedup"
```

---

## Task 3：make_project_key wrapper

**Files:**
- Modify: `lib/agent_session_store/__init__.py`
- Create: `tests/agent_session_store/test_make_project_key.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/agent_session_store/test_make_project_key.py
"""make_project_key must agree with SDK live mirror's project_key derivation."""
from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import project_key_for_directory

from lib.agent_session_store import make_project_key


def test_matches_sdk_helper(tmp_path: Path):
    cwd = tmp_path / "projects" / "demo"
    cwd.mkdir(parents=True)
    assert make_project_key(cwd) == project_key_for_directory(str(cwd))


def test_accepts_string_path(tmp_path: Path):
    cwd = tmp_path / "projects" / "demo"
    cwd.mkdir(parents=True)
    assert make_project_key(str(cwd)) == project_key_for_directory(str(cwd))
```

- [ ] **Step 2: 运行测试看失败**

Run: `uv run python -m pytest tests/agent_session_store/test_make_project_key.py -v`
Expected: FAIL — `ImportError: cannot import name 'make_project_key'`

- [ ] **Step 3: 实现 wrapper**

```python
# lib/agent_session_store/__init__.py
"""Agent SessionStore — SDK transcript mirror to project DB."""
from __future__ import annotations

from pathlib import Path

from claude_agent_sdk import project_key_for_directory

from lib.agent_session_store.models import AgentSessionEntry, AgentSessionSummary


def make_project_key(project_cwd: Path | str) -> str:
    """Derive the SessionStore project_key for a project cwd.

    Thin wrapper around SDK's public ``project_key_for_directory`` so adapter
    callers and SDK live-mirror writes agree on the key.
    """
    return project_key_for_directory(str(project_cwd))


__all__ = [
    "AgentSessionEntry",
    "AgentSessionSummary",
    "make_project_key",
]
```

- [ ] **Step 4: 运行测试**

Run: `uv run python -m pytest tests/agent_session_store/test_make_project_key.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add lib/agent_session_store/__init__.py tests/agent_session_store/test_make_project_key.py
git commit -m "feat(session-store): make_project_key delegates to SDK"
```

---

## Task 4：DbSessionStore 骨架 + append/load（最小实现）

**Files:**
- Create: `lib/agent_session_store/store.py`
- Create: `tests/agent_session_store/test_store_append.py`
- Create: `tests/agent_session_store/test_store_load.py`

- [ ] **Step 1: 写 append 失败测试**

```python
# tests/agent_session_store/test_store_append.py
"""DbSessionStore.append basic semantics."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from lib.agent_session_store import AgentSessionEntry
from lib.agent_session_store.store import DbSessionStore

KEY = {"project_key": "proj", "session_id": "sess"}


@pytest.mark.asyncio
async def test_append_writes_rows_in_call_order(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append(KEY, [
        {"type": "user", "uuid": "u-1", "timestamp": "2026-05-01T00:00:00Z"},
        {"type": "assistant", "uuid": "u-2", "timestamp": "2026-05-01T00:00:01Z"},
    ])

    async with session_factory() as session:
        rows = (await session.execute(
            select(AgentSessionEntry).order_by(AgentSessionEntry.seq)
        )).scalars().all()
    assert [r.seq for r in rows] == [0, 1]
    assert [r.uuid for r in rows] == ["u-1", "u-2"]
    assert all(r.user_id == "u1" for r in rows)


@pytest.mark.asyncio
async def test_append_dedups_by_uuid(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    entry = {"type": "user", "uuid": "dup", "timestamp": "t"}
    await store.append(KEY, [entry])
    await store.append(KEY, [entry])  # 重放：必须幂等

    async with session_factory() as session:
        count = len((await session.execute(select(AgentSessionEntry))).scalars().all())
    assert count == 1


@pytest.mark.asyncio
async def test_append_does_not_dedup_when_uuid_missing(session_factory):
    """SDK 协议：无 uuid 的 entries（titles/tags/mode markers）不去重。"""
    store = DbSessionStore(session_factory, user_id="u1")
    e = {"type": "tag", "tag": "demo"}
    await store.append(KEY, [e])
    await store.append(KEY, [e])

    async with session_factory() as session:
        count = len((await session.execute(select(AgentSessionEntry))).scalars().all())
    assert count == 2


@pytest.mark.asyncio
async def test_append_empty_is_noop(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append(KEY, [])  # 不应抛、不应建空行

    async with session_factory() as session:
        count = len((await session.execute(select(AgentSessionEntry))).scalars().all())
    assert count == 0
```

- [ ] **Step 2: 写 load 失败测试**

```python
# tests/agent_session_store/test_store_load.py
"""DbSessionStore.load basic semantics."""
from __future__ import annotations

import pytest

from lib.agent_session_store.store import DbSessionStore


@pytest.mark.asyncio
async def test_load_returns_None_for_unknown_key(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    assert await store.load({"project_key": "proj", "session_id": "nope"}) is None


@pytest.mark.asyncio
async def test_load_round_trip(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    key = {"project_key": "proj", "session_id": "sess"}
    entries = [
        {"type": "user", "uuid": "a", "n": 1},
        {"type": "assistant", "uuid": "b", "n": 2},
    ]
    await store.append(key, entries)
    loaded = await store.load(key)
    assert loaded == entries


@pytest.mark.asyncio
async def test_load_subpath_isolated(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    main = {"project_key": "proj", "session_id": "sess"}
    sub = {"project_key": "proj", "session_id": "sess", "subpath": "subagents/a"}
    await store.append(main, [{"type": "user", "uuid": "m"}])
    await store.append(sub, [{"type": "user", "uuid": "s"}])

    assert (await store.load(main)) == [{"type": "user", "uuid": "m"}]
    assert (await store.load(sub)) == [{"type": "user", "uuid": "s"}]
```

- [ ] **Step 3: 跑测试看失败**

Run: `uv run python -m pytest tests/agent_session_store/test_store_append.py tests/agent_session_store/test_store_load.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lib.agent_session_store.store'`

- [ ] **Step 4: 实现 DbSessionStore（最小可工作版本）**

```python
# lib/agent_session_store/store.py
"""DbSessionStore — SQLAlchemy-backed SDK SessionStore implementation."""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from lib.agent_session_store.models import AgentSessionEntry, AgentSessionSummary
from lib.db.base import DEFAULT_USER_ID, utc_now

logger = logging.getLogger("arcreel.session_store")


def _normalize_key(key: dict) -> tuple[str, str, str]:
    return key["project_key"], key["session_id"], key.get("subpath", "") or ""


def _entry_type(entry: dict) -> str:
    t = entry.get("type")
    return t if isinstance(t, str) else ""


def _entry_uuid(entry: dict) -> str | None:
    u = entry.get("uuid")
    return u if isinstance(u, str) and u else None


class DbSessionStore:
    """SDK SessionStore mirroring transcripts into the project database.

    Bind one instance per logical user — appends carry ``user_id`` for
    FK CASCADE on user deletion.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        *,
        user_id: str = DEFAULT_USER_ID,
    ) -> None:
        self._session_factory = session_factory
        self._user_id = user_id

    # --- required: append + load ---------------------------------------------

    async def append(self, key: dict, entries: list[dict]) -> None:
        if not entries:
            return
        project_key, session_id, subpath = _normalize_key(key)
        now_ms = int(time.time() * 1000)
        now_dt = utc_now()

        async with self._session_factory() as session:
            # take seq atomically inside the transaction
            seq_start_row = await session.execute(
                select(func.coalesce(func.max(AgentSessionEntry.seq), -1) + 1).where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.session_id == session_id,
                    AgentSessionEntry.subpath == subpath,
                )
            )
            seq_start = int(seq_start_row.scalar_one())

            rows = [
                {
                    "project_key": project_key,
                    "session_id": session_id,
                    "subpath": subpath,
                    "seq": seq_start + i,
                    "uuid": _entry_uuid(entry),
                    "entry_type": _entry_type(entry),
                    "payload": entry,
                    "mtime_ms": now_ms,
                    "user_id": self._user_id,
                    "created_at": now_dt,
                    "updated_at": now_dt,
                }
                for i, entry in enumerate(entries)
            ]

            await self._insert_entries(session, rows)
            await session.commit()

        logger.info(
            "append: session=%s subpath=%s entries=%d seq_start=%d",
            session_id, subpath or "<main>", len(entries), seq_start,
        )

    async def _insert_entries(self, session, rows: list[dict]) -> None:
        """Dialect-aware INSERT ... ON CONFLICT (uuid) DO NOTHING."""
        bind = session.bind
        dialect = bind.dialect.name if bind is not None else "sqlite"

        if dialect == "postgresql":
            stmt = pg_insert(AgentSessionEntry).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["project_key", "session_id", "subpath", "uuid"]
            )
        else:
            stmt = sqlite_insert(AgentSessionEntry).values(rows)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["project_key", "session_id", "subpath", "uuid"]
            )
        await session.execute(stmt)

    async def load(self, key: dict) -> list[dict] | None:
        project_key, session_id, subpath = _normalize_key(key)
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEntry.payload)
                .where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.session_id == session_id,
                    AgentSessionEntry.subpath == subpath,
                )
                .order_by(AgentSessionEntry.seq)
            )
            payloads = [row[0] for row in result.all()]
        if not payloads:
            return None
        return payloads
```

- [ ] **Step 5: 跑测试**

Run: `uv run python -m pytest tests/agent_session_store/test_store_append.py tests/agent_session_store/test_store_load.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add lib/agent_session_store/store.py tests/agent_session_store/test_store_append.py tests/agent_session_store/test_store_load.py
git commit -m "feat(session-store): DbSessionStore append + load with uuid dedup"
```

---

## Task 5：append 并发：seq 取号竞争与重试

**Files:**
- Modify: `lib/agent_session_store/store.py`
- Create: `tests/agent_session_store/test_store_concurrency.py`

- [ ] **Step 1: 写并发失败测试**

```python
# tests/agent_session_store/test_store_concurrency.py
"""Concurrent appends to the same session must serialize cleanly."""
from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from lib.agent_session_store import AgentSessionEntry
from lib.agent_session_store.store import DbSessionStore


@pytest.mark.asyncio
async def test_concurrent_append_no_seq_collision(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    key = {"project_key": "proj", "session_id": "sess"}

    async def push(i: int):
        await store.append(key, [{"type": "user", "uuid": f"u-{i}", "n": i}])

    await asyncio.gather(*(push(i) for i in range(20)))

    async with session_factory() as session:
        rows = (await session.execute(
            select(AgentSessionEntry).order_by(AgentSessionEntry.seq)
        )).scalars().all()

    assert len(rows) == 20
    assert [r.seq for r in rows] == list(range(20))
    assert {r.uuid for r in rows} == {f"u-{i}" for i in range(20)}
```

- [ ] **Step 2: 跑测试看失败**

Run: `uv run python -m pytest tests/agent_session_store/test_store_concurrency.py -v`

Expected: 在 SQLite + 单写入器场景大概率通过（隐式 BEGIN IMMEDIATE 串行化）。
但如果失败，会看到 `IntegrityError`（PK 冲突）或行数不足 20。

- [ ] **Step 3: 加重试逻辑（应对 PK 冲突）**

修改 `lib/agent_session_store/store.py` 的 `append` 方法，把「取号 + 插入」包进重试循环，PK 冲突时重读 max(seq) 重试，最多 5 次：

```python
# 在 lib/agent_session_store/store.py 顶部
from sqlalchemy.exc import IntegrityError

_MAX_APPEND_RETRY = 5

# 重写 append 方法
async def append(self, key: dict, entries: list[dict]) -> None:
    if not entries:
        return
    project_key, session_id, subpath = _normalize_key(key)
    now_ms = int(time.time() * 1000)

    for attempt in range(_MAX_APPEND_RETRY):
        try:
            await self._append_once(
                project_key, session_id, subpath, entries, now_ms
            )
            return
        except IntegrityError as exc:
            # PK conflict on (project_key, session_id, subpath, seq) means
            # a concurrent append took our seq slot. Retry with a fresh max.
            if attempt == _MAX_APPEND_RETRY - 1:
                logger.error(
                    "append: PK conflict after %d retries session=%s",
                    _MAX_APPEND_RETRY, session_id,
                )
                raise
            logger.warning(
                "append: seq race retry=%d session=%s err=%s",
                attempt + 1, session_id, exc,
            )

async def _append_once(
    self,
    project_key: str,
    session_id: str,
    subpath: str,
    entries: list[dict],
    now_ms: int,
) -> None:
    now_dt = utc_now()
    async with self._session_factory() as session:
        seq_start_row = await session.execute(
            select(func.coalesce(func.max(AgentSessionEntry.seq), -1) + 1).where(
                AgentSessionEntry.project_key == project_key,
                AgentSessionEntry.session_id == session_id,
                AgentSessionEntry.subpath == subpath,
            )
        )
        seq_start = int(seq_start_row.scalar_one())

        rows = [
            {
                "project_key": project_key,
                "session_id": session_id,
                "subpath": subpath,
                "seq": seq_start + i,
                "uuid": _entry_uuid(entry),
                "entry_type": _entry_type(entry),
                "payload": entry,
                "mtime_ms": now_ms,
                "user_id": self._user_id,
                "created_at": now_dt,
                "updated_at": now_dt,
            }
            for i, entry in enumerate(entries)
        ]

        await self._insert_entries(session, rows)
        await session.commit()

    logger.info(
        "append: session=%s subpath=%s entries=%d seq_start=%d",
        session_id, subpath or "<main>", len(entries), seq_start,
    )
```

- [ ] **Step 4: 跑并发测试**

Run: `uv run python -m pytest tests/agent_session_store/test_store_concurrency.py -v`
Expected: 1 passed

- [ ] **Step 5: 回归 append/load 测试**

Run: `uv run python -m pytest tests/agent_session_store/test_store_append.py tests/agent_session_store/test_store_load.py -v`
Expected: 7 passed

- [ ] **Step 6: Commit**

```bash
git add lib/agent_session_store/store.py tests/agent_session_store/test_store_concurrency.py
git commit -m "feat(session-store): retry append on seq PK race"
```

---

## Task 6：append 内 fold summary（list_session_summaries 快路径）

**Files:**
- Modify: `lib/agent_session_store/store.py`
- Create: `tests/agent_session_store/test_store_summary.py`

- [ ] **Step 1: 写 summary 失败测试**

```python
# tests/agent_session_store/test_store_summary.py
"""append() must maintain agent_session_summaries via fold_session_summary."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from lib.agent_session_store import AgentSessionSummary
from lib.agent_session_store.store import DbSessionStore


KEY = {"project_key": "proj", "session_id": "sess"}


@pytest.mark.asyncio
async def test_summary_created_on_first_append(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append(KEY, [{"type": "user", "uuid": "u-1", "timestamp": "t"}])

    async with session_factory() as session:
        rows = (await session.execute(select(AgentSessionSummary))).scalars().all()
    assert len(rows) == 1
    assert rows[0].project_key == "proj"
    assert rows[0].session_id == "sess"
    assert rows[0].mtime_ms > 0
    assert isinstance(rows[0].data, dict)


@pytest.mark.asyncio
async def test_summary_mtime_monotonic_across_appends(session_factory):
    import asyncio

    store = DbSessionStore(session_factory, user_id="u1")
    await store.append(KEY, [{"type": "user", "uuid": "a", "timestamp": "t1"}])

    async with session_factory() as session:
        first = (await session.execute(select(AgentSessionSummary))).scalar_one()
    first_mtime = first.mtime_ms

    await asyncio.sleep(0.01)  # ensure clock advances at ms granularity
    await store.append(KEY, [{"type": "user", "uuid": "b", "timestamp": "t2"}])

    async with session_factory() as session:
        second = (await session.execute(select(AgentSessionSummary))).scalar_one()
    assert second.mtime_ms >= first_mtime


@pytest.mark.asyncio
async def test_summary_skipped_for_subpath(session_factory):
    """SDK 协议：subagent transcripts (subpath != '') 不参与 main summary fold。"""
    store = DbSessionStore(session_factory, user_id="u1")
    sub_key = {"project_key": "proj", "session_id": "sess", "subpath": "subagents/a"}
    await store.append(sub_key, [{"type": "user", "uuid": "x"}])

    async with session_factory() as session:
        rows = (await session.execute(select(AgentSessionSummary))).scalars().all()
    assert rows == []
```

- [ ] **Step 2: 跑测试看失败**

Run: `uv run python -m pytest tests/agent_session_store/test_store_summary.py -v`
Expected: 3 failed (summaries 表为空)

- [ ] **Step 3: 在 _append_once 末尾追加 summary fold**

修改 `lib/agent_session_store/store.py`：

```python
# 顶部 import
from claude_agent_sdk import fold_session_summary

# 修改 _append_once：在 await session.commit() 之前 / 之后均可，
# 推荐放进同一事务避免 commit 半截。把 commit 后移：

async def _append_once(
    self,
    project_key: str,
    session_id: str,
    subpath: str,
    entries: list[dict],
    now_ms: int,
) -> None:
    now_dt = utc_now()
    async with self._session_factory() as session:
        seq_start_row = await session.execute(
            select(func.coalesce(func.max(AgentSessionEntry.seq), -1) + 1).where(
                AgentSessionEntry.project_key == project_key,
                AgentSessionEntry.session_id == session_id,
                AgentSessionEntry.subpath == subpath,
            )
        )
        seq_start = int(seq_start_row.scalar_one())

        rows = [
            {
                "project_key": project_key,
                "session_id": session_id,
                "subpath": subpath,
                "seq": seq_start + i,
                "uuid": _entry_uuid(entry),
                "entry_type": _entry_type(entry),
                "payload": entry,
                "mtime_ms": now_ms,
                "user_id": self._user_id,
                "created_at": now_dt,
                "updated_at": now_dt,
            }
            for i, entry in enumerate(entries)
        ]

        await self._insert_entries(session, rows)

        # Maintain per-session summary for list_session_summaries fast path.
        # Per SDK protocol: skip for subagent transcripts (subpath != "").
        if subpath == "":
            await self._fold_summary_locked(
                session, project_key, session_id, entries, now_ms, now_dt
            )

        await session.commit()

    logger.info(
        "append: session=%s subpath=%s entries=%d seq_start=%d",
        session_id, subpath or "<main>", len(entries), seq_start,
    )


async def _fold_summary_locked(
    self,
    session,
    project_key: str,
    session_id: str,
    entries: list[dict],
    now_ms: int,
    now_dt,
) -> None:
    """Read-fold-write the per-session summary inside the active transaction.

    Acquires a row lock (PG: SELECT ... FOR UPDATE; SQLite: BEGIN IMMEDIATE
    has already serialized writers) so concurrent appends can't lose folds.
    """
    bind = session.bind
    dialect = bind.dialect.name if bind is not None else "sqlite"

    stmt = select(AgentSessionSummary).where(
        AgentSessionSummary.project_key == project_key,
        AgentSessionSummary.session_id == session_id,
    )
    if dialect == "postgresql":
        stmt = stmt.with_for_update()
    prev_row = (await session.execute(stmt)).scalar_one_or_none()

    prev: dict[str, Any] | None
    if prev_row is None:
        prev = None
    else:
        prev = {"session_id": session_id, "mtime": prev_row.mtime_ms, "data": prev_row.data}

    folded = fold_session_summary(prev, entries)
    new_data = folded["data"] if folded else {}

    if prev_row is None:
        session.add(
            AgentSessionSummary(
                project_key=project_key,
                session_id=session_id,
                mtime_ms=now_ms,
                data=new_data,
                user_id=self._user_id,
                created_at=now_dt,
                updated_at=now_dt,
            )
        )
    else:
        prev_row.mtime_ms = now_ms
        prev_row.data = new_data
        prev_row.updated_at = now_dt
```

> **Note**: `fold_session_summary` 的精确入参/返回结构按 SDK 公开签名调用即可
> （`from claude_agent_sdk import fold_session_summary`）。如果在实现时发现签名与
> 上述假设不符，请按 SDK docstring 调整 `prev` / `folded` 结构 — 协议已规定 `data`
> 字段不透明，store 不要解释。

- [ ] **Step 4: 跑测试**

Run: `uv run python -m pytest tests/agent_session_store/test_store_summary.py -v`
Expected: 3 passed

- [ ] **Step 5: 回归之前所有 store 测试**

Run: `uv run python -m pytest tests/agent_session_store/ -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add lib/agent_session_store/store.py tests/agent_session_store/test_store_summary.py
git commit -m "feat(session-store): maintain per-session summary via fold_session_summary"
```

---

## Task 7：可选方法 list_sessions / list_session_summaries / delete / list_subkeys

**Files:**
- Modify: `lib/agent_session_store/store.py`
- Create: `tests/agent_session_store/test_store_optional.py`

- [ ] **Step 1: 写四个可选方法的测试**

```python
# tests/agent_session_store/test_store_optional.py
"""Optional SessionStore methods: list_sessions / list_session_summaries / delete / list_subkeys."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from lib.agent_session_store import AgentSessionEntry, AgentSessionSummary
from lib.agent_session_store.store import DbSessionStore


@pytest.mark.asyncio
async def test_list_sessions_returns_unique_session_ids_with_mtime(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append({"project_key": "p", "session_id": "s2"}, [{"type": "user", "uuid": "b"}])
    await store.append({"project_key": "other", "session_id": "s3"}, [{"type": "user", "uuid": "c"}])

    items = await store.list_sessions("p")
    sids = sorted(x["session_id"] for x in items)
    assert sids == ["s1", "s2"]
    for x in items:
        assert isinstance(x["mtime"], int)
        assert x["mtime"] > 0


@pytest.mark.asyncio
async def test_list_sessions_excludes_subagent_subpaths(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append(
        {"project_key": "p", "session_id": "s1", "subpath": "subagents/x"},
        [{"type": "user", "uuid": "b"}],
    )
    items = await store.list_sessions("p")
    assert [x["session_id"] for x in items] == ["s1"]  # not duplicated


@pytest.mark.asyncio
async def test_list_session_summaries(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append({"project_key": "p", "session_id": "s2"}, [{"type": "user", "uuid": "b"}])

    summaries = await store.list_session_summaries("p")
    assert sorted(s["session_id"] for s in summaries) == ["s1", "s2"]
    for s in summaries:
        assert isinstance(s["mtime"], int)
        assert isinstance(s["data"], dict)


@pytest.mark.asyncio
async def test_delete_main_cascades_subpaths(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append(
        {"project_key": "p", "session_id": "s1", "subpath": "subagents/x"},
        [{"type": "user", "uuid": "b"}],
    )
    await store.delete({"project_key": "p", "session_id": "s1"})

    async with session_factory() as session:
        rows = (await session.execute(select(AgentSessionEntry))).scalars().all()
        sums = (await session.execute(select(AgentSessionSummary))).scalars().all()
    assert rows == []
    assert sums == []


@pytest.mark.asyncio
async def test_delete_subpath_targets_only_that_subpath(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append(
        {"project_key": "p", "session_id": "s1", "subpath": "subagents/x"},
        [{"type": "user", "uuid": "b"}],
    )
    await store.delete({"project_key": "p", "session_id": "s1", "subpath": "subagents/x"})

    main_load = await store.load({"project_key": "p", "session_id": "s1"})
    sub_load = await store.load({"project_key": "p", "session_id": "s1", "subpath": "subagents/x"})
    assert main_load is not None and len(main_load) == 1
    assert sub_load is None


@pytest.mark.asyncio
async def test_list_subkeys(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    base = {"project_key": "p", "session_id": "s1"}
    await store.append(base, [{"type": "user", "uuid": "main"}])
    await store.append({**base, "subpath": "subagents/a"}, [{"type": "user", "uuid": "x"}])
    await store.append({**base, "subpath": "subagents/b"}, [{"type": "user", "uuid": "y"}])

    keys = await store.list_subkeys(base)
    assert sorted(keys) == ["subagents/a", "subagents/b"]
```

- [ ] **Step 2: 跑测试看失败**

Run: `uv run python -m pytest tests/agent_session_store/test_store_optional.py -v`
Expected: 6 errors — `AttributeError: 'DbSessionStore' object has no attribute 'list_sessions'`

- [ ] **Step 3: 实现四个可选方法**

在 `lib/agent_session_store/store.py` 末尾追加：

```python
    # --- optional: list_sessions / list_session_summaries -------------------

    async def list_sessions(self, project_key: str) -> list[dict]:
        async with self._session_factory() as session:
            stmt = (
                select(
                    AgentSessionEntry.session_id,
                    func.max(AgentSessionEntry.mtime_ms).label("mtime"),
                )
                .where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.subpath == "",
                )
                .group_by(AgentSessionEntry.session_id)
            )
            result = await session.execute(stmt)
            return [
                {"session_id": r.session_id, "mtime": int(r.mtime)}
                for r in result.all()
            ]

    async def list_session_summaries(self, project_key: str) -> list[dict]:
        async with self._session_factory() as session:
            stmt = select(AgentSessionSummary).where(
                AgentSessionSummary.project_key == project_key,
            )
            result = await session.execute(stmt)
            return [
                {"session_id": r.session_id, "mtime": int(r.mtime_ms), "data": r.data}
                for r in result.scalars().all()
            ]

    # --- optional: delete + list_subkeys -----------------------------------

    async def delete(self, key: dict) -> None:
        project_key, session_id, subpath = _normalize_key(key)
        async with self._session_factory() as session:
            entry_stmt = sa_delete(AgentSessionEntry).where(
                AgentSessionEntry.project_key == project_key,
                AgentSessionEntry.session_id == session_id,
            )
            if "subpath" in key and key["subpath"] != "":
                entry_stmt = entry_stmt.where(AgentSessionEntry.subpath == subpath)
            entry_result = await session.execute(entry_stmt)

            sum_rows = 0
            if subpath == "" and "subpath" not in key:
                # main delete cascades to summary
                sum_result = await session.execute(
                    sa_delete(AgentSessionSummary).where(
                        AgentSessionSummary.project_key == project_key,
                        AgentSessionSummary.session_id == session_id,
                    )
                )
                sum_rows = sum_result.rowcount or 0

            await session.commit()
        logger.info(
            "delete: session=%s subpath=%s entries=%d summaries=%d",
            session_id, subpath or "<main>", entry_result.rowcount or 0, sum_rows,
        )

    async def list_subkeys(self, key: dict) -> list[str]:
        project_key, session_id, _subpath = _normalize_key(key)
        async with self._session_factory() as session:
            result = await session.execute(
                select(AgentSessionEntry.subpath)
                .where(
                    AgentSessionEntry.project_key == project_key,
                    AgentSessionEntry.session_id == session_id,
                    AgentSessionEntry.subpath != "",
                )
                .distinct()
            )
            return [row[0] for row in result.all()]
```

- [ ] **Step 4: 跑测试**

Run: `uv run python -m pytest tests/agent_session_store/test_store_optional.py -v`
Expected: 6 passed

- [ ] **Step 5: 回归全套 store 测试**

Run: `uv run python -m pytest tests/agent_session_store/ -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add lib/agent_session_store/store.py tests/agent_session_store/test_store_optional.py
git commit -m "feat(session-store): list_sessions / list_session_summaries / delete / list_subkeys"
```

---

## Task 8：跑 SDK 官方 conformance 测试

**Files:**
- Create: `tests/agent_session_store/test_conformance.py`

- [ ] **Step 1: 写 conformance 测试**

```python
# tests/agent_session_store/test_conformance.py
"""Run the SDK's official 14-contract SessionStore conformance suite."""
from __future__ import annotations

import pytest
from claude_agent_sdk.testing import run_session_store_conformance

from lib.agent_session_store.store import DbSessionStore


@pytest.mark.asyncio
async def test_db_session_store_passes_sdk_conformance(session_factory):
    """DbSessionStore must satisfy all required + optional SessionStore contracts."""

    def make_store():
        # `make_store` is invoked once per contract for isolation; we pass the
        # SAME in-memory factory so all contracts see the same DB. Contracts
        # use distinct project_key/session_id values so isolation is fine.
        return DbSessionStore(session_factory, user_id="conformance")

    await run_session_store_conformance(make_store)
```

- [ ] **Step 2: 跑测试**

Run: `uv run python -m pytest tests/agent_session_store/test_conformance.py -v`

Expected: 1 passed (or fail with specific contract id; fix the contract that fails as a follow-up step in this same task)

If a contract fails, read the SDK assertion message, identify the offending method, fix it in `lib/agent_session_store/store.py`, re-run until green.

- [ ] **Step 3: 回归**

Run: `uv run python -m pytest tests/agent_session_store/ -v`
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add tests/agent_session_store/test_conformance.py
git commit -m "test(session-store): pass SDK official conformance suite"
```

---

## Task 9：import_local 启动迁移模块

**Files:**
- Create: `lib/agent_session_store/import_local.py`
- Create: `tests/agent_session_store/test_import_local.py`

- [ ] **Step 1: 写迁移失败测试**

```python
# tests/agent_session_store/test_import_local.py
"""Startup hook: migrate local SDK jsonl transcripts into store."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from claude_agent_sdk import project_key_for_directory

from lib.agent_session_store.store import DbSessionStore


def _write_fake_local_transcript(project_cwd: Path, session_id: str, sdk_root: Path):
    """Mimic the SDK on-disk layout: <CLAUDE_CONFIG_DIR>/projects/<sanitized>/<session_id>.jsonl."""
    sanitized = project_key_for_directory(str(project_cwd))
    sdk_dir = sdk_root / "projects" / sanitized
    sdk_dir.mkdir(parents=True, exist_ok=True)
    jsonl = sdk_dir / f"{session_id}.jsonl"
    jsonl.write_text(
        "\n".join(
            json.dumps(e)
            for e in [
                {"type": "user", "uuid": f"{session_id}-u1", "timestamp": "2026-05-01T00:00:00Z",
                 "message": {"content": "hi"}},
                {"type": "assistant", "uuid": f"{session_id}-u2", "timestamp": "2026-05-01T00:00:01Z",
                 "message": {"content": "hello"}},
            ]
        )
        + "\n",
        encoding="utf-8",
    )


@pytest.fixture
def fake_sdk_home(tmp_path: Path, monkeypatch):
    """Redirect SDK to a tmp config dir for the duration of one test."""
    sdk_home = tmp_path / "claude_home"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(sdk_home))
    return sdk_home


@pytest.mark.asyncio
async def test_migrate_imports_local_jsonl(tmp_path, fake_sdk_home, session_factory):
    from lib.agent_session_store.import_local import migrate_local_transcripts_to_store

    projects_root = tmp_path / "projects"
    proj = projects_root / "demo"
    proj.mkdir(parents=True)
    sid = "00000000-0000-0000-0000-0000000000aa"
    _write_fake_local_transcript(proj, sid, fake_sdk_home)

    store = DbSessionStore(session_factory, user_id="u1")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    stats = await migrate_local_transcripts_to_store(
        store, projects_root=projects_root, data_dir=data_dir
    )

    assert stats["imported"] == 1
    assert stats["skipped"] == 0
    assert stats["failed"] == 0

    # Verify the entries actually landed in the store
    loaded = await store.load(
        {"project_key": project_key_for_directory(str(proj)), "session_id": sid}
    )
    assert loaded is not None and len(loaded) == 2
    assert (data_dir / ".session_store_migration_done").exists()


@pytest.mark.asyncio
async def test_migrate_is_idempotent_via_marker(tmp_path, fake_sdk_home, session_factory):
    from lib.agent_session_store.import_local import migrate_local_transcripts_to_store

    projects_root = tmp_path / "projects"
    proj = projects_root / "demo"
    proj.mkdir(parents=True)
    sid = "00000000-0000-0000-0000-0000000000bb"
    _write_fake_local_transcript(proj, sid, fake_sdk_home)
    store = DbSessionStore(session_factory, user_id="u1")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    s1 = await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)
    s2 = await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)

    assert s1["imported"] == 1
    # Second run hits marker fast-path
    assert s2["imported"] == 0 and s2.get("skipped_via_marker") is True


@pytest.mark.asyncio
async def test_migrate_skips_already_in_store_when_marker_missing(
    tmp_path, fake_sdk_home, session_factory,
):
    """Marker误删后重启应通过 store.load 探测跳过已迁会话。"""
    from lib.agent_session_store.import_local import migrate_local_transcripts_to_store

    projects_root = tmp_path / "projects"
    proj = projects_root / "demo"
    proj.mkdir(parents=True)
    sid = "00000000-0000-0000-0000-0000000000cc"
    _write_fake_local_transcript(proj, sid, fake_sdk_home)
    store = DbSessionStore(session_factory, user_id="u1")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)
    (data_dir / ".session_store_migration_done").unlink()

    s2 = await migrate_local_transcripts_to_store(store, projects_root=projects_root, data_dir=data_dir)
    assert s2["imported"] == 0
    assert s2["skipped"] == 1
    assert s2["failed"] == 0


@pytest.mark.asyncio
async def test_migrate_zero_data_user(tmp_path, fake_sdk_home, session_factory):
    """No projects + no SDK dir → marker still written, migration succeeds."""
    from lib.agent_session_store.import_local import migrate_local_transcripts_to_store

    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    store = DbSessionStore(session_factory, user_id="u1")
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    stats = await migrate_local_transcripts_to_store(
        store, projects_root=projects_root, data_dir=data_dir
    )
    assert stats == {"imported": 0, "skipped": 0, "failed": 0}
    assert (data_dir / ".session_store_migration_done").exists()
```

- [ ] **Step 2: 跑测试看失败**

Run: `uv run python -m pytest tests/agent_session_store/test_import_local.py -v`
Expected: ImportError — `cannot import name 'migrate_local_transcripts_to_store'`

- [ ] **Step 3: 实现 import_local 模块**

```python
# lib/agent_session_store/import_local.py
"""Startup hook: import local SDK jsonl transcripts into DbSessionStore.

Uses only SDK public APIs:
- claude_agent_sdk.list_sessions(directory=cwd)
- claude_agent_sdk.import_session_to_store(session_id, store, *, directory=cwd)
- claude_agent_sdk.project_key_for_directory(cwd)

so docker / CLAUDE_CONFIG_DIR / git-worktree path resolution is delegated
to the SDK and stays correct as SDK evolves.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    import_session_to_store,
    list_sessions,
    project_key_for_directory,
)

from lib.agent_session_store.store import DbSessionStore

logger = logging.getLogger("arcreel.session_store.import")

MARKER_FILENAME = ".session_store_migration_done"


async def migrate_local_transcripts_to_store(
    store: DbSessionStore,
    *,
    projects_root: Path,
    data_dir: Path,
) -> dict[str, Any]:
    """Replay all on-disk SDK transcripts into ``store``.

    Idempotent via:
      1. ``data_dir / MARKER_FILENAME``  — fast-path skip on subsequent boots
      2. ``store.load(key)``            — fallback when marker is absent

    Single-process safe; for multi-worker uvicorn, an outer config-table lock
    must wrap this call (see server.app lifespan).

    Returns stats dict: ``{imported, skipped, failed}`` and (when marker hit)
    ``skipped_via_marker: True``.
    """
    marker = data_dir / MARKER_FILENAME
    if marker.exists():
        logger.info("transcript migration: marker present, skipping")
        return {"imported": 0, "skipped": 0, "failed": 0, "skipped_via_marker": True}

    imported = skipped = failed = 0

    if projects_root.exists():
        for project_cwd in sorted(projects_root.iterdir()):
            if not project_cwd.is_dir() or project_cwd.name.startswith("."):
                continue
            try:
                sessions = list_sessions(directory=str(project_cwd))
            except Exception:
                logger.exception("list_sessions failed for %s", project_cwd)
                continue

            project_key = project_key_for_directory(str(project_cwd))

            for info in sessions:
                key = {"project_key": project_key, "session_id": info.session_id}
                try:
                    if await store.load(key) is not None:
                        skipped += 1
                        continue
                    await import_session_to_store(
                        info.session_id, store, directory=str(project_cwd)
                    )
                    imported += 1
                except Exception:
                    logger.exception(
                        "failed to migrate session=%s cwd=%s",
                        info.session_id, project_cwd,
                    )
                    failed += 1

    logger.info(
        "transcript migration: imported=%d skipped=%d failed=%d",
        imported, skipped, failed,
    )

    # Always write marker — even with zero data — so we don't rescan next boot.
    marker.write_text(
        json.dumps({"imported": imported, "skipped": skipped, "failed": failed}),
        encoding="utf-8",
    )

    return {"imported": imported, "skipped": skipped, "failed": failed}
```

- [ ] **Step 4: 跑测试**

Run: `uv run python -m pytest tests/agent_session_store/test_import_local.py -v`
Expected: 4 passed

- [ ] **Step 5: 全套回归**

Run: `uv run python -m pytest tests/agent_session_store/ -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add lib/agent_session_store/import_local.py tests/agent_session_store/test_import_local.py
git commit -m "feat(session-store): startup migration via SDK public APIs"
```

---

## Task 10：session_manager 注入 store + 环境变量回滚

**Files:**
- Modify: `server/agent_runtime/session_manager.py`
- Create: `tests/agent_runtime/test_session_manager_store_injection.py`

- [ ] **Step 1: 写测试**

```python
# tests/agent_runtime/test_session_manager_store_injection.py
"""SessionManager._build_session_store reads ARCREEL_SDK_SESSION_STORE."""
from __future__ import annotations

import os

import pytest

from lib.agent_session_store.store import DbSessionStore
from server.agent_runtime.session_manager import SessionManager


@pytest.mark.asyncio
async def test_store_enabled_by_default(monkeypatch, session_factory, tmp_path):
    monkeypatch.delenv("ARCREEL_SDK_SESSION_STORE", raising=False)
    sm = SessionManager(data_dir=tmp_path, projects_root=tmp_path)
    sm._session_factory = session_factory   # test seam
    store = sm._build_session_store()
    assert isinstance(store, DbSessionStore)


@pytest.mark.asyncio
async def test_store_off_returns_none(monkeypatch, session_factory, tmp_path):
    monkeypatch.setenv("ARCREEL_SDK_SESSION_STORE", "off")
    sm = SessionManager(data_dir=tmp_path, projects_root=tmp_path)
    sm._session_factory = session_factory
    store = sm._build_session_store()
    assert store is None
```

> Note: existing SessionManager constructor signature may differ; adjust the
> instantiation lines above to match (e.g. it might require additional fixture
> arguments — see existing tests in `tests/test_session_manager_*.py` for the
> minimal valid construction pattern in this codebase).

- [ ] **Step 2: 跑测试看失败**

Run: `uv run python -m pytest tests/agent_runtime/test_session_manager_store_injection.py -v`
Expected: FAIL — `_build_session_store` does not exist

- [ ] **Step 3: 加 _build_session_store 到 session_manager.py**

在 `SessionManager` 类中加：

```python
# top of file
import os
from lib.agent_session_store.store import DbSessionStore
from lib.db.engine import async_session_factory
from lib.db.base import DEFAULT_USER_ID

# in SessionManager class
def _build_session_store(self):
    """Create a per-user DbSessionStore, or None if store mode is disabled."""
    mode = os.getenv("ARCREEL_SDK_SESSION_STORE", "db")
    if mode == "off":
        return None
    factory = getattr(self, "_session_factory", None) or async_session_factory
    user_id = getattr(self, "_user_id", DEFAULT_USER_ID)
    return DbSessionStore(factory, user_id=user_id)
```

- [ ] **Step 4: 在 _build_options 中注入 store**

找到 `_build_options` 方法返回 `ClaudeAgentOptions(...)` 的部分（约 533 行），加 `session_store=...`：

```python
# 在 _build_options 顶部
session_store = self._build_session_store()

# 在 ClaudeAgentOptions(...) 调用里加
return ClaudeAgentOptions(
    cwd=str(project_cwd),
    setting_sources=self.DEFAULT_SETTING_SOURCES,
    allowed_tools=self.DEFAULT_ALLOWED_TOOLS,
    max_turns=self.max_turns,
    system_prompt=SystemPromptPreset(...),
    include_partial_messages=True,
    resume=resume_id,
    can_use_tool=can_use_tool,
    hooks=hooks,
    session_store=session_store,   # NEW
)
```

- [ ] **Step 5: 跑测试 + 回归 session_manager 现有测试**

Run: `uv run python -m pytest tests/agent_runtime/test_session_manager_store_injection.py tests/test_session_manager_more.py tests/test_session_manager_project_scope.py -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add server/agent_runtime/session_manager.py tests/agent_runtime/test_session_manager_store_injection.py
git commit -m "feat(agent-runtime): inject DbSessionStore via env-gated factory"
```

---

## Task 11：sdk_transcript_adapter 改用 *_from_store

**Files:**
- Modify: `server/agent_runtime/sdk_transcript_adapter.py`
- Modify: `tests/test_sdk_transcript_adapter.py`

- [ ] **Step 1: 阅读现有测试**

Run: `cat tests/test_sdk_transcript_adapter.py`

了解现有断言形式，避免破坏。

- [ ] **Step 2: 重写 adapter 走 store 路径，保留旧路径作回退**

```python
# server/agent_runtime/sdk_transcript_adapter.py
"""SDK-based transcript adapter using public SessionStore helpers."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from claude_agent_sdk import get_session_messages_from_store

from lib.agent_session_store import make_project_key
from lib.agent_session_store.store import DbSessionStore

logger = logging.getLogger(__name__)


class SdkTranscriptAdapter:
    """Read conversation history via SDK's SessionStore-backed helper.

    Replaces the previous JSONL parsing + private ``_read_session_file`` shim.
    SessionMessage timestamps come straight from the store entries — no need
    to backfill from raw transcript files.
    """

    def __init__(
        self,
        store: DbSessionStore | None,
        project_cwd: Path | str,
    ) -> None:
        self._store = store
        self._project_cwd = project_cwd

    def read_raw_messages(self, sdk_session_id: str | None) -> list[dict[str, Any]]:
        if not sdk_session_id:
            return []
        if self._store is None:
            # Roll-back path: ARCREEL_SDK_SESSION_STORE=off — fall through to
            # SDK's filesystem reader. Kept until store path soaks for 1-2
            # releases per design doc §4.4.
            return self._read_via_local_jsonl(sdk_session_id)

        try:
            import asyncio
            key = {
                "project_key": make_project_key(self._project_cwd),
                "session_id": sdk_session_id,
            }
            messages = asyncio.get_event_loop().run_until_complete(
                get_session_messages_from_store(self._store, key)
            )
        except Exception:
            logger.warning(
                "Failed to read SDK session %s via store", sdk_session_id, exc_info=True,
            )
            return []

        return [self._adapt(msg) for msg in (messages or [])]

    def exists(self, sdk_session_id: str | None) -> bool:
        if not sdk_session_id or self._store is None:
            return self._exists_via_local_jsonl(sdk_session_id)
        return bool(self.read_raw_messages(sdk_session_id))

    def _adapt(self, msg: Any) -> dict[str, Any]:
        message_data = getattr(msg, "message", {}) or {}
        if isinstance(message_data, dict):
            content = message_data.get("content", "")
        else:
            content = ""
        result: dict[str, Any] = {
            "type": getattr(msg, "type", ""),
            "content": content,
            "uuid": getattr(msg, "uuid", None),
            "timestamp": getattr(msg, "timestamp", None),
        }
        parent_tool_use_id = getattr(msg, "parent_tool_use_id", None)
        if parent_tool_use_id:
            result["parent_tool_use_id"] = parent_tool_use_id
        return result

    # --- legacy fallback (ARCREEL_SDK_SESSION_STORE=off) -------------------

    def _read_via_local_jsonl(self, sdk_session_id: str) -> list[dict[str, Any]]:
        """Use SDK public get_session_messages (filesystem) as fallback."""
        from claude_agent_sdk import get_session_messages
        try:
            messages = get_session_messages(sdk_session_id)
        except Exception:
            logger.warning("legacy read failed: session=%s", sdk_session_id, exc_info=True)
            return []
        return [self._adapt(m) for m in messages]

    def _exists_via_local_jsonl(self, sdk_session_id: str | None) -> bool:
        if not sdk_session_id:
            return False
        from claude_agent_sdk import get_session_messages
        try:
            return len(get_session_messages(sdk_session_id, limit=1)) > 0
        except Exception:
            return False
```

> **Note:** the existing adapter is created without a store. Calling code must
> now pass the store (or `None`) explicitly. Update construction sites in
> `session_manager.py` (and any direct test instantiation) accordingly.

- [ ] **Step 3: 修复 SdkTranscriptAdapter 的所有 caller**

Run: `grep -rn "SdkTranscriptAdapter(" server/ tests/ 2>/dev/null`

对每个 caller 加上 `store` 与 `project_cwd` 参数。如果 caller 在 `session_manager.py` 中，使用 `self._build_session_store()` 与已有的 `project_cwd`。

- [ ] **Step 4: 跑现有 adapter 测试 + 回归**

Run: `uv run python -m pytest tests/test_sdk_transcript_adapter.py tests/test_session_actor.py tests/test_session_lifecycle.py -v`

Expected: 必要时根据 store fixture 调整测试，保持业务断言不变。所有 pass。

- [ ] **Step 5: 验证私有 import 已删**

Run: `grep -n "_read_session_file\|_internal\.sessions" server/agent_runtime/sdk_transcript_adapter.py`
Expected: 无输出

- [ ] **Step 6: Commit**

```bash
git add server/agent_runtime/sdk_transcript_adapter.py tests/test_sdk_transcript_adapter.py
git commit -m "refactor(agent-runtime): adapter uses get_session_messages_from_store"
```

---

## Task 12：service.py list/delete 改用 *_via_store

**Files:**
- Modify: `server/agent_runtime/service.py`
- Add tests as needed

- [ ] **Step 1: 阅读现有 service.py 中 list/delete 调用**

Run: `grep -n "sdk_list_sessions\|sdk_delete_session\|tag_session\|list_sessions\|delete_session" server/agent_runtime/service.py`

记录每个调用点的上下文（用什么 cwd / sdk_session_id）。

- [ ] **Step 2: 加 store 依赖 + 改造 list 调用**

```python
# server/agent_runtime/service.py 顶部
from claude_agent_sdk import (
    delete_session_via_store,
    list_sessions_from_store,
)
from lib.agent_session_store import make_project_key
```

把原本调用 `sdk_list_sessions(...)` 的位置改为：

```python
# 假设这里已能拿到 project_cwd 与 store
project_key = make_project_key(project_cwd)
items = await list_sessions_from_store(store, project_key)
```

`store` 来源：通过 `assistant_service.session_manager._build_session_store()` 获取，
或在 service 层初始化时持有一份。具体的 wiring 与 session_manager 的 store 实例
保持一致（同一进程同一 user 共用即可）。

- [ ] **Step 3: 改造 delete 调用**

```python
key = {
    "project_key": make_project_key(project_cwd),
    "session_id": sdk_session_id,
}
await delete_session_via_store(store, key)
```

`tag_session` **不动**（按 spec §4.2，SDK 内部会通过 store.append 自动镜像）。

- [ ] **Step 4: 增加 / 调整测试**

Run 现有 list/delete 路径相关测试：

`uv run python -m pytest tests/test_assistant_routes.py tests/test_assistant_router_full.py tests/test_assistant_service_streaming.py -v`

Expected: 所有 pass。如果有 mock 了旧 SDK 函数的 fixture，迁移到 mock store 上。

- [ ] **Step 5: Commit**

```bash
git add server/agent_runtime/service.py tests/
git commit -m "refactor(agent-runtime): service layer uses *_via_store helpers"
```

---

## Task 13：stream_projector 识别 mirror_error

**Files:**
- Modify: `server/agent_runtime/stream_projector.py`
- Create: `tests/agent_runtime/test_stream_projector_mirror_error.py`

- [ ] **Step 1: 写失败测试**

```python
# tests/agent_runtime/test_stream_projector_mirror_error.py
"""stream_projector should surface SystemMessage(subtype='mirror_error') to UI."""
from __future__ import annotations

from server.agent_runtime.stream_projector import is_mirror_error_event


def test_recognizes_mirror_error():
    event = {"type": "system", "subtype": "mirror_error", "message": "DB write failed"}
    assert is_mirror_error_event(event) is True


def test_ignores_other_system_events():
    assert is_mirror_error_event({"type": "system", "subtype": "init"}) is False


def test_ignores_non_system_events():
    assert is_mirror_error_event({"type": "assistant"}) is False
    assert is_mirror_error_event({"type": "user"}) is False
    assert is_mirror_error_event({}) is False
    assert is_mirror_error_event(None) is False
```

- [ ] **Step 2: 跑测试看失败**

Run: `uv run python -m pytest tests/agent_runtime/test_stream_projector_mirror_error.py -v`
Expected: FAIL — `cannot import name 'is_mirror_error_event'`

- [ ] **Step 3: 加 helper + 在事件循环里发出告警 turn**

在 `server/agent_runtime/stream_projector.py` 末尾追加：

```python
def is_mirror_error_event(event: object) -> bool:
    """True if event is a SDK SessionStore mirror failure system message.

    Per design doc §6.2, surfacing this to the UI is REQUIRED — silent drop
    means the user won't know their PG-mirrored history has a gap.
    """
    if not isinstance(event, dict):
        return False
    return event.get("type") == "system" and event.get("subtype") == "mirror_error"
```

然后在已有的事件投影主循环中（按现有代码风格）找到处理 `system` 消息的地方，
加上一个分支：

```python
if is_mirror_error_event(event):
    # Project as a visible system turn so the front-end renders an alert.
    return _project_system_turn(
        event,
        kind="mirror_error",
        text="会话镜像写入失败：本次重启后历史可能不完整。",
    )
```

> 具体函数名（`_project_system_turn` 等）以 `stream_projector.py` 现有风格为准；
> 如果项目里没有现成的"投影一个 system turn"函数，本任务保持原 system event 透传
> （SDK 已经把它当 system 推出去），只要不静默丢弃即可，并在日志里 logger.warning。

- [ ] **Step 4: 跑测试**

Run: `uv run python -m pytest tests/agent_runtime/test_stream_projector_mirror_error.py tests/test_stream_projector_more.py -v`
Expected: 4 passed (原有 + 新增)

- [ ] **Step 5: Commit**

```bash
git add server/agent_runtime/stream_projector.py tests/agent_runtime/test_stream_projector_mirror_error.py
git commit -m "feat(agent-runtime): surface SDK SessionStore mirror_error to UI"
```

---

## Task 14：app.py lifespan 调用迁移

**Files:**
- Modify: `server/app.py`
- Create: `tests/test_session_store_startup_migration.py`

- [ ] **Step 1: 写启动迁移钩子的失败测试**

```python
# tests/test_session_store_startup_migration.py
"""Lifespan should invoke session-store transcript migration once."""
from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_lifespan_invokes_session_store_migration(tmp_path, monkeypatch):
    # Arrange: stub out everything the lifespan touches except our hook.
    monkeypatch.setenv("PROJECT_ROOT", str(tmp_path))
    with patch(
        "server.app.migrate_local_transcripts_to_store"
    ) as migrate_mock:
        migrate_mock.return_value = {"imported": 0, "skipped": 0, "failed": 0}
        from server.app import app, lifespan
        async with lifespan(app):
            pass
    migrate_mock.assert_called_once()
```

> Note: This test is intentionally light — full lifespan exercise lives in
> existing `test_app_startup_migration.py`. We only need to assert the new
> hook is wired in. If the existing lifespan has many startup steps that fail
> in a sandbox, the test may need extra `patch(...)` calls or be skipped
> when wiring is verified by manual smoke + e2e in Task 15.

- [ ] **Step 2: 加迁移调用到 app.py lifespan**

在 `server/app.py` lifespan 里，紧跟 `await init_db()` 之后（约 116 行后）：

```python
# server/app.py 顶部 imports
from lib.agent_session_store.import_local import migrate_local_transcripts_to_store
from lib.agent_session_store.store import DbSessionStore
from lib.db.engine import async_session_factory

# in lifespan, right after `await init_db()`:
try:
    store = DbSessionStore(async_session_factory)
    await migrate_local_transcripts_to_store(
        store,
        projects_root=PROJECT_ROOT / "projects",
        data_dir=PROJECT_ROOT / "projects" / ".arcreel_data",
    )
except Exception:
    logger.exception("session-store transcript migration failed (non-fatal)")
```

> data_dir 选址需与 ArcReel 当前 `.arcreel.db` 同级，确保 docker volume 自然带上；
> 如果项目已有约定的 data_dir 常量，直接复用。

- [ ] **Step 3: 跑测试**

Run: `uv run python -m pytest tests/test_session_store_startup_migration.py tests/test_app_startup_migration.py -v`
Expected: all pass

- [ ] **Step 4: 启动一次本地服务做手动烟测**

```bash
uv run uvicorn server.app:app --reload --reload-dir server --reload-dir lib --port 1241
```

观察日志中应出现：
```
INFO ... transcript migration: imported=N skipped=M failed=0
```
首次启动 N 应等于本地实际 jsonl 数；二次启动应有 `marker present, skipping`。

按 Ctrl+C 停止。

- [ ] **Step 5: Commit**

```bash
git add server/app.py tests/test_session_store_startup_migration.py
git commit -m "feat(app): wire session-store transcript migration into lifespan"
```

---

## Task 15：end-to-end 烟测

**Files:**
- Create: `tests/agent_runtime/test_session_store_e2e.py`

- [ ] **Step 1: 写 e2e 测试**

```python
# tests/agent_runtime/test_session_store_e2e.py
"""End-to-end smoke: append → list → load via SDK helpers, then resume."""
from __future__ import annotations

import pytest
from claude_agent_sdk import (
    get_session_messages_from_store,
    list_sessions_from_store,
)

from lib.agent_session_store.store import DbSessionStore


@pytest.mark.asyncio
async def test_append_then_list_then_load_via_sdk_helpers(session_factory):
    store = DbSessionStore(session_factory, user_id="e2e")
    project_key = "demo-project"
    sid = "00000000-0000-0000-0000-000000000abc"
    key = {"project_key": project_key, "session_id": sid}

    entries = [
        {"type": "user", "uuid": "1", "timestamp": "2026-05-01T00:00:00Z",
         "message": {"content": "hello"}},
        {"type": "assistant", "uuid": "2", "timestamp": "2026-05-01T00:00:01Z",
         "message": {"content": "world"}},
    ]
    await store.append(key, entries)

    listing = await list_sessions_from_store(store, project_key)
    assert any(item.session_id == sid for item in listing)

    messages = await get_session_messages_from_store(store, key)
    assert len(messages) == 2
    assert getattr(messages[0], "type", None) in {"user", "assistant"}
```

- [ ] **Step 2: 跑 e2e**

Run: `uv run python -m pytest tests/agent_runtime/test_session_store_e2e.py -v`
Expected: 1 passed

- [ ] **Step 3: 全套测试回归**

Run: `uv run python -m pytest -x`
Expected: 全部 pass

- [ ] **Step 4: Commit**

```bash
git add tests/agent_runtime/test_session_store_e2e.py
git commit -m "test(session-store): end-to-end via SDK *_from_store helpers"
```

---

## Task 16：CI matrix 加 PG dialect

**Files:**
- Modify: `.github/workflows/<existing-test-workflow>.yml`

- [ ] **Step 1: 找到现有 CI 测试 workflow**

Run: `ls .github/workflows/`

定位运行 pytest 的 workflow（通常是 `tests.yml` / `ci.yml`）。

- [ ] **Step 2: 加 matrix（仅对 session_store 子集跑 PG）**

修改 workflow：在 strategy 中增加 dialect axis，并在 services 里加 postgres：

```yaml
strategy:
  matrix:
    db: [sqlite, postgres]

services:
  postgres:
    image: postgres:16
    env:
      POSTGRES_USER: test
      POSTGRES_PASSWORD: test
      POSTGRES_DB: arcreel_test
    options: >-
      --health-cmd pg_isready --health-interval 10s --health-timeout 5s --health-retries 5
    ports:
      - 5432:5432

steps:
  - name: Set DATABASE_URL (postgres)
    if: matrix.db == 'postgres'
    run: echo "DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/arcreel_test" >> $GITHUB_ENV
  - name: Run session-store conformance (postgres)
    if: matrix.db == 'postgres'
    run: uv run alembic upgrade head && uv run python -m pytest tests/agent_session_store/ -v
  - name: Run full test suite (sqlite)
    if: matrix.db == 'sqlite'
    run: uv run python -m pytest -x
```

精确语法以现有 workflow 结构为准 — 不要破坏现有 sqlite 流程。

- [ ] **Step 3: 本地用 PG 验证一次 conformance**

```bash
docker run --rm -d --name arcreel-pg-test -e POSTGRES_USER=test -e POSTGRES_PASSWORD=test \
  -e POSTGRES_DB=arcreel_test -p 5432:5432 postgres:16

DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/arcreel_test \
  uv run alembic upgrade head

DATABASE_URL=postgresql+asyncpg://test:test@localhost:5432/arcreel_test \
  uv run python -m pytest tests/agent_session_store/ -v

docker stop arcreel-pg-test
```

Expected: all pass under PG dialect。如有失败，常见原因：

- `sqlite_where` 语法在 PG 被忽略 → 应使用 `postgresql_where`（已在 model 中处理）
- ON CONFLICT index_elements 必须命中部分唯一索引 → 已正确指定

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/
git commit -m "ci: run session-store tests on sqlite + postgres matrix"
```

---

## Self-Review

### Spec coverage

| Spec section | Plan task |
|---|---|
| §1 总体架构（DbSessionStore + lib/agent_session_store/） | Task 1, 4–7 |
| §2 数据模型（entries / summaries 表 + mtime_ms） | Task 1, 2 |
| §3 append 数据流（seq 取号 + uuid 部分唯一 + summary fold） | Task 4, 5, 6 |
| §3 load / list_sessions / delete / list_subkeys / list_session_summaries | Task 4, 7 |
| §3 project_key（make_project_key wrapper） | Task 3 |
| §4 调用面改造（session_manager / sdk_transcript_adapter / service.py） | Task 10, 11, 12 |
| §4.3 环境变量回滚 ARCREEL_SDK_SESSION_STORE | Task 10 |
| §5 启动迁移（公开 API + marker + 单条容错） | Task 9, 14 |
| §6.2 mirror_error system 消息识别 | Task 13 |
| §6.3 日志规范 | Task 4–9（每个 store 方法都加了 logger.info/warning/error） |
| §7.1 Layer 1 SDK conformance | Task 8 |
| §7.1 Layer 2 单测（pkey / seq concurrency / summary fold / migration） | Task 3, 5, 6, 9 |
| §7.1 Layer 3 e2e | Task 15 |
| §7.3 CI matrix sqlite + postgres | Task 16 |
| 验收标准 1（pytest 全绿） | Task 8, 15 末步 |
| 验收标准 2（CI matrix） | Task 16 |
| 验收标准 3（旧会话 UI 可见） | Task 14 step 4 手动烟测 |
| 验收标准 4（删 marker 不重复入库） | Task 9 step 1 第三个 case |
| 验收标准 5（adapter 不再 import 私有 API） | Task 11 step 5 grep 验证 |
| 验收标准 6（mirror_error 前端可见） | Task 13 |
| 验收标准 7（环境变量 off 退化可用） | Task 10 |

**Gap 检查**：
- 验收标准 8（清理阶段 grep 整个仓库无 `_internal.sessions` 引用）属于 follow-up，spec
  也明确「本期不强制」，不在本计划范围。
- spec §6.4 指标（mirror_errors_total / append_p99_ms）spec 自身写了「也可以这一期不做」
  ，本计划按"不做"处理，留给 follow-up。

### 多 worker 并发锁

Spec §5.4 第 6 条要求「config 表锁 + marker 双重保护」。本计划当前只实现了 marker，
单 worker 部署足够。**多 worker 部署的额外锁留给 Task 17 增补**——但 ArcReel 当前
`uvicorn` 默认单 worker，不影响首期发布。如果你的部署确实是多 worker，请在 Task 14
之后插入：

> #### Task 17（条件性，仅多 worker 部署需要）：config 表锁
> 在 `migrate_local_transcripts_to_store` 内层最前面加 `INSERT INTO config(key='session_store_migration_lock', value=...) ON CONFLICT DO NOTHING`，未拿到锁时直接 return。

### 类型一致性

- `key: dict` 在 store 全部方法签名里类型一致（SDK Protocol 用 `SessionKey` TypedDict，dict 鸭子类型兼容）
- `make_project_key(project_cwd)` 单一签名，全 plan 一致
- `DbSessionStore(session_factory, *, user_id=...)` 构造签名全 plan 一致

### Placeholder 扫描

- 所有 "implement later" / "TBD" 等已通过具体伪代码替代
- Task 11 与 Task 12 中 "调整 caller" 的步骤都给了 `grep` 命令定位 caller，没有
  无目的性的 "fix elsewhere as needed"
- Task 16 workflow YAML 标注「以现有 workflow 结构为准」是合理的环境差异，不是占位符

---

## Plan complete and saved to `docs/superpowers/plans/2026-05-01-sdk-session-store.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 我每个 task 派一个 fresh subagent，task 间审查，迭代快。

**2. Inline Execution** — 在当前会话里用 `executing-plans` 批量执行，到检查点暂停。

哪种方式？
