"""append() must maintain agent_session_summaries via fold_session_summary."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from lib.agent_session_store import AgentSessionSummary
from lib.agent_session_store.store import DbSessionStore

KEY = {"project_key": "proj", "session_id": "sess"}


@pytest.mark.asyncio
async def test_summary_created_on_first_append(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append(KEY, [{"type": "user", "uuid": "u-1", "timestamp": "2026-05-01T00:00:00Z"}])

    async with session_factory() as session:
        rows = (await session.execute(select(AgentSessionSummary))).scalars().all()
    assert len(rows) == 1
    assert rows[0].project_key == "proj"
    assert rows[0].session_id == "sess"
    assert rows[0].mtime_ms > 0
    assert isinstance(rows[0].data, dict)


@pytest.mark.asyncio
async def test_summary_mtime_monotonic_across_appends(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append(KEY, [{"type": "user", "uuid": "a", "timestamp": "2026-05-01T00:00:00Z"}])

    async with session_factory() as session:
        first = (await session.execute(select(AgentSessionSummary))).scalar_one()
    first_mtime = first.mtime_ms

    await asyncio.sleep(0.01)  # ensure clock advances at ms granularity
    await store.append(KEY, [{"type": "user", "uuid": "b", "timestamp": "2026-05-01T00:00:01Z"}])

    async with session_factory() as session:
        second = (await session.execute(select(AgentSessionSummary))).scalar_one()
    assert second.mtime_ms >= first_mtime


@pytest.mark.asyncio
async def test_summary_skipped_for_subpath(session_factory):
    """SDK 协议：subagent transcripts (subpath != '') 不参与 main summary fold."""
    store = DbSessionStore(session_factory, user_id="u1")
    sub_key = {"project_key": "proj", "session_id": "sess", "subpath": "subagents/a"}
    await store.append(sub_key, [{"type": "user", "uuid": "x"}])

    async with session_factory() as session:
        rows = (await session.execute(select(AgentSessionSummary))).scalars().all()
    assert rows == []
