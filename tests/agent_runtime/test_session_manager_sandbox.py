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
    # 网络白名单覆盖 ArcReel 内置 provider + dev 常用域
    # 用 any(==) 显式列表成员比较，避免 CodeQL py/incomplete-url-substring-sanitization 误报
    allowed_domains = opts.sandbox.get("network", {}).get("allowedDomains", [])
    assert any(d == "anthropic.com" for d in allowed_domains)
    assert any(d == "*.googleapis.com" for d in allowed_domains)
    assert any(d == "example.com" for d in allowed_domains)
    # filesystem.denyRead 注入：sandbox profile 内核级文件读拒绝
    deny_read = opts.sandbox.get("filesystem", {}).get("denyRead", [])
    assert isinstance(deny_read, list)


def test_sandbox_allowed_domains_env_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    """ARCREEL_SANDBOX_EXTRA_ALLOWED_DOMAINS 逗号分隔扩展白名单。"""
    from server.agent_runtime.session_manager import SessionManager

    monkeypatch.setenv(
        "ARCREEL_SANDBOX_EXTRA_ALLOWED_DOMAINS",
        "custom-provider.com, *.internal.corp",
    )
    SessionManager._build_sandbox_allowed_domains.cache_clear()
    try:
        domains = SessionManager._build_sandbox_allowed_domains()
        # 用 any(==) 显式列表成员比较，避免 CodeQL py/incomplete-url-substring-sanitization 误报
        assert any(d == "custom-provider.com" for d in domains)
        assert any(d == "*.internal.corp" for d in domains)
        # 默认清单仍保留
        assert any(d == "anthropic.com" for d in domains)
    finally:
        SessionManager._build_sandbox_allowed_domains.cache_clear()


def test_bash_env_scrub_collects_pattern_matched_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """unset 清单除了固定名单还要动态命中 *_API_KEY / *_AUTH_TOKEN 等模式。"""
    from server.agent_runtime.session_manager import SessionManager

    monkeypatch.setenv("GEMINI_CLI_IDE_AUTH_TOKEN", "abc")
    monkeypatch.setenv("RANDOM_VENDOR_API_KEY", "def")
    monkeypatch.setenv("PATH", "/usr/bin")  # 不应命中

    SessionManager._collect_env_keys_to_scrub.cache_clear()
    SessionManager._env_scrub_wrap_prefix.cache_clear()
    try:
        keys = SessionManager._collect_env_keys_to_scrub()
        assert "GEMINI_CLI_IDE_AUTH_TOKEN" in keys
        assert "RANDOM_VENDOR_API_KEY" in keys
        assert "PATH" not in keys
        # 固定清单
        assert "ANTHROPIC_API_KEY" in keys
        assert "ARK_API_KEY" in keys
    finally:
        SessionManager._collect_env_keys_to_scrub.cache_clear()
        SessionManager._env_scrub_wrap_prefix.cache_clear()


def test_build_sensitive_abs_paths_includes_existing_files(tmp_path: Path) -> None:
    """枚举 worktree 下实际存在的敏感文件，跳过不存在项。"""
    from server.agent_runtime.session_manager import SessionManager
    from server.agent_runtime.session_store import SessionMetaStore

    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env").write_text("X=1", encoding="utf-8")
    (root / ".env.local").write_text("Y=2", encoding="utf-8")
    (root / "projects").mkdir()
    (root / "projects" / ".arcreel.db").write_bytes(b"sqlite-fake")
    (root / "projects" / ".arcreel.db-shm").write_bytes(b"shm")
    (root / "agent_runtime_profile" / ".claude").mkdir(parents=True)
    (root / "agent_runtime_profile" / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "vertex_keys").mkdir()

    sm = SessionManager(root, tmp_path / "data", SessionMetaStore())
    paths = sm._build_sensitive_abs_paths()

    # 必须命中真实存在的关键路径
    assert str(root.resolve() / ".env") in paths
    assert str(root.resolve() / ".env.local") in paths
    assert str(root.resolve() / "agent_runtime_profile" / ".claude" / "settings.json") in paths
    assert str(root.resolve() / "vertex_keys") in paths

    # 不存在的 system_config.json 不应出现（SDK 会跳过 non-existent path）
    assert all(".system_config.json" not in p for p in paths)
    # .arcreel.db 不在敏感清单里 — skill 入队需要访问
    assert all(".arcreel.db" not in p for p in paths)


@pytest.mark.asyncio
async def test_bash_env_scrub_hook_wraps_command_with_env_unset() -> None:
    """Bash PreToolUse hook 把 command 包装成 ``env -u ANTHROPIC_* sh -c '<orig>'``。"""
    from lib.config.env_keys import ANTHROPIC_ENV_KEYS

    result = await SessionManager._bash_env_scrub_hook(
        {"tool_name": "Bash", "tool_input": {"command": "env | grep ANTHROPIC"}},
        None,
        None,
    )

    out = result.get("hookSpecificOutput")
    assert out is not None
    assert out["hookEventName"] == "PreToolUse"
    # updatedInput 必须配 permissionDecision=allow，否则修改后命令仍会触发权限询问（SDK hooks.md）
    assert out["permissionDecision"] == "allow"
    new_cmd = out["updatedInput"]["command"]
    # 每个 ANTHROPIC_* key 都被 unset
    for key in ANTHROPIC_ENV_KEYS:
        assert f"-u {key}" in new_cmd
    # 原命令被 shlex.quote 包到 sh -c 内
    assert "sh -c " in new_cmd
    assert "'env | grep ANTHROPIC'" in new_cmd


@pytest.mark.asyncio
async def test_bash_env_scrub_hook_handles_single_quotes() -> None:
    """命令含单引号时不能破坏 shell 引号闭合。"""
    result = await SessionManager._bash_env_scrub_hook(
        {"tool_name": "Bash", "tool_input": {"command": "echo 'hello world'"}},
        None,
        None,
    )
    new_cmd = result["hookSpecificOutput"]["updatedInput"]["command"]
    # shlex.quote 把 'hello world' 转义为 'echo '"'"'hello world'"'"''
    assert new_cmd.endswith("'\"'\"'hello world'\"'\"''")


@pytest.mark.asyncio
async def test_bash_env_scrub_hook_passthrough_when_no_command() -> None:
    """空 command 时直接放行，不做包装。"""
    result = await SessionManager._bash_env_scrub_hook(
        {"tool_name": "Bash", "tool_input": {}},
        None,
        None,
    )
    assert result == {"continue_": True}


# ============================================================
# Windows 沙箱回退：sandbox_enabled=False 分支
# ============================================================


def _make_session_manager(tmp_path: Path, *, sandbox_enabled: bool) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir(exist_ok=True)
    (project_root / "projects").mkdir(exist_ok=True)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    return SessionManager(
        project_root,
        data_dir,
        SessionMetaStore(),
        sandbox_enabled=sandbox_enabled,
    )


def test_build_sandbox_settings_disabled_returns_only_enabled_false(tmp_path: Path) -> None:
    """sandbox_enabled=False（Windows 回退）时只返回 {"enabled": False}。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    assert sm._build_sandbox_settings() == {"enabled": False}


def test_build_sandbox_settings_enabled_returns_full_config(tmp_path: Path) -> None:
    """sandbox_enabled=True（默认）依然返回完整 dict（含 network / filesystem）。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=True)
    settings = sm._build_sandbox_settings()
    assert settings["enabled"] is True
    assert settings["autoAllowBashIfSandboxed"] is True
    assert settings["allowUnsandboxedCommands"] is False
    assert "allowedDomains" in settings["network"]
    assert "denyRead" in settings["filesystem"]


@pytest.mark.asyncio
@pytest.mark.parametrize("sandbox_enabled", [True, False])
async def test_build_options_bash_in_allowed_tools_by_sandbox(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sandbox_enabled: bool
) -> None:
    """sandbox 关闭时剥离 Bash/BashOutput/KillBash，启用时保留。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=sandbox_enabled)
    proj_dir = sm.project_root / "projects" / "test_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "project.json").write_text('{"title":"t"}', encoding="utf-8")

    async def fake_env(_self):
        return {"ANTHROPIC_API_KEY": "sk"}

    monkeypatch.setattr(SessionManager, "_build_provider_env_overrides", fake_env)
    opts = await sm._build_options("test_proj")

    for tool in SessionManager._BASH_TOOLS:
        assert (tool in opts.allowed_tools) is sandbox_enabled
    assert "Read" in opts.allowed_tools
    assert "Skill" in opts.allowed_tools
    if not sandbox_enabled:
        assert opts.sandbox == {"enabled": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,expected",
    [
        ("python .claude/skills/manage-project/scripts/peek_split_point.py --proj=x", "PermissionResultAllow"),
        ("ffmpeg -i in.mp4 out.mp4", "PermissionResultAllow"),
        ("ffprobe in.mp4", "PermissionResultAllow"),
        ("cat /etc/passwd", "PermissionResultDeny"),
        ("ls -la", "PermissionResultDeny"),
    ],
)
async def test_windows_bash_whitelist_matches_main_behavior(tmp_path: Path, command: str, expected: str) -> None:
    """sandbox 关闭时白名单 prefix 放行，其余拒；deny 文案派生自 _WINDOWS_BASH_PREFIX_WHITELIST。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    callback = await sm._build_can_use_tool_callback("test_sid", [None])
    result = await callback("Bash", {"command": command}, None)
    assert type(result).__name__ == expected
    if expected == "PermissionResultDeny":
        assert "Bash 白名单" in result.message
        # deny 文案必须包含所有白名单 prefix（单一真相源）
        for prefix in SessionManager._WINDOWS_BASH_PREFIX_WHITELIST:
            assert prefix in result.message


@pytest.mark.asyncio
async def test_windows_bash_management_tools_allowed(tmp_path: Path) -> None:
    """BashOutput / KillBash 是 Bash 管理工具，回退模式下直接放行。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    callback = await sm._build_can_use_tool_callback("test_sid", [None])
    for tool in ("BashOutput", "KillBash"):
        result = await callback(tool, {}, None)
        assert type(result).__name__ == "PermissionResultAllow"
