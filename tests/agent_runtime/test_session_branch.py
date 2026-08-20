"""分支会话服务：锚点校验、前缀落库、原会话 superseded、事件日志重建。

用真实 DB fixture（transcript 镜像表 + 会话元数据表 + 身份映射表同库），
断言的是服务的外部行为，不触及前缀是怎么拼出来的。
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from lib.agent_session_store import make_project_key
from lib.agent_session_store.store import DbSessionStore
from server.agent_runtime.event_log import EventLogService, EventLogStore
from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter
from server.agent_runtime.session_branch import BranchedSession, SessionBranchError, SessionBranchService
from server.agent_runtime.session_store import SessionMetaStore
from tests.agent_runtime.conftest import make_transcript_entry

pytestmark = pytest.mark.integration

PROJECT_NAME = "demo"
FIRST_USER_ENTRY = "user-first"
SECOND_USER_ENTRY = "user-second"


@pytest.fixture
async def branching(session_factory, tmp_path):
    """一个有两轮对话、两条用户消息都已建立身份映射的原会话。"""
    store = DbSessionStore(session_factory)
    meta_store = SessionMetaStore(session_factory=session_factory)
    log_store = EventLogStore(session_factory=session_factory)

    service = SessionBranchService(
        store=store,
        meta_store=meta_store,
        event_log=EventLogService(log_store, SdkTranscriptAdapter(store=store)),
        resolve_project_cwd=lambda _name: tmp_path,
    )

    session_id = str(uuid4())
    await meta_store.create(PROJECT_NAME, session_id)

    u1, a1, u2, a2 = (f"m{i}-{uuid4().hex[:8]}" for i in range(4))
    await store.append(
        {"project_key": make_project_key(tmp_path), "session_id": session_id},
        [
            make_transcript_entry(u1, None, "user", session_id, "你好"),
            make_transcript_entry(a1, u1, "assistant", session_id, "在"),
            make_transcript_entry(u2, a1, "user", session_id, "再改一下"),
            make_transcript_entry(a2, u2, "assistant", session_id, "改好了"),
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
    """身份映射表若因脏数据指向智能体条目，仍按「锚点非用户消息」拒绝。"""
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


async def test_second_branch_of_the_same_origin_is_refused(branching):
    """已被取代的会话再分叉即冲突：首个指针不被覆盖，冲突分支的元数据与 transcript 都不留。"""
    service, meta_store, store, _, session_id, tmp_path = branching
    project_key = make_project_key(tmp_path)
    first = await service.branch(session_id, SECOND_USER_ENTRY)
    sessions_before = {row["session_id"] for row in await store.list_sessions(project_key)}

    # 同一个非空锚点：第二次分叉会先把前缀复制到新 session_id 下，冲突后须整个撤回。
    with pytest.raises(SessionBranchError):
        await service.branch(session_id, SECOND_USER_ENTRY)

    origin = await meta_store.get(session_id)
    assert origin is not None
    assert origin.superseded_by == first.session_id
    assert {meta.id for meta in await meta_store.list(project_name=PROJECT_NAME)} == {first.session_id}
    assert {row["session_id"] for row in await store.list_sessions(project_key)} == sessions_before


async def test_deleting_the_branch_brings_the_origin_back_to_the_list(branching):
    """删掉分支会话后原会话必须回到列表——否则它会带着一个指向不存在会话的指针永久消失。"""
    service, meta_store, _, _, session_id, _ = branching
    branched = await service.branch(session_id, SECOND_USER_ENTRY)

    deleted = await meta_store.delete(branched.session_id)
    assert deleted

    origin = await meta_store.get(session_id)
    assert origin is not None
    assert origin.superseded_by is None
    assert {meta.id for meta in await meta_store.list(project_name=PROJECT_NAME)} == {session_id}


async def test_deleting_a_middle_branch_keeps_the_origin_pointing_at_the_live_end(branching):
    """链中间被删时前身改指后继：否则前身与末端会同时出现在列表里，取代关系断掉。"""
    service, meta_store, store, log_store, session_id, tmp_path = branching
    middle = await service.branch(session_id, SECOND_USER_ENTRY)
    # 分支会话被续写后，新消息才有身份映射、才能再被分叉——链由此形成。
    project_key = make_project_key(tmp_path)
    copied = await store.load({"project_key": project_key, "session_id": middle.session_id})
    assert copied is not None
    continued = f"m-cont-{uuid4().hex[:8]}"
    await store.append(
        {"project_key": project_key, "session_id": middle.session_id},
        [make_transcript_entry(continued, copied[-1]["uuid"], "user", middle.session_id, "继续")],
    )
    await log_store.record_user_message_link(middle.session_id, "user-in-branch", continued)
    tail = await service.branch(middle.session_id, "user-in-branch")

    deleted = await meta_store.delete(middle.session_id)
    assert deleted

    origin = await meta_store.get(session_id)
    assert origin is not None
    assert origin.superseded_by == tail.session_id, "前身改指仍然活着的末端"
    assert {meta.id for meta in await meta_store.list(project_name=PROJECT_NAME)} == {tail.session_id}


class _StoreGone(Exception):
    """store 在复制途中失联。"""


class _StoreFailingAfterMainWrite:
    """主 transcript 已写入、收集子代理子路径时失败的 store。"""

    def __init__(self, inner: DbSessionStore) -> None:
        self._inner = inner

    async def load(self, key: dict) -> list[dict] | None:
        return await self._inner.load(key)

    async def append(self, key: dict, entries: list[dict]) -> None:
        await self._inner.append(key, entries)

    async def list_subkeys(self, key: dict) -> list[str]:
        raise _StoreGone

    async def delete(self, key: dict) -> None:
        await self._inner.delete(key)


class _MetaStoreLosingTheCallerAfterCommit(SessionMetaStore):
    """指针已提交、调用方在拿到结果前被取消。"""

    async def mark_superseded(self, session_id: str, superseded_by: str) -> bool:
        await super().mark_superseded(session_id, superseded_by)
        raise asyncio.CancelledError


async def _seed_session_with_a_subagent(store, meta_store, log_store, project_key, tmp_path):
    """一个前缀里派出过子任务的原会话，锚点是末轮用户消息。"""
    session_id = str(uuid4())
    await meta_store.create(PROJECT_NAME, session_id)
    u1, a1, r1, u2 = (f"m{i}-{uuid4().hex[:8]}" for i in range(4))
    await store.append(
        {"project_key": project_key, "session_id": session_id},
        [
            make_transcript_entry(u1, None, "user", session_id, "查一下"),
            make_transcript_entry(a1, u1, "assistant", session_id, "好"),
            {**make_transcript_entry(r1, a1, "user", session_id, "done"), "toolUseResult": {"agentId": "abc123"}},
            make_transcript_entry(u2, r1, "user", session_id, "再改一下"),
        ],
    )
    await log_store.record_user_message_link(session_id, SECOND_USER_ENTRY, u2)
    return session_id


async def test_failure_midway_through_copying_leaves_no_trace_of_the_new_session(session_factory, tmp_path):
    """store 的每次 append 各自提交，半份 transcript 会被会话枚举看见——中途失败须整体撤回。"""
    store = DbSessionStore(session_factory)
    meta_store = SessionMetaStore(session_factory=session_factory)
    log_store = EventLogStore(session_factory=session_factory)
    project_key = make_project_key(tmp_path)
    session_id = await _seed_session_with_a_subagent(store, meta_store, log_store, project_key, tmp_path)
    sessions_before = {row["session_id"] for row in await store.list_sessions(project_key)}

    service = SessionBranchService(
        store=_StoreFailingAfterMainWrite(store),
        meta_store=meta_store,
        event_log=EventLogService(log_store, SdkTranscriptAdapter(store=store)),
        resolve_project_cwd=lambda _name: tmp_path,
    )

    with pytest.raises(_StoreGone):
        await service.branch(session_id, SECOND_USER_ENTRY)

    assert {row["session_id"] for row in await store.list_sessions(project_key)} == sessions_before
    assert {meta.id for meta in await meta_store.list(project_name=PROJECT_NAME)} == {session_id}


async def test_pointer_is_withdrawn_when_the_caller_is_cancelled_after_it_commits(session_factory, tmp_path):
    """指针提交后当场被取消：撤回不能只删新会话，否则原会话指向一个不存在的会话且自己也从列表消失。"""
    store = DbSessionStore(session_factory)
    meta_store = _MetaStoreLosingTheCallerAfterCommit(session_factory=session_factory)
    log_store = EventLogStore(session_factory=session_factory)
    project_key = make_project_key(tmp_path)
    session_id = await _seed_session_with_a_subagent(store, meta_store, log_store, project_key, tmp_path)
    sessions_before = {row["session_id"] for row in await store.list_sessions(project_key)}

    service = SessionBranchService(
        store=store,
        meta_store=meta_store,
        event_log=EventLogService(log_store, SdkTranscriptAdapter(store=store)),
        resolve_project_cwd=lambda _name: tmp_path,
    )

    with pytest.raises(asyncio.CancelledError):
        await service.branch(session_id, SECOND_USER_ENTRY)

    origin = await meta_store.get(session_id)
    assert origin is not None
    assert origin.superseded_by is None, "原会话须回到未被取代的状态"
    assert {meta.id for meta in await meta_store.list(project_name=PROJECT_NAME)} == {session_id}
    assert {row["session_id"] for row in await store.list_sessions(project_key)} == sessions_before


async def test_branching_requires_the_db_transcript_store(session_factory, tmp_path):
    """ARCREEL_SDK_SESSION_STORE=off 时没有可复制的镜像，拒绝而非静默产出空会话。"""
    service = SessionBranchService(
        store=None,
        meta_store=SessionMetaStore(session_factory=session_factory),
        event_log=EventLogService(EventLogStore(session_factory=session_factory), SdkTranscriptAdapter(store=None)),
        resolve_project_cwd=lambda _name: tmp_path,
    )

    with pytest.raises(SessionBranchError):
        await service.branch(str(uuid4()), SECOND_USER_ENTRY)


async def _seed_three_rounds(store, meta_store, project_key):
    """三轮对话的原会话，只有末轮用户消息建了身份映射。

    前两轮模拟「早于映射表的历史」：分支会话拿到的前缀条目同样没有映射行，
    正是恒等回退要服务的那一类锚点。
    """
    session_id = str(uuid4())
    await meta_store.create(PROJECT_NAME, session_id)
    uuids = [f"m{i}-{uuid4().hex[:8]}" for i in range(6)]
    parents: list[str | None] = [None, *uuids[:-1]]
    await store.append(
        {"project_key": project_key, "session_id": session_id},
        [
            make_transcript_entry(uuid, parent, "user" if index % 2 == 0 else "assistant", session_id, f"第{index}条")
            for index, (uuid, parent) in enumerate(zip(uuids, parents, strict=True))
        ],
    )
    return session_id, uuids


async def test_a_prefix_message_without_a_mapping_row_can_still_be_rewritten(branching):
    """AC：分支会话的前缀历史消息没有映射行，靠恒等性仍能作锚点再分叉。"""
    service, meta_store, store, log_store, _, tmp_path = branching
    project_key = make_project_key(tmp_path)
    origin, uuids = await _seed_three_rounds(store, meta_store, project_key)
    await log_store.record_user_message_link(origin, "user-third", uuids[4])

    branched = await service.branch(origin, "user-third")
    # 分支会话的前缀是前两轮，其中第二条用户消息在新会话里没有任何映射行。
    again = await service.branch(branched.session_id, uuids[2])

    assert again.resumable is True
    copied = await store.load({"project_key": project_key, "session_id": again.session_id})
    assert copied is not None
    assert [e["type"] for e in copied] == ["user", "assistant"]


async def test_rewriting_recurses_through_branches_of_branches(branching):
    """AC：分支的分支同样可再改写——uuid 随前缀复制原样保留，恒等性递归成立。"""
    service, meta_store, store, log_store, _, tmp_path = branching
    project_key = make_project_key(tmp_path)
    origin, uuids = await _seed_three_rounds(store, meta_store, project_key)
    await log_store.record_user_message_link(origin, "user-third", uuids[4])

    first = await service.branch(origin, "user-third")
    second = await service.branch(first.session_id, uuids[2])
    third = await service.branch(second.session_id, uuids[0])

    assert third.session_id not in {origin, first.session_id, second.session_id}
    # 锚点是整段历史的第一条，前缀为空——新会话就是全新会话。
    assert third.resumable is False


async def test_a_continued_branch_message_is_anchored_through_the_mapping(branching):
    """续写进分支会话的新消息走映射表这一段，与前缀历史消息两类锚点并存。"""
    service, _, store, log_store, session_id, tmp_path = branching
    project_key = make_project_key(tmp_path)
    branched = await service.branch(session_id, SECOND_USER_ENTRY)
    copied = await store.load({"project_key": project_key, "session_id": branched.session_id})
    assert copied is not None
    continued = f"m-cont-{uuid4().hex[:8]}"
    await store.append(
        {"project_key": project_key, "session_id": branched.session_id},
        [make_transcript_entry(continued, copied[-1]["uuid"], "user", branched.session_id, "继续")],
    )
    await log_store.record_user_message_link(branched.session_id, "user-in-branch", continued)

    again = await service.branch(branched.session_id, "user-in-branch")

    assert again.resumable is True


async def test_branch_records_where_it_forked_from(branching):
    """分叉血统在 branch 时刻写入：父会话 + transcript 域的锚点 uuid。"""
    service, meta_store, store, _, session_id, tmp_path = branching
    main = await store.load({"project_key": make_project_key(tmp_path), "session_id": session_id})
    assert main is not None
    anchor_uuid = [e["uuid"] for e in main if e["type"] == "user"][1]

    branched = await service.branch(session_id, SECOND_USER_ENTRY)

    meta = await meta_store.get(branched.session_id)
    assert meta is not None
    assert meta.fork_parent_session_id == session_id
    assert meta.fork_anchor_uuid == anchor_uuid
    origin = await meta_store.get(session_id)
    assert origin is not None
    assert origin.fork_parent_session_id is None, "原会话不是分叉产物"


async def test_a_tool_result_carrying_entry_is_refused_as_an_anchor(branching):
    """transcript 把工具回执也写成 type:"user"；混排 tool_result 的条目作锚会悬空前一轮 tool_use。"""
    service, meta_store, store, _, _, tmp_path = branching
    project_key = make_project_key(tmp_path)
    session_id = str(uuid4())
    await meta_store.create(PROJECT_NAME, session_id)
    u1, a1, mixed = (f"m{i}-{uuid4().hex[:8]}" for i in range(3))
    await store.append(
        {"project_key": project_key, "session_id": session_id},
        [
            make_transcript_entry(u1, None, "user", session_id, "跑一下"),
            make_transcript_entry(a1, u1, "assistant", session_id, "好"),
            {
                **make_transcript_entry(mixed, a1, "user", session_id, ""),
                "message": {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": "toolu_1", "content": "done"},
                        {"type": "text", "text": "顺带说一句"},
                    ],
                },
            },
        ],
    )

    sessions_before = {row["session_id"] for row in await store.list_sessions(project_key)}

    with pytest.raises(SessionBranchError):
        await service.branch(session_id, mixed)

    assert {row["session_id"] for row in await store.list_sessions(project_key)} == sessions_before


async def test_an_identity_anchor_that_transcript_never_saw_fails_loud(branching):
    """活跃路径条目丢了映射行：恒等回退放行，由前缀切片拒绝，不静默切错位置。"""
    service, meta_store, store, log_store, _, tmp_path = branching
    project_key = make_project_key(tmp_path)
    session_id = str(uuid4())
    await meta_store.create(PROJECT_NAME, session_id)
    u1, a1 = (f"m{i}-{uuid4().hex[:8]}" for i in range(2))
    await store.append(
        {"project_key": project_key, "session_id": session_id},
        [
            make_transcript_entry(u1, None, "user", session_id, "你好"),
            make_transcript_entry(a1, u1, "assistant", session_id, "在"),
        ],
    )
    # 事件日志里有这条主线用户条目，transcript 里没有——mint 出来的 id 本该由
    # 回显认领落进映射表，认领失败就是这个形态。
    ghost = f"user-{uuid4().hex[:8]}"
    await log_store.append(session_id, [{"type": "user", "uuid": ghost, "content": "改我"}])

    sessions_before = {row["session_id"] for row in await store.list_sessions(project_key)}

    # 拒绝来自前缀切片（"not on the main timeline"），不是恒等回退自己拦下的。
    with pytest.raises(SessionBranchError, match="main timeline"):
        await service.branch(session_id, ghost)

    assert {row["session_id"] for row in await store.list_sessions(project_key)} == sessions_before


async def test_new_session_event_log_is_rebuilt_from_the_copied_transcript(branching):
    """AC：新会话的事件日志按既有重放重建机制从 transcript 懒生成。"""
    service, _, store, log_store, session_id, tmp_path = branching
    event_log = EventLogService(log_store, SdkTranscriptAdapter(store=store))

    branched = await service.branch(session_id, SECOND_USER_ENTRY)
    assert not await log_store.has_entries(branched.session_id)

    await event_log.ensure_backfilled(branched.session_id, tmp_path)

    entries = await event_log.list_entries(branched.session_id, tmp_path)
    assert [entry["type"] for entry in entries] == ["user", "assistant"]
