"""SessionManager sandbox + options.env 集成测试。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


@pytest.fixture
def session_manager(tmp_path: Path) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "projects").mkdir()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    meta_store = SessionMetaStore()
    sm = SessionManager(project_root, data_dir, meta_store)
    sm._in_docker = False
    return sm


@pytest.mark.asyncio
async def test_provider_env_overrides_includes_anthropic_and_empties(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_dict = {
        "ANTHROPIC_API_KEY": "sk-from-db",
        "ANTHROPIC_BASE_URL": "https://anthropic.example.com",
        "ANTHROPIC_MODEL": "claude-opus-4-7",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "",
        "CLAUDE_CODE_SUBAGENT_MODEL": "",
    }

    async def fake_build(_session):
        return fake_dict

    with patch("lib.config.service.build_anthropic_env_dict", side_effect=fake_build):
        env = await session_manager._build_provider_env_overrides()

    # Anthropic 注入真值
    assert env["ANTHROPIC_API_KEY"] == "sk-from-db"
    assert env["ANTHROPIC_BASE_URL"] == "https://anthropic.example.com"

    # 其他 provider 空值覆盖
    assert env["ARK_API_KEY"] == ""
    assert env["XAI_API_KEY"] == ""
    assert env["GEMINI_API_KEY"] == ""
    assert env["VIDU_API_KEY"] == ""
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == ""


def test_default_allowed_tools_includes_bash() -> None:
    """sandbox 启用后 Bash/BashOutput/KillBash 必须在 allowed_tools 列表。"""
    assert "Bash" in SessionManager.DEFAULT_ALLOWED_TOOLS
    assert "BashOutput" in SessionManager.DEFAULT_ALLOWED_TOOLS
    assert "KillBash" in SessionManager.DEFAULT_ALLOWED_TOOLS


@pytest.mark.asyncio
async def test_build_options_includes_sandbox_settings(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proj_dir = session_manager.project_root / "projects" / "test_proj"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")

    async def fake_env(_self):
        return {"ANTHROPIC_API_KEY": "sk", "ARK_API_KEY": ""}

    monkeypatch.setattr(SessionManager, "_build_provider_env_overrides", fake_env)

    opts = await session_manager._build_options("test_proj")

    assert opts.sandbox is not None
    assert opts.sandbox.get("enabled") is True
    assert opts.sandbox.get("autoAllowBashIfSandboxed") is True
    # 非 Docker 默认 weakerNested=False
    assert opts.sandbox.get("enableWeakerNestedSandbox") is False
