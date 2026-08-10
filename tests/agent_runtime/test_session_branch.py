"""分支会话服务：锚点校验、前缀落库、原会话 superseded、事件日志重建。

用真实 DB fixture（transcript 镜像表 + 会话元数据表 + 身份映射表同库），
断言的是服务的外部行为，不触及前缀是怎么拼出来的。
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from lib.agent_session_store import make_project_key
from lib.agent_session_store.store import DbSessionStore
from server.agent_runtime.event_log import EventLogService, EventLogStore
from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter
from server.agent_runtime.session_branch import BranchedSession, SessionBranchError, SessionBranchService
from server.agent_runtime.session_store import SessionMetaStore
from tests.agent_session_store.conftest import session_factory  # noqa: F401

pytestmark = pytest.mark.unit

PROJECT_NAME = "demo"
FIRST_USER_ENTRY = "user-first"
SECOND_USER_ENTRY = "user-second"


def _entry(uuid: str, parent: str | None, entry_type: str, session_id: str, text: str) -> dict:
    return {
        "uuid": uuid,
        "parentUuid": parent,
        "sessionId": session_id,
        "type": entry_type,
        "timestamp": "2026-01-01T00:00:00Z",
        "message": {"role": entry_type, "content": text},
    }


@pytest.fixture
async def branching(session_factory, tmp_path):  # noqa: F811
    """一个有两轮对话、两条用户消息都已建立身份映射的原会话。"""
    store = DbSessionStore(session_factory)
    meta_store = SessionMetaStore(session_factory=session_factory)
    log_store = EventLogStore(session_factory=session_factory)

    service = SessionBranchService(
        store=store,
        meta_store=meta_store,
        event_log_store=log_store,
        resolve_project_cwd=lambda _name: tmp_path,
    )

    session_id = str(uuid4())
    await meta_store.create(PROJECT_NAME, session_id)

    u1, a1, u2, a2 = (f"m{i}-{uuid4().hex[:8]}" for i in range(4))
    await store.append(
        {"project_key": make_project_key(tmp_path), "session_id": session_id},
        [
            _entry(u1, None, "user", session_id, "你好"),
            _entry(a1, u1, "assistant", session_id, "在"),
            _entry(u2, a1, "user", session_id, "再改一下"),
            _entry(a2, u2, "assistant", session_id, "改好了"),
        ],
    )
    await log_store.record_user_message_link(session_id, FIRST_USER_ENTRY, u1)
    await log_store.record_user_message_link(session_id, SECOND_USER_ENTRY, u2)

    return service, meta_store, store, log_store, session_id, tmp_path


async def test_branch_returns_a_resumable_new_session(branching):
    service, _, _, _, session_id, _ = branching

    branched = await service.branch(session_id, SECOND_USER_ENTRY)

    assert isinstance(branched, BranchedSession)
    assert branched.session_id != session_id
    assert branched.resumable is True


async def test_branch_copies_the_prefix_under_the_new_session(branching):
    service, _, store, _, session_id, tmp_path = branching

    branched = await service.branch(session_id, SECOND_USER_ENTRY)

    copied = await store.load({"project_key": make_project_key(tmp_path), "session_id": branched.session_id})
    assert copied is not None
    assert [e["type"] for e in copied] == ["user", "assistant"]


async def test_origin_is_superseded_and_the_pointer_is_traceable(branching):
    service, meta_store, _, _, session_id, _ = branching

    branched = await service.branch(session_id, SECOND_USER_ENTRY)

    origin = await meta_store.get(session_id)
    assert origin is not None, "原会话数据整行保留"
    assert origin.superseded_by == branched.session_id


async def test_superseded_session_leaves_the_list_and_the_new_one_joins(branching):
    service, meta_store, _, _, session_id, _ = branching

    branched = await service.branch(session_id, SECOND_USER_ENTRY)

    listed = {meta.id for meta in await meta_store.list(project_name=PROJECT_NAME)}
    assert session_id not in listed
    assert branched.session_id in listed


async def test_editing_the_first_message_yields_a_fresh_session(branching):
    """AC：空前缀 = 新会话——仍有新 session_id 与 superseded 指针，只是无可 resume 的前缀。"""
    service, meta_store, store, _, session_id, tmp_path = branching

    branched = await service.branch(session_id, FIRST_USER_ENTRY)

    assert branched.resumable is False
    assert await store.load({"project_key": make_project_key(tmp_path), "session_id": branched.session_id}) is None
    origin = await meta_store.get(session_id)
    assert origin is not None
    assert origin.superseded_by == branched.session_id


async def test_new_session_id_is_a_uuid_the_sdk_accepts(branching):
    """SDK 以 session_id 作路径分量并校验 UUID 形态，非 UUID 会被当作无此会话。"""
    from uuid import UUID

    service, _, _, _, session_id, _ = branching

    branched = await service.branch(session_id, SECOND_USER_ENTRY)

    assert str(UUID(branched.session_id)) == branched.session_id


async def test_anchor_from_another_session_is_rejected(branching):
    service, _, _, log_store, session_id, _ = branching
    other_session = str(uuid4())
    await log_store.record_user_message_link(other_session, "user-elsewhere", "whatever")

    with pytest.raises(SessionBranchError):
        await service.branch(session_id, "user-elsewhere")


async def test_unmapped_anchor_is_rejected(branching):
    service, _, _, _, session_id, _ = branching

    with pytest.raises(SessionBranchError):
        await service.branch(session_id, "user-never-recorded")


async def test_anchor_pointing_at_a_non_user_entry_is_rejected(branching):
    """身份映射表若因脏数据指向助手条目，仍按「锚点非用户消息」拒绝。"""
    service, _, store, log_store, session_id, tmp_path = branching
    main = await store.load({"project_key": make_project_key(tmp_path), "session_id": session_id})
    assert main is not None
    assistant_uuid = next(e["uuid"] for e in main if e["type"] == "assistant")
    await log_store.record_user_message_link(session_id, "user-mislinked", assistant_uuid)

    with pytest.raises(SessionBranchError):
        await service.branch(session_id, "user-mislinked")


async def test_unknown_session_is_rejected(branching):
    service, *_ = branching

    with pytest.raises(SessionBranchError):
        await service.branch(str(uuid4()), SECOND_USER_ENTRY)


async def test_rejection_leaves_no_new_session_behind(branching):
    service, meta_store, _, _, session_id, _ = branching
    before = {meta.id for meta in await meta_store.list(project_name=PROJECT_NAME)}

    with pytest.raises(SessionBranchError):
        await service.branch(session_id, "user-never-recorded")

    assert {meta.id for meta in await meta_store.list(project_name=PROJECT_NAME)} == before
    origin = await meta_store.get(session_id)
    assert origin is not None
    assert origin.superseded_by is None


async def test_branching_requires_the_db_transcript_store(session_factory, tmp_path):  # noqa: F811
    """ARCREEL_SDK_SESSION_STORE=off 时没有可复制的镜像，拒绝而非静默产出空会话。"""
    service = SessionBranchService(
        store=None,
        meta_store=SessionMetaStore(session_factory=session_factory),
        event_log_store=EventLogStore(session_factory=session_factory),
        resolve_project_cwd=lambda _name: tmp_path,
    )

    with pytest.raises(SessionBranchError):
        await service.branch(str(uuid4()), SECOND_USER_ENTRY)


async def test_new_session_event_log_is_rebuilt_from_the_copied_transcript(branching):
    """AC：新会话的事件日志按既有重放重建机制从 transcript 懒生成。"""
    service, _, store, log_store, session_id, tmp_path = branching
    event_log = EventLogService(log_store, SdkTranscriptAdapter(store=store))

    branched = await service.branch(session_id, SECOND_USER_ENTRY)
    assert not await log_store.has_entries(branched.session_id)

    await event_log.ensure_backfilled(branched.session_id, tmp_path)

    entries = await event_log.list_entries(branched.session_id, tmp_path)
    assert [entry["type"] for entry in entries] == ["user", "assistant"]
