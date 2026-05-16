"""Shared pytest fixtures for the ArcReel test suite."""

from __future__ import annotations

from collections.abc import Callable


def make_translator(locale: str = "zh") -> Callable[..., str]:
    """Create a translator function bound to a fixed locale for testing."""
    from lib.i18n import _ as i18n_translate

    def translate(key: str, **kwargs) -> str:
        return i18n_translate(key, locale=locale, **kwargs)

    return translate


import os
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import lib.generation_queue as generation_queue_module
from lib.db.base import Base
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore

# ---------------------------------------------------------------------------
# General utilities
# ---------------------------------------------------------------------------


def make_test_video(path: Path, *, duration_sec: float = 1.0, fps: int = 30) -> None:
    """使用 ffmpeg 生成极短测试视频（64x64 像素）"""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=black:size=64x64:duration={duration_sec}:rate={fps}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )


@pytest.fixture(autouse=True)
def _reset_app_data_dir_cache():
    """``app_data_dir()`` uses ``functools.cache`` for production; reset it between
    tests so per-test monkeypatching of ARCREEL_DATA_DIR / AI_ANIME_PROJECTS takes
    effect immediately."""
    from lib.app_data_dir import _reset_for_tests

    _reset_for_tests()
    yield
    _reset_for_tests()


@pytest.fixture(autouse=True)
def _stub_sandbox_check(monkeypatch, request):
    """Mock ``check_sandbox_available`` 返回 True，避免测试机不满足真实 bwrap probe。

    GitHub Actions Ubuntu 24.04 runner 上 ``apparmor_restrict_unprivileged_userns=1``
    会让 ``server.app.check_sandbox_available`` 的 bwrap probe 启动失败，连带
    把任何走 FastAPI lifespan 的测试（TestClient / lifespan / startup hook 集成测试）
    全部拖崩。测试本不该依赖 host 能跑非特权 user namespace；该函数本身的契约
    由 ``tests/server/test_startup_assertions.py`` 独立覆盖（用更精细的 subprocess.run
    stub）— 那个文件需要走真实函数，故按文件名跳过此 autouse stub。
    """
    if request.path.name == "test_startup_assertions.py":
        return
    monkeypatch.setattr("server.app.check_sandbox_available", lambda: True)


@pytest.fixture(autouse=True)
def _profile_env(monkeypatch, tmp_path):
    """Pin ``agent_profile_dir()`` to a per-test ``tmp_path/agent_runtime_profile``
    so tests that build a fake profile under tmp_path are exercised against the
    env-driven contract instead of the repo-level default.

    Also seed the profile with a minimal ``.claude/`` + ``CLAUDE.md`` so unrelated
    tests that go through ``ProjectManager.create_project`` (which triggers
    profile sync) don't trip the ``ProfileMissingError`` / ``ProfileEmptyError``
    入口防御 — those guards are deployment-correctness contracts, not test fixtures.
    Tests that explicitly need profile-missing / empty scenarios still work because
    they ``setenv`` to a different path under tmp_path.
    """
    profile_dir = tmp_path / "agent_runtime_profile"
    profile_dir.mkdir(parents=True, exist_ok=True)
    # 仅 touch 顶层 CLAUDE.md（最少 1 个可同步文件以避开 ProfileEmptyError）。
    # 不预创建 ``.claude/`` —— 让需要自己 mkdir(".claude", parents=True) 的下游测试
    # 不撞 FileExistsError；那些测试自己会构造完整 profile 内容。
    (profile_dir / "CLAUDE.md").write_text("")
    monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_dir))


@pytest.fixture()
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
# SessionManager family (used by 3+ test files)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def meta_store():
    """Create an async SessionMetaStore backed by in-memory SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionMetaStore(session_factory=factory)
    yield store
    await engine.dispose()


@pytest.fixture()
async def session_manager(tmp_path: Path, meta_store: SessionMetaStore) -> SessionManager:
    """Create a SessionManager wired to *tmp_path* and *meta_store*."""
    return SessionManager(
        project_root=tmp_path,
        data_dir=tmp_path,
        meta_store=meta_store,
    )


# ---------------------------------------------------------------------------
# GenerationQueue family (used by 2+ test files)
# ---------------------------------------------------------------------------


def pytest_collection_modifyitems(config, items):
    """Auto-mark dialect-sensitive tests with `uses_db`.

    Mark a test only when it consumes a fixture defined in a canonical
    dialect-sensitive conftest — the main `tests/conftest.py` (`async_session`,
    reads DATABASE_URL) or `tests/agent_session_store/conftest.py`
    (`session_factory` / `file_session_factory`, also reads DATABASE_URL).
    Tests that locally override the same fixture name with a hard-coded
    SQLite engine (e.g. `tests/test_custom_providers_api.py`) are excluded so
    the postgres-compat job stays a true dialect signal rather than running
    SQLite-only code under a `postgres` coverage flag.
    """
    target_fixtures = {"async_session", "session_factory", "file_session_factory"}
    canonical_modules = {"tests.conftest", "tests.agent_session_store.conftest"}
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


@pytest.fixture()
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
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
async def generation_queue():
    """Create an async GenerationQueue backed by in-memory SQLite.

    Automatically resets the module singleton on teardown.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    queue = generation_queue_module.GenerationQueue(session_factory=factory)
    generation_queue_module._QUEUE_INSTANCE = queue
    yield queue
    generation_queue_module._QUEUE_INSTANCE = None
    await engine.dispose()
