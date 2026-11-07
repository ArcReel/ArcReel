"""Shared pytest fixtures for the ArcReel test suite.

Only fixtures, collection hooks, and startup selection validation live here; no modules import this file.
Substitute implementations live in `tests/fakes.py`; input builders in `tests/factories.py`.

All DB fixtures derive from the sole `make_test_engine` construction entry:

- `session_factory` / `async_session` are dialect-aware, using real PostgreSQL when `DATABASE_URL` selects it.
  `concurrent_session_factory` is likewise dialect-aware, with independently connected WAL-file SQLite locally.
  `file_session_factory` always uses file SQLite for `sqlite_only` boundary cases.
  `pytest_collection_modifyitems` injects `uses_db` for all four, forming the database selection;
  PostgreSQL compatibility jobs select `uses_db and not sqlite_only`.
- `db_engine` / `db_session` / `db_factory` (memory) and `file_db_factory` (file) always use SQLite
  without `uses_db`, for model/repository unit tests outside that selection.
- The sole exception is PostgreSQL `async_session`, bound to public schemas already created by CI
  `alembic upgrade head`. Outer transactions + SAVEPOINT differ from `make_test_engine` per-test schemas
  and `create_all`, requiring a separate engine.
"""

from __future__ import annotations

import asyncio
import atexit
import ipaddress
import os
import shutil
import socket
import sys
import tempfile
import uuid as _uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

# The same pytest entry parses positional collection arguments and raises UsageError for missing selections;
# reuse it in `pytest_sessionstart` so startup/collection validation shares one source.
from _pytest.main import resolve_collection_argument
from sqlalchemy import event, pool, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from tests.memory_db_guard import install_shared_connection_write_guard

# `lib.db.engine` binds module-level engines to `DATABASE_URL` at import, without in-process reconstruction.
# Override before any imports reaching that module. Otherwise repository-root
# `projects/arcreel.db` is shared by xdist workers, with schema created incidentally by whichever test
# runs first, creating implicit ordering dependencies. Bind process-exclusive temporary DBs instead,
# with schema explicitly created by `shared_db_schema`. Preserve external DATABASE_URL values from postgres-compat
# jobs or per-case Alembic monkeypatches.
#
# xdist workers inherit controller environments, including URLs written here;
# `_OWNED_DB_MARKER` identifies test-created rather than externally supplied URLs, making workers create separate DBs
# so every process owns one exclusively.
_OWNED_DB_MARKER = "ARCREEL_TEST_OWNED_DB"
_OWNED_TEST_DB_DIR: str | None = None
_OWNED_TEST_DB_OWNER_PID = os.getpid()


def _remove_owned_test_db_dir() -> None:
    """Reclaim temporary DBs only in their creating process.

    fork copies atexit registrations into children. Mutmut per-mutant pytest children exiting normally
    instead of ``os._exit`` would run parent callbacks; without PID guards they could delete DBs shared
    by parallel children, causing all session-level DB fixtures to fail.
    """
    if _OWNED_TEST_DB_DIR is None or os.getpid() != _OWNED_TEST_DB_OWNER_PID:
        return
    shutil.rmtree(_OWNED_TEST_DB_DIR, ignore_errors=True)


if not os.environ.get("DATABASE_URL", "").strip() or os.environ.get(_OWNED_DB_MARKER) == "1":
    _OWNED_TEST_DB_DIR = tempfile.mkdtemp(prefix="arcreel-test-db-")
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_OWNED_TEST_DB_DIR}/arcreel.db"
    os.environ[_OWNED_DB_MARKER] = "1"
    # Use atexit rather than fixture teardown: `--collect-only` marker gates and collection interruptions
    # import conftest without executing fixtures.
    atexit.register(_remove_owned_test_db_dir)

# Data roots/Claude SDK directories likewise default to repository `projects/` and user `~/.claude`.
# Layout migration moves directories/sessions there; collecting `server.app` creates Assistant singletons
# bound to that root, so pin process-exclusive temporary directories before any imports.
# `isolated_data_root` then supplies empty per-case roots.
_OWNED_TEST_HOME_DIR = tempfile.mkdtemp(prefix="arcreel-test-home-")
os.environ["ARCREEL_DATA_DIR"] = str(Path(_OWNED_TEST_HOME_DIR) / "data")
os.environ["CLAUDE_CONFIG_DIR"] = str(Path(_OWNED_TEST_HOME_DIR) / "claude-config")
os.environ.pop("AI_ANIME_PROJECTS", None)

# Without AUTH_TOKEN_SECRET, lifespan reads/generates signing keys under the data root and caches them process-wide.
# Direct token issuance without lifespan would first read/write keys on event loops, with cross-test caches
# making first access order-dependent. Pin a value; persistence tests delete their environment override themselves.
if not os.environ.get("AUTH_TOKEN_SECRET"):
    os.environ["AUTH_TOKEN_SECRET"] = "arcreel-test-token-secret-0123456789abcdef"

# `import lib` loads .env through `lib.infra.env_init`; absent local .env searches upward, so `.worktrees/`
# can inherit main-checkout provider keys that cause startup-key checks to block lifespan tests.
# Results must not depend on checkout or ancestor .env, so disable reads for the entire test process and children;
# tests of loading rules remove the override themselves.
os.environ["PYTHON_DOTENV_DISABLED"] = "1"

# Lifespan `ensure_auth_password` generates/writes root .env when AUTH_PASSWORD is absent.
# Pin it so lifespan cases retaining that step do not modify repository .env; generation/writeback cases
# clear AUTH_PASSWORD and supply temporary env_path themselves.
if not os.environ.get("AUTH_PASSWORD"):
    os.environ["AUTH_PASSWORD"] = "arcreel-test-password"


def _remove_owned_test_home_dir() -> None:
    """Reclaim temporary directories only in their creating process; PID rationale matches ``_remove_owned_test_db_dir``."""
    if os.getpid() == _OWNED_TEST_DB_OWNER_PID:
        shutil.rmtree(_OWNED_TEST_HOME_DIR, ignore_errors=True)


atexit.register(_remove_owned_test_home_dir)

import lib.generation.generation_queue as generation_queue_module
from lib.db.base import Base
from lib.db.engine import register_sqlite_functions
from lib.generation.video_request_facts import VideoRequestFacts, VideoRequestFactsFailure
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore

# Enable event-loop blocking guards throughout sync/async tests; nonexempt blocking fails, see tests/blocking_guard.
# With ARCREEL_TEST_HANG_DUMP_DIR, dump all thread stacks after stalled progress; see tests/hang_watchdog.py.
pytest_plugins = ["tests.blocking_guard", "tests.hang_watchdog"]


@pytest.fixture
def set_admission_video_request_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[VideoRequestFacts | VideoRequestFactsFailure], None]:
    """Let batch admission consume explicitly supplied test video-request facts."""
    from server.services.admission import video_batch_admission

    def configure(facts: VideoRequestFacts | VideoRequestFactsFailure) -> None:
        monkeypatch.setattr(video_batch_admission, "evaluate_video_request_facts", AsyncMock(return_value=facts))

    return configure


VideoRequestFactsResult = VideoRequestFacts | VideoRequestFactsFailure


@pytest.fixture
def set_video_request_facts(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[VideoRequestFactsResult | Mapping[str, VideoRequestFactsResult]], None]:
    """Capability consumers read constructed facts; evaluation tests retain real resolvers.

    A single result serves all buckets; mappings keyed ``"i2v"`` / ``"r2v"`` answer per task bucket
    for consumers with differing bucket configurations.
    """
    from lib.script import script_generator
    from lib.script.reference_video import request_projection
    from server.services.admission import cost_estimation
    from server.services.tasks import video_caps

    def configure(facts: VideoRequestFactsResult | Mapping[str, VideoRequestFactsResult]) -> None:
        if isinstance(facts, Mapping):
            by_bucket = dict(facts)

            async def evaluate(_project, *, generation_type, **_kwargs):
                return by_bucket[generation_type]

            fake = AsyncMock(side_effect=evaluate)
        else:
            fake = AsyncMock(return_value=facts)
        for consumer in (script_generator, request_projection, cost_estimation, video_caps):
            monkeypatch.setattr(consumer, "evaluate_video_request_facts", fake)

    return configure


@pytest.fixture
def video_request_facts(set_video_request_facts) -> None:
    """Supply deterministic i2v tiers for consumer cases unrelated to duration branches."""
    from tests.factories import make_video_request_facts

    set_video_request_facts(make_video_request_facts(route="reference_video", generation_type="i2v"))


@pytest.fixture
def set_error_handler_sessions(monkeypatch: pytest.MonkeyPatch) -> Callable[[async_sessionmaker[AsyncSession]], None]:
    """App error handlers load display-name catalogs through supplied session factories.

    They open sessions without dependency injection (``server.i18n.request_display_names``),
    outside ``dependency_overrides[get_async_session]`` control.
    """
    from server import i18n

    def configure(sessions: async_sessionmaker[AsyncSession]) -> None:
        monkeypatch.setattr(i18n, "async_session_factory", sessions)

    return configure


def _discard_pooled_connections_in_forked_child() -> None:
    """Fork children discard module-engine pooled connections inherited from parents.

    Each aiosqlite connection has a worker thread, but fork copies only the calling thread; inherited pooled
    connections lack their workers and queries wait forever. ``close=False`` discards references without
    closing parent connections. Mutmut forks per-mutant tests, whose first ``lib.db.engine`` access
    would otherwise hang until timeout. If parents never imported it, there are no connections to discard,
    and do not create engines in children.
    """
    engine_module = sys.modules.get("lib.db.engine")
    if engine_module is None:
        return
    engine_module.async_engine.sync_engine.dispose(close=False)


_fork_hook_registered = False


@pytest.fixture(scope="session", autouse=True)
def discard_pooled_connections_after_fork() -> None:
    """Register ``_discard_pooled_connections_in_forked_child`` as this process's fork hook.

    Hooks are process-wide and cannot unregister; mutmut may start multiple pytest sessions in one parent
    before forking mutants, so register once.
    """
    global _fork_hook_registered
    if _fork_hook_registered or not hasattr(os, "register_at_fork"):
        return
    os.register_at_fork(after_in_child=_discard_pooled_connections_in_forked_child)
    _fork_hook_registered = True


@pytest.fixture(autouse=True)
def isolated_data_root(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Give every test an empty temporary data root by default.

    Clear process caches for ``app_data_dir()`` / ``get_project_manager()`` before/after each case;
    first access after setting ``ARCREEL_DATA_DIR`` resolves the new root. Autouse fixtures importing app
    modules must depend on this fixture so intermediate resolutions remain temporary.
    """
    from lib.infra.app_data_dir import reset_for_tests
    from lib.project.project_manager import reset_project_manager_for_tests

    monkeypatch.setenv("ARCREEL_DATA_DIR", str(tmp_path_factory.mktemp("data-root")))
    reset_for_tests()
    reset_project_manager_for_tests()
    yield
    reset_for_tests()
    reset_project_manager_for_tests()


@pytest.fixture(autouse=True)
def stub_sandbox_check(isolated_data_root, monkeypatch, request):
    """Make ``check_sandbox_available`` return True without requiring actual bwrap support.

    Ubuntu 24.04 GitHub runners with ``apparmor_restrict_unprivileged_userns=1`` fail
    ``server.app.check_sandbox_available`` bwrap probes, breaking all lifespan-based TestClient/startup
    integrations. Tests must not depend on host unprivileged namespaces. Independent
    ``tests/unit/server/test_startup_assertions.py`` cases use finer subprocess.run stubs to cover
    the real function, so skip this autouse substitute by that filename.
    """
    if request.path.name == "test_startup_assertions.py":
        return
    monkeypatch.setattr("server.app.check_sandbox_available", lambda: True)


@pytest.fixture(autouse=True)
def offline_official_catalog_sync(isolated_data_root, monkeypatch):
    """Replace lifespan official-catalog synchronization with a nonrequesting idle task.

    Real startup immediately fetches manifests from catalog raw URLs, making results depend on network timing.
    Offline failures write sync tables/process status; online connection stalls can delay shutdown.
    Replace only the lifespan-imported entry; autouse also prevents new lifespan cases from sending traffic.
    ``stop_official_sync`` still cancels idle tasks. Direct ``lib.catalog.official_sync`` coverage in
    ``tests/integration/lib/catalog/test_official_sync.py`` remains unaffected.
    """

    def start_offline(*args, **kwargs) -> asyncio.Task[bool]:
        return asyncio.get_running_loop().create_task(asyncio.Event().wait(), name="catalog-official-sync-offline")

    monkeypatch.setattr("server.app.start_official_sync", start_offline)


@pytest.fixture(autouse=True)
def idle_asset_library_ingest(monkeypatch):
    """Replace lifespan material-library ingest with an idle loop using neither DB nor network.

    Real startup queries ingest tables and polls, changing test database/shutdown timing. Ingest behavior is covered directly
    through ``lib.asset_library.ingest.run_once`` in ``tests/unit/lib/asset_library`` and
    ``tests/unit/server/routers/test_provider_asset_links_api.py``.
    """
    from lib.asset_library import ingest

    class _IdleRunner(ingest.AssetIngestRunner):
        async def _loop(self) -> None:
            await asyncio.Event().wait()

    def start_idle(session_factory) -> ingest.AssetIngestRunner:
        runner = _IdleRunner(session_factory)
        runner.start()
        return runner

    monkeypatch.setattr("server.app.start_asset_ingest", start_idle)


@pytest.fixture(autouse=True)
def idle_material_maintenance(monkeypatch):
    """Replace lifespan remote-material maintenance with idle loops, for the same reason as ``idle_asset_library_ingest``.

    Settlement, compensating deletion, and working-file cleanup call ``lib.material`` directly in
    ``tests/integration/server/routers/test_remote_material_storage.py``.
    """
    from lib.material import maintenance

    class _IdleMaintenance(maintenance.MaterialMaintenance):
        async def _loop(self) -> None:
            await asyncio.Event().wait()

    def start_idle(
        session_factory, projects_dir, deleted_projects_dir=None, data_root=None
    ) -> maintenance.MaterialMaintenance:
        runner = _IdleMaintenance(session_factory, projects_dir, deleted_projects_dir, data_root)
        runner.start()
        return runner

    monkeypatch.setattr("server.app.start_material_maintenance", start_idle)


#: All test hostnames resolve here: TEST-NET-3, public but nonroutable.
_OFFLINE_DNS_ADDRESS = "203.0.113.10"


@pytest.fixture(autouse=True)
def offline_dns(monkeypatch):
    """Event-loop hostname ``getaddrinfo`` returns ``_OFFLINE_DNS_ADDRESS`` without real DNS.

    Downloads resolve hosts before each request; respx intercepts transports but DNS would still reach
    local resolvers. IP literals and ``localhost`` for local DBs/test servers retain real resolution.
    """
    real_getaddrinfo = asyncio.base_events.BaseEventLoop.getaddrinfo

    async def getaddrinfo(self, host, port, *args, **kwargs):
        if host is None or host == "localhost" or _is_ip_literal(host):
            return await real_getaddrinfo(self, host, port, *args, **kwargs)
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (_OFFLINE_DNS_ADDRESS, port or 0))]

    monkeypatch.setattr(asyncio.base_events.BaseEventLoop, "getaddrinfo", getaddrinfo)


def _is_ip_literal(host: str | bytes) -> bool:
    try:
        ipaddress.ip_address(host.decode() if isinstance(host, bytes) else host)
    except ValueError:
        return False
    return True


@pytest.fixture(scope="session", autouse=True)
def profile_env(tmp_path_factory):
    """Provide one minimal runtime profile per pytest worker.

    Tests exercising another profile location set ``ARCREEL_PROFILE_DIR`` explicitly.
    """
    profile_dir = tmp_path_factory.mktemp("agent-runtime-profile")
    (profile_dir / "CLAUDE.md").write_text("", encoding="utf-8")

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
    """Migrate module-engine DBs to head for direct use by any test.

    Only conftest-created temporary DBs migrate here; external DATABASE_URL schemas belong to postgres-compat
    jobs. Use Alembic, not ``create_all``: ``lib.db.init_db()`` stamps base then upgrades databases
    with tables but no alembic_version, duplicating precreated create_all tables.
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
    """Register every ORM model with ``Base.metadata``.

    Otherwise tested-module import chains determine created tables, giving the same fixture
    different schemas across files.
    """
    from lib.agent.agent_session_store.models import register_models as register_agent_session_models
    from lib.db.models import register_models as register_db_models

    register_agent_session_models()
    register_db_models()


@asynccontextmanager
async def make_test_engine(*, dialect_aware: bool = True, file_path: Path | None = None) -> AsyncGenerator[AsyncEngine]:
    """DB fixture engine construction, except PostgreSQL `async_session`.

    That branch binds CI-created public schemas after `alembic upgrade head`, isolating with outer
    transactions + SAVEPOINT rather than per-test schemas + `create_all`, requiring separate construction.

    With ``dialect_aware`` and PG ``DATABASE_URL``, create per-test schemas via search_path, seed user
    FK dependencies, then ``DROP SCHEMA CASCADE`` on exit. This genuinely covers dialect-sensitive
    partial unique indexes + ON CONFLICT and SELECT ... FOR UPDATE.

    Otherwise use SQLite: ``file_path`` None means memory; supplied paths use NullPool files for concurrency
    bypassing StaticPool serialization. Memory DBs attach ``tests.memory_db_guard`` write checks, failing concurrent
    task writes with guidance to matching file fixtures. ``dialect_aware=False`` fixes SQLite even in postgres-compat
    jobs; unit tests must not be redirected to PG.
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

    write_guard = None
    if file_path is None:
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        # Memory sessions share one connection; concurrent task writes fail with matching file-fixture guidance.
        write_guard = install_shared_connection_write_guard(
            engine, file_fixture="concurrent_session_factory" if dialect_aware else "file_db_factory"
        )
    else:
        engine = create_async_engine(f"sqlite+aiosqlite:///{file_path}", poolclass=pool.NullPool)

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        register_sqlite_functions(dbapi_conn)
        if file_path is None:
            return
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
    if write_guard is not None:
        write_guard.raise_if_violated()


@pytest.fixture
async def db_engine() -> AsyncIterator[AsyncEngine]:
    """In-memory SQLite engine, shared by model/repository unit tests."""
    async with make_test_engine(dialect_aware=False) as engine:
        yield engine


@pytest.fixture
async def db_session(db_engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """AsyncSession on ``db_engine``."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session


@pytest.fixture
async def file_db_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """File SQLite + NullPool factory for independent-connection concurrency tests.

    Unlike ``file_session_factory``, omit `uses_db` so unit concurrency cases
    stay outside postgres-compat selection.
    """
    async with make_test_engine(dialect_aware=False, file_path=tmp_path / "concurrency.db") as engine:
        yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory on ``db_engine``."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
async def custom_providers_app_session_factory(db_engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory bound to ``custom_providers_app``, also used to seed endpoint/model rows."""
    return async_sessionmaker(db_engine, expire_on_commit=False)


@pytest.fixture
def custom_providers_app(custom_providers_app_session_factory):
    """Admin-auth-bypassed FastAPI app with custom-provider routes and an in-memory DB.

    Three behavior-specific files (protocol-neutral CRUD, overrides, ComfyUI) share the tested app.
    Import within functions: root conftest loads across all tests and must not pull server packages
    into every collection just for three files.
    """
    from fastapi import FastAPI

    from lib.db import get_async_session
    from server.auth import CurrentUserInfo, get_current_user
    from server.error_handlers import register_error_handlers
    from server.routers import custom_providers
    from tests.auth_deps import AUTH_DEPENDENCIES

    app = FastAPI()

    async def _override_session():
        async with custom_providers_app_session_factory() as db_session:
            yield db_session

    app.dependency_overrides[get_async_session] = _override_session
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="test", sub="test", role="admin")
    app.include_router(custom_providers.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    return app


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Dialect-aware session factory: PG per-test schemas, otherwise memory SQLite."""
    async with make_test_engine() as engine:
        yield async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def concurrent_session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Dialect-aware independent connections: real PG schemas or local WAL-file SQLite."""
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
# Collection hooks: tier markers and boundary validation.
# ---------------------------------------------------------------------------


TESTS_ROOT = Path(__file__).parent
CLASSIFICATION_MARKS = ("unit", "integration", "e2e")


def _tier_from_path(item: pytest.Item) -> str | None:
    """Case tier directory name, the first `tests/unit|integration|e2e/…` segment."""
    try:
        rel = Path(str(item.path)).relative_to(TESTS_ROOT)
    except ValueError:
        return None
    head = rel.parts[0] if rel.parts else ""
    return head if head in CLASSIFICATION_MARKS else None


def _validate_selection_args(config: pytest.Config) -> None:
    """Pass every positional argument through pytest resolution, raising UsageError for missing paths/modules.

    ``resolve_collection_argument`` alone determines existence, ``--pyargs`` module/namespace resolution
    under ``consider_namespace_packages``, forbidden directory ``::`` selectors, and parameterization
    ``[]`` positions, matching collection exactly.
    """
    invocation_path = config.invocation_params.dir
    as_pypath = bool(config.getoption("pyargs"))
    consider_namespace_packages = bool(config.getini("consider_namespace_packages"))
    for index, arg in enumerate(config.args):
        resolve_collection_argument(
            invocation_path,
            arg,
            index,
            as_pypath=as_pypath,
            consider_namespace_packages=consider_namespace_packages,
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Missing targeted paths/modules immediately raise usage errors.

    Without -n, pytest collection gives UsageError/exit 4; xdist controllers only aggregate workers,
    dropping missing selections together with valid files and leaving no-tests-ran/exit 5 without
    error lines. Make both fail loudly before collection.
    """
    _validate_selection_args(session.config)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Inject directory-based tier markers, fixture-origin `uses_db`, then validate collection.

    Tiers derive from `tests/unit|integration|e2e/`, never handwritten. Inject `uses_db` only
    for this conftest's dialect-aware `async_session` / `session_factory` / `concurrent_session_factory` /
    `file_session_factory`. Local same-named hardcoded-SQLite overrides must not receive it, preserving
    genuine dialect signals instead of counting SQLite-only code toward the `postgres` coverage marker.
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
    _skip_local_port_tests_without_bind_permission(items)


def _skip_local_port_tests_without_bind_permission(items: list[pytest.Item]) -> None:
    """Skip `local_port` cases with reasons when environments forbid 127.0.0.1 binding.

    Agent sandboxes may deny local ports, producing only `PermissionError`; explain skips
    and direct reruns in environments permitting binds.
    """
    port_items = [item for item in items if item.get_closest_marker("local_port") is not None]
    if not port_items:
        return
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
    except PermissionError:
        skip = pytest.mark.skip(reason="当前环境禁止绑定 127.0.0.1 上的端口；在允许绑定本地端口的环境中运行此用例")
        for item in port_items:
            item.add_marker(skip)


def _enforce_classification_markers(items: list[pytest.Item]) -> None:
    """Collection enforces exactly one directory-derived tier marker and forbids real DB access in `unit`.

    Missing markers mean cases outside all three directories; multiple markers mean handwritten
    conflicts with directories. Reject both before CI. `uses_db` ∧ `unit` is itself a tier
    violation indicating misplaced tests.
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
        # The sole branch outside `make_test_engine` binds CI Alembic-created public schemas;
        # `make_test_engine` creates per-test schemas/create_all, a different isolation primitive.
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
    """Fixture form of ``bounded_poll_clock``: use fake clocks for whole-case polling/backoff.

    Compression/timeouts exactly match context-manager semantics for cases needing the clock throughout.
    """
    from tests.fakes import bounded_poll_clock

    with bounded_poll_clock():
        yield


# ---------------------------------------------------------------------------
# Edit timeline projects (final cut and Jianying draft tests)
# ---------------------------------------------------------------------------


@pytest.fixture
def timeline_project(tmp_path: Path):
    """Reference-video ``demo`` project with episode 1 units E1U1/E1U2 and no videos; return ProjectManager."""
    from lib.project.project_manager import ProjectManager

    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    manager.update_project("demo", lambda project: project.update({"generation_mode": "reference_video"}))
    manager.save_script(
        "demo",
        {
            "episode": 1,
            "title": "第一集",
            "content_mode": "narration",
            "generation_mode": "reference_video",
            "summary": "摘要",
            "novel": {"title": "小说", "chapter": "第一章"},
            "video_units": [
                {"unit_id": unit_id, "text": f"镜头 {unit_id}", "duration_seconds": 4} for unit_id in ("E1U1", "E1U2")
            ],
        },
        "episode_1.json",
    )
    return manager
