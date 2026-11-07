"""Regression coverage for AgentStartupError forwarding SDK stderr.

Motivation: on nonzero SDK subprocess exit, ProcessError.stderr is a hardcoded placeholder; ArcReel previously supplied no stderr callback, so frontend showed only "Check stderr output for details".
Coverage:
  1. AgentStartupError __str__ joins message + stderr for every except Exception: str(exc) path.
  2. _build_options forwards the stderr callback into ClaudeAgentOptions.
  3. send_new_session collects stderr and wraps actor.start failures as AgentStartupError.
  4. Router translates AgentStartupError into 502 and structured failure observations."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from server.agent_runtime.session_manager import (
    AgentStartupError,
    SessionManager,
)
from server.agent_runtime.session_store import SessionMetaStore
from tests.fakes import use_provider_env, use_sdk_client


class _RecordingClient:
    """Fake SDK client retaining this round's assembled options.

    The actor receives a ``client_factory`` closing over options; one call exposes the stderr callback. Do not replace the assembler's callback registration, which is the step under test."""

    def __init__(self, options: Any) -> None:
        self.options = options


def test_agent_startup_error_str_includes_stderr() -> None:
    exc = AgentStartupError(
        "Command failed with exit code 1",
        sdk_stderr="Claude Code on Windows requires either Git for Windows (for bash) or PowerShell.",
    )
    rendered = str(exc)
    assert "Command failed with exit code 1" in rendered
    assert "Git for Windows" in rendered
    # Separate message and stderr with a blank line for frontend display.
    assert "\n\n" in rendered


def test_agent_startup_error_without_stderr_keeps_message() -> None:
    exc = AgentStartupError("启动失败")
    assert str(exc) == "启动失败"
    assert exc.sdk_stderr == ""


@pytest.fixture
def seeded_session_manager(tmp_path: Path) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    manager = SessionManager(project_root, SessionMetaStore())
    proj_dir = manager.layout.projects_dir / "demo"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")
    return manager


@pytest.mark.asyncio
async def test_build_options_forwards_stderr_callback(
    seeded_session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_build_options forwards the stderr callback to ClaudeAgentOptions."""

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    use_provider_env(monkeypatch, fake_env)

    captured: list[str] = []

    def collector(line: str) -> None:
        captured.append(line)

    opts = await seeded_session_manager._build_options("demo", stderr=collector)
    assert opts.stderr is collector

    # Simulate one stderr line forwarded by the SDK.
    opts.stderr("hello from claude.exe")
    assert captured == ["hello from claude.exe"]


@pytest.mark.asyncio
async def test_send_new_session_wraps_actor_failure_with_stderr(
    seeded_session_manager: SessionManager,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Collect stderr and raise wrapped AgentStartupError when actor.start() fails."""

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    use_provider_env(monkeypatch, fake_env)

    captured_stderr_cb: list = []
    secret = "startup-secret-must-not-leak"

    class _FakeActor:
        def __init__(self, *_, on_message=None, client_factory=None, **__):
            self.task = None
            self._on_message = on_message
            captured_stderr_cb.append(client_factory().options.stderr)

        async def start(self):
            # Simulate SDK stderr followed by exit during connect.
            cb = captured_stderr_cb[0]
            cb("Claude Code on Windows requires either Git for Windows (for bash) or PowerShell.")
            cb("Or set CLAUDE_CODE_GIT_BASH_PATH to your bash.exe location.")
            cb(f"Authorization: Bearer {secret}")
            raise RuntimeError(f"Command failed with exit code 1; api_key={secret}")

        def add_done_callback(self, _cb):
            pass

    use_sdk_client(monkeypatch, _RecordingClient)
    monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)
    caplog.set_level("ERROR", logger="server.agent_runtime.session_manager")

    with pytest.raises(AgentStartupError) as exc_info:
        await seeded_session_manager.send_new_session("demo", "你好")

    err = exc_info.value
    assert "Git for Windows" in err.sdk_stderr
    assert "CLAUDE_CODE_GIT_BASH_PATH" in err.sdk_stderr
    # __str__ must join both parts for direct router/frontend display.
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
    seeded_session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Wrap actor.start failure as AgentStartupError even without SDK stderr, preserving its cause chain."""

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    use_provider_env(monkeypatch, fake_env)

    class _FakeActor:
        def __init__(self, *_, on_message=None, client_factory=None, **__):
            self.task = None

        async def start(self):
            raise OSError("ENOENT")

        def add_done_callback(self, _cb):
            pass

    monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)

    with pytest.raises(AgentStartupError) as exc_info:
        await seeded_session_manager.send_new_session("demo", "你好")

    assert exc_info.value.sdk_stderr == ""
    assert "ENOENT" in str(exc_info.value)
    # Preserve the cause chain for logger.exception inspection of the underlying error.
    assert isinstance(exc_info.value.__cause__, OSError)


@pytest.mark.asyncio
async def test_send_new_session_wraps_option_assembly_failure(
    seeded_session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Credential injection is a real options-assembly failure source; propagate it unchanged to send_new_session.
    use_provider_env(monkeypatch, AsyncMock(side_effect=LookupError("credential missing")))

    with pytest.raises(AgentStartupError) as exc_info:
        await seeded_session_manager.send_new_session("demo", "你好")

    failure = exc_info.value.failure_observation
    assert failure is not None
    assert failure["phase"] == "startup"
    assert failure["summary"]["type"] == "LookupError"
    assert failure["summary"]["message"] == "credential missing"
    assert seeded_session_manager.sessions == {}


@pytest.mark.asyncio
async def test_get_or_connect_wraps_actor_failure_with_stderr(
    seeded_session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Historical-session recovery also wraps actor.start failure as AgentStartupError.

    ``get_or_connect`` and ``send_new_session`` are independent paths; cover both so neither can regress unnoticed."""
    from tests.factories import make_session_meta

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    use_provider_env(monkeypatch, fake_env)

    captured_stderr_cb: list = []

    class _FakeActor:
        def __init__(self, *_, on_message=None, client_factory=None, **__):
            self.task = None
            captured_stderr_cb.append(client_factory().options.stderr)

        async def start(self):
            cb = captured_stderr_cb[0]
            cb("resume-failed: cannot rehydrate transcript")
            raise RuntimeError("Command failed with exit code 1")

        def add_done_callback(self, _cb):
            pass

    use_sdk_client(monkeypatch, _RecordingClient)
    monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)

    meta = make_session_meta(id="resumed-session", project_name="demo", status="idle")

    with pytest.raises(AgentStartupError) as exc_info:
        await seeded_session_manager.get_or_connect("resumed-session", meta=meta)

    err = exc_info.value
    assert "resume-failed" in err.sdk_stderr
    assert "Command failed with exit code 1" in str(err)
    # Failed sessions must not remain in memory.
    assert "resumed-session" not in seeded_session_manager.sessions


@pytest.mark.asyncio
async def test_startup_stderr_is_not_truncated(
    seeded_session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Include all startup stderr in failure observations without clipping line count."""

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    use_provider_env(monkeypatch, fake_env)

    captured_stderr_cb: list = []
    observed_count = 250

    class _FakeActor:
        def __init__(self, *_, on_message=None, client_factory=None, **__):
            self.task = None
            captured_stderr_cb.append(client_factory().options.stderr)

        async def start(self):
            cb = captured_stderr_cb[0]
            for i in range(observed_count):
                cb(f"line-{i:04d}")
            raise RuntimeError("Command failed with exit code 1")

        def add_done_callback(self, _cb):
            pass

    use_sdk_client(monkeypatch, _RecordingClient)
    monkeypatch.setattr("server.agent_runtime.session_manager.SessionActor", _FakeActor)

    with pytest.raises(AgentStartupError) as exc_info:
        await seeded_session_manager.send_new_session("demo", "你好")

    lines = exc_info.value.sdk_stderr.split("\n")
    assert len(lines) == observed_count
    assert lines[0] == "line-0000"
    assert lines[-1] == f"line-{observed_count - 1:04d}"
