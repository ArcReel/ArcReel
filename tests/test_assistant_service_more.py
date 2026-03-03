import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.factories import make_session_meta
from server.agent_runtime.service import AssistantService
from server.agent_runtime.session_store import SessionMetaStore
from server.agent_runtime.stream_projector import AssistantStreamProjector


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

    def get(self, session_id):
        return self.metas.get(session_id)

    def list(self, project_name=None, status=None, limit=50, offset=0):
        return list(self.metas.values())

    def update_title(self, session_id, title):
        meta = self.metas.get(session_id)
        if not meta:
            return False
        self.metas[session_id] = make_session_meta(**{**meta.model_dump(), "title": title})
        return True

    def delete(self, session_id):
        return self.metas.pop(session_id, None) is not None


class _FakeSessionManager:
    def __init__(self):
        self.sessions = {}
        self.created = []
        self.sent = []
        self.answered = []
        self.interrupted = []
        self.unsubscribed = []
        self.status = "running"
        self.buffer = []
        self.pending = []

    async def create_session(self, project_name, title):
        self.created.append((project_name, title))
        return make_session_meta(id="s-created", project_name=project_name, title=title)

    def get_status(self, session_id):
        return self.status

    def get_buffered_messages(self, session_id):
        return list(self.buffer)

    async def get_pending_questions_snapshot(self, session_id):
        return list(self.pending)

    async def send_message(self, session_id, content):
        self.sent.append((session_id, content))

    async def answer_user_question(self, session_id, question_id, answers):
        self.answered.append((session_id, question_id, answers))

    async def interrupt_session(self, session_id):
        self.interrupted.append(session_id)
        return "interrupted"

    async def subscribe(self, session_id, replay_buffer=True):
        q = asyncio.Queue()
        for m in self.buffer:
            q.put_nowait(m)
        return q

    async def unsubscribe(self, session_id, queue):
        self.unsubscribed.append(session_id)

    async def shutdown_gracefully(self):
        return None


class _FakeTranscriptReader:
    def __init__(self, history=None):
        self.history = history or []

    def read_raw_messages(self, session_id, sdk_session_id, project_name):
        return list(self.history)


class _ManagedForDelete:
    def __init__(self, disconnect_raises=False):
        self.cancelled = False
        self.consumer_task = asyncio.create_task(asyncio.sleep(3600))
        self.client = SimpleNamespace(disconnect=self._disconnect)
        self._disconnect_raises = disconnect_raises

    def cancel_pending_questions(self, _reason):
        self.cancelled = True

    async def _disconnect(self):
        if self._disconnect_raises:
            raise RuntimeError("disconnect failed")


class TestAssistantServiceMore:
    def test_service_init_interrupts_stale_running_sessions(self, tmp_path):
        data_dir = tmp_path / "projects" / ".agent_data"
        store = SessionMetaStore(data_dir / "sessions.db")

        running = store.create("demo", "Running")
        completed = store.create("demo", "Completed")
        store.update_status(running.id, "running")
        store.update_status(completed.id, "completed")

        service = AssistantService(project_root=tmp_path)

        refreshed_running = service.meta_store.get(running.id)
        refreshed_completed = service.meta_store.get(completed.id)
        assert refreshed_running is not None
        assert refreshed_running.status == "interrupted"
        assert refreshed_completed is not None
        assert refreshed_completed.status == "completed"

    @pytest.mark.asyncio
    async def test_crud_and_message_validation(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1", status="idle")

        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([meta])

        created = await service.create_session("demo", "  ")
        assert created.title == "demo 会话"
        assert sm.created == [("demo", "demo 会话")]

        listed = service.list_sessions()
        assert len(listed) == 1

        fetched = service.get_session("s1")
        assert fetched.status == "idle"
        sm.sessions["s1"] = SimpleNamespace(status="running")
        fetched_live = service.get_session("s1")
        assert fetched_live.status == "running"

        assert service.update_session_title("missing", "x") is None
        updated = service.update_session_title("s1", "  ")
        assert updated.title == "未命名会话"

        with pytest.raises(ValueError):
            await service.send_message("s1", "   ")

        with pytest.raises(FileNotFoundError):
            await service.send_message("missing", "hello")
        accepted = await service.send_message("s1", " hello ")
        assert accepted == {"status": "accepted", "session_id": "s1"}
        assert sm.sent == [("s1", "hello")]

        with pytest.raises(FileNotFoundError):
            await service.answer_user_question("missing", "q1", {"a": "b"})
        await service.answer_user_question("s1", "q1", {"a": "b"})
        assert sm.answered == [("s1", "q1", {"a": "b"})]

        with pytest.raises(FileNotFoundError):
            await service.interrupt_session("missing")
        interrupted = await service.interrupt_session("s1")
        assert interrupted["session_status"] == "interrupted"

    @pytest.mark.asyncio
    async def test_delete_session_handles_active_and_disconnect_error(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1")
        service.meta_store = _FakeMetaStore([meta])
        sm = _FakeSessionManager()
        managed = _ManagedForDelete(disconnect_raises=True)
        sm.sessions["s1"] = managed
        service.session_manager = sm

        ok = await service.delete_session("s1")
        assert ok is True
        assert managed.cancelled is True
        assert "s1" not in sm.sessions

        missing = await service.delete_session("missing")
        assert missing is False

    @pytest.mark.asyncio
    async def test_snapshot_and_stream_helpers(self, tmp_path):
        service = AssistantService(project_root=tmp_path)
        meta = make_session_meta(id="s1", status="running")
        service.meta_store = _FakeMetaStore([meta])
        sm = _FakeSessionManager()
        sm.status = "running"
        sm.buffer = [{"type": "runtime_status", "status": "running"}]
        sm.pending = [{"type": "ask_user_question", "question_id": "aq-1"}]
        service.session_manager = sm
        service.transcript_reader = _FakeTranscriptReader(history=[])

        with pytest.raises(FileNotFoundError):
            await service.get_snapshot("missing")

        snapshot = await service.get_snapshot("s1")
        assert snapshot["status"] == "running"
        assert snapshot["pending_questions"][0]["question_id"] == "aq-1"

        replayed, overflow = service._drain_replay(asyncio.Queue())
        assert replayed == []
        assert overflow is False
        q = asyncio.Queue()
        q.put_nowait({"type": "_queue_overflow"})
        replayed2, overflow2 = service._drain_replay(q)
        assert replayed2 == []
        assert overflow2 is True

        projector = AssistantStreamProjector(initial_messages=[])
        events, should_break = service._dispatch_live_message(
            {"type": "_queue_overflow"},
            projector,
            "s1",
        )
        assert should_break is True
        assert events == []

        events2, stop2 = service._dispatch_live_message(
            {"type": "system", "subtype": "compact_boundary"},
            projector,
            "s1",
        )
        assert stop2 is False
        assert any(event.event == "compact" for event in events2)

        events3, stop3 = service._dispatch_live_message(
            {"type": "runtime_status", "status": "interrupted"},
            projector,
            "s1",
        )
        assert stop3 is True
        assert any(event.event == "status" for event in events3)

        events4, stop4 = service._dispatch_live_message(
            {"type": "result", "subtype": "success", "is_error": False},
            projector,
            "s1",
        )
        assert stop4 is True
        assert any(event.event == "status" for event in events4)

        assert service._check_runtime_status_terminal({"status": "???."}, "s1") is None
        assert service._handle_heartbeat_timeout("s1", "running", projector) is None
        sm.status = "completed"
        status_event = service._handle_heartbeat_timeout("s1", "running", projector)
        assert status_event is not None
        assert status_event.event == "status"
        patch_event = service._sse_event("patch", {"x": 1})
        assert patch_event.event == "patch"
        assert patch_event.data == {"x": 1}

    def test_merge_and_dedup_helpers(self, tmp_path):
        service = AssistantService(project_root=tmp_path)

        assert service._message_key({"uuid": "u1"}) == "uuid:u1"
        fallback_key = service._message_key({"type": "assistant", "content": []})
        assert fallback_key.startswith("{")

        assert service._content_key({"type": "assistant", "content": [{"text": "A"}]}) == "content:assistant:t:A"
        result_key = service._content_key(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "session_id": "s1",
                "timestamp": "2026-02-01T00:00:00Z",
            }
        )
        assert result_key == "content:result:success:False"
        assert service._content_key({"type": "user", "content": "x"}) is None

        seen_keys, seen_content = service._build_seen_sets([{"uuid": "u1"}, "bad"])
        assert "uuid:u1" in seen_keys
        assert isinstance(seen_content, set)

        assert service._is_duplicate({"uuid": "u1"}, {"uuid:u1"}, set()) is True
        assert (
            service._is_duplicate(
                {"type": "assistant", "content": [{"text": "A"}]},
                set(),
                {"content:assistant:t:A"},
            )
            is True
        )

        assert service._parse_iso_datetime(None) is None
        assert service._parse_iso_datetime("bad") is None
        naive = service._parse_iso_datetime("2026-02-01T00:00:00")
        assert naive.tzinfo is not None
        assert service._parse_iso_datetime("2026-02-01T00:00:00Z") is not None

        history = [{"type": "user", "content": "hello", "timestamp": "2026-02-01T00:00:01Z"}]
        local_echo = {
            "type": "user",
            "content": "hello",
            "local_echo": True,
            "timestamp": "2026-02-01T00:00:00Z",
        }
        assert service._should_skip_local_echo(local_echo, history) is True
        assert service._should_skip_local_echo({"type": "assistant"}, history) is False

        assert service._extract_plain_user_content({"type": "assistant"}) is None
        assert (
            service._extract_plain_user_content(
                {"type": "user", "content": [{"type": "text", "text": " ok "}]}
            )
            == "ok"
        )
        assert service._is_groupable_message("bad") is False  # type: ignore[arg-type]

        assert service._resolve_result_status({"session_status": "interrupted"}) == "interrupted"
        assert service._resolve_result_status({"subtype": "error_x", "is_error": True}) == "error"
        payload = service._build_status_event_payload("error", "s1", None)
        assert payload["status"] == "error"
        assert payload["subtype"] == "error"
        assert payload["is_error"] is True

    def test_skill_listing_and_metadata_parsing(self, tmp_path, monkeypatch):
        service = AssistantService(project_root=tmp_path)
        service.pm = _FakePM(valid_project="demo")

        project_skill = tmp_path / ".claude" / "skills" / "s1"
        project_skill.mkdir(parents=True)
        (project_skill / "SKILL.md").write_text(
            "---\nname: project-skill\ndescription: from frontmatter\n---\n# body\n",
            encoding="utf-8",
        )

        fake_home = tmp_path / "home"
        user_skill = fake_home / ".claude" / "skills" / "s2"
        user_skill.mkdir(parents=True)
        (user_skill / "SKILL.md").write_text(
            "first non heading line\n# heading\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

        all_skills = service.list_available_skills()
        names = {item["name"] for item in all_skills}
        assert "project-skill" in names
        assert "s2" in names

        for_project = service.list_available_skills(project_name="demo")
        assert len(for_project) >= 1

        fallback = service._load_skill_metadata(user_skill / "SKILL.md", "fallback")
        assert fallback["name"] == "fallback"
        assert fallback["description"] == "first non heading line"

        # no .env => no-op path
        service._load_project_env(tmp_path / "missing")
