import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from lib.db.base import Base
from server.agent_runtime import session_manager as sm_mod
from server.agent_runtime.session_manager import ManagedSession
from server.agent_runtime.session_store import SessionMetaStore
from tests.fakes import FakeSDKClient


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
        meta = await meta_store.create("demo", "sdk-build-opts")

        with monkeypatch.context() as m:
            m.setattr(sm_mod, "SDK_AVAILABLE", True)
            m.setattr(sm_mod, "ClaudeAgentOptions", _FakeOptions)
            m.setattr(sm_mod, "ClaudeSDKClient", _FakeClaudeClient)
            m.setattr(sm_mod, "HookMatcher", None)
            managed = await session_manager.get_or_connect(meta.id)
            assert managed.client.connected
            assert managed is await session_manager.get_or_connect(meta.id)

        assert await session_manager._keep_stream_open_hook({}, None, None) == {"continue_": True}

    @pytest.mark.asyncio
    async def test_resolve_project_scope_and_status_helpers(self, session_manager, tmp_path, meta_store):
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
        with pytest.raises(ValueError):
            session_manager._resolve_project_cwd("../evil")

        assert await session_manager.get_status("missing") is None
        meta = await meta_store.create("demo", "sdk-resolve-status")
        assert await session_manager.get_status(meta.id) == "idle"

    @pytest.mark.asyncio
    async def test_send_message_and_interrupt_branches(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-send-msg")
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
        assert (await meta_store.get(meta.id)).status == "error"

        with pytest.raises(FileNotFoundError):
            await session_manager.interrupt_session("missing")

        meta2 = await meta_store.create("demo", "sdk-interrupt-1")
        await meta_store.update_status(meta2.id, "running")
        assert await session_manager.interrupt_session(meta2.id) == "interrupted"
        assert (await meta_store.get(meta2.id)).status == "interrupted"

        meta3 = await meta_store.create("demo", "sdk-interrupt-2")
        assert await session_manager.interrupt_session(meta3.id) == "idle"

        managed_idle = ManagedSession(session_id=meta3.id, client=FakeSDKClient(), status="completed")
        session_manager.sessions[meta3.id] = managed_idle
        assert await session_manager.interrupt_session(meta3.id) == "completed"

    @pytest.mark.asyncio
    async def test_consume_messages_terminal_paths(self, session_manager, meta_store):
        meta = await meta_store.create("demo", "sdk-cancel-1")
        managed_cancel = ManagedSession(session_id=meta.id, client=_CancelClient(), status="running")
        session_manager.sessions[meta.id] = managed_cancel
        await meta_store.update_status(meta.id, "running")
        with pytest.raises(asyncio.CancelledError):
            await session_manager._consume_messages(managed_cancel)
        assert managed_cancel.status == "interrupted"

        meta2 = await meta_store.create("demo", "sdk-cancel-2")
        managed_error = ManagedSession(session_id=meta2.id, client=_ErrorClient(), status="running")
        session_manager.sessions[meta2.id] = managed_error
        await meta_store.update_status(meta2.id, "running")
        with pytest.raises(RuntimeError):
            await session_manager._consume_messages(managed_error)
        assert managed_error.status == "error"

    @pytest.mark.asyncio
    async def test_can_use_tool_callback_branches(self, session_manager, monkeypatch):
        monkeypatch.setattr(sm_mod, "PermissionResultAllow", _FakeAllow)
        monkeypatch.setattr(sm_mod, "PermissionResultDeny", _FakeDeny)

        allow_cb = await session_manager._build_can_use_tool_callback("unknown-session")
        # Non-AskUserQuestion tools should be denied (whitelist fallback)
        result = await allow_cb("Read", {"x": 1}, None)
        assert isinstance(result, _FakeDeny)
        assert "未授权" in result.message
        # AskUserQuestion still handled
        result2 = await allow_cb("AskUserQuestion", {"questions": []}, None)
        assert result2.updated_input == {"questions": []}

        managed = ManagedSession(session_id="s1", client=FakeSDKClient(), status="running")
        session_manager.sessions["s1"] = managed
        ask_cb = await session_manager._build_can_use_tool_callback("s1")

        task = asyncio.create_task(ask_cb("AskUserQuestion", {"questions": [{"question": "Q"}]}, None))
        await asyncio.sleep(0)
        assert managed.pending_questions
        managed.cancel_pending_questions("user interrupted")
        deny = await task
        assert deny.interrupt is True
        assert "user interrupted" in deny.message

    def test_misc_helpers_and_serialization(self, session_manager):
        assert sm_mod.SessionManager._extract_plain_user_content({"type": "user", "content": " hi "}) == "hi"
        assert (
            sm_mod.SessionManager._extract_plain_user_content(
                {"type": "user", "content": [{"type": "text", "text": " hello "}]}
            )
            == "hello"
        )
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

        meta = await meta_store.create("demo", "sdk-buffer-snap")
        client = _InterruptibleClient(disconnect_raises=False)
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
    async def test_file_access_hook_allows_read_within_project_root(self, tmp_path):
        """Hook allows Read for any path within project_root (e.g. other projects, docs)."""
        own_project = tmp_path / "projects" / "alpha"
        own_project.mkdir(parents=True)
        other_project = tmp_path / "projects" / "beta"
        other_project.mkdir(parents=True)
        docs_dir = tmp_path / "docs"
        docs_dir.mkdir(parents=True)

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        meta_store = SessionMetaStore(session_factory=factory)

        mgr = sm_mod.SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=meta_store,
        )

        hook = mgr._build_file_access_hook(own_project)

        # Read own project file — allowed (within project_cwd)
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(own_project / "script.json")}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Read other project file — allowed (within project_root)
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(other_project / "script.json")}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Read docs dir — allowed (within project_root)
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(docs_dir / "guide.md")}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Read outside project_root — denied
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_file_access_hook_blocks_write_to_readonly_dir(self, tmp_path):
        """Hook denies Write to lib/, allows own project."""
        own_project = tmp_path / "projects" / "alpha"
        own_project.mkdir(parents=True)
        lib_dir = tmp_path / "lib"
        lib_dir.mkdir(parents=True)

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        meta_store = SessionMetaStore(session_factory=factory)

        mgr = sm_mod.SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=meta_store,
        )

        hook = mgr._build_file_access_hook(own_project)

        # Write own project file — allowed
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "output.txt")}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Write to lib/ (readonly) — denied
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(lib_dir / "hack.py")}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_file_access_hook_allows_bash_without_path_check(self, tmp_path):
        """Hook skips Bash (not in _PATH_TOOLS)."""
        own_project = tmp_path / "projects" / "alpha"
        own_project.mkdir(parents=True)

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        meta_store = SessionMetaStore(session_factory=factory)

        mgr = sm_mod.SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=meta_store,
        )

        hook = mgr._build_file_access_hook(own_project)

        # Bash — not a path tool, hook continues
        result = await hook(
            {"tool_name": "Bash", "tool_input": {"command": "ls /etc"}},
            None,
            None,
        )
        assert result.get("continue_") is True

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_file_access_hook_blocks_write_non_whitelisted_ext(self, tmp_path):
        """Hook denies Write/Edit for non-whitelisted file extensions in project dir."""
        own_project = tmp_path / "projects" / "alpha"
        own_project.mkdir(parents=True)

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        meta_store = SessionMetaStore(session_factory=factory)

        mgr = sm_mod.SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=meta_store,
        )

        hook = mgr._build_file_access_hook(own_project)

        # Write .py in project dir — denied
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "helper.py")}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert ".json" in result["hookSpecificOutput"]["permissionDecisionReason"]

        # Edit .sh in project dir — denied
        result = await hook(
            {"tool_name": "Edit", "tool_input": {"file_path": str(own_project / "run.sh")}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        # Write .json — allowed
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "project.json")}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Write .md — allowed
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "notes.md")}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Write .txt — allowed
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "episode.txt")}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Read .py — allowed (only write is restricted)
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(own_project / "helper.py")}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Write file without extension (e.g. Makefile) — denied
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "Makefile")}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        # Write .JSON (uppercase) — allowed (case-insensitive check)
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(own_project / "data.JSON")}},
            None,
            None,
        )
        assert result.get("continue_") is True

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_file_access_hook_allows_read_agent_profile(self, tmp_path):
        """Hook allows Read for agent_runtime_profile/ files."""
        own_project = tmp_path / "projects" / "alpha"
        own_project.mkdir(parents=True)
        profile_md = tmp_path / "agent_runtime_profile" / "CLAUDE.md"
        profile_md.parent.mkdir(parents=True)
        profile_md.write_text("# Agent instructions")

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        meta_store = SessionMetaStore(session_factory=factory)

        mgr = sm_mod.SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=meta_store,
        )

        hook = mgr._build_file_access_hook(own_project)

        # Read agent_runtime_profile/CLAUDE.md — allowed (readonly dir)
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(profile_md)}},
            None,
            None,
        )
        assert result.get("continue_") is True

        await engine.dispose()

    async def _make_sdk_hook_env(self, tmp_path, monkeypatch):
        """Create a SessionManager + hook with SDK dir outside project_root."""
        app_root = tmp_path / "app"
        own_project = app_root / "projects" / "alpha"
        own_project.mkdir(parents=True)

        claude_home = tmp_path / "claude_home" / "projects"
        claude_home.mkdir(parents=True)

        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        meta_store = SessionMetaStore(session_factory=factory)

        mgr = sm_mod.SessionManager(
            project_root=app_root,
            data_dir=app_root,
            meta_store=meta_store,
        )
        monkeypatch.setattr(sm_mod.SessionManager, "_CLAUDE_PROJECTS_DIR", claude_home)

        hook = mgr._build_file_access_hook(own_project)
        return hook, own_project, claude_home, engine

    @pytest.mark.asyncio
    async def test_file_access_hook_allows_read_sdk_tool_results(self, tmp_path, monkeypatch):
        """Hook allows Read for SDK tool-results of the CURRENT project only."""
        hook, own_project, claude_home, engine = await self._make_sdk_hook_env(
            tmp_path,
            monkeypatch,
        )

        encoded = sm_mod.SessionManager._encode_sdk_project_path(own_project)
        tool_results_dir = claude_home / encoded / "abc-session" / "tool-results"
        tool_results_dir.mkdir(parents=True)
        result_file = tool_results_dir / "toolu_01Abc.txt"
        result_file.write_text("full bash output here")

        # Read own project's SDK tool-results — allowed
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(result_file)}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Read own project's SDK session transcript (NOT tool-results) — denied
        transcript = claude_home / encoded / "abc-session" / "transcript.jsonl"
        transcript.write_text("{}")
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(transcript)}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        # Write to SDK tool-results — still denied (write tools only allow project_cwd)
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": str(result_file)}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_file_access_hook_denies_read_other_project_sdk_data(self, tmp_path, monkeypatch):
        """Hook denies Read for ANOTHER project's SDK session data."""
        hook, _, claude_home, engine = await self._make_sdk_hook_env(
            tmp_path,
            monkeypatch,
        )

        other_project = tmp_path / "app" / "projects" / "beta"
        other_project.mkdir(parents=True)
        other_encoded = sm_mod.SessionManager._encode_sdk_project_path(other_project)
        other_tool_results = claude_home / other_encoded / "xyz-session" / "tool-results"
        other_tool_results.mkdir(parents=True)
        other_file = other_tool_results / "toolu_other.txt"
        other_file.write_text("other project output")

        # Read OTHER project's SDK data — denied (cross-project isolation)
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": str(other_file)}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_file_access_hook_denies_read_outside_all_allowed_paths(self, tmp_path, monkeypatch):
        """Hook denies Read for paths outside project_root AND SDK directory."""
        hook, _, _, engine = await self._make_sdk_hook_env(tmp_path, monkeypatch)

        # Path completely outside all allowed zones
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": "/etc/passwd"}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_file_access_hook_allows_read_sdk_task_output(self, tmp_path, monkeypatch):
        """Hook allows Read for SDK task output files under /tmp/claude-*."""
        hook, _, _, engine = await self._make_sdk_hook_env(tmp_path, monkeypatch)

        # SDK task output path pattern: /tmp/claude-{N}/{encoded}/tasks/{id}.output
        task_output = "/tmp/claude-0/-app-projects-alpha-abc123/tasks/bdgaof0ba.output"
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": task_output}},
            None,
            None,
        )
        assert result.get("continue_") is True

        # Write to task output — denied (write tools only allow project_cwd)
        result = await hook(
            {"tool_name": "Write", "tool_input": {"file_path": task_output}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        # /tmp/claude-* path WITHOUT tasks/ segment — denied
        non_task_path = "/tmp/claude-0/-app-projects-alpha/sessions/abc.jsonl"
        result = await hook(
            {"tool_name": "Read", "tool_input": {"file_path": non_task_path}},
            None,
            None,
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

        await engine.dispose()


class TestJsonValidationHook:
    """Tests for the PreToolUse JSON validation hook."""

    def _make_manager(self, tmp_path):
        """Build a SessionManager with minimal fakes (SDK not required)."""
        from server.agent_runtime.session_manager import SessionManager
        from server.agent_runtime.session_store import SessionMetaStore

        return SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path / "data",
            meta_store=SessionMetaStore(),
        )

    async def _call_hook(
        self,
        manager,
        tool_input: dict,
        tool_name: str = "Edit",
        project_cwd=None,
    ):
        """Helper: invoke the JSON validation hook callback directly."""
        from pathlib import Path

        hook_fn = manager._build_json_validation_hook(
            Path(project_cwd) if project_cwd else Path("/tmp"),
        )
        input_data = {
            "hook_event_name": "PreToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        return await hook_fn(input_data, None, None)

    # --- Edit: valid replacement keeps JSON valid → allow ---

    async def test_edit_valid_replacement_returns_empty(self, tmp_path):
        """Edit that keeps JSON valid is allowed."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"title": "old"}')
        manager = self._make_manager(tmp_path)

        result = await self._call_hook(
            manager,
            {
                "file_path": str(json_file),
                "old_string": '"old"',
                "new_string": '"new"',
            },
        )
        assert result == {}

    # --- Edit: replacement breaks JSON → deny ---

    async def test_edit_breaking_replacement_denies(self, tmp_path):
        """Edit that would produce invalid JSON is denied."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"title": "old value"}')
        manager = self._make_manager(tmp_path)

        result = await self._call_hook(
            manager,
            {
                "file_path": str(json_file),
                "old_string": '"old value"',
                "new_string": '"has "quotes" inside"',  # unescaped quotes
            },
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert (
            "无效 JSON" in result["hookSpecificOutput"]["permissionDecisionReason"]
            or "JSON" in result["hookSpecificOutput"]["permissionDecisionReason"]
        )

    # --- Edit: replace_all ---

    async def test_edit_replace_all_breaking_denies(self, tmp_path):
        """Edit with replace_all that breaks JSON is denied."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"a": "x", "b": "x"}')
        manager = self._make_manager(tmp_path)

        result = await self._call_hook(
            manager,
            {
                "file_path": str(json_file),
                "old_string": '"x"',
                "new_string": '"y",',  # trailing comma on last occurrence
                "replace_all": True,
            },
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    # --- Write: valid content → allow ---

    async def test_write_valid_json_returns_empty(self, tmp_path):
        """Write with valid JSON content is allowed."""
        manager = self._make_manager(tmp_path)
        result = await self._call_hook(
            manager,
            {
                "file_path": str(tmp_path / "new.json"),
                "content": '{"segments": []}',
            },
            tool_name="Write",
        )
        assert result == {}

    # --- Write: invalid content → deny ---

    async def test_write_invalid_json_denies(self, tmp_path):
        """Write with invalid JSON content is denied."""
        manager = self._make_manager(tmp_path)
        result = await self._call_hook(
            manager,
            {
                "file_path": str(tmp_path / "bad.json"),
                "content": '{"a": 1,,}',
            },
            tool_name="Write",
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"

    # --- Non-.json file → skip ---

    async def test_non_json_file_returns_empty(self, tmp_path):
        """Hook ignores non-.json files."""
        manager = self._make_manager(tmp_path)
        result = await self._call_hook(
            manager,
            {
                "file_path": str(tmp_path / "notes.md"),
                "content": "not json {{{{",
            },
            tool_name="Write",
        )
        assert result == {}

    # --- Edit: file not found → skip (let Edit handle the error) ---

    async def test_edit_missing_file_returns_empty(self, tmp_path):
        """Hook skips if the target file doesn't exist yet."""
        manager = self._make_manager(tmp_path)
        result = await self._call_hook(
            manager,
            {
                "file_path": str(tmp_path / "ghost.json"),
                "old_string": "x",
                "new_string": "y",
            },
        )
        assert result == {}

    # --- Non-Write/Edit tool → skip ---

    async def test_non_write_edit_tool_returns_empty(self, tmp_path):
        """Hook ignores tools other than Write/Edit."""
        manager = self._make_manager(tmp_path)
        result = await self._call_hook(
            manager,
            {
                "file_path": "/some/file.json",
            },
            tool_name="Read",
        )
        assert result == {}

    # --- Edit: old_string not in file → skip (Edit will fail on its own) ---

    async def test_edit_old_string_not_found_returns_empty(self, tmp_path):
        """Hook skips if old_string is not in the file."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"title": "hello"}')
        manager = self._make_manager(tmp_path)

        result = await self._call_hook(
            manager,
            {
                "file_path": str(json_file),
                "old_string": "not found",
                "new_string": "replacement",
            },
        )
        assert result == {}

    # --- Edit: curly/smart quotes in new_string → deny ---

    async def test_edit_curly_quotes_in_new_string_denies(self, tmp_path):
        """Edit whose new_string contains curly quotes is denied even when
        old_string doesn't exactly match the file (Claude Code may normalise
        quotes internally, bypassing the hook's str.replace simulation)."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"segment_break": true, "title": "test"}')
        manager = self._make_manager(tmp_path)

        # old_string uses curly quotes (won't match file via Python str `in`),
        # but new_string also has curly quotes → must be blocked.
        result = await self._call_hook(
            manager,
            {
                "file_path": str(json_file),
                "old_string": "\u201csegment_break\u201d: true",
                "new_string": "\u201csegment_break\u201d: false",
            },
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "弯引号" in result["hookSpecificOutput"]["permissionDecisionReason"]

    async def test_edit_curly_quotes_old_only_is_allowed(self, tmp_path):
        """If only old_string has curly quotes but new_string is clean,
        don't block (edit will likely fail on its own)."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"segment_break": true}')
        manager = self._make_manager(tmp_path)

        result = await self._call_hook(
            manager,
            {
                "file_path": str(json_file),
                "old_string": "\u201csegment_break\u201d: true",
                "new_string": '"segment_break": false',
            },
        )
        # old_string not in file → hook skips → allowed
        assert result == {}

    async def test_edit_curly_quotes_in_new_string_straight_old_denies(self, tmp_path):
        """Edit with straight-quote old_string that matches file but
        curly-quote new_string is denied via the early curly-quote check."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"segment_break": true, "title": "test"}')
        manager = self._make_manager(tmp_path)

        result = await self._call_hook(
            manager,
            {
                "file_path": str(json_file),
                "old_string": '"segment_break": true',
                "new_string": "\u201csegment_break\u201d: false",
            },
        )
        assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert "弯引号" in result["hookSpecificOutput"]["permissionDecisionReason"]


class TestJsonPostValidationHook:
    """Tests for the PostToolUse JSON validation hook (safety net)."""

    def _make_manager(self, tmp_path):
        from server.agent_runtime.session_manager import SessionManager
        from server.agent_runtime.session_store import SessionMetaStore

        return SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path / "data",
            meta_store=SessionMetaStore(),
        )

    async def _call_post_hook(
        self,
        manager,
        tool_input: dict,
        tool_name: str = "Edit",
        project_cwd=None,
        json_backups=None,
    ):
        from pathlib import Path

        hook_fn = manager._build_json_post_validation_hook(
            Path(project_cwd) if project_cwd else Path("/tmp"),
            json_backups if json_backups is not None else {},
        )
        input_data = {
            "hook_event_name": "PostToolUse",
            "tool_name": tool_name,
            "tool_input": tool_input,
        }
        return await hook_fn(input_data, "test-tool-use-id", None)

    # --- Valid JSON after edit → pass ---

    async def test_valid_json_after_edit_returns_empty(self, tmp_path):
        """PostToolUse returns empty when file is valid JSON after edit."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"title": "new"}')
        manager = self._make_manager(tmp_path)

        result = await self._call_post_hook(
            manager,
            {
                "file_path": str(json_file),
            },
            project_cwd=str(tmp_path),
        )
        assert result == {}

    # --- Invalid JSON after edit with backup → restore + additionalContext ---

    async def test_invalid_json_restores_backup(self, tmp_path):
        """PostToolUse restores backup when file is invalid JSON after edit."""
        json_file = tmp_path / "ep.json"
        original_content = '{"title": "original"}'

        # Simulate: file was corrupted by edit
        json_file.write_text('{"title": "broken",,}')
        manager = self._make_manager(tmp_path)

        # Provide backup
        backups: dict = {
            "test-tool-use-id": (json_file, original_content),
        }

        result = await self._call_post_hook(
            manager,
            {"file_path": str(json_file)},
            project_cwd=str(tmp_path),
            json_backups=backups,
        )

        # Should report the issue via additionalContext
        assert "additionalContext" in result.get("hookSpecificOutput", {})
        assert "回滚" in result["hookSpecificOutput"]["additionalContext"]

        # File should be restored
        assert json_file.read_text() == original_content

        # Backup should be consumed (popped)
        assert "test-tool-use-id" not in backups

    # --- Invalid JSON after edit without backup → report only ---

    async def test_invalid_json_no_backup_reports_error(self, tmp_path):
        """PostToolUse reports error when file is corrupt and no backup exists."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"broken":,}')
        manager = self._make_manager(tmp_path)

        result = await self._call_post_hook(
            manager,
            {"file_path": str(json_file)},
            project_cwd=str(tmp_path),
            json_backups={},
        )

        ctx = result.get("hookSpecificOutput", {}).get("additionalContext", "")
        assert "无法恢复" in ctx
        assert "回滚" not in ctx

    # --- Non-.json file → skip ---

    async def test_non_json_file_returns_empty(self, tmp_path):
        """PostToolUse ignores non-.json files."""
        manager = self._make_manager(tmp_path)
        result = await self._call_post_hook(
            manager,
            {
                "file_path": str(tmp_path / "notes.md"),
            },
            project_cwd=str(tmp_path),
        )
        assert result == {}

    # --- Backup is consumed even on success ---

    async def test_backup_consumed_on_success(self, tmp_path):
        """PostToolUse pops backup even when validation succeeds."""
        json_file = tmp_path / "ep.json"
        json_file.write_text('{"title": "ok"}')
        manager = self._make_manager(tmp_path)

        backups: dict = {
            "test-tool-use-id": (json_file, '{"title": "old"}'),
        }

        result = await self._call_post_hook(
            manager,
            {"file_path": str(json_file)},
            project_cwd=str(tmp_path),
            json_backups=backups,
        )

        assert result == {}
        # Backup should be consumed to prevent memory leaks
        assert "test-tool-use-id" not in backups


# --- ManagedSession 状态机（Session Actor 重构）-----------------------------


def _make_managed_for_state_test():
    """构造一个 ManagedSession 用于状态机测试，actor 字段用 None 占位。"""
    from server.agent_runtime.session_manager import ManagedSession

    return ManagedSession(
        session_id="test",
        actor=None,  # 状态机测试不触及 actor
        status="running",
        project_name="demo",
    )


def test_on_actor_message_result_success_sets_idle():
    managed = _make_managed_for_state_test()
    managed._on_actor_message({"type": "result", "subtype": "success"})
    assert managed.status == "idle"


def test_on_actor_message_result_error_during_execution_sets_interrupted():
    managed = _make_managed_for_state_test()
    managed._on_actor_message({"type": "result", "subtype": "error_during_execution"})
    assert managed.status == "interrupted"


def test_on_actor_message_result_other_error_sets_error():
    managed = _make_managed_for_state_test()
    managed._on_actor_message({"type": "result", "subtype": "error_max_turns"})
    assert managed.status == "error"


def test_on_actor_message_non_result_message_preserves_status():
    managed = _make_managed_for_state_test()
    managed.status = "running"
    managed._on_actor_message({"type": "assistant", "content": "hi"})
    assert managed.status == "running"


def test_on_actor_message_appends_to_buffer():
    managed = _make_managed_for_state_test()
    managed._on_actor_message({"type": "assistant", "content": "hi"})
    # add_message 负责 buffer + broadcast；这里只验 buffer
    buffered = list(managed.message_buffer)
    assert len(buffered) == 1
    assert buffered[0]["type"] == "assistant"


# --- ManagedSession 对 actor 的代理 -----------------------------------------


@pytest.mark.asyncio
async def test_send_query_sets_running_and_awaits_done():
    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession
    from tests.fakes import FakeSDKClient

    client = FakeSDKClient(messages=[{"type": "result", "subtype": "success"}])
    managed_ref: list = []

    def on_message(msg):
        managed_ref[0]._on_actor_message(msg)

    actor = SessionActor(client_factory=lambda: client, on_message=on_message)
    managed = ManagedSession(session_id="t", actor=actor, status="idle", project_name="p")
    managed_ref.append(managed)

    await actor.start()
    await managed.send_query("hi")
    assert client.sent_queries == ["hi"]
    assert managed.status == "idle"  # result=success → idle

    # 收尾
    await managed.send_disconnect()


@pytest.mark.asyncio
async def test_send_query_raises_on_cmd_error():
    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession

    class _Explode:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *e):
            return False

        async def query(self, *a, **k):
            raise RuntimeError("boom")

        async def interrupt(self):
            pass

        async def receive_response(self):
            if False:
                yield {}

    actor = SessionActor(client_factory=lambda: _Explode(), on_message=lambda m: None)
    managed = ManagedSession(session_id="t", actor=actor, status="idle", project_name="p")
    await actor.start()
    with pytest.raises(RuntimeError, match="boom"):
        await managed.send_query("hi")
    assert managed.status == "error"


@pytest.mark.asyncio
async def test_send_interrupt_is_idempotent_via_flag():
    """interrupt_requested 标志防止重入。"""
    from server.agent_runtime.session_actor import SessionActor, SessionCommand
    from server.agent_runtime.session_manager import ManagedSession
    from tests.fakes import FakeSDKClient

    client = FakeSDKClient(
        block_forever=True,
        interrupt_message={"type": "result", "subtype": "error_during_execution"},
    )
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    managed = ManagedSession(session_id="t", actor=actor, status="running", project_name="p")
    await actor.start()

    # 发一个 query 让 receive_response 开始
    q = SessionCommand(type="query", prompt="x")
    await actor.enqueue(q)
    await asyncio.sleep(0.05)

    # 并发两次 send_interrupt；第二次应走 interrupt_requested fast-return
    await asyncio.gather(managed.send_interrupt(), managed.send_interrupt())
    # client.interrupt 至少被调一次（具体次数视 asyncio 调度，允许 1 或 2）
    assert client.interrupted

    await q.done.wait()
    await managed.send_disconnect()


@pytest.mark.asyncio
async def test_send_disconnect_waits_actor_task_done():
    from server.agent_runtime.session_actor import SessionActor
    from server.agent_runtime.session_manager import ManagedSession
    from tests.fakes import FakeSDKClient

    client = FakeSDKClient()
    actor = SessionActor(client_factory=lambda: client, on_message=lambda m: None)
    managed = ManagedSession(session_id="t", actor=actor, status="idle", project_name="p")
    await actor.start()
    await managed.send_disconnect()
    assert managed.status == "closed"
    assert actor._task is not None and actor._task.done()
    assert client.disconnected
