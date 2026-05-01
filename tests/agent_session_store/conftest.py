"""Fixtures for agent_session_store tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy import event, pool
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


@pytest.fixture
async def file_session_factory(tmp_path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """File-backed SQLite with NullPool — each connection is independent.

    Required for concurrency tests that must NOT serialize via StaticPool
    (which is the default for ``sqlite+aiosqlite:///:memory:``).
    """
    db_path = tmp_path / "concurrency.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        poolclass=pool.NullPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA foreign_keys=OFF")
        cursor.close()

    async with engine.begin() as conn:
        import lib.agent_session_store.models  # noqa: F401
        import lib.db.models  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()
