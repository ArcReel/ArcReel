"""Shared pytest fixtures for the ArcReel test suite."""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import uuid as _uuid
from collections.abc import AsyncGenerator, AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from sqlalchemy import event, pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

# `lib.db.engine` 的模块级 engine 在 import 期就按 `DATABASE_URL` 绑定，进程内不再重建，
# 所以覆写必须发生在下方任何会传染到该模块的 import 之前。未显式指定时它落在仓库根的
# `projects/.arcreel.db`：一份文件被 xdist 的多个 worker 共用，且 schema 只由某个先跑到
# 的用例顺带建出——用例间因此存在隐式顺序依赖。钉到本进程独占的临时库上，schema 由
# `shared_db_schema` 显式建立。DATABASE_URL 已由外部给定（postgres-compat job、
# 逐个用例 monkeypatch 的 alembic 用例）时不介入。
#
# xdist 的 worker 从 controller 继承 environ，会连这里写下的 URL 一起带过去；
# `_OWNED_DB_MARKER` 让 worker 认出「这是测试自己铸的、不是外部给的」，各自另铸一份，
# 从而每个进程都独占一个库。
_OWNED_DB_MARKER = "ARCREEL_TEST_OWNED_DB"
_OWNED_TEST_DB_DIR: str | None = None
_OWNED_TEST_DB_OWNER_PID = os.getpid()


def _remove_owned_test_db_dir() -> None:
    """只在铸造临时库的进程里回收它。

    atexit 登记表随 fork 复制到子进程。mutmut 按 mutant fork 出的 pytest 会话若走了
    正常退出路径而非 ``os._exit``，会带着父进程的登记表跑一遍；不设 pid 守卫时它会
    删掉所有并行子进程共用的库，之后每个子进程的会话级 DB fixture 都报错。
    """
    if _OWNED_TEST_DB_DIR is None or os.getpid() != _OWNED_TEST_DB_OWNER_PID:
        return
    shutil.rmtree(_OWNED_TEST_DB_DIR, ignore_errors=True)


if not os.environ.get("DATABASE_URL", "").strip() or os.environ.get(_OWNED_DB_MARKER) == "1":
    _OWNED_TEST_DB_DIR = tempfile.mkdtemp(prefix="arcreel-test-db-")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_OWNED_TEST_DB_DIR}/.arcreel.db"
    os.environ[_OWNED_DB_MARKER] = "1"
    # 回收挂在 atexit 而非 fixture teardown 上：`--collect-only`（CI 的分类 marker 闸门）
    # 与收集期中断都只 import conftest、不跑 fixture。
    atexit.register(_remove_owned_test_db_dir)

import lib.generation_queue as generation_queue_module
from lib.db.base import Base
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore

REPO_ROOT = Path(__file__).resolve().parents[1]


def _discard_pooled_connections_in_forked_child() -> None:
    """fork 出的子进程丢弃模块级 engine 池里从父进程继承的连接。

    aiosqlite 每个连接由一条专属工作线程驱动，fork 只复制调用线程，子进程里池中
    连接的工作线程不存在，经它发出的任何查询都永远等不到结果。``close=False``
    只丢引用不关连接，父进程那份连接不受影响。mutmut 按 mutant fork 跑测试，
    子进程首个走 ``lib.db.engine`` 的用例会就此挂到超时。父进程从未 import 过
    该模块时池中无连接可丢，也不在子进程里触发 engine 创建。
    """
    engine_module = sys.modules.get("lib.db.engine")
    if engine_module is None:
        return
    engine_module.async_engine.sync_engine.dispose(close=False)


_fork_hook_registered = False


@pytest.fixture(scope="session", autouse=True)
def discard_pooled_connections_after_fork() -> None:
    """把 ``_discard_pooled_connections_in_forked_child`` 挂到本进程的 fork 钩子上。

    钩子是进程级、不可注销的；mutmut 会在同一父进程里连开几个 pytest 会话再按 mutant
    fork，故只注册一次。
    """
    global _fork_hook_registered
    if _fork_hook_registered or not hasattr(os, "register_at_fork"):
        return
    os.register_at_fork(after_in_child=_discard_pooled_connections_in_forked_child)
    _fork_hook_registered = True


@pytest.fixture(autouse=True)
def reset_app_data_dir_cache():
    """``app_data_dir()`` uses ``functools.cache`` for production; reset it between
    tests so per-test monkeypatching of ARCREEL_DATA_DIR / AI_ANIME_PROJECTS takes
    effect immediately."""
    from lib.app_data_dir import reset_for_tests

    reset_for_tests()
    yield
    reset_for_tests()


@pytest.fixture(autouse=True)
def stub_sandbox_check(monkeypatch, request):
    """Mock ``check_sandbox_available`` 返回 True，避免测试机不满足真实 bwrap probe。

    GitHub Actions Ubuntu 24.04 runner 上 ``apparmor_restrict_unprivileged_userns=1``
    会让 ``server.app.check_sandbox_available`` 的 bwrap probe 启动失败，连带
    把任何走 FastAPI lifespan 的测试（TestClient / lifespan / startup hook 集成测试）
    全部拖崩。测试本不该依赖 host 能跑非特权 user namespace；该函数本身的契约
    由 ``tests/unit/server/test_startup_assertions.py`` 独立覆盖（用更精细的 subprocess.run
    stub）— 那个文件需要走真实函数，故按文件名跳过此 autouse stub。
    """
    if request.path.name == "test_startup_assertions.py":
        return
    monkeypatch.setattr("server.app.check_sandbox_available", lambda: True)


@pytest.fixture(scope="session", autouse=True)
def profile_env(tmp_path_factory):
    """Provide one minimal runtime profile per pytest worker.

    Tests exercising another profile location set ``ARCREEL_PROFILE_DIR`` explicitly.
    """
    profile_dir = tmp_path_factory.mktemp("agent-runtime-profile")
    (profile_dir / "CLAUDE.md").write_text("", encoding="utf-8")
    # ``.claude/references/`` 下的规则正文由 prompt builder 在运行时读入，桩 profile 须原样带上，
    # 否则任何构建 prompt 的测试都会撞 FileNotFoundError。
    references = profile_dir / ".claude" / "references"
    references.mkdir(parents=True, exist_ok=True)
    for rule_file in (REPO_ROOT / "agent_runtime_profile" / ".claude" / "references").glob("*.md"):
        (references / rule_file.name).write_text(rule_file.read_text(encoding="utf-8"), encoding="utf-8")

    previous = os.environ.get("ARCREEL_PROFILE_DIR")
    os.environ["ARCREEL_PROFILE_DIR"] = str(profile_dir)
    yield
    if previous is None:
        os.environ.pop("ARCREEL_PROFILE_DIR", None)
    else:
        os.environ["ARCREEL_PROFILE_DIR"] = previous


@pytest.fixture
def fd_count():
    """Return a callable that reports the current process file-descriptor count.

    Returns -1 on platforms where /dev/fd and /proc/self/fd are unavailable.
    """

    def _count() -> int:
        for fd_dir in ("/dev/fd", "/proc/self/fd"):
            try:
                return len(os.listdir(fd_dir))
            except OSError:
                continue
        return -1

    return _count


# ---------------------------------------------------------------------------
# Shared database fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def shared_db_schema():
    """把模块级 engine 所指的库迁到 head，使任何用例都能直接用它。

    只对本 conftest 自建的临时库执行：外部给定 DATABASE_URL 时（postgres-compat job）
    schema 由该 job 的 alembic 步骤负责。走 alembic 而非 ``create_all``，因为
    ``lib.db.init_db()`` 对「有表无 alembic_version」的库会先 stamp base 再 upgrade，
    预置的 create_all schema 会让它重复建表。
    """
    if _OWNED_TEST_DB_DIR is None:
        return

    from alembic.config import Config

    from alembic import command

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parent.parent / "alembic"))
    command.upgrade(cfg, "head")


def _pg_url_from_env() -> str | None:
    """Return DATABASE_URL iff it's a PostgreSQL+asyncpg URL, else None."""
    url = os.environ.get("DATABASE_URL")
    if url and url.startswith("postgresql+asyncpg://"):
        return url
    return None


# Test fixtures attribute writes to a small set of fixed user_ids; seed them
# on PG so FK constraints (which SQLite tests bypass via PRAGMA foreign_keys=OFF)
# don't reject inserts.
_PG_TEST_USER_IDS = ("default", "u1", "conformance", "e2e", "crash-recover", "long-turn")


async def _seed_pg_users(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        for uid in _PG_TEST_USER_IDS:
            await conn.execute(
                text(
                    "INSERT INTO users (id, username, role, is_active, created_at, updated_at) "
                    "VALUES (:id, :username, 'user', true, NOW(), NOW()) "
                    "ON CONFLICT (id) DO NOTHING"
                ),
                {"id": uid, "username": uid},
            )


def _register_models() -> None:
    """把全部 ORM 模型登记到 ``Base.metadata``。

    不这么做时建表范围取决于被测模块的 import 链，同一 fixture 在不同文件下建出的
    schema 不同。
    """
    from lib.agent_session_store.models import register_models as register_agent_session_models
    from lib.db.models import register_models as register_db_models

    register_agent_session_models()
    register_db_models()


@asynccontextmanager
async def make_test_engine(*, dialect_aware: bool = True, file_path: Path | None = None) -> AsyncGenerator[AsyncEngine]:
    """DB fixture 的 engine 构造点，唯一例外是 `async_session` 的 PG 分支。

    那条分支绑定 CI job 已 `alembic upgrade head` 建好的 public schema，隔离原语是外层
    事务 + SAVEPOINT，与这里的 per-test schema + `create_all` 不同，故自建 engine。

    ``dialect_aware`` 且 ``DATABASE_URL`` 指向 PG 时建 per-test schema 并按 search_path
    建表、播种 FK 依赖的 user 行，退出时 ``DROP SCHEMA CASCADE``——方言相关代码路径
    （partial unique index + ON CONFLICT、SELECT ... FOR UPDATE）由此被真实覆盖。

    其余情况建 SQLite：``file_path`` 为 None 时用内存库；给了路径则用 NullPool 文件库，
    供必须绕开 StaticPool 串行化的并发用例。``dialect_aware=False`` 固定走 SQLite，
    postgres-compat job 里也不改道——unit 档用例不该被拉去跑 PG。
    """
    pg_url = _pg_url_from_env() if dialect_aware else None
    if pg_url:
        schema = f"test_{_uuid.uuid4().hex[:12]}"
        engine = create_async_engine(pg_url, connect_args={"server_settings": {"search_path": schema}})
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
        async with engine.begin() as conn:
            _register_models()
            await conn.run_sync(Base.metadata.create_all)
        # PG enforces FK constraints (SQLite tests run with PRAGMA foreign_keys=OFF).
        await _seed_pg_users(engine)
        try:
            yield engine
        finally:
            async with engine.begin() as conn:
                await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await engine.dispose()
        return

    if file_path is None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{file_path}", poolclass=pool.NullPool)

        # 由 SQLAlchemy 的 event.listens_for 注册，模块内无其它引用；basedpyright 把函数作用域内的
        # 符号一律判为私有，本处的 reportUnusedFunction 是工具误报。
        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _record):  # pyright: ignore[reportUnusedFunction]
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.close()

    async with engine.begin() as conn:
        _register_models()
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """内存 SQLite engine —— models / repositories 单测的共享入口。"""
    async with make_test_engine(dialect_aware=False) as engine:
        yield engine


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """``db_engine`` 上的 AsyncSession。"""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def file_db_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """文件 SQLite + NullPool 的 factory —— 需要独立连接的并发用例用它。

    与 ``file_session_factory`` 的差别只在不带 `uses_db`：unit 档的并发用例
    不该被拉进 postgres-compat 选集。
    """
    async with make_test_engine(dialect_aware=False, file_path=tmp_path / "concurrency.db") as engine:
        yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """``db_engine`` 上的 session factory。"""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """方言敏感的 session factory：PG 下走 per-test schema，否则内存 SQLite。"""
    async with make_test_engine() as engine:
        yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def concurrent_session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """方言敏感且允许独立连接：PG 走真实 schema，本地 SQLite 走 WAL 文件库。"""
    async with make_test_engine(file_path=tmp_path / "concurrency-aware.db") as engine:
        yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def file_session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """File-backed SQLite with NullPool — each connection is independent.

    Required for concurrency tests that must NOT serialize via StaticPool
    (which is the default for ``sqlite+aiosqlite:///:memory:``).

    Always SQLite regardless of ``DATABASE_URL`` — tests that depend on this
    fixture are SQLite-specific edge cases marked ``@pytest.mark.sqlite_only``.
    """
    async with make_test_engine(dialect_aware=False, file_path=tmp_path / "concurrency.db") as engine:
        yield async_sessionmaker(engine, expire_on_commit=False)


# ---------------------------------------------------------------------------
# SessionManager family (used by 3+ test files)
# ---------------------------------------------------------------------------


@pytest.fixture
async def meta_store(db_factory: async_sessionmaker[AsyncSession]) -> SessionMetaStore:
    """Create an async SessionMetaStore backed by in-memory SQLite."""
    return SessionMetaStore(session_factory=db_factory)


@pytest.fixture
async def session_manager(tmp_path: Path, meta_store: SessionMetaStore) -> SessionManager:
    """Create a SessionManager wired to *tmp_path* and *meta_store*."""
    return SessionManager(
        project_root=tmp_path,
        meta_store=meta_store,
    )


# ---------------------------------------------------------------------------
# 收集期钩子：档位 marker 注入与边界校验
# ---------------------------------------------------------------------------


TESTS_ROOT = Path(__file__).parent
CLASSIFICATION_MARKS = ("unit", "integration", "e2e")


def _tier_from_path(item: pytest.Item) -> str | None:
    """用例所在的档位目录名（`tests/unit|integration|e2e/…` 的第一段）。"""
    try:
        rel = Path(str(item.path)).relative_to(TESTS_ROOT)
    except ValueError:
        return None
    head = rel.parts[0] if rel.parts else ""
    return head if head in CLASSIFICATION_MARKS else None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """按目录注入分类 marker，再按 fixture 来源注入 `uses_db`，最后跑收集期校验。

    分类 marker 由用例所在的 `tests/unit|integration|e2e/` 段决定，不手写。
    `uses_db` 只在用例消费本 conftest 的方言敏感 fixture（`async_session` /
    `session_factory` / `concurrent_session_factory` / `file_session_factory`）时注入；用例在本地覆写同名 fixture
    为硬编码 SQLite engine 时不注入，使 postgres-compat job 保持真实的方言信号，
    而不是把只跑 SQLite 的代码算进 `postgres` 覆盖率标记。
    """
    for item in items:
        tier = _tier_from_path(item)
        if tier is not None:
            item.add_marker(getattr(pytest.mark, tier))

    target_fixtures = {"async_session", "session_factory", "concurrent_session_factory", "file_session_factory"}
    canonical_modules = {"tests.conftest"}
    uses_db = pytest.mark.uses_db
    for item in items:
        info = getattr(item, "_fixtureinfo", None)
        if info is None:
            continue
        fixturenames = set(getattr(item, "fixturenames", ()) or ())
        for fname in target_fixtures & fixturenames:
            # `name2fixturedefs[fname]` is pytest's fixture override chain
            # (general → specific). The last element is the definition that
            # actually wins for this test; only it determines whether the
            # test really hits a dialect-sensitive engine.
            defs = info.name2fixturedefs.get(fname) or ()
            if not defs:
                continue
            active = defs[-1]
            if getattr(active.func, "__module__", "") in canonical_modules:
                item.add_marker(uses_db)
                break

    _enforce_classification_markers(items)


def _enforce_classification_markers(items: list[pytest.Item]) -> None:
    """收集期强制：分类 marker 恰好一个且来自目录，`unit` 档不得触达真实数据库。

    分类 marker 由目录注入，所以「缺失」等价于用例不在三个档位目录之下，「多标」
    等价于文件里还留着与目录相冲突的手写 marker——两者都拦在收集期，不进 CI。
    `uses_db` ∧ `unit` 是档位边界本身：unit 档禁真实 DB，命中说明用例放错了目录。
    """
    missing = []
    conflicting = []
    db_in_unit = []
    for item in items:
        marks = {m.name for m in item.iter_markers()}
        classify = marks & set(CLASSIFICATION_MARKS)
        if not classify:
            missing.append(item.nodeid)
        elif len(classify) > 1:
            conflicting.append(f"{item.nodeid}（{'/'.join(sorted(classify))}）")
        if "uses_db" in marks and "unit" in classify:
            db_in_unit.append(item.nodeid)
    problems = []
    if missing:
        listing = "\n".join(f"  - {nodeid}" for nodeid in missing)
        problems.append(
            f"{len(missing)} 个测试用例不在档位目录下（分类 marker 由目录注入）：\n{listing}\n"
            "把文件移到 tests/unit|integration|e2e/<源码目录镜像>/ 下。"
        )
    if conflicting:
        listing = "\n".join(f"  - {entry}" for entry in conflicting)
        problems.append(
            f"{len(conflicting)} 个测试用例带多个分类 marker（三者互斥）：\n{listing}\n"
            "删掉用例/类/模块三层中手写的分类 marker，档位只由目录决定。"
        )
    if db_in_unit:
        listing = "\n".join(f"  - {nodeid}" for nodeid in db_in_unit)
        problems.append(
            f"{len(db_in_unit)} 个 unit 档用例触达真实数据库（`uses_db`）：\n{listing}\n"
            "unit 档禁真实 DB，把文件移到 tests/integration/ 下的镜像位置。"
        )
    if problems:
        raise pytest.UsageError("\n".join(problems))


@pytest.fixture
async def async_session():
    """Generic AsyncSession for repository tests.

    PG (DATABASE_URL=postgresql+...): trusts that ``alembic upgrade head`` has
    already created the schema (CI job does this before pytest). Each test
    opens a fresh NullPool engine, an outer transaction, and uses SAVEPOINT
    semantics so any `session.commit()` is contained — teardown ROLLBACKs the
    outer transaction, so data writes never persist.

    SQLite (default): each test gets a fresh in-memory engine + ORM
    ``create_all`` — engine is throwaway, no isolation primitive needed.
    """
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgresql"):
        # 唯一不走 `make_test_engine` 的分支：它绑定 CI job 已 alembic 建好的 public schema，
        # 而 `make_test_engine` 建 per-test schema 并 create_all，两者的隔离原语不同。
        # Per-test engine with NullPool: avoids cross-event-loop reuse of
        # asyncpg connections (each pytest-asyncio test runs on a fresh loop).
        from sqlalchemy.pool import NullPool

        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with engine.connect() as conn:
                outer = await conn.begin()
                try:
                    factory = async_sessionmaker(
                        bind=conn,
                        expire_on_commit=False,
                        join_transaction_mode="create_savepoint",
                    )
                    async with factory() as session:
                        yield session
                finally:
                    await outer.rollback()
        finally:
            await engine.dispose()
        return

    # SQLite in-memory — engine is throwaway, ORM-driven schema.
    async with make_test_engine(dialect_aware=False) as engine:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            yield session


# ---------------------------------------------------------------------------
# GenerationQueue family (used by 2+ test files)
# ---------------------------------------------------------------------------


@pytest.fixture
async def generation_queue(db_factory: async_sessionmaker[AsyncSession]):
    """Create an async GenerationQueue backed by in-memory SQLite.

    Automatically resets the module singleton on teardown.
    """
    queue = generation_queue_module.GenerationQueue(session_factory=db_factory)
    generation_queue_module._QUEUE_INSTANCE = queue
    yield queue
    generation_queue_module._QUEUE_INSTANCE = None


# ---------------------------------------------------------------------------
# Polling / retry clock (used by 2+ test files)
# ---------------------------------------------------------------------------


@pytest.fixture
def poll_clock():
    """``bounded_poll_clock`` 的 fixture 形态：整条用例的轮询与退避等待都走假表。

    压缩语义与超时兜底与上下文管理器形态完全一致，适用于整条用例都需要假表的场景。
    """
    from tests.fakes import bounded_poll_clock

    with bounded_poll_clock():
        yield
