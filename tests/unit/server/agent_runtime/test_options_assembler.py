"""OptionsAssembler unit tests driven by injected fakes without monkeypatching private methods.

The assembler owns dependencies, permits I/O, and asynchronously builds SDK options. Construct it directly with fake policy / project_cwd resolver / credential loader, asserting credential empty-value overrides, prompt assembly, options fields and hook registration independently of SessionManager."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from lib.agent.agent_memory_paths import project_memory_dir
from lib.agent.credential_tiers import AgentPricing
from lib.config.service import AnthropicEnv
from lib.db.base import DEFAULT_USER_ID
from lib.infra.data_root_layout import DataRootLayout
from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.options_assembler import (
    CLI_STDOUT_MAX_BUFFER_BYTES,
    OptionsAssembler,
    agent_pricing_of,
    load_provider_env_overrides,
)
from server.agent_toolset.toolset import ARCREEL_MCP_TOOL_IDS
from server.auth import verify_token
from tests.fakes import blocking_file_read_gate

_ALLOWED_TOOLS = ["Skill", "Task", "Bash", "BashOutput", "KillBash", "Read", "Write", "Edit"]
_SETTING_SOURCES = ["project"]


def _make_policy(tmp_path: Path, *, sandbox_enabled: bool = True) -> AgentAccessPolicy:
    return AgentAccessPolicy(
        project_root=(tmp_path / "repo").resolve(),
        data_root=(tmp_path / "projects").resolve(),
        agent_profile_root=(tmp_path / "profile").resolve(),
        sandbox_enabled=sandbox_enabled,
        in_docker=False,
    )


def _make_assembler(
    tmp_path: Path,
    *,
    policy: AgentAccessPolicy | None = None,
    max_turns: int | None = None,
    provider_env_loader=None,
    user_id: str = DEFAULT_USER_ID,
) -> OptionsAssembler:
    projects_root = (tmp_path / "projects").resolve()
    projects_root.mkdir(parents=True, exist_ok=True)
    (projects_root / "demo").mkdir(exist_ok=True)
    resolved_policy = policy or _make_policy(tmp_path)
    return OptionsAssembler(
        data_root=projects_root,
        allowed_tools=_ALLOWED_TOOLS,
        setting_sources=_SETTING_SOURCES,
        access_policy_provider=lambda: resolved_policy,
        max_turns_provider=lambda: max_turns,
        resolve_project_cwd=lambda name: projects_root / name,
        provider_env_loader=provider_env_loader,
        user_id_provider=lambda: user_id,
    )


@pytest.mark.asyncio
async def test_load_provider_env_overrides_injects_anthropic_and_empties() -> None:
    """Credential injection sets real ANTHROPIC_* values and empty overrides for every other provider environment variable."""
    fake_dict = {
        "ANTHROPIC_API_KEY": "sk-from-db",
        "ANTHROPIC_BASE_URL": "https://anthropic.example.com",
    }

    async def fake_build(_session):
        return AnthropicEnv(env=fake_dict, pricing=AgentPricing(credential_id=3, multiplier=0.5))

    with patch("lib.config.service.build_anthropic_env", side_effect=fake_build):
        env = await load_provider_env_overrides()

    assert agent_pricing_of(SimpleNamespace(env=env)) == AgentPricing(credential_id=3, multiplier=0.5)

    assert env["ANTHROPIC_API_KEY"] == "sk-from-db"
    assert env["ANTHROPIC_BASE_URL"] == "https://anthropic.example.com"
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
    # Override other provider values with empties.
    assert env["ARK_API_KEY"] == ""
    assert env["XAI_API_KEY"] == ""
    assert env["GEMINI_API_KEY"] == ""
    assert env["VIDU_API_KEY"] == ""
    assert env["GOOGLE_APPLICATION_CREDENTIALS"] == ""


@pytest.mark.asyncio
async def test_build_provider_env_overrides_uses_injected_loader(tmp_path: Path) -> None:
    """With provider_env_loader injected, build_provider_env_overrides uses it rather than the DB."""
    sentinel = {"ANTHROPIC_API_KEY": "injected"}

    async def fake_loader():
        return sentinel

    assembler = _make_assembler(tmp_path, provider_env_loader=fake_loader)
    assert await assembler.build_provider_env_overrides() == sentinel


@pytest.mark.asyncio
async def test_build_threads_injected_deps_into_options(tmp_path: Path) -> None:
    """build puts injected cwd / credentials / max_turns into ClaudeAgentOptions."""
    projects_root = (tmp_path / "projects").resolve()

    async def fake_loader():
        return {"ANTHROPIC_API_KEY": "sk"}

    assembler = _make_assembler(tmp_path, max_turns=7, provider_env_loader=fake_loader)
    options = await assembler.build("demo")

    assert options.cwd == str(projects_root / "demo")
    assert options.env["ANTHROPIC_API_KEY"] == "sk"
    assert options.max_turns == 7
    assert list(options.setting_sources) == _SETTING_SOURCES
    # Always register the file-access hook.
    assert "PreToolUse" in options.hooks
    # Enabled sandbox compiles sandbox settings into options.
    assert options.sandbox.get("enabled") is True
    # User-message replay switch: without it, SDK copies are absent and identity mapping cannot form.
    assert options.extra_args == {"replay-user-messages": None}
    # CLI stdout NDJSON per-line buffer limit: image-bearing request replay copies exceed the default 1 MiB.
    assert options.max_buffer_size == CLI_STDOUT_MAX_BUFFER_BYTES == 32 * 1024 * 1024
    # Session-state frames: CLI reports running while owing subsequent turns, protecting the session from disconnect.
    assert options.env["CLAUDE_CODE_EMIT_SESSION_STATE_EVENTS"] == "1"


@pytest.mark.asyncio
async def test_build_injects_short_lived_arcreel_api_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each session options object receives the localhost API URL and a 15-minute JWT."""

    async def fake_loader():
        return {"ANTHROPIC_API_KEY": "sk"}

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_TOKEN_SECRET", "test-secret-key-that-is-at-least-32-bytes")
    monkeypatch.setenv("ARCREEL_API_BASE", "http://127.0.0.1:9123/api/v1/")
    options = await _make_assembler(tmp_path, provider_env_loader=fake_loader).build("demo")

    payload = verify_token(options.env["ARCREEL_API_TOKEN"])
    assert options.env["ARCREEL_EMBEDDED_AGENT"] == "1"
    assert options.env["ARCREEL_API_BASE"] == "http://127.0.0.1:9123/api/v1"
    assert payload is not None
    assert payload["sub"] == "embedded-agent"
    assert payload["exp"] - payload["iat"] == 900


@pytest.mark.asyncio
async def test_build_adds_keep_alive_hook_with_can_use_tool(tmp_path: Path) -> None:
    """When can_use_tool exists, keep-alive precedes file access in hook order."""

    async def fake_loader():
        return {}

    async def _can_use_tool(_tool, _input, _ctx):
        return None

    assembler = _make_assembler(tmp_path, provider_env_loader=fake_loader)
    without = await assembler.build("demo")
    with_cut = await assembler.build("demo", can_use_tool=_can_use_tool)

    pre_without = without.hooks["PreToolUse"][0].hooks
    pre_with = with_cut.hooks["PreToolUse"][0].hooks
    assert len(pre_without) == 2
    assert len(pre_with) == 3
    assert pre_with[0] is assembler._keep_stream_open_hook


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("locale", "language"),
    [("zh", "中文"), ("en", "English"), ("vi", "Tiếng Việt"), ("ru", "Русский"), ("xx", "中文")],
)
async def test_build_system_prompt_renders_language_rule_for_locale(tmp_path: Path, locale: str, language: str) -> None:
    """Appended system prompts render locale language rules and contain only language rules, project context and user-memory sections."""

    async def fake_loader():
        return {}

    assembler = _make_assembler(tmp_path, provider_env_loader=fake_loader)
    options = await assembler.build("demo", locale=locale)
    append = options.system_prompt["append"]

    assert f"- **You must answer the user in {language}**:" in append
    assert f"- **Prompts use {language}**:" in append
    assert [line for line in append.splitlines() if line.startswith("## ")] == [
        "## Language rules",
        "## Current project context",
        "## User memory",
    ]
    # SDK loads persona from the content-mode CLAUDE.md; server no longer appends it.
    assert "ArcReel Agent" not in append


@pytest.mark.asyncio
async def test_build_sandbox_disabled_strips_bash(tmp_path: Path) -> None:
    """Disabled sandbox (Windows fallback) strips the Bash family from allowed_tools."""

    async def fake_loader():
        return {}

    policy = _make_policy(tmp_path, sandbox_enabled=False)
    assembler = _make_assembler(tmp_path, policy=policy, provider_env_loader=fake_loader)
    options = await assembler.build("demo")

    for tool in AgentAccessPolicy.BASH_TOOLS:
        assert tool not in options.allowed_tools
    assert "Read" in options.allowed_tools
    assert options.sandbox == {"enabled": False}


@pytest.mark.asyncio
async def test_json_pre_validation_hook_keeps_event_loop_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PreToolUse JSON validation reads entire script JSON; offload to a thread to avoid blocking the event loop."""
    project_cwd = tmp_path / "projects" / "demo"
    project_cwd.mkdir(parents=True, exist_ok=True)
    script = project_cwd / "script.json"
    script.write_text('{"a": 1}', encoding="utf-8")

    hook = _make_assembler(tmp_path)._build_json_validation_hook(project_cwd)
    input_data = {
        "tool_name": "Edit",
        "tool_input": {"file_path": str(script), "old_string": '"a": 1', "new_string": '"a": 2'},
    }

    with blocking_file_read_gate(monkeypatch, script, method="read_text") as gate:
        task = asyncio.create_task(hook(input_data, "tu-1", None))
        await gate.wait_until_read_started()
        gate.release()
        result = await task
        gate.assert_read_was_offloaded()

    # Replacement remains valid JSON; allow.
    assert result == {}


@pytest.mark.asyncio
async def test_json_post_validation_hook_keeps_event_loop_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PostToolUse JSON validation rereads persisted output; offload to a thread to avoid blocking the event loop."""
    project_cwd = tmp_path / "projects" / "demo"
    project_cwd.mkdir(parents=True, exist_ok=True)
    script = project_cwd / "script.json"
    script.write_text('{"a": 2}', encoding="utf-8")

    hook = _make_assembler(tmp_path)._build_json_post_validation_hook(project_cwd, {})
    input_data = {"tool_name": "Edit", "tool_input": {"file_path": str(script)}}

    with blocking_file_read_gate(monkeypatch, script, method="read_text") as gate:
        task = asyncio.create_task(hook(input_data, "tu-1", None))
        await gate.wait_until_read_started()
        gate.release()
        result = await task
        gate.assert_read_was_offloaded()

    assert result == {}


@pytest.mark.asyncio
async def test_build_settings_redirects_auto_memory_to_project_memory_dir(tmp_path: Path) -> None:
    """Settings JSON redirects native auto memory to project memory; sandbox remains a separate field merged into the same JSON by SDK command-line assembly."""

    async def fake_loader():
        return {}

    options = await _make_assembler(tmp_path, provider_env_loader=fake_loader).build("demo")

    project_cwd = (tmp_path / "projects").resolve() / "demo"
    assert json.loads(options.settings) == {"autoMemoryDirectory": str(project_memory_dir(project_cwd))}
    # Explicit autoMemoryEnabled overrides native defaults; assembly leaves it untouched.
    assert "autoMemoryEnabled" not in options.settings
    assert options.sandbox.get("enabled") is True


@pytest.mark.asyncio
async def test_append_prompt_carries_user_memory_dir_and_index(tmp_path: Path) -> None:
    """User-memory section supplies its absolute directory, two-level routing rules and complete index."""
    assembler = _make_assembler(tmp_path)
    memory_dir = DataRootLayout((tmp_path / "projects").resolve()).user_memory_dir(DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("- [配音偏好](voice.md) — 固定用女声\n", encoding="utf-8")

    prompt = await assembler._build_append_prompt("demo")

    assert "## User memory" in prompt
    assert memory_dir.as_posix() in prompt
    assert "When uncertain, use project memory" in prompt
    # Kernel sandbox denies user-memory reads and permits no writes; memory access uses built-in file tools only.
    assert "Bash cannot read or write this directory" in prompt
    assert "- [配音偏好](voice.md) — 固定用女声" in prompt


@pytest.mark.asyncio
async def test_append_prompt_omits_index_when_user_memory_absent(tmp_path: Path) -> None:
    """If the directory is absent, still supply the section so the Agent knows where to write; omit index highlights and do not create it."""
    assembler = _make_assembler(tmp_path)
    memory_dir = DataRootLayout((tmp_path / "projects").resolve()).user_memory_dir(DEFAULT_USER_ID)

    prompt = await assembler._build_append_prompt("demo")

    assert "## User memory" in prompt
    assert "**Index**" not in prompt
    assert not memory_dir.exists()


@pytest.mark.asyncio
async def test_append_prompt_omits_index_when_user_memory_index_blank(tmp_path: Path) -> None:
    """If the existing index is whitespace-only, omit highlights rather than inject an empty index."""
    assembler = _make_assembler(tmp_path)
    memory_dir = DataRootLayout((tmp_path / "projects").resolve()).user_memory_dir(DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("\n   \n", encoding="utf-8")

    prompt = await assembler._build_append_prompt("demo")

    assert "## User memory" in prompt
    assert "**Index**" not in prompt


@pytest.mark.asyncio
async def test_append_prompt_truncates_user_memory_index_over_line_limit(tmp_path: Path) -> None:
    """If the index exceeds 200 lines, inject only the first 200 with an over-limit notice."""
    assembler = _make_assembler(tmp_path)
    memory_dir = DataRootLayout((tmp_path / "projects").resolve()).user_memory_dir(DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("\n".join(f"- 第 {i} 条" for i in range(300)), encoding="utf-8")

    prompt = await assembler._build_append_prompt("demo")

    assert "- 第 199 条" in prompt
    assert "- 第 200 条" not in prompt
    assert "has 300 lines" in prompt


@pytest.mark.asyncio
async def test_append_prompt_truncates_user_memory_index_over_byte_limit(tmp_path: Path) -> None:
    """If line count fits but bytes exceed 25,000, truncate at the last newline and append an over-limit notice."""
    assembler = _make_assembler(tmp_path)
    memory_dir = DataRootLayout((tmp_path / "projects").resolve()).user_memory_dir(DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    # Ten lines × over 3,000 bytes each (three bytes per Chinese character) ≈30 KB, well below 200 lines.
    index = "\n".join(f"- 第 {i} 条：" + "记" * 1000 for i in range(10))
    (memory_dir / "MEMORY.md").write_text(index, encoding="utf-8")

    prompt = await assembler._build_append_prompt("demo")

    # 25,000 bytes falls within line 9; retreat to the previous newline and retain the first 8 complete lines.
    assert "- 第 7 条：" in prompt
    assert "- 第 8 条：" not in prompt
    assert f"has 10 lines / {len(index.encode('utf-8'))} bytes" in prompt


@pytest.mark.asyncio
async def test_append_prompt_omits_index_when_user_memory_index_not_utf8(tmp_path: Path) -> None:
    """If the index is not UTF-8 (saved using another encoding), omit highlights and assemble the session normally."""
    assembler = _make_assembler(tmp_path)
    memory_dir = DataRootLayout((tmp_path / "projects").resolve()).user_memory_dir(DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_bytes("- [配音偏好](voice.md) — 固定用女声\n".encode("gbk"))

    prompt = await assembler._build_append_prompt("demo")

    assert "## User memory" in prompt
    assert "**Index**" not in prompt


@pytest.mark.asyncio
async def test_append_prompt_omits_user_memory_for_invalid_user_id(tmp_path: Path) -> None:
    """If user_id is not one path segment, omit the whole section, matching fail-closed boundaries."""
    assembler = _make_assembler(tmp_path, user_id="../escape")

    prompt = await assembler._build_append_prompt("demo")

    assert "## User memory" not in prompt
    assert "## Language rules" in prompt


#: Built-in tools: beyond three read-only tools, include file writes, commands, child sessions, networking and questions.
_BUILTIN_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "Write",
    "Edit",
    "NotebookEdit",
    "Bash",
    "BashOutput",
    "KillBash",
    "Task",
    "Agent",
    "Skill",
    "WebFetch",
    "WebSearch",
    "TodoWrite",
    "AskUserQuestion",
)

#: All read-only subagent tools: video review sees media, image review sees images, and both can read project files.
_READ_ONLY_SUBAGENT_TOOLS = {
    "review-footage": {"Read", "Glob", "Grep", "mcp__arcreel__inspect_video_units"},
    "review-images": {"Read", "Glob", "Grep", "mcp__arcreel__inspect_images"},
}


async def _denied_by_pre_tool_use(options, tool_name: str, **context: str) -> bool:
    """Follow SDK ordering through matching PreToolUse hooks; any denial rejects the call."""

    payload = {"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": {}, **context}
    for matcher in options.hooks["PreToolUse"]:
        if matcher.matcher is not None and not re.fullmatch(matcher.matcher, tool_name):
            continue
        for hook in matcher.hooks:
            output = await hook(payload, "toolu_1", None)
            if (output.get("hookSpecificOutput") or {}).get("permissionDecision") == "deny":
                return True
    return False


@pytest.mark.asyncio
@pytest.mark.parametrize("agent_type", sorted(_READ_ONLY_SUBAGENT_TOOLS))
async def test_read_only_subagents_cannot_call_any_tool_beyond_reading(tmp_path: Path, agent_type: str) -> None:
    """Read-only subagents may inspect images and read files only: PreToolUse rejects writes, commands, delegation, generation and all other ArcReel tools."""

    async def fake_loader():
        return {}

    options = await _make_assembler(tmp_path, provider_env_loader=fake_loader).build("demo")
    every_tool = [*_BUILTIN_TOOLS, *(f"mcp__arcreel__{tool_id}" for tool_id in ARCREEL_MCP_TOOL_IDS)]
    reviewer = {"agent_type": agent_type, "agent_id": "agent-1"}

    allowed = {tool for tool in every_tool if not await _denied_by_pre_tool_use(options, tool, **reviewer)}

    assert allowed == _READ_ONLY_SUBAGENT_TOOLS[agent_type]
    for writer in ("Write", "Edit", "Bash", "mcp__arcreel__edit_timeline", "mcp__arcreel__generate_assets"):
        assert not await _denied_by_pre_tool_use(options, writer), f"主对话的 {writer} 不受只读名单约束"
        assert not await _denied_by_pre_tool_use(options, writer, agent_type="generate-assets", agent_id="agent-2"), (
            f"其他子智能体的 {writer} 不受只读名单约束"
        )
