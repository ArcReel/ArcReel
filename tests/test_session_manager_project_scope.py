"""Unit tests for SessionManager project cwd scoping."""

import json
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

    def test_build_system_prompt_injects_project_context(self, tmp_path):
        """Verify full project.json fields are injected into the system prompt."""
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        project_json = project_dir / "project.json"
        project_json.write_text(json.dumps({
            "title": "重生之皇后威武",
            "content_mode": "narration",
            "style": "Photographic",
            "style_description": "Soft diffused lighting, muted earth tones",
            "overview": {
                "synopsis": "姜月茴重生后逆袭的故事",
                "genre": "古装宫斗、重生复仇",
                "theme": "复仇与救赎",
                "world_setting": "架空古代皇朝"
            }
        }, ensure_ascii=False), encoding="utf-8")

        store = SessionMetaStore(tmp_path / "sessions.db")
        manager = SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=store,
        )

        prompt = manager._build_system_prompt("demo")

        # Base prompt must always be present
        assert manager.system_prompt in prompt

        # Project metadata fields
        assert "重生之皇后威武" in prompt
        assert "narration" in prompt
        assert "Photographic" in prompt
        assert "Soft diffused lighting" in prompt

        # Overview fields
        assert "姜月茴重生后逆袭的故事" in prompt
        assert "古装宫斗" in prompt
        assert "复仇与救赎" in prompt
        assert "架空古代皇朝" in prompt

    def test_build_system_prompt_graceful_fallback_no_project_json(self, tmp_path):
        """Verify graceful degradation when project.json does not exist."""
        project_dir = tmp_path / "projects" / "empty"
        project_dir.mkdir(parents=True)
        # No project.json created

        store = SessionMetaStore(tmp_path / "sessions.db")
        manager = SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=store,
        )

        prompt = manager._build_system_prompt("empty")

        # Should return exactly the base prompt without project context
        assert prompt == manager.system_prompt

    def test_build_system_prompt_partial_fields(self, tmp_path):
        """Verify partial project.json (some fields missing) works correctly."""
        project_dir = tmp_path / "projects" / "partial"
        project_dir.mkdir(parents=True)
        project_json = project_dir / "project.json"
        project_json.write_text(json.dumps({
            "title": "测试项目",
            "content_mode": "drama",
            # No style, style_description, or overview
        }, ensure_ascii=False), encoding="utf-8")

        store = SessionMetaStore(tmp_path / "sessions.db")
        manager = SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=store,
        )

        prompt = manager._build_system_prompt("partial")

        # Base prompt must always be present
        assert manager.system_prompt in prompt

        # Present fields should be injected
        assert "测试项目" in prompt
        assert "drama" in prompt

        # Missing fields should NOT cause errors or appear
        assert "Photographic" not in prompt
        assert "项目概述" not in prompt  # No overview section header
