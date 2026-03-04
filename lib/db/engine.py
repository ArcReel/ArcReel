"""Async engine and session factory configuration."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


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
    if _is_sqlite:
        connect_args["timeout"] = 30

    engine = create_async_engine(
        url,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
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


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends generator for per-request AsyncSession."""
    async with async_session_factory() as session:
        yield session
