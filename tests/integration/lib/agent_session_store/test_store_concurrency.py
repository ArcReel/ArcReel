"""Concurrent appends to the same session must serialize cleanly."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from lib.agent_session_store import AgentSessionEntry
from lib.agent_session_store.store import DbSessionStore


@pytest.mark.sqlite_only
@pytest.mark.asyncio
async def test_concurrent_append_no_seq_collision(file_session_factory):
    store = DbSessionStore(file_session_factory, user_id="u1")
    key = {"project_key": "proj", "session_id": "sess"}

    async def push(i: int):
        await store.append(key, [{"type": "user", "uuid": f"u-{i}", "n": i}])

    await asyncio.gather(*(push(i) for i in range(20)))

    async with file_session_factory() as session:
        rows = (await session.execute(select(AgentSessionEntry).order_by(AgentSessionEntry.seq))).scalars().all()

    assert len(rows) == 20
    assert [r.seq for r in rows] == list(range(20))
    assert {r.uuid for r in rows} == {f"u-{i}" for i in range(20)}
