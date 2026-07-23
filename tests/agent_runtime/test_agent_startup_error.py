"""AgentStartupError 透传 SDK stderr 的回归覆盖。

修这条路径的动机：SDK 子进程退出非 0 时 ProcessError.stderr 写死为占位符，
之前 ArcReel 没传 stderr 回调，前端只看到 "Check stderr output for details"。
此处覆盖：
  1. AgentStartupError __str__ 自动拼 message + stderr，所有 except Exception:
     str(exc) 路径都能透传；
  2. _build_options 把 stderr 回调透传到 ClaudeAgentOptions；
  3. send_new_session 在 actor.start 失败时收集 stderr 并包装为
     AgentStartupError；
  4. router 把 AgentStartupError 翻译成 502 + 结构化故障观测。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from server.agent_runtime.session_manager import (
    AgentStartupError,
    SessionManager,
)
from server.agent_runtime.session_store import SessionMetaStore


def test_agent_startup_error_str_includes_stderr() -> None:
    exc = AgentStartupError(
        "Command failed with exit code 1",
        sdk_stderr="Claude Code on Windows requires either Git for Windows (for bash) or PowerShell.",
    )
    rendered = str(exc)
    assert "Command failed with exit code 1" in rendered
    assert "Git for Windows" in rendered
    # message 与 stderr 之间应留空行，方便前端展示
    assert "\n\n" in rendered


def test_agent_startup_error_without_stderr_keeps_message() -> None:
    exc = AgentStartupError("启动失败")
    assert str(exc) == "启动失败"
    assert exc.sdk_stderr == ""


@pytest.fixture
def session_manager(tmp_path: Path) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "projects").mkdir()
    proj_dir = project_root / "projects" / "demo"
    proj_dir.mkdir()
    (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")

    meta_store = SessionMetaStore()
    return SessionManager(project_root, meta_store)


@pytest.mark.asyncio
async def test_build_options_forwards_stderr_callback(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_build_options 必须把 stderr 回调透传给 ClaudeAgentOptions。"""

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)

    captured: list[str] = []

    def collector(line: str) -> None:
        captured.append(line)

    opts = await session_manager._build_options("demo", stderr=collector)
    assert opts.stderr is collector

    # 模拟 SDK 透传一行 stderr
    opts.stderr("hello from claude.exe")
    assert captured == ["hello from claude.exe"]


@pytest.mark.asyncio
async def test_send_new_session_wraps_actor_failure_with_stderr(
    session_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """actor.start() 抛错时应收集 stderr 并包装为 AgentStartupError 抛出。"""

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)

    captured_stderr_cb: list = []
    secret = "startup-secret-must-not-leak"

    class _FakeActor:
        def __init__(self, *_, on_message=None, client_factory=None):
            self.task = None
            self._on_message = on_message
            # client_factory 是个 lambda，里面闭包了 options；从 client_factory
            # 反推 options 比较麻烦，改由 monkeypatch 直接捕获 _build_options。

        async def start(self):
            # 模拟 SDK 在 connect 阶段先喷 stderr 再退出
            cb = captured_stderr_cb[0]
            cb("Claude Code on Windows requires either Git for Windows (for bash) or PowerShell.")
            cb("Or set CLAUDE_CODE_GIT_BASH_PATH to your bash.exe location.")
            cb(f"Authorization: Bearer {secret}")
            raise RuntimeError(f"Command failed with exit code 1; api_key={secret}")

        def add_done_callback(self, _cb):
            pass

    # 包装 _build_options，把 stderr 回调暴露给假 actor
    real_build_options = SessionManager._build_options

    async def wrapped_build_options(self, *args, **kwargs):
        opts = await real_build_options(self, *args, **kwargs)
        captured_stderr_cb.append(opts.stderr)
        return opts

    monkeypatch.setattr(SessionManager, "_build_options", wrapped_build_options)
    monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)

    # _ensure_capacity 内部访问 DB，跳过
    monkeypatch.setattr(SessionManager, "_ensure_capacity", AsyncMock(return_value=None))
    caplog.set_level("ERROR", logger="server.agent_runtime.session_manager")

    with pytest.raises(AgentStartupError) as exc_info:
        await session_manager.send_new_session("demo", "你好")

    err = exc_info.value
    assert "Git for Windows" in err.sdk_stderr
    assert "CLAUDE_CODE_GIT_BASH_PATH" in err.sdk_stderr
    # __str__ 必须把两段拼出来，给 router/前端直接看
    text = str(err)
    assert "Command failed with exit code 1" in text
    assert "Git for Windows" in text
    assert err.failure_observation is not None
    assert err.failure_observation["phase"] == "startup"
    assert err.failure_observation["project_name"] == "demo"
    assert err.failure_observation["raw"]["sdk_stderr"].startswith("Claude Code on Windows")
    assert secret not in str(err)

    rendered_logs = "\n".join(record.getMessage() for record in caplog.records)
    assert "failure_observation=" in rendered_logs
    assert '"type": "RuntimeError"' in rendered_logs
    assert "CLAUDE_CODE_GIT_BASH_PATH" in rendered_logs
    assert secret not in rendered_logs


@pytest.mark.asyncio
async def test_send_new_session_no_stderr_still_wraps(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """即使 SDK 没产生 stderr，actor.start 失败也应包装为 AgentStartupError（保留原因链）。"""

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)

    class _FakeActor:
        def __init__(self, *_, on_message=None, client_factory=None):
            self.task = None

        async def start(self):
            raise OSError("ENOENT")

        def add_done_callback(self, _cb):
            pass

    monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)
    monkeypatch.setattr(SessionManager, "_ensure_capacity", AsyncMock(return_value=None))

    with pytest.raises(AgentStartupError) as exc_info:
        await session_manager.send_new_session("demo", "你好")

    assert exc_info.value.sdk_stderr == ""
    assert "ENOENT" in str(exc_info.value)
    # 原因链保留，便于 logger.exception 看到底层异常
    assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.asyncio
async def test_send_new_session_wraps_option_assembly_failure(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(SessionManager, "_ensure_capacity", AsyncMock(return_value=None))
    monkeypatch.setattr(SessionManager, "_build_options", AsyncMock(side_effect=LookupError("credential missing")))

    with pytest.raises(AgentStartupError) as exc_info:
        await session_manager.send_new_session("demo", "你好")

    failure = exc_info.value.failure_observation
    assert failure is not None
    assert failure["phase"] == "startup"
    assert failure["summary"]["type"] == "LookupError"
    assert failure["summary"]["message"] == "credential missing"
    assert session_manager.sessions == {}


@pytest.mark.asyncio
async def test_get_or_connect_wraps_actor_failure_with_stderr(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """恢复历史会话路径同样要把 actor.start 失败包装为 AgentStartupError。

    ``get_or_connect`` 与 ``send_new_session`` 是两条独立路径，单独覆盖避免
    其中一条回归没人发现。
    """
    from tests.factories import make_session_meta

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)

    captured_stderr_cb: list = []

    class _FakeActor:
        def __init__(self, *_, on_message=None, client_factory=None):
            self.task = None

        async def start(self):
            cb = captured_stderr_cb[0]
            cb("resume-failed: cannot rehydrate transcript")
            raise RuntimeError("Command failed with exit code 1")

        def add_done_callback(self, _cb):
            pass

    real_build_options = SessionManager._build_options

    async def wrapped_build_options(self, *args, **kwargs):
        opts = await real_build_options(self, *args, **kwargs)
        captured_stderr_cb.append(opts.stderr)
        return opts

    monkeypatch.setattr(SessionManager, "_build_options", wrapped_build_options)
    monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)
    monkeypatch.setattr(SessionManager, "_ensure_capacity", AsyncMock(return_value=None))

    meta = make_session_meta(id="resumed-session", project_name="demo", status="idle")

    with pytest.raises(AgentStartupError) as exc_info:
        await session_manager.get_or_connect("resumed-session", meta=meta)

    err = exc_info.value
    assert "resume-failed" in err.sdk_stderr
    assert "Command failed with exit code 1" in str(err)
    # 失败后会话不应残留在内存里
    assert "resumed-session" not in session_manager.sessions


@pytest.mark.asyncio
async def test_startup_stderr_is_not_truncated(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """启动阶段实际收到的 stderr 必须完整进入故障观测，不做行数裁剪。"""

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)

    captured_stderr_cb: list = []
    observed_count = 250

    class _FakeActor:
        def __init__(self, *_, on_message=None, client_factory=None):
            self.task = None

        async def start(self):
            cb = captured_stderr_cb[0]
            for i in range(observed_count):
                cb(f"line-{i:04d}")
            raise RuntimeError("Command failed with exit code 1")

        def add_done_callback(self, _cb):
            pass

    real_build_options = SessionManager._build_options

    async def wrapped_build_options(self, *args, **kwargs):
        opts = await real_build_options(self, *args, **kwargs)
        captured_stderr_cb.append(opts.stderr)
        return opts

    monkeypatch.setattr(SessionManager, "_build_options", wrapped_build_options)
    monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)
    monkeypatch.setattr(SessionManager, "_ensure_capacity", AsyncMock(return_value=None))

    with pytest.raises(AgentStartupError) as exc_info:
        await session_manager.send_new_session("demo", "你好")

    lines = exc_info.value.sdk_stderr.split("\n")
    assert len(lines) == observed_count
    assert lines[0] == "line-0000"
    assert lines[-1] == f"line-{observed_count - 1:04d}"
