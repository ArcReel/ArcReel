"""OptionsAssembler 单元测试：以注入假依赖驱动，不 monkeypatch 私有方法。

装配器持依赖、允许 I/O，异步 build 产出 SDK options。这里用注入的假 policy /
project_cwd 解析器 / 凭证 loader 直接构造装配器，断言凭证注入的空值覆盖、prompt
装配、options 字段与 hook 注册——与 SessionManager 解耦。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.options_assembler import (
    OptionsAssembler,
    load_provider_env_overrides,
)

_ALLOWED_TOOLS = ["Skill", "Task", "Bash", "BashOutput", "KillBash", "Read", "Write", "Edit"]
_SETTING_SOURCES = ["project"]


def _make_policy(tmp_path: Path, *, sandbox_enabled: bool = True) -> AgentAccessPolicy:
    return AgentAccessPolicy(
        project_root=(tmp_path / "repo").resolve(),
        projects_root=(tmp_path / "projects").resolve(),
        agent_profile_root=(tmp_path / "profile").resolve(),
        log_dir=(tmp_path / "logs").resolve(),
        sandbox_enabled=sandbox_enabled,
        in_docker=False,
    )


def _make_assembler(
    tmp_path: Path,
    *,
    policy: AgentAccessPolicy | None = None,
    max_turns: int | None = None,
    provider_env_loader=None,
) -> OptionsAssembler:
    projects_root = (tmp_path / "projects").resolve()
    projects_root.mkdir(parents=True, exist_ok=True)
    (projects_root / "demo").mkdir(exist_ok=True)
    resolved_policy = policy or _make_policy(tmp_path)
    return OptionsAssembler(
        projects_root=projects_root,
        allowed_tools=_ALLOWED_TOOLS,
        setting_sources=_SETTING_SOURCES,
        access_policy_provider=lambda: resolved_policy,
        max_turns_provider=lambda: max_turns,
        resolve_project_cwd=lambda name: projects_root / name,
        provider_env_loader=provider_env_loader,
    )


@pytest.mark.asyncio
async def test_load_provider_env_overrides_injects_anthropic_and_empties() -> None:
    """凭证注入：ANTHROPIC_* 取真值，其他 provider env 全部空值覆盖。"""
    fake_dict = {
        "ANTHROPIC_API_KEY": "sk-from-db",
        "ANTHROPIC_BASE_URL": "https://anthropic.example.com",
    }

    async def fake_build(_session):
        return fake_dict

    with patch("lib.config.service.build_anthropic_env_dict", side_effect=fake_build):
        env = await load_provider_env_overrides()

    assert env["ANTHROPIC_API_KEY"] == "sk-from-db"
    assert env["ANTHROPIC_BASE_URL"] == "https://anthropic.example.com"
    # 其他 provider 空值覆盖
    assert env["ARK_API_KEY"] == ""
    assert env["XAI_API_KEY"] == ""
    assert env["GEMINI_API_KEY"] == ""
    assert env["VIDU_API_KEY"] == ""
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == ""


@pytest.mark.asyncio
async def test_build_provider_env_overrides_uses_injected_loader(tmp_path: Path) -> None:
    """注入 provider_env_loader 时，build_provider_env_overrides 走注入源而非 DB。"""
    sentinel = {"ANTHROPIC_API_KEY": "injected"}

    async def fake_loader():
        return sentinel

    assembler = _make_assembler(tmp_path, provider_env_loader=fake_loader)
    assert await assembler.build_provider_env_overrides() == sentinel


@pytest.mark.asyncio
async def test_build_threads_injected_deps_into_options(tmp_path: Path) -> None:
    """build 把注入的 cwd / 凭证 / max_turns 逐一装进 ClaudeAgentOptions。"""
    projects_root = (tmp_path / "projects").resolve()

    async def fake_loader():
        return {"ANTHROPIC_API_KEY": "sk"}

    assembler = _make_assembler(tmp_path, max_turns=7, provider_env_loader=fake_loader)
    options = await assembler.build("demo")

    assert options.cwd == str(projects_root / "demo")
    assert options.env == {"ANTHROPIC_API_KEY": "sk"}
    assert options.max_turns == 7
    assert list(options.setting_sources) == _SETTING_SOURCES
    # file access hook 恒注册
    assert "PreToolUse" in options.hooks
    # sandbox 启用 → sandbox settings 编译进 options
    assert options.sandbox.get("enabled") is True


@pytest.mark.asyncio
async def test_build_adds_keep_alive_hook_with_can_use_tool(tmp_path: Path) -> None:
    """can_use_tool 存在时，keep-alive hook 排在 file access hook 之前。"""

    async def fake_loader():
        return {}

    async def _can_use_tool(_tool, _input, _ctx):
        return None

    assembler = _make_assembler(tmp_path, provider_env_loader=fake_loader)
    without = await assembler.build("demo")
    with_cut = await assembler.build("demo", can_use_tool=_can_use_tool)

    pre_without = without.hooks["PreToolUse"][0].hooks
    pre_with = with_cut.hooks["PreToolUse"][0].hooks
    assert len(pre_without) == 1
    assert len(pre_with) == 2
    assert pre_with[0] is assembler._keep_stream_open_hook


@pytest.mark.asyncio
async def test_build_append_prompt_carries_locale_language(tmp_path: Path) -> None:
    """prompt 装配按 locale 渲染语言规范段。"""
    assembler = _make_assembler(tmp_path)
    prompt = assembler._build_append_prompt("demo", locale="vi")
    assert "Tiếng Việt" in prompt or "vi" in prompt.lower()
    # persona 恒在
    assert "ArcReel 智能体" in prompt


@pytest.mark.asyncio
async def test_build_sandbox_disabled_strips_bash(tmp_path: Path) -> None:
    """sandbox 关闭（Windows 回退）→ Bash 系列剥离出 allowed_tools。"""

    async def fake_loader():
        return {}

    policy = _make_policy(tmp_path, sandbox_enabled=False)
    assembler = _make_assembler(tmp_path, policy=policy, provider_env_loader=fake_loader)
    options = await assembler.build("demo")

    for tool in AgentAccessPolicy.BASH_TOOLS:
        assert tool not in options.allowed_tools
    assert "Read" in options.allowed_tools
    assert options.sandbox == {"enabled": False}
