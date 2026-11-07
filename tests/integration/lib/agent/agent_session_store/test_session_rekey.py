"""SessionRekey moves only named sessions to the target storage key; sessions recorded on both sides stay unchanged, and repeated execution is a no-op."""

from __future__ import annotations

from lib.agent.agent_session_store.rekey import SessionRekey
from lib.agent.agent_session_store.store import DbSessionStore

ENTRY = {"type": "user", "uuid": "u-1", "timestamp": "2026-05-01T00:00:00Z"}


async def _rekey(session_factory, rekey: SessionRekey) -> set[str]:
    async with session_factory() as session:
        moved = await rekey.rewrite_records(session)
        await session.commit()
    return moved


async def _keys_of(store: DbSessionStore, session_id: str, keys: tuple[str, ...]) -> list[str]:
    return [key for key in keys if await store.load({"project_key": key, "session_id": session_id})]


async def test_rekey_without_source_moves_named_sessions_from_any_key(session_factory):
    store = DbSessionStore(session_factory)
    await store.append({"project_key": "old-a", "session_id": "s1"}, [ENTRY])
    await store.append({"project_key": "old-b", "session_id": "s2"}, [ENTRY])
    await store.append({"project_key": "old-a", "session_id": "other"}, [ENTRY])
    rekey = SessionRekey.of(["s1", "s2", "missing"], "new")

    assert await _rekey(session_factory, rekey) == {"s1", "s2"}
    keys = ("old-a", "old-b", "new")
    assert await _keys_of(store, "s1", keys) == ["new"]
    assert await _keys_of(store, "s2", keys) == ["new"]
    assert await _keys_of(store, "other", keys) == ["old-a"]
    assert {row["session_id"] for row in await store.list_session_summaries("new")} == {"s1", "s2"}
    # Already under the target key: running again changes no records.
    assert await _rekey(session_factory, rekey) == set()


async def test_rekey_leaves_sessions_with_records_under_several_keys(session_factory):
    store = DbSessionStore(session_factory)
    await store.append({"project_key": "old", "session_id": "at-target"}, [ENTRY])
    await store.append({"project_key": "new", "session_id": "at-target"}, [ENTRY])
    await store.append({"project_key": "old-a", "session_id": "split"}, [ENTRY])
    await store.append({"project_key": "old-b", "session_id": "split"}, [ENTRY])

    assert await _rekey(session_factory, SessionRekey.of(["at-target", "split"], "new")) == set()
    assert await _keys_of(store, "at-target", ("old", "new")) == ["old", "new"]
    assert await _keys_of(store, "split", ("old-a", "old-b", "new")) == ["old-a", "old-b"]


async def test_rekey_with_source_only_moves_records_under_that_key(session_factory):
    store = DbSessionStore(session_factory)
    await store.append({"project_key": "elsewhere", "session_id": "s1"}, [ENTRY])

    assert await _rekey(session_factory, SessionRekey.of(["s1"], "new", source_key="old")) == set()
    assert await _keys_of(store, "s1", ("elsewhere", "new")) == ["elsewhere"]
