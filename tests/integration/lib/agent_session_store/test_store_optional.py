"""Optional SessionStore methods: list_sessions / list_session_summaries / delete / list_subkeys."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from lib.agent_session_store import AgentSessionEntry, AgentSessionSummary
from lib.agent_session_store.store import DbSessionStore


@pytest.mark.asyncio
async def test_list_sessions_returns_unique_session_ids_with_mtime(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append({"project_key": "p", "session_id": "s2"}, [{"type": "user", "uuid": "b"}])
    await store.append({"project_key": "other", "session_id": "s3"}, [{"type": "user", "uuid": "c"}])

    items = await store.list_sessions("p")
    sids = sorted(x["session_id"] for x in items)
    assert sids == ["s1", "s2"]
    for x in items:
        assert isinstance(x["mtime"], int)
        assert x["mtime"] > 0


@pytest.mark.asyncio
async def test_list_sessions_excludes_subagent_subpaths(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append(
        {"project_key": "p", "session_id": "s1", "subpath": "subagents/x"},
        [{"type": "user", "uuid": "b"}],
    )
    items = await store.list_sessions("p")
    assert [x["session_id"] for x in items] == ["s1"]  # not duplicated


@pytest.mark.asyncio
async def test_list_session_summaries(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append({"project_key": "p", "session_id": "s2"}, [{"type": "user", "uuid": "b"}])

    summaries = await store.list_session_summaries("p")
    assert sorted(s["session_id"] for s in summaries) == ["s1", "s2"]
    for s in summaries:
        assert isinstance(s["mtime"], int)
        assert isinstance(s["data"], dict)


@pytest.mark.asyncio
async def test_delete_main_cascades_subpaths(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append(
        {"project_key": "p", "session_id": "s1", "subpath": "subagents/x"},
        [{"type": "user", "uuid": "b"}],
    )
    await store.delete({"project_key": "p", "session_id": "s1"})

    async with session_factory() as session:
        rows = (await session.execute(select(AgentSessionEntry))).scalars().all()
        sums = (await session.execute(select(AgentSessionSummary))).scalars().all()
    assert rows == []
    assert sums == []


@pytest.mark.asyncio
async def test_delete_subpath_targets_only_that_subpath(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    await store.append({"project_key": "p", "session_id": "s1"}, [{"type": "user", "uuid": "a"}])
    await store.append(
        {"project_key": "p", "session_id": "s1", "subpath": "subagents/x"},
        [{"type": "user", "uuid": "b"}],
    )
    await store.delete({"project_key": "p", "session_id": "s1", "subpath": "subagents/x"})

    main_load = await store.load({"project_key": "p", "session_id": "s1"})
    sub_load = await store.load({"project_key": "p", "session_id": "s1", "subpath": "subagents/x"})
    assert main_load is not None and len(main_load) == 1
    assert sub_load is None


@pytest.mark.asyncio
async def test_list_subkeys(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    base = {"project_key": "p", "session_id": "s1"}
    await store.append(base, [{"type": "user", "uuid": "main"}])
    await store.append({**base, "subpath": "subagents/a"}, [{"type": "user", "uuid": "x"}])
    await store.append({**base, "subpath": "subagents/b"}, [{"type": "user", "uuid": "y"}])

    keys = await store.list_subkeys(base)
    assert sorted(keys) == ["subagents/a", "subagents/b"]
