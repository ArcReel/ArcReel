"""Unit tests for SessionManager project cwd scoping."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from lib.db.base import Base
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


class _FakeOptions:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeHookMatcher:
    def __init__(self, matcher=None, hooks=None):
        self.matcher = matcher
        self.hooks = hooks or []


async def _make_store():
    """Create an async SessionMetaStore backed by in-memory SQLite."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = SessionMetaStore(session_factory=factory, _skip_init_db=True)
    return store, engine


class TestSessionManagerProjectScope:
    @pytest.mark.asyncio
    async def test_build_options_uses_project_directory_as_cwd(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        store, engine = await _make_store()
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
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_options_raises_when_project_missing(self, tmp_path):
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
        store, engine = await _make_store()
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

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_options_with_can_use_tool_adds_keep_alive_hook(self, tmp_path):
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        store, engine = await _make_store()
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

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_system_prompt_injects_project_context(self, tmp_path):
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

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=store,
        )

        prompt = manager._build_system_prompt("demo")

        # Project metadata fields
        assert "项目标识：demo" in prompt
        assert "项目标题：重生之皇后威武" in prompt
        assert "重生之皇后威武" in prompt
        assert "narration" in prompt
        assert "Photographic" in prompt
        assert "Soft diffused lighting" in prompt
        assert f"项目根目录绝对路径：{project_dir.resolve()}" in prompt
        assert "必须使用绝对路径" in prompt
        assert "不要把项目标题当成目录名" in prompt

        # Overview fields
        assert "姜月茴重生后逆袭的故事" in prompt
        assert "古装宫斗" in prompt
        assert "复仇与救赎" in prompt
        assert "架空古代皇朝" in prompt

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_system_prompt_graceful_fallback_no_project_json(self, tmp_path):
        """Verify graceful degradation when project.json does not exist."""
        project_dir = tmp_path / "projects" / "empty"
        project_dir.mkdir(parents=True)
        # No project.json created

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=store,
        )

        prompt = manager._build_system_prompt("empty")

        # Should return empty string — base prompt is auto-loaded by SDK
        assert prompt == ""

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_system_prompt_partial_fields(self, tmp_path):
        """Verify partial project.json (some fields missing) works correctly."""
        project_dir = tmp_path / "projects" / "partial"
        project_dir.mkdir(parents=True)
        project_json = project_dir / "project.json"
        project_json.write_text(json.dumps({
            "title": "测试项目",
            "content_mode": "drama",
            # No style, style_description, or overview
        }, ensure_ascii=False), encoding="utf-8")

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            data_dir=tmp_path,
            meta_store=store,
        )

        prompt = manager._build_system_prompt("partial")

        # Present fields should be injected
        assert "项目标识：partial" in prompt
        assert "项目标题：测试项目" in prompt
        assert f"项目根目录绝对路径：{project_dir.resolve()}" in prompt
        assert "测试项目" in prompt
        assert "drama" in prompt

        # Missing fields should NOT cause errors or appear
        assert "Photographic" not in prompt
        assert "项目概述" not in prompt  # No overview section header

        await engine.dispose()


class TestAllowedToolsAndConstants:
    @pytest.mark.asyncio
    async def test_default_allowed_tools_matches_sdk(self, tmp_path):
        """Verify allowed tools align with SDK documentation."""
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )
        tools = manager.DEFAULT_ALLOWED_TOOLS
        assert "Task" in tools
        assert "Skill" in tools
        assert "Read" in tools
        assert "AskUserQuestion" in tools
        assert "MultiEdit" not in tools
        assert "LS" not in tools
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_path_tools_no_ls(self, tmp_path):
        """LS should not be in _PATH_TOOLS."""
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )
        assert "LS" not in manager._PATH_TOOLS
        assert "MultiEdit" not in manager._PATH_TOOLS
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_readonly_dirs_includes_agent_profile(self, tmp_path):
        """agent_runtime_profile should be in readonly dirs."""
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )
        assert "agent_runtime_profile" in manager._READONLY_DIRS
        assert ".claude/skills" not in manager._READONLY_DIRS
        await engine.dispose()


class TestSystemPromptProjectContext:
    @pytest.mark.asyncio
    async def test_build_system_prompt_returns_empty_without_project_json(self, tmp_path):
        """Without project.json, system_prompt should be empty (SDK auto-loads CLAUDE.md)."""
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )

        prompt = manager._build_system_prompt("demo")
        assert prompt == ""
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_system_prompt_returns_project_context_only(self, tmp_path):
        """system_prompt should only contain project.json context, not base prompt."""
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        (project_dir / "project.json").write_text(
            json.dumps({"title": "测试项目"}, ensure_ascii=False),
            encoding="utf-8",
        )

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path, data_dir=tmp_path, meta_store=store,
        )

        prompt = manager._build_system_prompt("demo")
        assert "项目标题：测试项目" in prompt
        assert "当前项目上下文" in prompt
        await engine.dispose()


