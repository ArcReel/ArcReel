"""SSE entry 流与 REST entries：cursor 续传、draft 首帧、状态事件。"""

from __future__ import annotations

import asyncio
import contextlib

import pytest
from fastapi import FastAPI
from fastapi.sse import ServerSentEvent
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from lib.i18n import DEFAULT_LOCALE, get_translator
from server.agent_runtime.event_log import EventLogService, EventLogStore
from server.agent_runtime.models import LiveMessage, SubscriptionReady
from server.agent_runtime.service import AssistantService
from server.auth import CurrentUserInfo, get_current_user, get_current_user_flexible
from server.routers import assistant
from tests.conftest import make_translator
from tests.factories import make_session_meta

SESSION_ID = "entry-stream-s1"


class _FakeMetaStore:
    def __init__(self, meta):
        self._meta = meta

    async def get(self, session_id):
        return self._meta if session_id == self._meta.id else None


class _FakeAdapter:
    def __init__(self, messages=None):
        self._messages = messages or []

    async def read_raw_messages(self, sdk_session_id=None, project_cwd=None):
        return list(self._messages)

    async def read_subagent_timelines(self, sdk_session_id=None, project_cwd=None):
        return {}


class _FakeEntrySessionManager:
    def __init__(self, status="running", draft_state=None, pending=None):
        self.status_value = status
        self.queue: asyncio.Queue = asyncio.Queue()
        self.draft_state = draft_state or {"draft": None, "rev": 0}
        self.pending = pending or []

    async def get_status(self, session_id):
        return self.status_value

    def get_draft_state(self, session_id):
        return dict(self.draft_state)

    async def get_pending_questions_snapshot(self, session_id):
        return list(self.pending)

    @contextlib.asynccontextmanager
    async def stream_messages(self, session_id, *, idle_timeout=20.0, locale=DEFAULT_LOCALE):
        async def _iter():
            yield SubscriptionReady()
            while True:
                message = await self.queue.get()
                if message is None:
                    return
                yield LiveMessage(message=message)

        yield _iter()


@pytest.fixture()
async def entry_service(tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = EventLogStore(session_factory=factory)

    service = AssistantService(project_root=tmp_path)
    meta = make_session_meta(id=SESSION_ID, status="running")
    service.meta_store = _FakeMetaStore(meta)
    service.event_log_store = store
    service.event_log = EventLogService(store, _FakeAdapter())
    yield service, store
    await engine.dispose()


def _collect(event: ServerSentEvent) -> tuple[str, dict, str | None]:
    data = event.data if isinstance(event.data, dict) else {}
    return event.event or "", data, event.id


class TestStreamEntryEvents:
    async def test_non_running_emits_entries_then_terminal_status(self, entry_service):
        service, store = entry_service
        service.session_manager = _FakeEntrySessionManager(status="completed")
        await store.append(SESSION_ID, [{"type": "user", "uuid": "a"}, {"type": "assistant", "uuid": "b"}])

        events = [_collect(e) async for e in service.stream_entry_events(SESSION_ID)]

        assert [name for name, _, _ in events] == ["entry", "entry", "status"]
        # SSE 事件 id 即 seq
        assert [sse_id for _, _, sse_id in events[:2]] == ["0", "1"]
        assert events[2][1]["status"] == "completed"

    async def test_after_cursor_skips_earlier_entries(self, entry_service):
        service, store = entry_service
        service.session_manager = _FakeEntrySessionManager(status="completed")
        await store.append(SESSION_ID, [{"type": "user", "uuid": "a"}, {"type": "assistant", "uuid": "b"}])

        events = [_collect(e) async for e in service.stream_entry_events(SESSION_ID, after_seq=0)]

        entry_events = [e for e in events if e[0] == "entry"]
        assert len(entry_events) == 1
        assert entry_events[0][2] == "1"

    async def test_running_stream_backlog_draft_live_and_dedup(self, entry_service):
        service, store = entry_service
        draft_state = {
            "draft": {"message_id": "msg_9", "content": [{"type": "text", "text": "部分"}], "rev": 7},
            "rev": 7,
        }
        manager = _FakeEntrySessionManager(status="running", draft_state=draft_state)
        service.session_manager = manager
        await store.append(SESSION_ID, [{"type": "user", "uuid": "a"}, {"type": "assistant", "uuid": "b"}])

        # 直播队列：重复条目（seq 1，已在存量中）须被 seq 门槛跳过；
        # 新条目 seq 2 放行；delta 透传；result 产出终态 status 并结束。
        manager.queue.put_nowait(
            {"type": "log_entry", "session_id": SESSION_ID, "entry": {"seq": 1, "type": "assistant", "uuid": "b"}}
        )
        manager.queue.put_nowait(
            {"type": "log_entry", "session_id": SESSION_ID, "entry": {"seq": 2, "type": "tool_result", "uuid": "c"}}
        )
        manager.queue.put_nowait(
            {
                "type": "log_delta",
                "session_id": SESSION_ID,
                "message_id": "msg_9",
                "delta_type": "text_delta",
                "block_index": 0,
                "text": "内容",
                "rev": 8,
            }
        )
        manager.queue.put_nowait({"type": "result", "subtype": "success", "is_error": False})
        manager.queue.put_nowait({"type": "log_turn_complete", "session_id": SESSION_ID})

        events = [_collect(e) async for e in service.stream_entry_events(SESSION_ID, after_seq=-1)]

        names = [name for name, _, _ in events]
        assert names == ["entry", "entry", "draft", "entry", "delta", "status"]
        # 存量 entry：seq 0、1；直播放行的只有 seq 2（seq 1 重复被跳过）
        assert [e[2] for e in events if e[0] == "entry"] == ["0", "1", "2"]
        # draft 首帧快照携带累积态与 rev 门槛
        draft_payload = events[2][1]
        assert draft_payload["draft"]["message_id"] == "msg_9"
        assert draft_payload["rev"] == 7
        # delta 事件不带 SSE id（不推进 Last-Event-ID）
        delta_event = next(e for e in events if e[0] == "delta")
        assert delta_event[2] is None
        assert delta_event[1]["text"] == "内容"
        assert events[-1][1]["status"] == "completed"

    async def test_running_stream_reconnect_cursor_no_full_replay(self, entry_service):
        """携带 cursor 订阅只收到其后的条目，重连不整帧重算。"""
        service, store = entry_service
        manager = _FakeEntrySessionManager(status="running")
        service.session_manager = manager
        await store.append(
            SESSION_ID,
            [{"type": "user", "uuid": "a"}, {"type": "assistant", "uuid": "b"}, {"type": "tool_result", "uuid": "c"}],
        )
        manager.queue.put_nowait({"type": "result", "subtype": "success", "is_error": False})
        manager.queue.put_nowait({"type": "log_turn_complete", "session_id": SESSION_ID})

        events = [_collect(e) async for e in service.stream_entry_events(SESSION_ID, after_seq=1)]

        entry_events = [e for e in events if e[0] == "entry"]
        assert [e[2] for e in entry_events] == ["2"]

    async def test_final_entry_after_raw_result_still_delivered(self, entry_service):
        """末条 log_entry 晚于原始 result 广播到达（inbox 落库延迟）时仍须送达。

        终态由 log_turn_complete 触发，不在原始 result 处提前终结。
        """
        service, store = entry_service
        manager = _FakeEntrySessionManager(status="running")
        service.session_manager = manager
        await store.append(SESSION_ID, [{"type": "user", "uuid": "a"}])

        # actor 回调先广播原始 result，末条 assistant 的 log_entry 随后才到
        manager.queue.put_nowait({"type": "result", "subtype": "success", "is_error": False})
        manager.queue.put_nowait(
            {"type": "log_entry", "session_id": SESSION_ID, "entry": {"seq": 1, "type": "assistant", "uuid": "b"}}
        )
        manager.queue.put_nowait({"type": "log_turn_complete", "session_id": SESSION_ID})

        events = [_collect(e) async for e in service.stream_entry_events(SESSION_ID)]

        names = [name for name, _, _ in events]
        assert names[-2:] == ["entry", "status"]
        assert [e[2] for e in events if e[0] == "entry"] == ["0", "1"]
        assert events[-1][1]["status"] == "completed"

    async def test_pending_questions_replayed_on_subscribe(self, entry_service):
        service, store = entry_service
        question = {"type": "ask_user_question", "question_id": "aq-1", "questions": [{"question": "选哪个?"}]}
        manager = _FakeEntrySessionManager(status="running", pending=[question])
        service.session_manager = manager
        manager.queue.put_nowait({"type": "result", "subtype": "success", "is_error": False})
        manager.queue.put_nowait({"type": "log_turn_complete", "session_id": SESSION_ID})

        events = [_collect(e) async for e in service.stream_entry_events(SESSION_ID)]

        question_events = [e for e in events if e[0] == "question"]
        assert len(question_events) == 1
        assert question_events[0][1]["question_id"] == "aq-1"


class TestListSessionEntries:
    async def test_response_shape_with_draft(self, entry_service):
        service, store = entry_service
        draft_state = {"draft": {"message_id": "m", "content": [], "rev": 3}, "rev": 3}
        service.session_manager = _FakeEntrySessionManager(status="running", draft_state=draft_state)
        await store.append(SESSION_ID, [{"type": "user", "uuid": "a"}])

        result = await service.list_session_entries(SESSION_ID)

        assert result["session_id"] == SESSION_ID
        assert result["status"] == "running"
        assert [e["seq"] for e in result["entries"]] == [0]
        assert result["draft"]["message_id"] == "m"
        assert result["draft_rev"] == 3

    async def test_non_running_has_no_draft(self, entry_service):
        service, store = entry_service
        service.session_manager = _FakeEntrySessionManager(status="idle")
        result = await service.list_session_entries(SESSION_ID, after_seq=-1)
        assert result["draft"] is None
        assert result["entries"] == []


_FAKE_USER = CurrentUserInfo(id="default", sub="testuser", role="admin")
PROJECT = "demo"
PREFIX = f"/api/v1/projects/{PROJECT}/assistant"


class _CursorCapturingService:
    """记录 stream_entry_events 收到的 after_seq，验证路由层游标优先级。"""

    def __init__(self):
        self.captured_after = None
        self.meta = make_session_meta(id=SESSION_ID, status="idle", project_name=PROJECT)

    async def get_session(self, session_id):
        return self.meta if session_id == SESSION_ID else None

    async def stream_entry_events(self, session_id, *, meta=None, request=None, after_seq=-1):
        self.captured_after = after_seq
        yield ServerSentEvent(event="status", data={"status": "idle"})

    async def list_session_entries(self, session_id, *, meta=None, after_seq=-1):
        self.captured_after = after_seq
        return {"session_id": session_id, "status": "idle", "entries": [], "draft": None, "draft_rev": 0}


def _build_client(monkeypatch, fake_service) -> TestClient:
    monkeypatch.setattr(assistant, "get_assistant_service", lambda: fake_service)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
    app.dependency_overrides[get_current_user_flexible] = lambda: _FAKE_USER
    app.dependency_overrides[get_translator] = lambda: make_translator()
    app.include_router(assistant.router, prefix="/api/v1/projects/{project_name}/assistant")
    return TestClient(app)


class TestEntryStreamRouter:
    def test_last_event_id_takes_precedence_over_after(self, monkeypatch):
        fake = _CursorCapturingService()
        with _build_client(monkeypatch, fake) as client:
            resp = client.get(
                f"{PREFIX}/sessions/{SESSION_ID}/entries/stream?after=2",
                headers={"Last-Event-ID": "5"},
            )
        assert resp.status_code == 200
        assert fake.captured_after == 5

    def test_after_query_used_without_last_event_id(self, monkeypatch):
        fake = _CursorCapturingService()
        with _build_client(monkeypatch, fake) as client:
            resp = client.get(f"{PREFIX}/sessions/{SESSION_ID}/entries/stream?after=3")
        assert resp.status_code == 200
        assert fake.captured_after == 3

    def test_invalid_last_event_id_falls_back_to_after(self, monkeypatch):
        fake = _CursorCapturingService()
        with _build_client(monkeypatch, fake) as client:
            client.get(
                f"{PREFIX}/sessions/{SESSION_ID}/entries/stream?after=4",
                headers={"Last-Event-ID": "not-a-number"},
            )
        assert fake.captured_after == 4

    def test_entries_rest_endpoint(self, monkeypatch):
        fake = _CursorCapturingService()
        with _build_client(monkeypatch, fake) as client:
            resp = client.get(f"{PREFIX}/sessions/{SESSION_ID}/entries?after=7")
        assert resp.status_code == 200
        assert resp.json()["session_id"] == SESSION_ID
        assert fake.captured_after == 7

    def test_entries_404_for_unknown_session(self, monkeypatch):
        fake = _CursorCapturingService()
        with _build_client(monkeypatch, fake) as client:
            resp = client.get(f"{PREFIX}/sessions/unknown/entries")
        assert resp.status_code == 404
