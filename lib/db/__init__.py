"""Database package — ORM models, engine, and session factory."""

from lib.db.engine import (
    async_engine,
    async_session_factory,
    get_async_session,
    get_database_url,
    is_sqlite_backend,
    safe_session_factory,
)
from lib.db.base import Base


async def init_db() -> None:
    """Create all tables (development convenience). Production uses Alembic."""
    async with async_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Dispose engine connections on shutdown.

    aiosqlite connections may already be dead when SSE tasks were cancelled,
    so we tolerate errors during pool cleanup.
    """
    try:
        await async_engine.dispose()
    except Exception:
        pass


__all__ = [
    "Base",
    "async_engine",
    "async_session_factory",
    "close_db",
    "get_async_session",
    "get_database_url",
    "init_db",
    "is_sqlite_backend",
    "safe_session_factory",
]
