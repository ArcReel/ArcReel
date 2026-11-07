"""DbSessionStore.append basic semantics."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lib.agent.agent_session_store import AgentSessionEntry
from lib.agent.agent_session_store.store import DbSessionStore

KEY = {"project_key": "proj", "session_id": "sess"}


@pytest.mark.asyncio
async def test_append_writes_rows_in_call_order(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append(
        KEY,
        [
            {"type": "user", "uuid": "u-1", "timestamp": "2026-05-01T00:00:00Z"},
            {"type": "assistant", "uuid": "u-2", "timestamp": "2026-05-01T00:00:01Z"},
        ],
    )

    async with session_factory() as session:
        rows = (await session.execute(select(AgentSessionEntry).order_by(AgentSessionEntry.seq))).scalars().all()
    assert [r.seq for r in rows] == [0, 1]
    assert [r.uuid for r in rows] == ["u-1", "u-2"]
    assert all(r.user_id == "u1" for r in rows)


@pytest.mark.asyncio
async def test_append_dedups_by_uuid(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    entry = {"type": "user", "uuid": "dup", "timestamp": "t"}
    await store.append(KEY, [entry])
    await store.append(KEY, [entry])  # Replay must be idempotent.

    async with session_factory() as session:
        count = len((await session.execute(select(AgentSessionEntry))).scalars().all())
    assert count == 1


@pytest.mark.asyncio
async def test_append_does_not_dedup_when_uuid_missing(session_factory):
    """SDK protocol: entries without UUIDs (titles/tags/mode markers) are not deduplicated."""
    store = DbSessionStore(session_factory, user_id="u1")
    e = {"type": "tag", "tag": "demo"}
    await store.append(KEY, [e])
    await store.append(KEY, [e])

    async with session_factory() as session:
        count = len((await session.execute(select(AgentSessionEntry))).scalars().all())
    assert count == 2


@pytest.mark.asyncio
async def test_append_empty_is_noop(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append(KEY, [])  # Must neither raise nor create empty rows.

    async with session_factory() as session:
        count = len((await session.execute(select(AgentSessionEntry))).scalars().all())
    assert count == 0


@pytest.mark.asyncio
async def test_append_stores_non_string_type_and_uuid_as_absent(session_factory):
    """Local JSONL import does not validate entries: nonstring type / uuid values must not fail writes."""
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append(KEY, [{"type": ["user"], "uuid": {"v": 1}}])

    async with session_factory() as session:
        row = (await session.execute(select(AgentSessionEntry))).scalar_one()
    assert row.entry_type == ""
    assert row.uuid is None
