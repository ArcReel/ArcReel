import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

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


class _MultiProjectPM:
    """ProjectManager accepting multiple valid project names for cross-project session-isolation tests."""

    def __init__(self, valid_projects):
        self.valid_projects = set(valid_projects)

    def get_project_path(self, project_name):
        if project_name not in self.valid_projects:
            raise FileNotFoundError(project_name)
        return Path(tempfile.gettempdir()) / project_name


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
    """No-op event log: unit tests cover service orchestration without touching a real DB."""

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
        return {"queued_message": {"id": "q1", "content": [], "timestamp": "t", "state": "queued"}}

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


class TestAssistantService:
    @pytest.mark.asyncio
    async def test_service_init_interrupts_stale_running_sessions(self, tmp_path, db_factory):
        # Create an in-memory async store and seed data
        store = SessionMetaStore(session_factory=db_factory)

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
        assert new_result == {"status": "accepted", "session_id": "sdk-new-id", "entry": None, "queued_message": None}
        assert len(sm.new_sessions) == 1
        assert sm.new_sessions[0][0] == "demo"

        # send_or_create — existing session
        existing_result = await service.send_or_create("demo", "world", session_id="s1")
        assert existing_result == {
            "status": "accepted",
            "session_id": "s1",
            "entry": None,
            "queued_message": {"id": "q1", "content": [], "timestamp": "t", "state": "queued"},
        }
        assert sm.sent == [("s1", "world")]

        # send_or_create — empty message raises ValueError
        with pytest.raises(ValueError, match=r"消息内容不能为空"):
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
        """Continuation (existing session) must forward the request locale to the
        revival, which renders the language regulation for a fresh session."""
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
        assert [block["type"] for block in sm.sent[0][1]] == ["image", "text"]

    @pytest.mark.asyncio
    async def test_send_or_create_concurrent_same_client_key_creates_one_session(self, tmp_path):
        """Concurrent new-session requests sharing client_key wait mutually exclusively until send_new_session completes, avoiding duplicate sessions and execution of the same prompt during the in-flight window."""
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

        async def fake_find_new_session_by_client_key(client_key):
            return None

        service.event_log_store.find_by_client_key = fake_find_by_client_key
        service.event_log_store.find_new_session_by_client_key = fake_find_new_session_by_client_key

        task1 = asyncio.create_task(service.send_or_create("demo", "hello", client_key="ck-race"))
        await asyncio.wait_for(entered.wait(), timeout=0.2)
        task2 = asyncio.create_task(service.send_or_create("demo", "hello", client_key="ck-race"))
        await asyncio.sleep(0)  # Let task2 suspend while awaiting the lock.
        release.set()

        result1 = await task1
        result2 = await task2

        assert (
            len(sm.new_sessions) == 1
        )  # Only one session created; the in-flight window did not cause duplicate delivery.
        assert result1["session_id"] == result2["session_id"] == "sdk-new-id"

    @pytest.mark.asyncio
    async def test_send_or_create_recovers_client_key_mapping_from_db_after_restart(self, tmp_path, db_factory):
        """After restart loses the in-process mapping, retry an already persisted acceptance (new-session first entry seq 0 contains client_key) through DB fallback to the existing session, without duplicate creation."""
        from server.agent_runtime.event_log import EventLogStore, build_user_entry

        store = EventLogStore(session_factory=db_factory)

        # First acceptance (before restart): persist the new session's first user entry with client_key.
        accepted_entry = build_user_entry([{"type": "text", "text": "hello"}])
        await store.append("sdk-prev", [accepted_entry], client_key="ck-restart")

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store
        assert service._new_session_client_keys == {}  # In-process mapping is empty after restart.

        result = await service.send_or_create("demo", "hello", client_key="ck-restart")

        assert sm.new_sessions == []  # No duplicate session created.
        assert result["session_id"] == "sdk-prev"
        assert result["entry"] is not None
        assert result["entry"]["uuid"] == accepted_entry["uuid"]
        # Backfill the in-process mapping on a hit; subsequent retries use the fast path.
        assert service._new_session_client_keys["ck-restart"] == "sdk-prev"

    @pytest.mark.asyncio
    async def test_send_or_create_recovers_client_key_after_lru_eviction(self, tmp_path, db_factory):
        """After LRU eviction of an old key, same-key retries find the original session through DB fallback."""
        from server.agent_runtime.event_log import EventLogStore

        store = EventLogStore(session_factory=db_factory)

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store
        service._new_session_client_keys_max = 1

        # Simulate SessionManager acceptance persistence: write first entry with client_key.
        async def send_new_session(project_name, prompt, *, user_entry=None, client_key=None, **kwargs):
            sid = f"sdk-{len(sm.new_sessions)}"
            sm.new_sessions.append((project_name, prompt))
            if user_entry is not None:
                await store.append_user_entry(sid, user_entry, client_key=client_key)
            return sid

        sm.send_new_session = send_new_session

        first = await service.send_or_create("demo", "hello", client_key="ck-a")
        await service.send_or_create("demo", "another", client_key="ck-b")
        assert "ck-a" not in service._new_session_client_keys  # Evicted from LRU.

        retry = await service.send_or_create("demo", "hello", client_key="ck-a")

        assert len(sm.new_sessions) == 2  # Retry did not create a third session.
        assert retry["session_id"] == first["session_id"] == "sdk-0"

    @staticmethod
    def _make_project_scoped_service(tmp_path, store, sm, meta_store):
        """Assemble a service whose cross-project send_new_session persists meta and event logs."""
        service = AssistantService(project_root=tmp_path)
        service.pm = _MultiProjectPM({"proj_a", "proj_b"})
        service.session_manager = sm
        service.meta_store = meta_store
        service.event_log = _FakeEventLogService()
        service.event_log_store = store

        async def send_new_session(project_name, prompt, *, user_entry=None, client_key=None, **kwargs):
            sid = f"sdk-{len(sm.new_sessions)}"
            sm.new_sessions.append((project_name, prompt))
            meta_store.metas[sid] = make_session_meta(id=sid, project_name=project_name)
            if user_entry is not None:
                await store.append_user_entry(sid, user_entry, client_key=client_key)
            return sid

        sm.send_new_session = send_new_session
        return service

    @pytest.mark.asyncio
    async def test_new_session_client_key_project_scoped_via_map_hit(self, tmp_path, db_factory):
        """For a client_key accepted in project A, send_or_create without session_id in project B returns a newly created B session rather than silently reconnecting A. Cover the in-process mapping fast path."""
        from server.agent_runtime.event_log import EventLogStore

        store = EventLogStore(session_factory=db_factory)

        sm = _FakeSessionManager()
        meta_store = _FakeMetaStore([])
        service = self._make_project_scoped_service(tmp_path, store, sm, meta_store)

        first = await service.send_or_create("proj_a", "hello", client_key="ck-shared")
        assert first["session_id"] == "sdk-0"
        # Mapping ready: project B first takes the in-process hit fast path.
        assert service._new_session_client_keys["ck-shared"] == "sdk-0"

        second = await service.send_or_create("proj_b", "hello", client_key="ck-shared")

        assert second["session_id"] == "sdk-1"  # Create project B session instead of reconnecting A.
        assert meta_store.metas["sdk-1"].project_name == "proj_b"
        assert sm.new_sessions == [("proj_a", "hello"), ("proj_b", "hello")]
        # Overwrite mapping with this project's session; subsequent same-project retries hit B.
        assert service._new_session_client_keys["ck-shared"] == "sdk-1"

    @pytest.mark.asyncio
    async def test_new_session_client_key_project_scoped_via_db_fallback(self, tmp_path, db_factory):
        """After clearing the mapping to simulate restart, DB fallback remains project-isolated: A's client_key misses in B and creates a B session."""
        from server.agent_runtime.event_log import EventLogStore

        store = EventLogStore(session_factory=db_factory)

        sm = _FakeSessionManager()
        meta_store = _FakeMetaStore([])
        service = self._make_project_scoped_service(tmp_path, store, sm, meta_store)

        first = await service.send_or_create("proj_a", "hello", client_key="ck-shared")
        assert first["session_id"] == "sdk-0"
        service._new_session_client_keys.clear()  # Simulate restart: in-process mapping lost.

        second = await service.send_or_create("proj_b", "hello", client_key="ck-shared")

        assert second["session_id"] == "sdk-1"  # DB fallback finds A but project differs; create B.
        assert meta_store.metas["sdk-1"].project_name == "proj_b"
        assert sm.new_sessions == [("proj_a", "hello"), ("proj_b", "hello")]

    @pytest.mark.asyncio
    async def test_new_session_client_key_same_project_retry_still_idempotent(self, tmp_path, db_factory):
        """Preserve same-project idempotency: resending the same client_key finds the original session without duplication through both mapping and DB fallback."""
        from server.agent_runtime.event_log import EventLogStore

        store = EventLogStore(session_factory=db_factory)

        sm = _FakeSessionManager()
        meta_store = _FakeMetaStore([])
        service = self._make_project_scoped_service(tmp_path, store, sm, meta_store)

        first = await service.send_or_create("proj_a", "hello", client_key="ck-same")
        assert first["session_id"] == "sdk-0"

        # Mapping fast-path hit.
        hit = await service.send_or_create("proj_a", "hello", client_key="ck-same")
        assert hit["session_id"] == "sdk-0"
        assert len(sm.new_sessions) == 1

        # After clearing the mapping, DB fallback also finds the original session.
        service._new_session_client_keys.clear()
        recovered = await service.send_or_create("proj_a", "hello", client_key="ck-same")
        assert recovered["session_id"] == "sdk-0"
        assert len(sm.new_sessions) == 1  # No duplicate session created throughout.

    @pytest.mark.asyncio
    async def test_send_or_create_hit_refreshes_lru_position(self, tmp_path, db_factory):
        """Refresh LRU position on mapping hits: frequently retried keys must not be evicted by later one-off keys merely because they were inserted earlier (FIFO degeneration)."""
        from server.agent_runtime.event_log import EventLogStore

        store = EventLogStore(session_factory=db_factory)

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store
        service._new_session_client_keys_max = 2

        async def send_new_session(project_name, prompt, *, user_entry=None, client_key=None, **kwargs):
            sid = f"sdk-{len(sm.new_sessions)}"
            sm.new_sessions.append((project_name, prompt))
            if user_entry is not None:
                await store.append_user_entry(sid, user_entry, client_key=client_key)
            return sid

        sm.send_new_session = send_new_session

        await service.send_or_create("demo", "hello-a", client_key="ck-a")
        await service.send_or_create("demo", "hello-b", client_key="ck-b")
        assert list(service._new_session_client_keys) == ["ck-a", "ck-b"]

        # Hit ck-a (idempotent retry, no new session): move ck-a to most recently used.
        hit = await service.send_or_create("demo", "hello-a", client_key="ck-a")
        assert hit["session_id"] == "sdk-0"
        assert list(service._new_session_client_keys) == ["ck-b", "ck-a"]

        # Third key evicts the least recently used ck-b rather than just-hit ck-a.
        await service.send_or_create("demo", "hello-c", client_key="ck-c")

        assert "ck-a" in service._new_session_client_keys
        assert "ck-b" not in service._new_session_client_keys
        assert len(sm.new_sessions) == 3  # a/b/c each truly create a session; hits create no duplicates.

    @pytest.mark.asyncio
    async def test_send_or_create_hit_survives_concurrent_eviction_during_db_lookup(
        self, tmp_path, monkeypatch, db_factory
    ):
        """If another request evicts a key during `await find_by_client_key(...)`, LRU refresh must safely handle its absence rather than move_to_end raising KeyError and turning an idempotent hit into an unhandled error."""
        from server.agent_runtime.event_log import EventLogStore

        store = EventLogStore(session_factory=db_factory)

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store

        async def send_new_session(project_name, prompt, *, user_entry=None, client_key=None, **kwargs):
            sid = f"sdk-{len(sm.new_sessions)}"
            sm.new_sessions.append((project_name, prompt))
            if user_entry is not None:
                await store.append_user_entry(sid, user_entry, client_key=client_key)
            return sid

        sm.send_new_session = send_new_session

        first = await service.send_or_create("demo", "hello", client_key="ck-a")
        assert "ck-a" in service._new_session_client_keys

        original_find_by_client_key = store.find_by_client_key

        async def find_by_client_key_with_concurrent_eviction(session_id, client_key):
            # Simulate another concurrent request evicting this key during the DB lookup.
            service._new_session_client_keys.pop(client_key, None)
            return await original_find_by_client_key(session_id, client_key)

        monkeypatch.setattr(store, "find_by_client_key", find_by_client_key_with_concurrent_eviction)

        retry = await service.send_or_create("demo", "hello", client_key="ck-a")

        assert retry["session_id"] == first["session_id"] == "sdk-0"
        assert len(sm.new_sessions) == 1  # Neither an exception nor missing key causes duplicate creation.
        assert service._new_session_client_keys["ck-a"] == "sdk-0"  # Safely recorded again after hit.

    @pytest.mark.asyncio
    async def test_send_or_create_falls_back_when_cached_mapping_points_to_deleted_session(self, tmp_path, db_factory):
        """If a mapped session entry no longer exists (for example, deletion left a stale mapping), do not return a ghost "accepted" response with entry=None. Clear the invalid mapping and create normally rather than directing callers to a nonexistent session."""
        from server.agent_runtime.event_log import EventLogStore

        store = EventLogStore(session_factory=db_factory)

        service = AssistantService(project_root=tmp_path)
        sm = _FakeSessionManager()
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = sm
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()
        service.event_log_store = store
        # Simulate a deleted session with stale in-process mapping (delete_session does not know that mapping).
        service._new_session_client_keys["ck-stale"] = "sdk-deleted"

        result = await service.send_or_create("demo", "hello", client_key="ck-stale")

        assert len(sm.new_sessions) == 1  # No ghost response; take normal creation path.
        assert result["session_id"] == "sdk-new-id"
        assert result["session_id"] != "sdk-deleted"
        # Mapping refreshed to the new session, no longer pointing at the deleted one.
        assert service._new_session_client_keys["ck-stale"] == "sdk-new-id"

    @pytest.mark.asyncio
    async def test_ghost_mapping_cleanup_does_not_clobber_concurrent_fresh_mapping(self, tmp_path):
        """If another request writes a new valid mapping during `await find_by_client_key(...)`, ghost cleanup must not unconditionally pop it. Even if later DB fallback recovers the correct session, avoid this unnecessary instability window."""
        service = AssistantService(project_root=tmp_path)
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = _FakeSessionManager()
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()

        async def find_by_client_key(session_id, client_key):
            # Simulate another concurrent request writing a new valid mapping for this key during lookup.
            service._new_session_client_keys[client_key] = "sdk-fresh"
            return  # The old session's sdk-deleted entry is already absent.

        async def find_new_session_by_client_key(client_key):
            return None  # This test covers only cleanup; no actual fallback recovery needed.

        service.event_log_store = SimpleNamespace(
            find_by_client_key=find_by_client_key,
            find_new_session_by_client_key=find_new_session_by_client_key,
        )
        service._new_session_client_keys["ck-a"] = "sdk-deleted"

        result = await service._find_accepted_new_session("ck-a", "demo")

        assert result is None  # Fallback lookup returns None without affecting the assertion under test.
        assert service._new_session_client_keys["ck-a"] == "sdk-fresh"  # Not incorrectly deleted.

    @pytest.mark.asyncio
    async def test_recovered_mapping_does_not_overwrite_concurrent_fresher_mapping(self, tmp_path):
        """If another request writes a newer mapping during `await find_new_session_by_client_key(...)`, DB fallback must not overwrite it with the older session_id found by this call."""
        service = AssistantService(project_root=tmp_path)
        service.pm = _FakePM(valid_project="demo")
        service.session_manager = _FakeSessionManager()
        service.meta_store = _FakeMetaStore([])
        service.event_log = _FakeEventLogService()

        async def find_by_client_key(session_id, client_key):
            return None

        async def find_new_session_by_client_key(client_key):
            # Simulate another concurrent request creating and mapping a new session during lookup.
            service._new_session_client_keys[client_key] = "sdk-fresh"
            return "sdk-old", {"seq": 0, "type": "user"}

        service.event_log_store = SimpleNamespace(
            find_by_client_key=find_by_client_key,
            find_new_session_by_client_key=find_new_session_by_client_key,
        )

        result = await service._find_accepted_new_session("ck-b", "demo")

        assert result is not None
        assert result["session_id"] == "sdk-old"  # This call still returns its own authoritative lookup result,
        # but must not overwrite the newer concurrent mapping.
        assert service._new_session_client_keys["ck-b"] == "sdk-fresh"

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

        payload = service._build_status_event_payload("error", "s1", None)
        assert payload["status"] == "error"
        assert payload["subtype"] == "error"
        assert payload["is_error"] is True

    def test_skill_listing_and_metadata_parsing(self, tmp_path, monkeypatch):
        service = AssistantService(project_root=tmp_path)
        service.pm = _FakePM(valid_project="demo")

        profile_root = tmp_path / "agent_runtime_profile"
        monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(profile_root))
        agent_skill = profile_root / ".claude" / "skills" / "s1"
        agent_skill.mkdir(parents=True)
        (agent_skill / "SKILL.md").write_text(
            "---\nname: project-skill\ndescription: from frontmatter\n---\n# body\n",
            encoding="utf-8",
        )

        # Create a fallback skill for metadata parsing test
        fallback_skill_dir = profile_root / ".claude" / "skills" / "s2"
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
