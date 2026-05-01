"""DbSessionStore.load basic semantics."""

from __future__ import annotations

import pytest

from lib.agent_session_store.store import DbSessionStore


@pytest.mark.asyncio
async def test_load_returns_None_for_unknown_key(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    assert await store.load({"project_key": "proj", "session_id": "nope"}) is None


@pytest.mark.asyncio
async def test_load_round_trip(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    key = {"project_key": "proj", "session_id": "sess"}
    entries = [
        {"type": "user", "uuid": "a", "n": 1},
        {"type": "assistant", "uuid": "b", "n": 2},
    ]
    await store.append(key, entries)
    loaded = await store.load(key)
    assert loaded == entries


@pytest.mark.asyncio
async def test_load_subpath_isolated(session_factory):
    store = DbSessionStore(session_factory, user_id="u1")
    main = {"project_key": "proj", "session_id": "sess"}
    sub = {"project_key": "proj", "session_id": "sess", "subpath": "subagents/a"}
    await store.append(main, [{"type": "user", "uuid": "m"}])
    await store.append(sub, [{"type": "user", "uuid": "s"}])

    assert (await store.load(main)) == [{"type": "user", "uuid": "m"}]
    assert (await store.load(sub)) == [{"type": "user", "uuid": "s"}]
