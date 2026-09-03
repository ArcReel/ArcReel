"""OptionsAssembler 单元测试：以注入假依赖驱动，不 monkeypatch 私有方法。

装配器持依赖、允许 I/O，异步 build 产出 SDK options。这里用注入的假 policy /
project_cwd 解析器 / 凭证 loader 直接构造装配器，断言凭证注入的空值覆盖、prompt
装配、options 字段与 hook 注册——与 SessionManager 解耦。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from lib.agent_memory_paths import project_memory_dir, user_memory_dir
from lib.db.base import DEFAULT_USER_ID
from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.options_assembler import (
    CLI_STDOUT_MAX_BUFFER_BYTES,
    OptionsAssembler,
    load_provider_env_overrides,
)
from server.auth import verify_token
from tests.fakes import blocking_file_read_gate

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
    user_id: str = DEFAULT_USER_ID,
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
        user_id_provider=lambda: user_id,
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
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == ""
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
    assert options.env["ANTHROPIC_API_KEY"] == "sk"
    assert options.max_turns == 7
    assert list(options.setting_sources) == _SETTING_SOURCES
    # file access hook 恒注册
    assert "PreToolUse" in options.hooks
    # sandbox 启用 → sandbox settings 编译进 options
    assert options.sandbox.get("enabled") is True
    # 用户消息回放开关：缺失则 SDK 不回放副本，身份映射无从建立
    assert options.extra_args == {"replay-user-messages": None}
    # CLI stdout 单条 NDJSON 行的缓冲上限：默认 1 MiB 会被附图请求的回放副本撞穿
    assert options.max_buffer_size == CLI_STDOUT_MAX_BUFFER_BYTES == 32 * 1024 * 1024


@pytest.mark.asyncio
async def test_build_injects_short_lived_arcreel_api_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """每次会话 options 都拿到 localhost API 地址与 15 分钟 JWT。"""

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
    prompt = await assembler._build_append_prompt("demo", locale="vi")
    assert "Tiếng Việt" in prompt or "vi" in prompt.lower()
    # persona 恒在
    assert "ArcReel Agent" in prompt


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


@pytest.mark.asyncio
async def test_json_pre_validation_hook_keeps_event_loop_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PreToolUse JSON 校验读整份剧本 JSON，必须卸载到线程，否则事件循环被读堵住。"""
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

    # 替换后仍是合法 JSON → 放行
    assert result == {}


@pytest.mark.asyncio
async def test_json_post_validation_hook_keeps_event_loop_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PostToolUse JSON 校验回读落盘结果，必须卸载到线程，否则事件循环被读堵住。"""
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
    """settings JSON 把原生 auto memory 重定向到项目记忆目录；sandbox 仍是独立字段，
    由 SDK 在拼命令行时并进同一份 JSON。"""

    async def fake_loader():
        return {}

    options = await _make_assembler(tmp_path, provider_env_loader=fake_loader).build("demo")

    project_cwd = (tmp_path / "projects").resolve() / "demo"
    assert json.loads(options.settings) == {"autoMemoryDirectory": str(project_memory_dir(project_cwd))}
    # 显式设 autoMemoryEnabled 会覆盖原生默认，装配不碰它
    assert "autoMemoryEnabled" not in options.settings
    assert options.sandbox.get("enabled") is True


@pytest.mark.asyncio
async def test_append_prompt_carries_user_memory_dir_and_index(tmp_path: Path) -> None:
    """用户记忆段给出目录绝对路径、两级分流规则与索引全文。"""
    assembler = _make_assembler(tmp_path)
    memory_dir = user_memory_dir((tmp_path / "projects").resolve(), DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("- [配音偏好](voice.md) — 固定用女声\n", encoding="utf-8")

    prompt = await assembler._build_append_prompt("demo")

    assert "## 用户记忆" in prompt
    assert memory_dir.as_posix() in prompt
    assert "拿不准留项目记忆" in prompt
    # Bash 侧对数据根整棵 denyRead，记忆读写只能走内置文件工具
    assert "Bash 读不到记忆目录" in prompt
    assert "- [配音偏好](voice.md) — 固定用女声" in prompt


@pytest.mark.asyncio
async def test_append_prompt_omits_index_when_user_memory_absent(tmp_path: Path) -> None:
    """目录不存在：段落照给（Agent 要知道该往哪写），索引要点省略，且不建目录。"""
    assembler = _make_assembler(tmp_path)
    memory_dir = user_memory_dir((tmp_path / "projects").resolve(), DEFAULT_USER_ID)

    prompt = await assembler._build_append_prompt("demo")

    assert "## 用户记忆" in prompt
    assert "**索引**" not in prompt
    assert not memory_dir.exists()


@pytest.mark.asyncio
async def test_append_prompt_omits_index_when_user_memory_index_blank(tmp_path: Path) -> None:
    """索引存在但只有空白：省略索引要点，不注入一段空索引。"""
    assembler = _make_assembler(tmp_path)
    memory_dir = user_memory_dir((tmp_path / "projects").resolve(), DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("\n   \n", encoding="utf-8")

    prompt = await assembler._build_append_prompt("demo")

    assert "## 用户记忆" in prompt
    assert "**索引**" not in prompt


@pytest.mark.asyncio
async def test_append_prompt_truncates_user_memory_index_over_line_limit(tmp_path: Path) -> None:
    """索引超 200 行：只注入前 200 行并附超限提示。"""
    assembler = _make_assembler(tmp_path)
    memory_dir = user_memory_dir((tmp_path / "projects").resolve(), DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text("\n".join(f"- 第 {i} 条" for i in range(300)), encoding="utf-8")

    prompt = await assembler._build_append_prompt("demo")

    assert "- 第 199 条" in prompt
    assert "- 第 200 条" not in prompt
    assert "共 300 行" in prompt


@pytest.mark.asyncio
async def test_append_prompt_truncates_user_memory_index_over_byte_limit(tmp_path: Path) -> None:
    """索引行数不超但字节超 25 000：按最后一个换行截断并附超限提示。"""
    assembler = _make_assembler(tmp_path)
    memory_dir = user_memory_dir((tmp_path / "projects").resolve(), DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    # 10 行 × 每行 3 000 余字节（中文 3 字节/字）≈ 30 KB，行数远在 200 以内
    index = "\n".join(f"- 第 {i} 条：" + "记" * 1000 for i in range(10))
    (memory_dir / "MEMORY.md").write_text(index, encoding="utf-8")

    prompt = await assembler._build_append_prompt("demo")

    # 25 000 字节落在第 9 行内，回退到上一个换行 → 前 8 行完整保留
    assert "- 第 7 条：" in prompt
    assert "- 第 8 条：" not in prompt
    assert f"共 10 行 / {len(index.encode('utf-8'))} 字节" in prompt


@pytest.mark.asyncio
async def test_append_prompt_omits_index_when_user_memory_index_not_utf8(tmp_path: Path) -> None:
    """索引不是 UTF-8（用户用别的编码存回）：省略索引要点，会话照常装配。"""
    assembler = _make_assembler(tmp_path)
    memory_dir = user_memory_dir((tmp_path / "projects").resolve(), DEFAULT_USER_ID)
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_bytes("- [配音偏好](voice.md) — 固定用女声\n".encode("gbk"))

    prompt = await assembler._build_append_prompt("demo")

    assert "## 用户记忆" in prompt
    assert "**索引**" not in prompt


@pytest.mark.asyncio
async def test_append_prompt_omits_user_memory_for_invalid_user_id(tmp_path: Path) -> None:
    """user_id 不是单个路径段：整段省略，与围栏的 fail-closed 同向。"""
    assembler = _make_assembler(tmp_path, user_id="../escape")

    prompt = await assembler._build_append_prompt("demo")

    assert "## 用户记忆" not in prompt
    assert "ArcReel Agent" in prompt
