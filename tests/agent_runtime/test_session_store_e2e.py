"""End-to-end smoke: append → list → load via SDK helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from claude_agent_sdk import (
    get_session_messages_from_store,
    list_sessions_from_store,
)

from lib.agent_session_store import make_project_key
from lib.agent_session_store.store import DbSessionStore


@pytest.mark.asyncio
async def test_append_then_list_then_load_via_sdk_helpers(session_factory, tmp_path: Path):
    """Round-trip via SDK store helpers — proves the production read path works."""
    store = DbSessionStore(session_factory, user_id="e2e")

    project_cwd = tmp_path / "projects" / "e2e_demo"
    project_cwd.mkdir(parents=True)
    sid = "00000000-0000-0000-0000-000000000abc"

    # Append via our store implementation (production write path)
    key = {"project_key": make_project_key(project_cwd), "session_id": sid}
    # Note: SDK builds the conversation chain by walking parentUuid from the
    # most recent terminal backwards — so the assistant entry must point at
    # the user entry to be linked into a 2-message chain.
    entries = [
        {
            "type": "user",
            "uuid": "1",
            "timestamp": "2026-05-01T00:00:00Z",
            "message": {"content": "hello"},
        },
        {
            "type": "assistant",
            "uuid": "2",
            "parentUuid": "1",
            "timestamp": "2026-05-01T00:00:01Z",
            "message": {"content": "world"},
        },
    ]
    await store.append(key, entries)

    # Read back via SDK's public helpers (production read path used by
    # server/agent_runtime/service.py and sdk_transcript_adapter.py)
    listing = await list_sessions_from_store(store, directory=str(project_cwd))
    assert any(item.session_id == sid for item in listing), (
        "list_sessions_from_store should surface our appended session"
    )

    messages = await get_session_messages_from_store(store, sid, directory=str(project_cwd))
    assert len(messages) == 2
    assert getattr(messages[0], "type", None) == "user"
    assert getattr(messages[1], "type", None) == "assistant"
    # SDK's SessionMessage dataclass (v0.1.71) does not expose a timestamp
    # field, so the helper-level pass-through can't be asserted directly.
    # The adapter (sdk_transcript_adapter.py) tolerates this with
    # ``getattr(msg, "timestamp", None)``. Verify the underlying contract
    # we actually rely on: the timestamp survives the store round-trip and
    # is available via ``store.load()`` for any future consumer.
    raw = await store.load(key)
    assert [e.get("timestamp") for e in raw] == [
        "2026-05-01T00:00:00Z",
        "2026-05-01T00:00:01Z",
    ], "timestamps must round-trip through DbSessionStore verbatim"
