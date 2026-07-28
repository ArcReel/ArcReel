"""Unit tests for SessionManager project cwd scoping."""

import json
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

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
    store = SessionMetaStore(session_factory=factory)
    return store, engine


async def _fake_provider_env():
    """Stub for OptionsAssembler 凭证注入 — 跳过 DB 访问。"""
    return {}


class TestSessionManagerProjectScope:
    @pytest.mark.asyncio
    async def test_build_options_uses_project_directory_as_cwd(self, tmp_path, monkeypatch):
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            meta_store=store,
        )
        monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", _fake_provider_env)

        with patch("server.agent_runtime.options_assembler.SDK_AVAILABLE", True):
            with patch(
                "server.agent_runtime.options_assembler.ClaudeAgentOptions",
                _FakeOptions,
            ):
                options = await manager._build_options("demo")

        assert options.kwargs["cwd"] == str(project_dir.resolve())
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_options_raises_when_project_missing(self, tmp_path, monkeypatch):
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            meta_store=store,
        )
        monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", _fake_provider_env)

        with patch("server.agent_runtime.options_assembler.SDK_AVAILABLE", True):
            with patch(
                "server.agent_runtime.options_assembler.ClaudeAgentOptions",
                _FakeOptions,
            ):
                with pytest.raises(FileNotFoundError):
                    await manager._build_options("missing-project")

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_options_always_adds_file_access_hook(self, tmp_path, monkeypatch):
        """File access hook is always registered, even without can_use_tool."""
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            meta_store=store,
        )
        monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", _fake_provider_env)

        with patch("server.agent_runtime.options_assembler.SDK_AVAILABLE", True):
            with patch(
                "server.agent_runtime.options_assembler.ClaudeAgentOptions",
                _FakeOptions,
            ):
                with patch(
                    "server.agent_runtime.options_assembler.HookMatcher",
                    _FakeHookMatcher,
                ):
                    options = await manager._build_options("demo")

        hooks = options.kwargs.get("hooks", {})
        assert "PreToolUse" in hooks
        matcher = hooks["PreToolUse"][0]
        assert matcher.matcher is None
        # Without can_use_tool: only file_access_hook
        assert len(matcher.hooks) == 1
        assert matcher.hooks[0] is not manager._options_assembler._keep_stream_open_hook

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_options_with_can_use_tool_adds_keep_alive_hook(self, tmp_path, monkeypatch):
        """With can_use_tool: keep_stream_open + file_access hooks."""
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            meta_store=store,
        )
        monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", _fake_provider_env)

        async def _can_use_tool(_tool_name, _input_data, _context):
            return None

        with patch("server.agent_runtime.options_assembler.SDK_AVAILABLE", True):
            with patch(
                "server.agent_runtime.options_assembler.ClaudeAgentOptions",
                _FakeOptions,
            ):
                with patch(
                    "server.agent_runtime.options_assembler.HookMatcher",
                    _FakeHookMatcher,
                ):
                    options = await manager._build_options(
                        "demo",
                        can_use_tool=_can_use_tool,
                    )

        hooks = options.kwargs.get("hooks", {})
        assert "PreToolUse" in hooks
        matcher = hooks["PreToolUse"][0]
        assert matcher.matcher is None
        assert len(matcher.hooks) == 2
        assert matcher.hooks[0] is manager._options_assembler._keep_stream_open_hook

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_project_context_excludes_mutable_metadata(self, tmp_path):
        """Mutable metadata must NOT leak into the session-fixed system prompt."""
        project_dir = tmp_path / "projects" / "demo"
        project_dir.mkdir(parents=True)
        project_json = project_dir / "project.json"
        project_json.write_text(
            json.dumps(
                {
                    "title": "重生之皇后威武",
                    "content_mode": "narration",
                    "style": "Photographic",
                    "style_description": "Soft diffused lighting, muted earth tones",
                    "overview": {
                        "synopsis": "姜月茴重生后逆袭的故事",
                        "genre": "古装宫斗、重生复仇",
                        "theme": "复仇与救赎",
                        "world_setting": "架空古代皇朝",
                    },
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            meta_store=store,
        )

        prompt = manager._options_assembler._build_project_context("demo")

        # Session-invariant facts are present
        assert "项目标识：demo" in prompt
        assert f"项目目录（即当前工作目录 cwd）：{project_dir.resolve().as_posix()}" in prompt
        assert "项目元数据（标题、风格、概述等）存于 project.json，需要时读取。" in prompt
        assert "Bash 命令必须写在单行" in prompt

        # The cwd line embeds an arbitrary temp path; exclude it so ASCII tokens
        # that may appear in the test's tmp_path can't cause false substring matches.
        body = "\n".join(line for line in prompt.splitlines() if "项目目录" not in line)

        # Mutable metadata (every label and value) must be absent — read on demand instead
        for token in (
            "项目标题",
            "重生之皇后威武",
            "内容模式",
            "narration",
            "视觉风格",
            "Photographic",
            "风格描述",
            "Soft diffused lighting",
            "项目概述",
            "姜月茴重生后逆袭的故事",
            "古装宫斗",
            "复仇与救赎",
            "架空古代皇朝",
        ):
            assert token not in body

        # Redundant path rules now live only in CLAUDE.{mode}.md — guard against re-adding them here
        assert "必须使用绝对路径" not in body
        assert "必须使用相对路径" not in body

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_build_project_context_emits_block_without_project_json(self, tmp_path):
        """Output is independent of project.json: the stable block is emitted even when it is absent."""
        project_dir = tmp_path / "projects" / "empty"
        project_dir.mkdir(parents=True)
        # No project.json created

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            meta_store=store,
        )

        prompt = manager._options_assembler._build_project_context("empty")

        assert "项目标识：empty" in prompt
        assert f"项目目录（即当前工作目录 cwd）：{project_dir.resolve().as_posix()}" in prompt
        assert "项目元数据（标题、风格、概述等）存于 project.json，需要时读取。" in prompt

        await engine.dispose()


class TestAllowedToolsAndConstants:
    @pytest.mark.asyncio
    async def test_default_allowed_tools_matches_sdk(self, tmp_path):
        """Verify allowed tools align with SDK documentation."""
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            meta_store=store,
        )
        tools = manager.DEFAULT_ALLOWED_TOOLS
        assert "Task" in tools
        assert "Skill" in tools
        assert "Read" in tools
        assert "AskUserQuestion" in tools
        assert "MultiEdit" not in tools
        assert "LS" not in tools
        # Task 4.2: Bash 现在在 allowed_tools，由 SDK Sandbox autoAllowBashIfSandboxed
        # 配合 SandboxSettings.enabled=True 自动放行命令。
        assert "Bash" in tools
        assert "BashOutput" in tools
        assert "KillBash" in tools
        await engine.dispose()

    @pytest.mark.asyncio
    async def test_path_tools_no_ls(self, tmp_path):
        """LS should not be in PATH_TOOLS."""
        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            meta_store=store,
        )
        assert "LS" not in manager.access_policy.PATH_TOOLS
        assert "MultiEdit" not in manager.access_policy.PATH_TOOLS
        await engine.dispose()


class TestSystemPromptProjectContext:
    @pytest.mark.asyncio
    async def test_build_project_context_returns_empty_when_project_dir_missing(self, tmp_path):
        """The only empty case left: no project directory at all (cwd cannot resolve)."""
        (tmp_path / "projects").mkdir(parents=True, exist_ok=True)

        store, engine = await _make_store()
        manager = SessionManager(
            project_root=tmp_path,
            meta_store=store,
        )

        prompt = manager._options_assembler._build_project_context("does-not-exist")
        assert prompt == ""
        await engine.dispose()
