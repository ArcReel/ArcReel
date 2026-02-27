"""Unit tests for SessionManager project cwd scoping."""

from pathlib import Path
from unittest.mock import patch

import pytest

from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


class _FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeHookMatcher:
    def __init__(self, matcher=None, hooks=None):
        self.matcher = matcher
        self.hooks = hooks or []


class TestSessionManagerProjectScope:
    def test_build_options_uses_project_directory_as_cwd(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        store = SessionMetaStore(tmp_path / "sessions.db")
        manager = SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=store,
        )

        with patch("server.agent_runtime.session_manager.SDK_AVAILABLE", True):
            with patch(
                "server.agent_runtime.session_manager.ClaudeAgentOptions",
                _FakeOptions,
            ):
                options = manager._build_options("demo")

        assert options.kwargs["cwd"] == str(project_dir.resolve())

    def test_build_options_raises_when_project_missing(self, tmp_path):
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
        store = SessionMetaStore(tmp_path / "sessions.db")
        manager = SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=store,
        )

        with patch("server.agent_runtime.session_manager.SDK_AVAILABLE", True):
            with patch(
                "server.agent_runtime.session_manager.ClaudeAgentOptions",
                _FakeOptions,
            ):
                with pytest.raises(FileNotFoundError):
                    manager._build_options("missing-project")

    def test_build_options_with_can_use_tool_adds_keep_alive_hook(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        store = SessionMetaStore(tmp_path / "sessions.db")
        manager = SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=store,
        )

        async def _can_use_tool(_tool_name, _input_data, _context):
            return None

        with patch("server.agent_runtime.session_manager.SDK_AVAILABLE", True):
            with patch(
                "server.agent_runtime.session_manager.ClaudeAgentOptions",
                _FakeOptions,
            ):
                with patch(
                    "server.agent_runtime.session_manager.HookMatcher",
                    _FakeHookMatcher,
                ):
                    options = manager._build_options(
                        "demo",
                        can_use_tool=_can_use_tool,
                    )

        assert "AskUserQuestion" in options.kwargs["allowed_tools"]
        hooks = options.kwargs.get("hooks", {})
        assert "PreToolUse" in hooks
        matcher = hooks["PreToolUse"][0]
        assert matcher.matcher is None
        assert len(matcher.hooks) == 1
        assert matcher.hooks[0] is manager._keep_stream_open_hook
