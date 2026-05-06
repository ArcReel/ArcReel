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
    # Verify the underlying contract: the timestamp survives the store
    # round-trip and is available via ``store.load()`` for any future consumer.
    raw = await store.load(key)
    assert [e.get("timestamp") for e in raw] == [
        "2026-05-01T00:00:00Z",
        "2026-05-01T00:00:01Z",
    ], "timestamps must round-trip through DbSessionStore verbatim"

    # Now exercise the production adapter path to verify timestamp backfill
    # works: the SDK SessionMessage lacks ``timestamp``, but
    # SdkTranscriptAdapter joins it back from store.load() on uuid so
    # downstream consumers (turn_grouper) keep getting stable timestamps.
    from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter

    adapter = SdkTranscriptAdapter(store=store)
    raw_adapted = await adapter.read_raw_messages(sid, project_cwd=str(project_cwd))
    assert len(raw_adapted) == 2
    timestamps = sorted(r["timestamp"] for r in raw_adapted if r["timestamp"])
    assert timestamps == ["2026-05-01T00:00:00Z", "2026-05-01T00:00:01Z"]


@pytest.mark.asyncio
async def test_partial_transcript_visible_after_simulated_crash(session_factory, tmp_path: Path):
    """eager flush durability：partial transcript 在进程"重启"后仍可读。

    模拟"服务进程崩溃"= 丢弃所有 in-memory 状态，仅保留 DB；新建 store
    实例（模拟新进程）继续读，验证之前 append 的 entries 完全可达。
    """
    store = DbSessionStore(session_factory, user_id="crash-recover")
    project_cwd = tmp_path / "projects" / "crash_demo"
    project_cwd.mkdir(parents=True)
    sid = "11111111-2222-3333-4444-555555555555"
    key = {"project_key": make_project_key(project_cwd), "session_id": sid}

    # 模拟"turn 进行中" eager flush 写入两条 entry（user + 部分 assistant）
    await store.append(
        key,
        [
            {
                "type": "user",
                "uuid": "1",
                "timestamp": "2026-05-06T10:00:00Z",
                "message": {"content": "long task"},
            },
        ],
    )
    await store.append(
        key,
        [
            {
                "type": "assistant",
                "uuid": "2",
                "parentUuid": "1",
                "timestamp": "2026-05-06T10:00:01Z",
                "message": {"content": "starting..."},
            },
        ],
    )

    # 模拟新进程：drop in-memory state, rebuild store
    store_after_restart = DbSessionStore(session_factory, user_id="crash-recover")

    raw = await store_after_restart.load(key)
    assert raw is not None and len(raw) == 2

    from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter

    adapter = SdkTranscriptAdapter(store=store_after_restart)
    msgs = await adapter.read_raw_messages(sid, project_cwd=str(project_cwd))
    assert len(msgs) == 2
    assert any(m.get("type") == "user" for m in msgs)
    assert any(m.get("type") == "assistant" for m in msgs)


@pytest.mark.asyncio
async def test_eager_persistence_independent_of_buffer(session_factory, tmp_path: Path):
    """长 turn 跨 buffer 驱逐：DB 应有完整 user/assistant 序列。

    DbSessionStore 的 append 调用与 in-memory buffer 完全解耦。本测试
    验证多次单条 append（模拟 SDK eager 模式）后 load 能拼出完整序列。
    """
    store = DbSessionStore(session_factory, user_id="long-turn")
    project_cwd = tmp_path / "projects" / "long_demo"
    project_cwd.mkdir(parents=True)
    sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    key = {"project_key": make_project_key(project_cwd), "session_id": sid}

    # 模拟 SDK eager 模式分多次 append（每个完整 frame 一次）
    frames = []
    for i in range(20):
        if i == 0:
            frames.append(
                {
                    "type": "user",
                    "uuid": str(i),
                    "timestamp": f"2026-05-06T10:00:{i:02d}Z",
                    "message": {"content": f"f{i}"},
                }
            )
        else:
            frames.append(
                {
                    "type": "assistant",
                    "uuid": str(i),
                    "parentUuid": str(i - 1),
                    "timestamp": f"2026-05-06T10:00:{i:02d}Z",
                    "message": {"content": f"f{i}"},
                }
            )
    for f in frames:
        await store.append(key, [f])

    raw = await store.load(key)
    assert raw is not None and len(raw) == 20
