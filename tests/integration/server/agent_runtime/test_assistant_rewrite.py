"""消息改写的编排：中断、分叉、派发一气呵成，以及每种拒绝与幂等。

用真实 DB fixture（transcript 镜像 + 会话元数据 + 事件日志同库）与一个替身
运行时——断言的是编排的外部行为：改写后落在哪个会话、原会话怎么了、日志里
留下什么、失败时撤回到什么状态。
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from lib.agent_session_store import make_project_key
from lib.agent_session_store.store import DbSessionStore
from lib.project_manager import ProjectManager
from server.agent_runtime.event_log import EventLogService, EventLogStore
from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter
from server.agent_runtime.service import (
    AssistantService,
    InterruptSettleTimeoutError,
    PendingQuestionError,
    RewriteAnchorError,
    RewriteUnavailableError,
    SessionSupersededError,
)
from server.agent_runtime.session_branch import SessionBranchService
from server.agent_runtime.session_store import SessionMetaStore
from server.routers.assistant import ImageAttachment
from tests.factories import make_sdk_transcript_entry

PROJECT_NAME = "demo"
FIRST_USER_ENTRY = "user-first"
SECOND_USER_ENTRY = "user-second"


class FakeSessionManager:
    """运行时替身：只保留改写编排真正依赖的那几件事。

    ``send_message`` 复刻真实实现里与编排相关的两步——先把用户条目写进事件日志
    分配身份（含同 client_key 命中即不重复投递），再把 prompt 投递出去——因此
    幂等与日志顺序的断言测的是真行为，而不是替身的记账。
    """

    def __init__(self, event_log_store: EventLogStore) -> None:
        self._event_log_store = event_log_store
        self.sessions: dict[str, Any] = {}
        self.statuses: dict[str, str] = {}
        self.pending_questions: dict[str, list[dict[str, Any]]] = {}
        self.dispatched: list[dict[str, Any]] = []
        self.interrupted: list[str] = []
        self.closed: list[str] = []
        self.send_failure: BaseException | None = None
        self.settle_after_interrupt = True

    async def get_pending_questions_snapshot(self, session_id: str) -> list[dict[str, Any]]:
        return self.pending_questions.get(session_id, [])

    async def get_status(self, session_id: str) -> str | None:
        return self.statuses.get(session_id)

    async def interrupt_session(self, session_id: str) -> str:
        self.interrupted.append(session_id)
        if self.statuses.get(session_id) != "running":
            return self.statuses.get(session_id, "idle")
        if self.settle_after_interrupt:
            self.statuses[session_id] = "interrupted"
            return "interrupted"
        return "running"

    async def close_session(self, session_id: str, *, reason: str = "") -> None:
        self.closed.append(session_id)

    async def send_message(
        self,
        session_id: str,
        prompt: Any,
        *,
        echo_text: str | None = None,
        echo_content: list[dict[str, Any]] | None = None,
        meta: Any = None,
        locale: str = "zh",
        user_entry: dict[str, Any] | None = None,
        client_key: str | None = None,
        resumable: bool = True,
    ) -> dict[str, Any] | None:
        if self.send_failure is not None:
            raise self.send_failure
        if user_entry is not None and client_key is not None:
            existing = await self._event_log_store.find_by_client_key(session_id, client_key)
            if existing is not None:
                return existing
        entry: dict[str, Any] | None = None
        if user_entry is not None:
            entry, _created = await self._event_log_store.append_user_entry(
                session_id, user_entry, client_key=client_key
            )
        self.dispatched.append(
            {
                "session_id": session_id,
                "prompt": prompt,
                "resumable": resumable,
                "client_key": client_key,
                "echo_text": echo_text,
                "echo_content": echo_content,
            }
        )
        self.statuses[session_id] = "running"
        return entry


@pytest.fixture
async def rewriting(session_factory, tmp_path):
    """一个有两轮对话、两条用户消息都已建立身份映射的原会话 + 装好替身运行时的服务。"""
    projects_root = tmp_path / "projects"
    project_cwd = projects_root / PROJECT_NAME
    project_cwd.mkdir(parents=True)

    service = AssistantService(project_root=tmp_path)
    service.projects_root = projects_root
    service.pm = ProjectManager(projects_root)

    store = DbSessionStore(session_factory)
    log_store = EventLogStore(session_factory=session_factory)
    meta_store = SessionMetaStore(session_factory=session_factory)
    service._session_store = store
    service.event_log_store = log_store
    service.meta_store = meta_store
    service.transcript_adapter = SdkTranscriptAdapter(store=store)
    service.event_log = EventLogService(log_store, service.transcript_adapter)
    service.session_branch = SessionBranchService(
        store=store,
        meta_store=meta_store,
        event_log=service.event_log,
        resolve_project_cwd=service._resolve_project_cwd_safe,
    )
    runtime = FakeSessionManager(log_store)
    service.session_manager = runtime  # type: ignore[assignment]

    session_id = str(uuid4())
    await meta_store.create(PROJECT_NAME, session_id)
    u1, a1, u2, a2 = (f"m{i}-{uuid4().hex[:8]}" for i in range(4))
    await store.append(
        {"project_key": make_project_key(project_cwd), "session_id": session_id},
        [
            make_sdk_transcript_entry(u1, None, "user", session_id, "逐集改"),
            make_sdk_transcript_entry(a1, u1, "assistant", session_id, "好的"),
            make_sdk_transcript_entry(u2, a1, "user", session_id, "再改一下"),
            make_sdk_transcript_entry(a2, u2, "assistant", session_id, "批量改好了"),
        ],
    )
    await log_store.record_user_message_link(session_id, FIRST_USER_ENTRY, u1)
    await log_store.record_user_message_link(session_id, SECOND_USER_ENTRY, u2)

    return service, runtime, session_id, project_cwd


async def _rewrite(service: AssistantService, session_id: str, anchor: str, **kwargs: Any) -> dict[str, Any]:
    params: dict[str, Any] = {"content": "只改第 3 集", "client_key": "ck-1"}
    params.update(kwargs)
    return await service.rewrite_message(PROJECT_NAME, session_id, anchor_entry_uuid=anchor, **params)


def _image(data: str, media_type: str = "image/png") -> ImageAttachment:
    return ImageAttachment(data=data, media_type=media_type)


def _image_block(data: str, media_type: str = "image/png") -> dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


async def _collect_prompt(prompt: Any) -> list[dict[str, Any]]:
    """多模态 prompt 是 async generator——把它投递出的 wire 消息收下来。"""
    return [message async for message in prompt]


class TestRewriteHappyPath:
    async def test_rewrite_lands_on_a_new_session_and_dispatches_the_new_instruction(self, rewriting):
        service, runtime, session_id, _ = rewriting

        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)

        assert result["status"] == "accepted"
        assert result["session_id"] != session_id
        assert result["origin_session_id"] == session_id
        assert result["entry"] is not None
        # 改写后的消息作为新会话的输入被派发出去，无需用户再操作一步。
        assert [d["session_id"] for d in runtime.dispatched] == [result["session_id"]]
        assert runtime.dispatched[0]["prompt"] == "只改第 3 集"

    async def test_origin_is_superseded_and_leaves_the_session_list(self, rewriting):
        service, _, session_id, _ = rewriting

        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)

        origin = await service.meta_store.get(session_id)
        assert origin is not None, "原会话整行保留，可经指针追溯"
        assert origin.superseded_by == result["session_id"]
        listed = {meta.id for meta in await service.meta_store.list(project_name=PROJECT_NAME)}
        assert session_id not in listed
        assert result["session_id"] in listed

    async def test_new_session_timeline_is_the_prefix_followed_by_the_rewrite(self, rewriting):
        """新会话的事件日志 = 改写点之前的历史 + 改写后的消息，顺序与 seq 都对。

        SSE 与冷读共用这份日志、按 seq 发号，时间线正确即断线重连的续传点正确。
        """
        service, _, session_id, project_cwd = rewriting

        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)

        entries = await service.event_log.list_entries(result["session_id"], project_cwd)
        assert [e["type"] for e in entries] == ["user", "assistant", "user"]
        assert [e["seq"] for e in entries] == [0, 1, 2]
        assert entries[-1]["uuid"] == result["entry"]["uuid"]
        # 被丢弃的分支不进新会话：原会话第二轮的回复不在时间线里。
        assert not any("批量改好了" in str(e) for e in entries)

    async def test_rewriting_the_first_message_starts_a_fresh_session(self, rewriting):
        """空前缀分支没有历史可 resume，派发时须告知运行时以预指定 id 起新会话。"""
        service, runtime, session_id, _ = rewriting

        result = await _rewrite(service, session_id, FIRST_USER_ENTRY)

        assert runtime.dispatched[0]["resumable"] is False
        entries = await service.event_log.list_entries(result["session_id"], None)
        assert [e["type"] for e in entries] == ["user"]

    async def test_running_session_is_interrupted_before_branching(self, rewriting):
        """AC：会话 running 时改写——中断、等终态、分叉、派发一气呵成。"""
        service, runtime, session_id, _ = rewriting
        runtime.statuses[session_id] = "running"

        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)

        assert runtime.interrupted == [session_id]
        assert runtime.dispatched[0]["session_id"] == result["session_id"]

    async def test_interrupt_is_skipped_for_an_idle_session(self, rewriting):
        service, runtime, session_id, _ = rewriting
        runtime.statuses[session_id] = "idle"

        await _rewrite(service, session_id, SECOND_USER_ENTRY)

        assert runtime.interrupted == [session_id], "冷/闲会话也走同一入口，只是立刻返回终态"
        assert runtime.dispatched

    async def test_attachments_ride_along_into_the_branch_first_input(self, rewriting):
        """AC：改写带图消息——分支会话首条用户消息含原图与编辑后文本，agent 可见图片。"""
        service, runtime, session_id, project_cwd = rewriting

        result = await _rewrite(
            service,
            session_id,
            SECOND_USER_ENTRY,
            images=[_image("AAAA"), _image("BBBB", "image/jpeg")],
        )

        # 派发给 SDK 的是多模态 prompt，与普通带图发送同构：图在前、文本在后
        messages = await _collect_prompt(runtime.dispatched[0]["prompt"])
        assert [m["message"]["content"] for m in messages] == [
            [
                _image_block("AAAA"),
                _image_block("BBBB", "image/jpeg"),
                {"type": "text", "text": "只改第 3 集"},
            ]
        ]
        # 落库的权威条目同样带图，刷新后时间线仍能渲染出这两张图
        entries = await service.event_log.list_entries(result["session_id"], project_cwd)
        assert entries[-1]["content"] == [
            _image_block("AAAA"),
            _image_block("BBBB", "image/jpeg"),
            {"type": "text", "text": "只改第 3 集"},
        ]
        assert entries[-1]["uuid"] == result["entry"]["uuid"]
        # 正文非空时 echo 匹配仍按改写后的文本落链——SDK 回放丢掉图块，只剩这段文本
        assert runtime.dispatched[0]["echo_text"] == "只改第 3 集"

    async def test_text_only_rewrite_stays_a_plain_string_prompt(self, rewriting):
        """不带附件的改写不因附件透传而改变形态。"""
        service, runtime, session_id, _ = rewriting

        await _rewrite(service, session_id, SECOND_USER_ENTRY, images=[])

        assert runtime.dispatched[0]["prompt"] == "只改第 3 集"
        assert runtime.dispatched[0]["echo_content"] is None


class TestRewriteIdempotency:
    async def test_same_client_key_resubmitted_yields_one_branch(self, rewriting):
        """AC：同一 client_key 重复提交只产生一个分支会话。"""
        service, runtime, session_id, _ = rewriting

        first = await _rewrite(service, session_id, SECOND_USER_ENTRY)
        second = await _rewrite(service, session_id, SECOND_USER_ENTRY)

        assert second["session_id"] == first["session_id"]
        assert second["entry"] == first["entry"]
        assert len(runtime.dispatched) == 1, "重试不得重复执行同一 prompt"
        listed = {meta.id for meta in await service.meta_store.list(project_name=PROJECT_NAME)}
        assert listed == {first["session_id"]}

    async def test_a_different_rewrite_of_a_superseded_session_is_refused(self, rewriting):
        """已被取代的会话不再是当前分支，对它发起新的改写应明确拒绝而非再分叉。"""
        service, _, session_id, _ = rewriting
        await _rewrite(service, session_id, SECOND_USER_ENTRY)

        with pytest.raises(SessionSupersededError):
            await _rewrite(service, session_id, SECOND_USER_ENTRY, client_key="ck-2")

    async def test_retry_without_a_client_key_is_refused_rather_than_branching_twice(self, rewriting):
        service, runtime, session_id, _ = rewriting
        await _rewrite(service, session_id, SECOND_USER_ENTRY, client_key=None)

        with pytest.raises(SessionSupersededError):
            await _rewrite(service, session_id, SECOND_USER_ENTRY, client_key=None)
        assert len(runtime.dispatched) == 1


class TestRewriteRejections:
    async def test_anchor_outside_the_session_is_refused(self, rewriting):
        service, runtime, session_id, _ = rewriting

        with pytest.raises(RewriteAnchorError):
            await _rewrite(service, session_id, "user-elsewhere")

        origin = await service.meta_store.get(session_id)
        assert origin is not None and origin.superseded_by is None
        assert runtime.interrupted == [], "被拒的请求不该已经打断用户的轮次"

    async def test_anchor_without_a_transcript_copy_is_refused_as_a_bad_anchor(self, rewriting):
        """锚点已在事件日志里、transcript 副本却还没落成：切片拒绝，理由仍是「锚点非法」。

        改写一条刚受理、SDK 尚未回放的用户消息就会走到这里——身份映射还没落表，
        恒等回退给出一个 transcript 查无此条的 uuid。这条路径过了编排层的预检，
        必须在分叉那一步保住可辨识性，而不是退化成一句「请稍后重试」。
        """
        service, runtime, session_id, project_cwd = rewriting
        await service.event_log.ensure_backfilled(session_id, project_cwd)
        pending, _created = await service.event_log_store.append_user_entry(
            session_id, service._build_user_log_entry("刚受理还没回放", None)
        )

        with pytest.raises(RewriteAnchorError):
            await _rewrite(service, session_id, pending["uuid"])

        assert runtime.interrupted == [session_id], "锚点过了预检，拒绝确实来自分叉那一步"
        origin = await service.meta_store.get(session_id)
        assert origin is not None and origin.superseded_by is None

    async def test_pending_question_blocks_the_rewrite(self, rewriting):
        """AC：未决问答卡片存在 → 拒绝且错误可辨识（问答优先）。"""
        service, runtime, session_id, _ = rewriting
        runtime.pending_questions[session_id] = [{"question_id": "aq_1"}]

        with pytest.raises(PendingQuestionError):
            await _rewrite(service, session_id, SECOND_USER_ENTRY)

        origin = await service.meta_store.get(session_id)
        assert origin is not None and origin.superseded_by is None
        assert runtime.interrupted == []

    async def test_empty_content_is_refused_before_any_side_effect(self, rewriting):
        service, runtime, session_id, _ = rewriting

        with pytest.raises(ValueError):
            await _rewrite(service, session_id, SECOND_USER_ENTRY, content="   ")

        assert runtime.interrupted == []
        assert runtime.dispatched == []

    async def test_empty_content_with_attachments_is_accepted(self, rewriting):
        """改写把正文清空的带图消息仍是有内容的消息——附件即内容。"""
        service, runtime, session_id, _ = rewriting

        result = await _rewrite(service, session_id, SECOND_USER_ENTRY, content="   ", images=[_image("AAAA")])

        assert result["status"] == "accepted"
        assert result["entry"]["content"] == [_image_block("AAAA")]
        # 纯图消息的 echo 匹配靠 sentinel：显示文本为空、echo_content 非空即走那条路径
        assert runtime.dispatched[0]["echo_text"] == ""
        assert runtime.dispatched[0]["echo_content"] == [_image_block("AAAA")]

    async def test_session_from_another_project_is_not_found(self, rewriting):
        service, _, session_id, _ = rewriting
        (service.projects_root / "other").mkdir()

        with pytest.raises(FileNotFoundError):
            await service.rewrite_message(
                "other", session_id, anchor_entry_uuid=SECOND_USER_ENTRY, content="x", client_key="ck-1"
            )

    async def test_rewrite_is_unavailable_without_the_transcript_store(self, rewriting):
        service, _, session_id, _ = rewriting
        service._session_store = None

        with pytest.raises(RewriteUnavailableError):
            await _rewrite(service, session_id, SECOND_USER_ENTRY)

    async def test_a_turn_that_never_settles_times_out_instead_of_branching(self, rewriting):
        service, runtime, session_id, _ = rewriting
        runtime.statuses[session_id] = "running"
        runtime.settle_after_interrupt = False
        service._INTERRUPT_SETTLE_TIMEOUT = 0.05

        with pytest.raises(InterruptSettleTimeoutError):
            await _rewrite(service, session_id, SECOND_USER_ENTRY)

        origin = await service.meta_store.get(session_id)
        assert origin is not None and origin.superseded_by is None


class TestRewriteDispatchFailure:
    async def test_failed_dispatch_discards_the_branch_and_frees_the_origin(self, rewriting):
        """派发失败即整次改写失败：分支整体撤回，原会话回到可再改写的状态。"""
        service, runtime, session_id, project_cwd = rewriting
        runtime.send_failure = RuntimeError("SDK 起不来")

        with pytest.raises(RuntimeError, match="SDK 起不来"):
            await _rewrite(service, session_id, SECOND_USER_ENTRY)

        origin = await service.meta_store.get(session_id)
        assert origin is not None and origin.superseded_by is None
        listed = {meta.id for meta in await service.meta_store.list(project_name=PROJECT_NAME)}
        assert listed == {session_id}, "撤回后不留下顶替原会话的空转分支"

        # 撤回后重试可以正常改写，用户不被卡死。
        runtime.send_failure = None
        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)
        assert result["session_id"] != session_id
        entries = await service.event_log.list_entries(result["session_id"], project_cwd)
        assert [e["type"] for e in entries] == ["user", "assistant", "user"]

    async def test_failed_prefix_backfill_discards_the_branch_too(self, rewriting):
        """补偿范围覆盖分支发布之后的每一步，不止派发那一句。"""
        service, _, session_id, project_cwd = rewriting
        original_backfill = service.event_log.ensure_backfilled

        async def failing_backfill(*args: object, **kwargs: object) -> None:
            raise RuntimeError("前缀回填炸了")

        service.event_log.ensure_backfilled = failing_backfill

        with pytest.raises(RuntimeError, match="前缀回填炸了"):
            await _rewrite(service, session_id, SECOND_USER_ENTRY)

        origin = await service.meta_store.get(session_id)
        assert origin is not None and origin.superseded_by is None
        listed = {meta.id for meta in await service.meta_store.list(project_name=PROJECT_NAME)}
        assert listed == {session_id}

        service.event_log.ensure_backfilled = original_backfill
        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)
        entries = await service.event_log.list_entries(result["session_id"], project_cwd)
        assert [e["type"] for e in entries] == ["user", "assistant", "user"]
