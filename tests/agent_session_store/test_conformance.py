"""Run the SDK's official 14-contract SessionStore conformance suite."""

from __future__ import annotations

import os
import uuid as _uuid

import pytest
from claude_agent_sdk.testing import run_session_store_conformance
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.agent_session_store.store import DbSessionStore
from lib.db.base import Base


def _pg_url_from_env() -> str | None:
    url = os.environ.get("DATABASE_URL")
    if url and url.startswith("postgresql+asyncpg://"):
        return url
    return None


@pytest.mark.asyncio
async def test_db_session_store_passes_sdk_conformance():
    """DbSessionStore must satisfy all required + optional SessionStore contracts.

    The SDK's conformance suite invokes ``make_store`` once per contract for
    isolation, and reuses the same ``_KEY`` ({project_key="proj",
    session_id="sess"}) across multiple contracts. We therefore build a brand
    new database (in-memory SQLite, or a fresh PG schema when DATABASE_URL
    is a postgresql+asyncpg URL) per invocation so contracts don't bleed state.
    """

    pg_url = _pg_url_from_env()
    engines: list = []
    schemas: list[str] = []

    async def make_store():
        if pg_url:
            schema = f"conf_{_uuid.uuid4().hex[:12]}"
            engine = create_async_engine(
                pg_url,
                connect_args={"server_settings": {"search_path": schema}},
            )
            async with engine.begin() as conn:
                await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
            schemas.append(schema)
        else:
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        engines.append(engine)
        async with engine.begin() as conn:
            # Import model modules to register tables on Base.metadata.
            import lib.agent_session_store.models  # noqa: F401
            import lib.db.models  # noqa: F401  (users / agent_sessions / config etc.)

            await conn.run_sync(Base.metadata.create_all)
        # PG enforces FK constraints; seed the conformance user.
        if pg_url:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "INSERT INTO users (id, username, role, is_active, created_at, updated_at) "
                        "VALUES ('conformance', 'conformance', 'user', true, NOW(), NOW()) "
                        "ON CONFLICT (id) DO NOTHING"
                    )
                )
        factory = async_sessionmaker(engine, expire_on_commit=False)
        return DbSessionStore(factory, user_id="conformance")

    try:
        await run_session_store_conformance(make_store)
    finally:
        # On PG, drop the per-store schemas before disposing the engines.
        if pg_url:
            for engine, schema in zip(engines, schemas, strict=True):
                async with engine.begin() as conn:
                    await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        for engine in engines:
            await engine.dispose()
