"""Prefix-fork store seam round trip: write, then read through public SDK helpers.

Verify both chain-integrity aspects: reading through SDK helpers establishes the external behavior
that the SDK can read the session, but ``SessionMessage`` drops ``parentUuid``. Assert absence of
dangling links and complete tool_use / tool_result pairing against raw ``store.load`` rows.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from claude_agent_sdk import (
    get_session_messages_from_store,
    get_subagent_messages_from_store,
    list_subagents_from_store,
    project_key_for_directory,
)

from lib.agent.agent_session_store.prefix_fork import (
    InvalidAnchorError,
    copy_session_prefix,
)
from lib.agent.agent_session_store.store import DbSessionStore

AGENT_ID = "abc123"
SUBAGENT_SUBPATH = f"subagents/agent-{AGENT_ID}"


def _entry(uuid: str, parent: str | None, entry_type: str, session_id: str, **extra) -> dict:
    return {
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": session_id,
        "type": entry_type,
        "timestamp": "2026-01-01T00:00:00Z",
        **extra,
    }


def _build_transcript(session_id: str) -> tuple[list[dict], list[dict], str]:
    """A three-turn transcript with a subagent; return (main timeline, child timeline, final user-message UUID).

    Turn one is plain conversation without subagents; turn two dispatches a Task subagent and fully
    completes it; the final user message anchors the fork. The three turns cover anchor positions
    before the first turn (empty prefix), before the second (conversation but no subagent dispatched),
    and before the final turn (the subagent must accompany the fork).
    """
    u0, a0, u1, a1, r1, a2, u2, a3 = (f"m{i}-{uuid4().hex[:8]}" for i in range(8))
    main = [
        _entry(u0, None, "user", session_id, message={"role": "user", "content": "你好"}),
        _entry(a0, u0, "assistant", session_id, message={"role": "assistant", "content": "在"}),
        _entry(u1, a0, "user", session_id, message={"role": "user", "content": "查一下"}),
        _entry(
            a1,
            u1,
            "assistant",
            session_id,
            message={
                "role": "assistant",
                "content": [{"type": "tool_use", "id": "tu-task-1", "name": "Task", "input": {}}],
            },
        ),
        _entry(
            r1,
            a1,
            "user",
            session_id,
            message={
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "tu-task-1", "content": "done"}],
            },
            toolUseResult={"agentId": AGENT_ID},
        ),
        _entry(a2, r1, "assistant", session_id, message={"role": "assistant", "content": "查到了"}),
        _entry(u2, a2, "user", session_id, message={"role": "user", "content": "再改一下"}),
        _entry(a3, u2, "assistant", session_id, message={"role": "assistant", "content": "改好了"}),
    ]
    s1, s2 = (f"s{i}-{uuid4().hex[:8]}" for i in range(2))
    subagent = [
        {"type": "agent_metadata", "agentId": AGENT_ID, "sessionId": session_id},
        _entry(s1, None, "user", session_id, isSidechain=True, message={"role": "user", "content": "子智能体"}),
        _entry(s2, s1, "assistant", session_id, isSidechain=True, message={"role": "assistant", "content": "子结果"}),
    ]
    return main, subagent, u2


@pytest.fixture
async def seeded(session_factory, tmp_path):
    """Write a transcript into the store and return the context needed for copying."""
    store = DbSessionStore(session_factory)
    project_key = project_key_for_directory(str(tmp_path))
    session_id = str(uuid4())
    main, subagent, anchor = _build_transcript(session_id)

    await store.append({"project_key": project_key, "session_id": session_id}, main)
    await store.append({"project_key": project_key, "session_id": session_id, "subpath": SUBAGENT_SUBPATH}, subagent)
    return store, project_key, session_id, anchor, tmp_path


async def _copy(seeded, anchor: str | None = None) -> tuple[str, object]:
    store, project_key, session_id, default_anchor, _ = seeded
    new_id = str(uuid4())
    result = await copy_session_prefix(
        store,
        project_key=project_key,
        session_id=session_id,
        anchor_uuid=anchor or default_anchor,
        new_session_id=new_id,
    )
    return new_id, result


async def test_prefix_stops_before_anchor(seeded):
    store, project_key, _, _, _ = seeded
    new_id, _ = await _copy(seeded)

    copied = await store.load({"project_key": project_key, "session_id": new_id})

    assert copied is not None
    # Neither the anchor nor subsequent Agent replies enter the prefix.
    assert [e["type"] for e in copied] == ["user", "assistant", "user", "assistant", "user", "assistant"]
    assert all(e["sessionId"] == new_id for e in copied)


async def test_sdk_reads_back_the_full_chain(seeded):
    """AC: public SDK helpers read the new session as a complete chain including child subagent timelines."""
    _, _, _, _, tmp_path = seeded
    store, _, _, _, _ = seeded
    new_id, result = await _copy(seeded)

    messages = await get_session_messages_from_store(store, new_id, directory=str(tmp_path))
    assert [m.type for m in messages] == ["user", "assistant", "user", "assistant", "user", "assistant"]
    assert {m.session_id for m in messages} == {new_id}

    assert result.subagent_subpaths == (SUBAGENT_SUBPATH,)
    assert await list_subagents_from_store(store, new_id, directory=str(tmp_path))
    sub_messages = await get_subagent_messages_from_store(store, new_id, AGENT_ID, directory=str(tmp_path))
    assert [m.type for m in sub_messages] == ["user", "assistant"]


async def test_parent_chain_has_no_dangling_links(seeded):
    store, project_key, _, _, _ = seeded
    new_id, _ = await _copy(seeded)

    copied = await store.load({"project_key": project_key, "session_id": new_id})
    assert copied is not None

    known: set[str] = set()
    for entry in copied:
        parent = entry["parentUuid"]
        assert parent is None or parent in known, f"dangling parentUuid on {entry['uuid']}"
        known.add(entry["uuid"])


async def test_tool_use_and_tool_result_stay_paired(seeded):
    store, project_key, _, _, _ = seeded
    new_id, _ = await _copy(seeded)

    copied = await store.load({"project_key": project_key, "session_id": new_id})
    assert copied is not None

    issued: set[str] = set()
    resolved: set[str] = set()
    for entry in copied:
        for block in entry.get("message", {}).get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") == "tool_use":
                issued.add(block["id"])
            elif block.get("type") == "tool_result":
                resolved.add(block["tool_use_id"])
    assert issued
    assert issued == resolved


async def test_subagent_metadata_entry_is_carried(seeded):
    """``agent_metadata`` rows accompany child timelines; SDK materialization uses them to write .meta.json sidecars."""
    store, project_key, _, _, _ = seeded
    new_id, _ = await _copy(seeded)

    sub = await store.load({"project_key": project_key, "session_id": new_id, "subpath": SUBAGENT_SUBPATH})
    assert sub is not None
    metadata = [e for e in sub if e["type"] == "agent_metadata"]
    assert len(metadata) == 1
    assert metadata[0]["sessionId"] == new_id
    # Only transcript rows carry provenance markers; sidecar files must not gain fields.
    assert "forkedFrom" not in metadata[0]


async def test_origin_session_is_untouched(seeded):
    store, project_key, session_id, _, _ = seeded
    before = await store.load({"project_key": project_key, "session_id": session_id})
    await _copy(seeded)
    after = await store.load({"project_key": project_key, "session_id": session_id})

    assert before == after


async def test_empty_prefix_copies_nothing(seeded):
    """AC: editing the first message gives an empty prefix, equivalent to a new session."""
    store, project_key, session_id, _, _ = seeded
    main = await store.load({"project_key": project_key, "session_id": session_id})
    assert main is not None
    first_user = main[0]["uuid"]

    new_id, result = await _copy(seeded, anchor=first_user)

    assert result.entries_copied == 0
    assert result.subagent_subpaths == ()
    assert await store.load({"project_key": project_key, "session_id": new_id}) is None


async def test_leading_metadata_rows_do_not_count_as_a_prefix(session_factory, tmp_path):
    """Opening transcript metadata rows do not form a prefix; emptiness concerns conversation entries, not row counts."""
    store = DbSessionStore(session_factory)
    project_key = project_key_for_directory(str(tmp_path))
    session_id = str(uuid4())
    anchor = f"m-{uuid4().hex[:8]}"
    await store.append(
        {"project_key": project_key, "session_id": session_id},
        [
            {"type": "file-history-snapshot", "sessionId": session_id, "snapshot": {}},
            _entry(anchor, None, "user", session_id, message={"role": "user", "content": "第一句"}),
        ],
    )

    new_id = str(uuid4())
    result = await copy_session_prefix(
        store,
        project_key=project_key,
        session_id=session_id,
        anchor_uuid=anchor,
        new_session_id=new_id,
    )

    assert result.entries_copied == 0
    assert await store.load({"project_key": project_key, "session_id": new_id}) is None


async def test_subagent_started_after_the_anchor_is_not_carried(seeded):
    """A subagent dispatched after the anchor does not accompany the fork: it belongs to the discarded branch."""
    store, project_key, session_id, _, _ = seeded
    main = await store.load({"project_key": project_key, "session_id": session_id})
    assert main is not None
    # Second-turn user message: the prefix contains turn-one conversation, but Task has not been issued.
    new_id, result = await _copy(seeded, anchor=main[2]["uuid"])

    assert result.entries_copied == 2
    assert result.subagent_subpaths == ()
    assert await store.load({"project_key": project_key, "session_id": new_id, "subpath": SUBAGENT_SUBPATH}) is None


async def test_nested_subagent_subpath_is_carried(session_factory, tmp_path):
    """SDK subagent paths may nest under ``subagents/workflows/<runId>/`` and still accompany the fork."""
    store = DbSessionStore(session_factory)
    project_key = project_key_for_directory(str(tmp_path))
    session_id = str(uuid4())
    main, subagent, anchor = _build_transcript(session_id)
    nested_subpath = f"subagents/workflows/run-1/agent-{AGENT_ID}"
    await store.append({"project_key": project_key, "session_id": session_id}, main)
    await store.append({"project_key": project_key, "session_id": session_id, "subpath": nested_subpath}, subagent)

    new_id = str(uuid4())
    result = await copy_session_prefix(
        store,
        project_key=project_key,
        session_id=session_id,
        anchor_uuid=anchor,
        new_session_id=new_id,
    )

    assert result.subagent_subpaths == (nested_subpath,)
    sub_messages = await get_subagent_messages_from_store(store, new_id, AGENT_ID, directory=str(tmp_path))
    assert [m.type for m in sub_messages] == ["user", "assistant"]


async def test_unknown_anchor_is_rejected(seeded):
    with pytest.raises(InvalidAnchorError):
        await _copy(seeded, anchor="not-in-this-session")


async def test_assistant_entry_is_not_a_valid_anchor(seeded):
    store, project_key, session_id, _, _ = seeded
    main = await store.load({"project_key": project_key, "session_id": session_id})
    assert main is not None
    assistant_uuid = next(e["uuid"] for e in main if e["type"] == "assistant")

    with pytest.raises(InvalidAnchorError):
        await _copy(seeded, anchor=assistant_uuid)


async def test_an_entry_carrying_tool_results_is_not_a_valid_anchor(seeded):
    """Tool results also use type:"user"; anchoring one would leave its paired tool_use dangling at the prefix end."""
    store, project_key, session_id, _, _ = seeded
    main = await store.load({"project_key": project_key, "session_id": session_id})
    assert main is not None
    mixed_uuid = f"mixed-{uuid4().hex[:8]}"
    await store.append(
        {"project_key": project_key, "session_id": session_id},
        [
            _entry(
                mixed_uuid,
                main[-1]["uuid"],
                "user",
                session_id,
                message={
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_x", "content": "ok"},
                        {"type": "text", "text": "顺带说一句"},
                    ],
                },
            )
        ],
    )

    with pytest.raises(InvalidAnchorError):
        await _copy(seeded, anchor=mixed_uuid)


async def test_legacy_entry_with_non_string_type_does_not_break_the_fork(session_factory, tmp_path):
    """Historical payload type values may be nonstrings; treat them as non-conversation entries during forking."""
    store = DbSessionStore(session_factory)
    project_key = project_key_for_directory(str(tmp_path))
    session_id = str(uuid4())
    anchor = f"m-{uuid4().hex[:8]}"
    await store.append(
        {"project_key": project_key, "session_id": session_id},
        [
            {"type": {"legacy": True}, "sessionId": session_id},
            _entry(anchor, None, "user", session_id, message={"role": "user", "content": "第一句"}),
        ],
    )

    result = await copy_session_prefix(
        store,
        project_key=project_key,
        session_id=session_id,
        anchor_uuid=anchor,
        new_session_id=str(uuid4()),
    )

    assert result.entries_copied == 0
