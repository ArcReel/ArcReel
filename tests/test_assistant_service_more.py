import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_store import SessionMetaStore
from tests.factories import make_session_meta


class _FakePM:
    def __init__(self, valid_project="demo"):
        self.valid_project = valid_project

    def get_project_path(self, project_name):
        if project_name != self.valid_project:
            raise FileNotFoundError(project_name)
        return Path("/tmp") / project_name


class _FakeMetaStore:
    def __init__(self, metas=None):
        self.metas = {m.id: m for m in (metas or [])}

    async def get(self, session_id):
        return self.metas.get(session_id)

    async def list(self, project_name=None, status=None, limit=50, offset=0):
        return list(self.metas.values())

    async def delete(self, session_id):
        return self.metas.pop(session_id, None) is not None


class _FakeEventLogService:
    """No-op 事件日志：单测聚焦 service 编排，不触达真实 DB。"""

    def __init__(self):
        self.backfilled = []

    async def ensure_backfilled(self, session_id, project_cwd):
        self.backfilled.append(session_id)


class _FakeSessionManager:
    def __init__(self):
        self.sessions = {}
        self.new_sessions = []
        self.sent = []
        self.sent_kwargs = []
        self.answered = []
        self.interrupted = []
        self.closed = []
        self.unsubscribed = []
        self.status = "running"
        self.buffer = []
        self.pending = []
        self.close_error = None

    async def send_new_session(self, project_name, prompt, **kwargs):
        self.new_sessions.append((project_name, prompt))
        return "sdk-new-id"

    async def get_status(self, session_id):
        return self.status

    async def get_pending_questions_snapshot(self, session_id):
        return list(self.pending)

    async def send_message(self, session_id, content, **kwargs):
        self.sent.append((session_id, content))
        self.sent_kwargs.append(kwargs)

    async def answer_user_question(self, session_id, question_id, answers):
        self.answered.append((session_id, question_id, answers))

    async def interrupt_session(self, session_id):
        self.interrupted.append(session_id)
        return "interrupted"

    async def close_session(self, session_id, *, reason="session closed"):
        self.closed.append((session_id, reason))
        if self.close_error is not None:
            raise self.close_error
        self.sessions.pop(session_id, None)

    async def shutdown_gracefully(self):
        return None


class TestAssistantServiceMore:
    @pytest.mark.asyncio
    async def test_service_init_interrupts_stale_running_sessions(self, tmp_path):
        # Create an in-memory async store and seed data
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        store = SessionMetaStore(session_factory=factory)

        running = await store.create("demo", "sdk-running-1")
        completed = await store.create("demo", "sdk-completed-1")
        await store.update_status(running.id, "running")
        await store.update_status(completed.id, "completed")

        service = AssistantService(project_root=tmp_path)
        # Replace the service's meta_store with our test store
        service.meta_store = store
        service.session_manager.meta_store = store

        # Manually run the interrupt logic (normally done in startup())
        await service._interrupt_stale_running_sessions()

        refreshed_running = await service.meta_store.get(running.id)
        refreshed_completed = await service.meta_store.get(completed.id)
        assert refreshed_running is not None
        assert refreshed_running.status == "interrupted"
        assert refreshed_completed is not None
        assert refreshed_completed.status == "completed"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_startup_waits_cleanup_and_is_idempotent(self, tmp_path, monkeypatch):
        service = AssistantService(project_root=tmp_path)
        calls = 0
        entered = asyncio.Event()
        release = asyncio.Event()

        async def fake_interrupt():
            nonlocal calls
            calls += 1
            entered.set()
            await release.wait()

        monkeypatch.setattr(service, "_interrupt_stale_running_sessions", fake_interrupt)

        startup_task = asyncio.create_task(service.startup())
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        assert not startup_task.done()

        release.set()
        await asyncio.wait_for(startup_task, timeout=0.2)
        assert calls == 1

        await service.startup()
        assert calls == 1

    @pytest.mark.asyncio
    async def test_crud_and_message_validation(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1", status="idle")

        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([meta])
        service.event_log = _FakeEventLogService()

        listed = await service.list_sessions()
        assert len(listed) == 1

        fetched = await service.get_session("s1")
        assert fetched.status == "idle"
        sm.sessions["s1"] = SimpleNamespace(status="running")
        fetched_live = await service.get_session("s1")
        assert fetched_live.status == "running"

        # send_or_create — new session (no session_id)
        new_result = await service.send_or_create("demo", "hello")
        assert new_result == {"status": "accepted", "session_id": "sdk-new-id", "entry": None}
        assert len(sm.new_sessions) == 1
        assert sm.new_sessions[0][0] == "demo"

        # send_or_create — existing session
        existing_result = await service.send_or_create("demo", "world", session_id="s1")
        assert existing_result == {"status": "accepted", "session_id": "s1", "entry": None}
        assert sm.sent == [("s1", "world")]

        # send_or_create — empty message raises ValueError
        with pytest.raises(ValueError):
            await service.send_or_create("demo", "   ")

        # send_or_create — missing session raises FileNotFoundError
        with pytest.raises(FileNotFoundError):
            await service.send_or_create("demo", "hello", session_id="missing")

        # send_or_create — project mismatch raises FileNotFoundError
        with pytest.raises(FileNotFoundError):
            await service.send_or_create("other_project", "hello", session_id="s1")

        with pytest.raises(FileNotFoundError):
            await service.answer_user_question("missing", "q1", {"a": "b"})
        await service.answer_user_question("s1", "q1", {"a": "b"})
        assert sm.answered == [("s1", "q1", {"a": "b"})]

        with pytest.raises(FileNotFoundError):
            await service.interrupt_session("missing")
        interrupted = await service.interrupt_session("s1")
        assert interrupted["session_status"] == "interrupted"

    @pytest.mark.asyncio
    async def test_send_or_create_threads_locale_into_continuation(self, tmp_path):
        """Continuation (existing session) must forward the request locale so a
        cold-recovered session rebuilds its language regulation correctly."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1", status="idle")

        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([meta])
        service.event_log = _FakeEventLogService()

        await service.send_or_create("demo", "world", session_id="s1", locale="en")

        assert sm.sent == [("s1", "world")]
        assert sm.sent_kwargs[0]["locale"] == "en"

    @pytest.mark.asyncio
    async def test_send_or_create_threads_locale_into_multimodal_continuation(self, tmp_path):
        """The image-bearing continuation branch (sdk_prompt is not None) must also
        forward the request locale, not just the text branch."""
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1", status="idle")

        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([meta])
        service.event_log = _FakeEventLogService()

        image = SimpleNamespace(data="ZmFrZQ==", media_type="image/png")
        await service.send_or_create("demo", "hello", session_id="s1", images=[image], locale="vi")

        assert sm.sent_kwargs[0]["locale"] == "vi"
        assert sm.sent_kwargs[0]["echo_content"] is not None

    @pytest.mark.asyncio
    async def test_send_or_create_concurrent_same_client_key_creates_one_session(self, tmp_path):
        """同一 client_key 的并发新建请求应在 send_new_session 完成前互斥等待，
        不因在途窗口各自建会话、重复执行同一 prompt。"""
        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()

        entered = asyncio.Event()
        release = asyncio.Event()

        async def slow_send_new_session(project_name, prompt, **kwargs):
            entered.set()
            await release.wait()
            sm.new_sessions.append((project_name, prompt))
            return "sdk-new-id"

        sm.send_new_session = slow_send_new_session

        async def fake_find_by_client_key(session_id, client_key):
            return {"seq": 0, "type": "user", "content": []}

        service.event_log_store.find_by_client_key = fake_find_by_client_key

        task1 = asyncio.create_task(service.send_or_create("demo", "hello", client_key="ck-race"))
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        task2 = asyncio.create_task(service.send_or_create("demo", "hello", client_key="ck-race"))
        await asyncio.sleep(0)  # 让 task2 跑到锁等待处挂起
        release.set()

        result1 = await task1
        result2 = await task2

        assert len(sm.new_sessions) == 1  # 只建了一个会话，未因在途窗口重复投递
        assert result1["session_id"] == result2["session_id"] == "sdk-new-id"

    @pytest.mark.asyncio
    async def test_delete_session_closes_active_session_before_delete(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1")
        service.meta_store = _FakeMetaStore([meta])
        sm = _FakeSessionManager()
        sm.sessions["s1"] = SimpleNamespace()
        service.session_manager = sm

        ok = await service.delete_session("s1")
        assert ok is True
        assert sm.closed == [("s1", "session deleted")]
        assert "s1" not in sm.sessions

        missing = await service.delete_session("missing")
        assert missing is False

    @pytest.mark.asyncio
    async def test_delete_session_propagates_close_error(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1")
        service.meta_store = _FakeMetaStore([meta])
        sm = _FakeSessionManager()
        sm.sessions["s1"] = SimpleNamespace()
        sm.close_error = RuntimeError("close failed")
        service.session_manager = sm

        with pytest.raises(RuntimeError, match="close failed"):
            await service.delete_session("s1")

        assert "s1" in sm.sessions

    def test_status_event_helpers(self, tmp_path):
        service = AssistantService(project_root=tmp_path)

        assert service._check_runtime_status_terminal({"status": "???."}, "s1") is None
        terminal_event = service._check_runtime_status_terminal({"status": "interrupted"}, "s1")
        assert terminal_event is not None
        assert terminal_event.event == "status"

        sse_event = service._sse_event("status", {"x": 1})
        assert sse_event.event == "status"
        assert sse_event.data == {"x": 1}

        assert service._resolve_result_status({"session_status": "interrupted"}) == "interrupted"
        assert service._resolve_result_status({"subtype": "error_x", "is_error": True}) == "error"
        payload = service._build_status_event_payload("error", "s1", None)
        assert payload["status"] == "error"
        assert payload["subtype"] == "error"
        assert payload["is_error"] is True

    def test_skill_listing_and_metadata_parsing(self, tmp_path, monkeypatch):
        service = AssistantService(project_root=tmp_path)
        service.pm = _FakePM(valid_project="demo")

        agent_skill = tmp_path / "agent_runtime_profile" / ".claude" / "skills" / "s1"
        agent_skill.mkdir(parents=True)
        (agent_skill / "SKILL.md").write_text(
            "---\nname: project-skill\ndescription: from frontmatter\n---\n# body\n",
            encoding="utf-8",
        )

        # Create a fallback skill for metadata parsing test
        fallback_skill_dir = tmp_path / "agent_runtime_profile" / ".claude" / "skills" / "s2"
        fallback_skill_dir.mkdir(parents=True)
        (fallback_skill_dir / "SKILL.md").write_text(
            "first non heading line\n# heading\n",
            encoding="utf-8",
        )

        all_skills = service.list_available_skills()
        names = {item["name"] for item in all_skills}
        assert "project-skill" in names
        assert "s2" in names

        for_project = service.list_available_skills(project_name="demo")
        assert len(for_project) >= 1

        fallback = service._load_skill_metadata(fallback_skill_dir / "SKILL.md", "fallback")
        assert fallback["name"] == "fallback"
        assert fallback["description"] == "first non heading line"
