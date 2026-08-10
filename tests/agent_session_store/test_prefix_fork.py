"""前缀分叉的 store 缝往返测试：写入后经 SDK 公开 helper 读回。

链完整性两条腿都验：SDK helper 读回满足「SDK 能读回」这一外部行为，但
``SessionMessage`` 丢弃了 ``parentUuid``——链无悬空、tool_use / tool_result
配对完整这两条要回到 ``store.load`` 的原始行上断言。
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

from lib.agent_session_store.prefix_fork import (
    InvalidAnchorError,
    copy_session_prefix,
)
from lib.agent_session_store.store import DbSessionStore

pytestmark = pytest.mark.integration

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
    """一段有 subagent 的三轮 transcript，返回 (主线, 子时间线, 末轮用户消息 uuid)。

    首轮是不派 subagent 的纯对话，次轮派出 Task subagent 并完整收尾，末轮的
    用户消息即分叉锚点。三轮各自对应一类锚点位置：首轮之前（空前缀）、次轮
    之前（有对话但 subagent 尚未派出）、末轮之前（subagent 应随行）。
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
        _entry(s1, None, "user", session_id, isSidechain=True, message={"role": "user", "content": "子任务"}),
        _entry(s2, s1, "assistant", session_id, isSidechain=True, message={"role": "assistant", "content": "子结果"}),
    ]
    return main, subagent, u2


@pytest.fixture
async def seeded(session_factory, tmp_path):
    """把一段 transcript 写进 store，返回复制所需的上下文。"""
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
    # 锚点及其后的助手回复都不进前缀。
    assert [e["type"] for e in copied] == ["user", "assistant", "user", "assistant", "user", "assistant"]
    assert all(e["sessionId"] == new_id for e in copied)


async def test_sdk_reads_back_the_full_chain(seeded):
    """AC：新会话经 SDK 公开 helper 读回为完整链，含 subagent 子时间线。"""
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
    assert issued and issued == resolved


async def test_subagent_metadata_entry_is_carried(seeded):
    """``agent_metadata`` 行随子时间线同行，SDK 物化时靠它写 .meta.json 旁车。"""
    store, project_key, _, _, _ = seeded
    new_id, _ = await _copy(seeded)

    sub = await store.load({"project_key": project_key, "session_id": new_id, "subpath": SUBAGENT_SUBPATH})
    assert sub is not None
    metadata = [e for e in sub if e["type"] == "agent_metadata"]
    assert len(metadata) == 1
    assert metadata[0]["sessionId"] == new_id
    # transcript 行才带溯源标记，旁车文件不该多出字段。
    assert "forkedFrom" not in metadata[0]


async def test_origin_session_is_untouched(seeded):
    store, project_key, session_id, _, _ = seeded
    before = await store.load({"project_key": project_key, "session_id": session_id})
    await _copy(seeded)
    after = await store.load({"project_key": project_key, "session_id": session_id})

    assert before == after


async def test_empty_prefix_copies_nothing(seeded):
    """AC：编辑第一条消息 → 空前缀 = 新会话。"""
    store, project_key, session_id, _, _ = seeded
    main = await store.load({"project_key": project_key, "session_id": session_id})
    assert main is not None
    first_user = main[0]["uuid"]

    new_id, result = await _copy(seeded, anchor=first_user)

    assert result.entries_copied == 0
    assert result.subagent_subpaths == ()
    assert await store.load({"project_key": project_key, "session_id": new_id}) is None


async def test_leading_metadata_rows_do_not_count_as_a_prefix(session_factory, tmp_path):
    """transcript 开头的元数据行不构成前缀——空看的是会话条目，不是行数。"""
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
    """锚点之后才派出的 subagent 不随行——它属于被丢弃的分支。"""
    store, project_key, session_id, _, _ = seeded
    main = await store.load({"project_key": project_key, "session_id": session_id})
    assert main is not None
    # 次轮的用户消息：前缀有首轮对话，但 Task 尚未发出。
    new_id, result = await _copy(seeded, anchor=main[2]["uuid"])

    assert result.entries_copied == 2
    assert result.subagent_subpaths == ()
    assert await store.load({"project_key": project_key, "session_id": new_id, "subpath": SUBAGENT_SUBPATH}) is None


async def test_nested_subagent_subpath_is_carried(session_factory, tmp_path):
    """SDK 的 subagent 子路径可嵌套在 ``subagents/workflows/<runId>/`` 下，同样随行。"""
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
