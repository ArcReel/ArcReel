import asyncio
from types import SimpleNamespace

import pytest

from tests.fakes import FakeSDKClient
from server.agent_runtime import session_manager as sm_mod
from server.agent_runtime.session_manager import ManagedSession
from server.agent_runtime.session_store import SessionMetaStore


class _FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeClaudeClient:
    def __init__(self, options):
        self.options = options
        self.connected = False

    async def connect(self):
        self.connected = True


class _InterruptibleClient:
    def __init__(self, disconnect_raises=False):
        self.interrupted = False
        self.disconnect_raises = disconnect_raises

    async def interrupt(self):
        self.interrupted = True

    async def disconnect(self):
        if self.disconnect_raises:
            raise RuntimeError("disconnect failed")

    async def receive_response(self):
        if False:
            yield None


class _CancelClient:
    async def receive_response(self):
        raise asyncio.CancelledError
        if False:
            yield None


class _ErrorClient:
    async def receive_response(self):
        raise RuntimeError("stream failed")
        if False:
            yield None


class _FakeAllow:
    def __init__(self, updated_input):
        self.updated_input = updated_input


class _FakeDeny:
    def __init__(self, message, interrupt=False):
        self.message = message
        self.interrupt = interrupt


class TestSessionManagerMore:
    def test_managed_session_buffer_and_queue_overflow(self):
        managed = ManagedSession(session_id="s1", client=object(), buffer_max_size=2)
        managed.message_buffer = [
            {"type": "stream_event", "id": "a"},
            {"type": "assistant", "id": "b"},
        ]
        managed.add_message({"type": "assistant", "id": "c"})
        assert len(managed.message_buffer) == 2
        assert all(msg["id"] != "a" for msg in managed.message_buffer)

        queue = asyncio.Queue(maxsize=1)
        queue.put_nowait({"type": "stream_event"})
        managed.subscribers = {queue}
        managed.add_message({"type": "result", "uuid": "r1"})
        assert queue.get_nowait()["type"] == "result"

        # queue has only critical message; next critical should overflow and drop subscriber
        stale_queue = asyncio.Queue(maxsize=1)
        stale_queue.put_nowait({"type": "result"})
        managed.subscribers = {stale_queue}
        managed.add_message({"type": "assistant"})
        assert stale_queue.get_nowait()["type"] == "_queue_overflow"
        assert stale_queue not in managed.subscribers

    @pytest.mark.asyncio
    async def test_pending_question_lifecycle(self):
        managed = ManagedSession(session_id="s1", client=object())
        pending = managed.add_pending_question({"type": "ask_user_question", "questions": []})
        assert pending.question_id
        assert managed.resolve_pending_question(pending.question_id, {"Q": "A"})
        assert await pending.answer_future == {"Q": "A"}
        assert not managed.resolve_pending_question("missing", {})

        pending2 = managed.add_pending_question({"type": "ask_user_question"})
        managed.cancel_pending_questions("closed")
        with pytest.raises(RuntimeError):
            await pending2.answer_future
        assert managed.get_pending_question_payloads() == []

    @pytest.mark.asyncio
    async def test_build_options_and_connect_paths(self, session_manager, meta_store, tmp_path, monkeypatch):
        with monkeypatch.context() as m:
            m.setattr(sm_mod, "SDK_AVAILABLE", False)
            with pytest.raises(RuntimeError):
                session_manager._build_options("demo")

        projects_demo = tmp_path / "projects" / "demo"
        projects_demo.mkdir(parents=True)
        meta = meta_store.create("demo", "title")

        with monkeypatch.context() as m:
            m.setattr(sm_mod, "SDK_AVAILABLE", True)
            m.setattr(sm_mod, "ClaudeAgentOptions", _FakeOptions)
            m.setattr(sm_mod, "ClaudeSDKClient", _FakeClaudeClient)
            m.setattr(sm_mod, "HookMatcher", None)
            managed = await session_manager.get_or_connect(meta.id)
            assert managed.client.connected
            assert managed is await session_manager.get_or_connect(meta.id)

        assert await session_manager._keep_stream_open_hook({}, None, None) == {"continue_": True}

    def test_resolve_project_scope_and_status_helpers(self, session_manager, tmp_path, meta_store):
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
        with pytest.raises(ValueError):
            session_manager._resolve_project_cwd("../evil")

        assert session_manager.get_status("missing") is None
        meta = meta_store.create("demo", "title")
        assert session_manager.get_status(meta.id) == "idle"

    @pytest.mark.asyncio
    async def test_send_message_and_interrupt_branches(self, session_manager, meta_store):
        meta = meta_store.create("demo", "title")
        managed_running = ManagedSession(session_id=meta.id, client=FakeSDKClient(), status="running")
        session_manager.sessions[meta.id] = managed_running
        with pytest.raises(ValueError):
            await session_manager.send_message(meta.id, "blocked")

        session_manager.sessions.pop(meta.id)
        client = FakeSDKClient()

        async def _boom(_content):
            raise RuntimeError("query failed")

        client.query = _boom  # type: ignore[method-assign]
        managed = ManagedSession(session_id=meta.id, client=client, status="idle")
        session_manager.sessions[meta.id] = managed
        with pytest.raises(RuntimeError):
            await session_manager.send_message(meta.id, "hello")
        assert managed.status == "error"
        assert meta_store.get(meta.id).status == "error"

        with pytest.raises(FileNotFoundError):
            await session_manager.interrupt_session("missing")

        meta2 = meta_store.create("demo", "title2")
        meta_store.update_status(meta2.id, "running")
        assert await session_manager.interrupt_session(meta2.id) == "interrupted"
        assert meta_store.get(meta2.id).status == "interrupted"

        meta3 = meta_store.create("demo", "title3")
        assert await session_manager.interrupt_session(meta3.id) == "idle"

        managed_idle = ManagedSession(session_id=meta3.id, client=FakeSDKClient(), status="completed")
        session_manager.sessions[meta3.id] = managed_idle
        assert await session_manager.interrupt_session(meta3.id) == "completed"

    @pytest.mark.asyncio
    async def test_consume_messages_terminal_paths(self, session_manager, meta_store):
        meta = meta_store.create("demo", "title")
        managed_cancel = ManagedSession(session_id=meta.id, client=_CancelClient(), status="running")
        session_manager.sessions[meta.id] = managed_cancel
        meta_store.update_status(meta.id, "running")
        with pytest.raises(asyncio.CancelledError):
            await session_manager._consume_messages(managed_cancel)
        assert managed_cancel.status == "interrupted"

        meta2 = meta_store.create("demo", "title2")
        managed_error = ManagedSession(session_id=meta2.id, client=_ErrorClient(), status="running")
        session_manager.sessions[meta2.id] = managed_error
        meta_store.update_status(meta2.id, "running")
        with pytest.raises(RuntimeError):
            await session_manager._consume_messages(managed_error)
        assert managed_error.status == "error"

    @pytest.mark.asyncio
    async def test_can_use_tool_callback_branches(self, session_manager, monkeypatch):
        monkeypatch.setattr(sm_mod, "PermissionResultAllow", _FakeAllow)
        monkeypatch.setattr(sm_mod, "PermissionResultDeny", _FakeDeny)

        allow_cb = session_manager._build_can_use_tool_callback("unknown-session")
        # With unknown session, project_cwd is None → fail-close: path-based tools denied
        result = await allow_cb("Read", {"x": 1}, None)
        assert hasattr(result, "message")  # denied
        # Non-path tools still allowed
        result2 = await allow_cb("AskUserQuestion", {"questions": []}, None)
        assert result2.updated_input == {"questions": []}

        managed = ManagedSession(session_id="s1", client=FakeSDKClient(), status="running")
        session_manager.sessions["s1"] = managed
        ask_cb = session_manager._build_can_use_tool_callback("s1")

        task = asyncio.create_task(ask_cb("AskUserQuestion", {"questions": [{"question": "Q"}]}, None))
        await asyncio.sleep(0)
        assert managed.pending_questions
        managed.cancel_pending_questions("user interrupted")
        deny = await task
        assert deny.interrupt is True
        assert "user interrupted" in deny.message

    def test_misc_helpers_and_serialization(self, session_manager):
        assert sm_mod.SessionManager._extract_plain_user_content({"type": "user", "content": " hi "}) == "hi"
        assert sm_mod.SessionManager._extract_plain_user_content(
            {"type": "user", "content": [{"type": "text", "text": " hello "}]}
        ) == "hello"
        assert sm_mod.SessionManager._extract_plain_user_content({"type": "assistant"}) is None

        msg = {}
        raw = SimpleNamespace(session_id="sdk-1")
        assert session_manager._extract_sdk_session_id(raw, msg) == "sdk-1"
        assert session_manager._extract_sdk_session_id(raw, {"sessionId": "sdk-2"}) == "sdk-2"

        status = session_manager._build_runtime_status_message("error", "s1")
        assert status["type"] == "runtime_status"
        assert status["is_error"] is True

        managed = ManagedSession(
            session_id="s1",
            client=object(),
            message_buffer=[{"type": "stream_event"}, {"type": "assistant"}, {"type": "custom"}],
        )
        session_manager._prune_transient_buffer(managed)
        assert managed.message_buffer == [{"type": "custom"}]
        managed.clear_buffer()
        assert managed.message_buffer == []

        assert session_manager._resolve_result_status({"subtype": "error_timeout"}) == "error"
        assert (
            session_manager._resolve_result_status(
                {"subtype": "success", "is_error": False},
                interrupt_requested=True,
            )
            == "completed"
        )

    @pytest.mark.asyncio
    async def test_buffer_snapshots_subscribe_and_shutdown(self, session_manager, meta_store):
        assert await session_manager.get_message_buffer_snapshot("missing") == []
        assert session_manager.get_buffered_messages("missing") == []
        assert await session_manager.get_pending_questions_snapshot("missing") == []
        with pytest.raises(ValueError):
            await session_manager.answer_user_question("missing", "q", {"a": "b"})

        meta = meta_store.create("demo", "title")
        client = _InterruptibleClient(disconnect_raises=True)
        managed = ManagedSession(
            session_id=meta.id,
            client=client,
            status="running",
            message_buffer=[{"type": "assistant", "uuid": "a1"}],
        )
        managed.consumer_task = asyncio.create_task(asyncio.sleep(3600))
        session_manager.sessions[meta.id] = managed

        queue = await session_manager.subscribe(meta.id, replay_buffer=True)
        assert queue.get_nowait()["uuid"] == "a1"
        await session_manager.unsubscribe(meta.id, queue)
        assert queue not in managed.subscribers

        await session_manager.shutdown_gracefully(timeout=0.01)
        assert client.interrupted is True
        assert session_manager.sessions == {}

    @pytest.mark.asyncio
    async def test_can_use_tool_blocks_read_outside_project(self, tmp_path, monkeypatch):
        """Read denied for other project dirs, allowed for own project and public dirs."""
        monkeypatch.setattr(sm_mod, "PermissionResultAllow", _FakeAllow)
        monkeypatch.setattr(sm_mod, "PermissionResultDeny", _FakeDeny)

        # Set up directories
        own_project = tmp_path / "projects" / "alpha"
        own_project.mkdir(parents=True)
        other_project = tmp_path / "projects" / "beta"
        other_project.mkdir(parents=True)
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True)

        meta_store = SessionMetaStore(tmp_path / "sessions.db")
        mgr = sm_mod.SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=meta_store,
        )

        meta = meta_store.create("alpha", "test session")
        managed = ManagedSession(session_id=meta.id, client=FakeSDKClient())
        mgr.sessions[meta.id] = managed

        cb = mgr._build_can_use_tool_callback(meta.id)

        # Read own project file — allowed
        result = await cb("Read", {"file_path": str(own_project / "script.json")}, None)
        assert hasattr(result, "updated_input")

        # Read other project file — denied
        result = await cb("Read", {"file_path": str(other_project / "script.json")}, None)
        assert hasattr(result, "message")
        assert "访问被拒绝" in result.message

        # Read public docs dir — allowed
        result = await cb("Read", {"file_path": str(docs_dir / "guide.md")}, None)
        assert hasattr(result, "updated_input")

    @pytest.mark.asyncio
    async def test_can_use_tool_blocks_write_to_readonly_dir(self, tmp_path, monkeypatch):
        """Write denied to lib/, allowed to own project."""
        monkeypatch.setattr(sm_mod, "PermissionResultAllow", _FakeAllow)
        monkeypatch.setattr(sm_mod, "PermissionResultDeny", _FakeDeny)

        own_project = tmp_path / "projects" / "alpha"
        own_project.mkdir(parents=True)
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir(parents=True)

        meta_store = SessionMetaStore(tmp_path / "sessions.db")
        mgr = sm_mod.SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=meta_store,
        )

        meta = meta_store.create("alpha", "test session")
        managed = ManagedSession(session_id=meta.id, client=FakeSDKClient())
        mgr.sessions[meta.id] = managed

        cb = mgr._build_can_use_tool_callback(meta.id)

        # Write own project file — allowed
        result = await cb("Write", {"file_path": str(own_project / "output.txt")}, None)
        assert hasattr(result, "updated_input")

        # Write to lib/ (readonly) — denied
        result = await cb("Write", {"file_path": str(lib_dir / "hack.py")}, None)
        assert hasattr(result, "message")
        assert "访问被拒绝" in result.message

    @pytest.mark.asyncio
    async def test_can_use_tool_allows_bash_without_path_check(self, tmp_path, monkeypatch):
        """Bash tool not intercepted by path check."""
        monkeypatch.setattr(sm_mod, "PermissionResultAllow", _FakeAllow)
        monkeypatch.setattr(sm_mod, "PermissionResultDeny", _FakeDeny)

        own_project = tmp_path / "projects" / "alpha"
        own_project.mkdir(parents=True)

        meta_store = SessionMetaStore(tmp_path / "sessions.db")
        mgr = sm_mod.SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=meta_store,
        )

        meta = meta_store.create("alpha", "test session")
        managed = ManagedSession(session_id=meta.id, client=FakeSDKClient())
        mgr.sessions[meta.id] = managed

        cb = mgr._build_can_use_tool_callback(meta.id)

        # Bash — allowed without path check
        result = await cb("Bash", {"command": "ls /etc"}, None)
        assert hasattr(result, "updated_input")

    @pytest.mark.asyncio
    async def test_can_use_tool_allows_read_claude_md(self, tmp_path, monkeypatch):
        """Read allowed for root CLAUDE.md."""
        monkeypatch.setattr(sm_mod, "PermissionResultAllow", _FakeAllow)
        monkeypatch.setattr(sm_mod, "PermissionResultDeny", _FakeDeny)

        own_project = tmp_path / "projects" / "alpha"
        own_project.mkdir(parents=True)
        claude_md = tmp_path / "CLAUDE.md"
        claude_md.write_text("# Project instructions")

        meta_store = SessionMetaStore(tmp_path / "sessions.db")
        mgr = sm_mod.SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=meta_store,
        )

        meta = meta_store.create("alpha", "test session")
        managed = ManagedSession(session_id=meta.id, client=FakeSDKClient())
        mgr.sessions[meta.id] = managed

        cb = mgr._build_can_use_tool_callback(meta.id)

        # Read CLAUDE.md — allowed
        result = await cb("Read", {"file_path": str(claude_md)}, None)
        assert hasattr(result, "updated_input")
