"""Database package — ORM models, engine, and session factory."""

import logging

from lib.db.engine import (
    async_engine,
    async_session_factory,
    get_async_session,
    get_database_url,
    is_sqlite_backend,
    safe_session_factory,
)
from lib.db.base import Base

_log = logging.getLogger(__name__)


async def init_db() -> None:
    """Run Alembic migrations to initialise / upgrade the database schema.

    Handles the transition from create_all to Alembic: if tables already exist
    but no alembic_version table is present, stamps the current head revision
    before running upgrade so existing databases migrate smoothly.
    """
    import asyncio
    from sqlalchemy import inspect as sa_inspect, text

    # Detect pre-Alembic databases (tables exist but no version tracking)
    async with async_engine.connect() as conn:
        tables = await conn.run_sync(lambda c: sa_inspect(c).get_table_names())
        has_app_tables = any(t in tables for t in ("tasks", "agent_sessions", "api_calls"))
        has_version = False
        if "alembic_version" in tables:
            row = (await conn.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).first()
            has_version = row is not None

    need_stamp = has_app_tables and not has_version

    from alembic.config import Config
    from alembic import command

    def _run_alembic():
        cfg = Config("alembic.ini")
        if need_stamp:
            _log.info("Detected pre-Alembic database, stamping current head")
            command.stamp(cfg, "head")
        command.upgrade(cfg, "head")

    await asyncio.get_event_loop().run_in_executor(None, _run_alembic)
    _log.info("Database schema is up to date")


async def close_db() -> None:
    """Dispose engine connections on shutdown.

    aiosqlite connections may already be dead when SSE tasks were cancelled,
    so we tolerate errors during pool cleanup.
    """
    try:
        await async_engine.dispose()
    except Exception:
        pass  # aiosqlite connections may already be dead after SSE task cancellation


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
