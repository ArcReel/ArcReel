"""Async engine and session factory configuration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Suppress noisy pool/connection errors caused by SSE task cancellation.
# When an SSE client disconnects, Starlette cancels the response task.
# aiosqlite connections that are being returned to the pool at that moment
# fail with CancelledError or "no active connection" during rollback.
# These are harmless — the connection was going to be discarded anyway.
logging.getLogger("sqlalchemy.pool.impl").setLevel(logging.CRITICAL)


def get_database_url() -> str:
    """Resolve DATABASE_URL from environment or default to SQLite."""
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return url
    project_root = Path(__file__).parent.parent.parent
    db_path = project_root / "projects" / ".arcreel.db"
    return f"sqlite+aiosqlite:///{db_path}"


def is_sqlite_backend() -> bool:
    """Check whether the configured backend is SQLite."""
    return get_database_url().startswith("sqlite")


def _create_engine():
    url = get_database_url()
    _is_sqlite = url.startswith("sqlite")

    connect_args = {}
    kwargs = {}
    if _is_sqlite:
        connect_args["timeout"] = 30
    else:
        kwargs.update(pool_size=10, max_overflow=20, pool_recycle=3600)

    engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
        **kwargs,
    )

    if _is_sqlite:

        @event.listens_for(engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


async_engine = _create_engine()

async_session_factory = async_sessionmaker(
    async_engine,
    expire_on_commit=False,
)


class _SafeSessionFactory:
    """A session factory whose context manager suppresses close() errors.

    When SSE clients disconnect, Starlette cancels the response task.
    aiosqlite connections that are mid-flight at that point raise
    ``OperationalError: no active connection`` during the implicit
    rollback inside ``AsyncSession.close()``.  This is harmless — the
    connection was going to be discarded anyway — so we swallow it.

    Usage is identical to ``async_session_factory``::

        async with safe_session_factory() as session:
            ...
    """

    def __call__(self) -> "_SafeSessionContext":
        return _SafeSessionContext(async_session_factory())


class _SafeSessionContext:
    """Async context manager wrapping AsyncSession with safe close."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        with contextlib.suppress(OperationalError, asyncio.CancelledError):
            await self._session.close()
        return False


safe_session_factory = _SafeSessionFactory()


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends generator for per-request AsyncSession."""
    async with async_session_factory() as session:
        yield session
