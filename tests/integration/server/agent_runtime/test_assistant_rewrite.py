"""Message rewrite orchestration: interruption, branching, dispatch, rejection cases, and idempotency.

Use real DB fixtures sharing transcript mirrors, session metadata, and event logs, plus a substitute runtime.
Assert external orchestration behavior: destination sessions, original-session state, logged records,
and rollback state on failure.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

import pytest

from lib.agent.agent_session_store import make_project_key
from lib.agent.agent_session_store.store import DbSessionStore
from lib.infra.data_root_layout import DataRootLayout
from lib.project.project_manager import ProjectManager
from server.agent_runtime.event_log import EventLogService, EventLogStore
from server.agent_runtime.sdk_transcript_adapter import SdkTranscriptAdapter
from server.agent_runtime.service import (
    AssistantService,
    InterruptSettleTimeoutError,
    PendingQuestionError,
    QueuedMessagesPendingError,
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
    """Runtime substitute retaining only rewrite-orchestration dependencies.

    ``send_message`` reproduces two relevant production steps: queue messages first, deduplicating matching client_key,
    then log only on Agent ``accept``. Thus idempotency/log-order assertions cover real behavior
    rather than substitute bookkeeping.
    """

    def __init__(self, event_log_store: EventLogStore) -> None:
        self._event_log_store = event_log_store
        self.sessions: dict[str, Any] = {}
        self.statuses: dict[str, str] = {}
        self.pending_questions: dict[str, list[dict[str, Any]]] = {}
        self.dispatched: list[dict[str, Any]] = []
        self.queued: dict[str, list[tuple[dict[str, Any], str | None]]] = {}
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

    @staticmethod
    def _queued_payload(entry: dict[str, Any]) -> dict[str, Any]:
        return {"id": entry["uuid"], "content": entry["content"], "timestamp": entry["timestamp"], "state": "queued"}

    def get_queued_messages_snapshot(self, session_id: str) -> list[dict[str, Any]]:
        return [self._queued_payload(entry) for entry, _key in self.queued.get(session_id, [])]

    def find_queued_message_by_client_key(self, session_id: str, client_key: str) -> dict[str, Any] | None:
        for entry, key in self.queued.get(session_id, []):
            if key == client_key:
                return {"queued_message": self._queued_payload(entry)}
        return None

    async def send_message(
        self,
        session_id: str,
        content: Any,
        *,
        meta: Any = None,
        locale: str = "zh",
        user_entry: dict[str, Any] | None = None,
        client_key: str | None = None,
        resumable: bool = True,
    ) -> dict[str, Any]:
        if self.send_failure is not None:
            raise self.send_failure
        if client_key is not None:
            accepted = self.find_queued_message_by_client_key(session_id, client_key)
            if accepted is not None:
                return accepted
            existing = await self._event_log_store.find_by_client_key(session_id, client_key)
            if existing is not None:
                return {"entry": existing}
        assert user_entry is not None
        self.queued.setdefault(session_id, []).append((user_entry, client_key))
        self.dispatched.append(
            {"session_id": session_id, "prompt": content, "resumable": resumable, "client_key": client_key}
        )
        self.statuses[session_id] = "running"
        return {"queued_message": self._queued_payload(user_entry)}

    async def accept(self, session_id: str) -> None:
        """CLI begins queued-message processing: append event logs and remove from queued messages."""
        for entry, client_key in self.queued.pop(session_id, []):
            await self._event_log_store.append_user_entry(session_id, entry, client_key=client_key)


@pytest.fixture
async def rewriting(session_factory, tmp_path):
    """Original two-round session with identities for both user messages, plus a service with substitute runtime installed."""
    project_cwd = DataRootLayout(tmp_path).projects_dir / PROJECT_NAME
    project_cwd.mkdir(parents=True)

    service = AssistantService(project_root=tmp_path)
    service.layout = DataRootLayout(tmp_path)
    service.data_root = tmp_path
    service.pm = ProjectManager(tmp_path)

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
    service.session_manager = runtime

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


class TestRewriteHappyPath:
    async def test_rewrite_lands_on_a_new_session_and_dispatches_the_new_instruction(self, rewriting):
        service, runtime, session_id, _ = rewriting

        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)

        assert result["status"] == "accepted"
        assert result["session_id"] != session_id
        assert result["origin_session_id"] == session_id
        assert result["queued_message"] is not None
        # Dispatch rewritten messages as new-session input without another user action.
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
        """New logs contain prerewrite history plus rewritten messages with correct order/seq.

        SSE/cold reads share these seq-numbered logs; correct timelines imply correct reconnection continuation points.
        """
        service, runtime, session_id, project_cwd = rewriting

        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)
        await runtime.accept(result["session_id"])

        entries = await service.event_log.list_entries(result["session_id"], project_cwd)
        assert [e["type"] for e in entries] == ["user", "assistant", "user"]
        assert [e["seq"] for e in entries] == [0, 1, 2]
        assert entries[-1]["uuid"] == result["queued_message"]["id"]
        # Discarded branches do not enter new sessions; original round-two replies stay outside the timeline.
        assert not any("批量改好了" in str(e) for e in entries)

    async def test_rewriting_the_first_message_starts_a_fresh_session(self, rewriting):
        """Empty-prefix branches have no resumable history; dispatch must tell runtimes to start with prespecified IDs."""
        service, runtime, session_id, _ = rewriting

        result = await _rewrite(service, session_id, FIRST_USER_ENTRY)
        await runtime.accept(result["session_id"])

        assert runtime.dispatched[0]["resumable"] is False
        entries = await service.event_log.list_entries(result["session_id"], None)
        assert [e["type"] for e in entries] == ["user"]

    async def test_running_session_is_interrupted_before_branching(self, rewriting):
        """AC: rewrite running sessions through interruption, terminal wait, branching, and dispatch together."""
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
        """AC: image-message rewrites preserve images with edited text in the branch's first user message, visible to Agents."""
        service, runtime, session_id, project_cwd = rewriting

        result = await _rewrite(
            service,
            session_id,
            SECOND_USER_ENTRY,
            images=[_image("AAAA"), _image("BBBB", "image/jpeg")],
        )

        # CLI dispatch is multimodal like ordinary image sends: images first, text last.
        assert runtime.dispatched[0]["prompt"] == [
            _image_block("AAAA"),
            _image_block("BBBB", "image/jpeg"),
            {"type": "text", "text": "只改第 3 集"},
        ]
        # Accepted authoritative rows also retain images so refreshed timelines render both.
        await runtime.accept(result["session_id"])
        entries = await service.event_log.list_entries(result["session_id"], project_cwd)
        assert entries[-1]["content"] == [
            _image_block("AAAA"),
            _image_block("BBBB", "image/jpeg"),
            {"type": "text", "text": "只改第 3 集"},
        ]
        assert entries[-1]["uuid"] == result["queued_message"]["id"]

    async def test_text_only_rewrite_stays_a_plain_string_prompt(self, rewriting):
        """Attachment passthrough does not change attachment-free rewrite shapes."""
        service, runtime, session_id, _ = rewriting

        await _rewrite(service, session_id, SECOND_USER_ENTRY, images=[])

        assert runtime.dispatched[0]["prompt"] == "只改第 3 集"


class TestRewriteIdempotency:
    async def test_same_client_key_resubmitted_yields_one_branch(self, rewriting):
        """AC: repeated client_key submission creates only one branch."""
        service, runtime, session_id, _ = rewriting

        first = await _rewrite(service, session_id, SECOND_USER_ENTRY)
        second = await _rewrite(service, session_id, SECOND_USER_ENTRY)

        assert second["session_id"] == first["session_id"]
        assert second["queued_message"] == first["queued_message"]
        await runtime.accept(first["session_id"])
        third = await _rewrite(service, session_id, SECOND_USER_ENTRY)
        assert third["entry"]["uuid"] == first["queued_message"]["id"], "已被接纳的重试返回权威条目"
        assert len(runtime.dispatched) == 1, "重试不得重复执行同一 prompt"
        listed = {meta.id for meta in await service.meta_store.list(project_name=PROJECT_NAME)}
        assert listed == {first["session_id"]}

    async def test_a_different_rewrite_of_a_superseded_session_is_refused(self, rewriting):
        """Superseded sessions are no longer current branches; reject fresh rewrites instead of branching again."""
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
        assert origin is not None
        assert origin.superseded_by is None
        assert runtime.interrupted == [], "被拒的请求不该已经打断用户的轮次"

    async def test_anchor_without_a_transcript_copy_is_refused_as_a_bad_anchor(self, rewriting):
        """Anchors present in event logs but not transcript mirrors reject slicing with recognizable invalid-anchor reasons.

        This occurs for newly accepted users before SDK replay/identity mappings: identity fallback yields a uuid absent
        from transcripts. Orchestration prechecks have passed, so branching must preserve the specific diagnosis
        instead of degrading to generic try-later messages.
        """
        service, runtime, session_id, project_cwd = rewriting
        await service.event_log.ensure_backfilled(session_id, project_cwd)
        pending, _created = await service.event_log_store.append_user_entry(
            session_id, service._build_user_log_entry("刚受理还没回放")
        )

        with pytest.raises(RewriteAnchorError):
            await _rewrite(service, session_id, pending["uuid"])

        assert runtime.interrupted == [session_id], "锚点过了预检，拒绝确实来自分叉那一步"
        origin = await service.meta_store.get(session_id)
        assert origin is not None
        assert origin.superseded_by is None

    async def test_pending_question_blocks_the_rewrite(self, rewriting):
        """AC: unresolved question cards reject with recognizable errors, prioritizing Q&A."""
        service, runtime, session_id, _ = rewriting
        runtime.pending_questions[session_id] = [{"question_id": "aq_1"}]

        with pytest.raises(PendingQuestionError):
            await _rewrite(service, session_id, SECOND_USER_ENTRY)

        origin = await service.meta_store.get(session_id)
        assert origin is not None
        assert origin.superseded_by is None
        assert runtime.interrupted == []

    async def test_queued_messages_block_the_rewrite(self, rewriting):
        """Queued messages outside conversations reject without interrupting or superseding original sessions."""
        service, runtime, session_id, _ = rewriting
        await runtime.send_message(session_id, "第 5 镜删掉", user_entry=service._build_user_log_entry("第 5 镜删掉"))

        with pytest.raises(QueuedMessagesPendingError):
            await _rewrite(service, session_id, SECOND_USER_ENTRY)

        origin = await service.meta_store.get(session_id)
        assert origin is not None
        assert origin.superseded_by is None
        assert runtime.interrupted == []

    async def test_empty_content_is_refused_before_any_side_effect(self, rewriting):
        service, runtime, session_id, _ = rewriting

        with pytest.raises(ValueError, match=r"消息内容不能为空"):
            await _rewrite(service, session_id, SECOND_USER_ENTRY, content="   ")

        assert runtime.interrupted == []
        assert runtime.dispatched == []

    async def test_empty_content_with_attachments_is_accepted(self, rewriting):
        """Image messages with rewritten-empty bodies remain nonempty because attachments are content."""
        service, runtime, session_id, _ = rewriting

        result = await _rewrite(service, session_id, SECOND_USER_ENTRY, content="   ", images=[_image("AAAA")])

        assert result["status"] == "accepted"
        assert result["queued_message"]["content"] == [_image_block("AAAA")]
        assert runtime.dispatched[0]["prompt"] == [_image_block("AAAA")]

    async def test_session_from_another_project_is_not_found(self, rewriting):
        service, _, session_id, _ = rewriting
        (service.layout.projects_dir / "other").mkdir()

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
        assert origin is not None
        assert origin.superseded_by is None


class TestRewriteAdmission:
    async def test_a_send_during_the_interrupt_waits_and_is_refused_once_the_origin_is_superseded(self, rewriting):
        """Concurrent sends during interruption settlement do not queue in old sessions; after branch publication, reject as superseded."""
        service, runtime, session_id, _ = rewriting
        runtime.statuses[session_id] = "running"
        runtime.settle_after_interrupt = False
        service._INTERRUPT_SETTLE_POLL = 0.01
        interrupted = asyncio.Event()
        interrupt_session = runtime.interrupt_session

        async def _interrupt(session_id: str) -> str:
            status = await interrupt_session(session_id)
            interrupted.set()
            return status

        runtime.interrupt_session = _interrupt
        rewrite = asyncio.create_task(_rewrite(service, session_id, SECOND_USER_ENTRY))
        await interrupted.wait()

        # Send reads session metadata under admission locks; whether it has read proves lock blocking.
        meta_reads: list[str] = []
        get_meta = service.meta_store.get

        async def _get_meta(session_id: str) -> Any:
            meta_reads.append(session_id)
            return await get_meta(session_id)

        service.meta_store.get = _get_meta
        send = asyncio.create_task(service.send_or_create(PROJECT_NAME, "第 5 镜删掉", session_id=session_id))
        # Advance sending to its first suspension point.
        await asyncio.sleep(0)
        assert meta_reads == []
        assert not send.done()

        runtime.statuses[session_id] = "interrupted"
        result = await rewrite
        with pytest.raises(SessionSupersededError):
            await send

        assert runtime.queued.get(session_id) is None
        assert [d["session_id"] for d in runtime.dispatched] == [result["session_id"]]


class TestRewriteDispatchFailure:
    async def test_failed_dispatch_discards_the_branch_and_frees_the_origin(self, rewriting):
        """Dispatch failure fails the whole rewrite, retracting branches and restoring original rewrite eligibility."""
        service, runtime, session_id, project_cwd = rewriting
        runtime.send_failure = RuntimeError("SDK 起不来")

        with pytest.raises(RuntimeError, match="SDK 起不来"):
            await _rewrite(service, session_id, SECOND_USER_ENTRY)

        origin = await service.meta_store.get(session_id)
        assert origin is not None
        assert origin.superseded_by is None
        listed = {meta.id for meta in await service.meta_store.list(project_name=PROJECT_NAME)}
        assert listed == {session_id}, "撤回后不留下顶替原会话的空转分支"

        # Retry after rollback succeeds without trapping users.
        runtime.send_failure = None
        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)
        assert result["session_id"] != session_id
        await runtime.accept(result["session_id"])
        entries = await service.event_log.list_entries(result["session_id"], project_cwd)
        assert [e["type"] for e in entries] == ["user", "assistant", "user"]

    async def test_failed_prefix_backfill_discards_the_branch_too(self, rewriting):
        """Compensation covers every postpublication step, not only dispatch."""
        service, runtime, session_id, project_cwd = rewriting
        original_backfill = service.event_log.ensure_backfilled

        async def failing_backfill(*args: object, **kwargs: object) -> None:
            raise RuntimeError("前缀回填炸了")

        service.event_log.ensure_backfilled = failing_backfill

        with pytest.raises(RuntimeError, match="前缀回填炸了"):
            await _rewrite(service, session_id, SECOND_USER_ENTRY)

        origin = await service.meta_store.get(session_id)
        assert origin is not None
        assert origin.superseded_by is None
        listed = {meta.id for meta in await service.meta_store.list(project_name=PROJECT_NAME)}
        assert listed == {session_id}

        service.event_log.ensure_backfilled = original_backfill
        result = await _rewrite(service, session_id, SECOND_USER_ENTRY)
        await runtime.accept(result["session_id"])
        entries = await service.event_log.list_entries(result["session_id"], project_cwd)
        assert [e["type"] for e in entries] == ["user", "assistant", "user"]
