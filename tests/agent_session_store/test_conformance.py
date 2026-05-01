"""Run the SDK's official 14-contract SessionStore conformance suite."""

from __future__ import annotations

import pytest
from claude_agent_sdk.testing import run_session_store_conformance
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.agent_session_store.store import DbSessionStore
from lib.db.base import Base


@pytest.mark.asyncio
async def test_db_session_store_passes_sdk_conformance():
    """DbSessionStore must satisfy all required + optional SessionStore contracts.

    The SDK's conformance suite invokes ``make_store`` once per contract for
    isolation, and reuses the same ``_KEY`` ({project_key="proj",
    session_id="sess"}) across multiple contracts. We therefore build a brand
    new in-memory SQLite DB per invocation so contracts don't bleed state.
    """

    engines: list = []

    async def make_store():
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        engines.append(engine)
        async with engine.begin() as conn:
            # Import model modules to register tables on Base.metadata.
            import lib.agent_session_store.models  # noqa: F401
            import lib.db.models  # noqa: F401  (users / agent_sessions / config etc.)

            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        return DbSessionStore(factory, user_id="conformance")

    try:
        await run_session_store_conformance(make_store)
    finally:
        for engine in engines:
            await engine.dispose()
