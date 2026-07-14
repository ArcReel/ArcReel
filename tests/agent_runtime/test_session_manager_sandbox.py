"""SessionManager sandbox 接线测试：options 装配、hook 返回格式、权限链顺序。

纯规则断言（路径裁决 / settings 编译 / 白名单谓词 / 密钥剥离变换）已搬家至
tests/agent_runtime/test_agent_access_policy.py；本文件只测 SDK 封皮侧的接线。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


@pytest.fixture
def session_manager(tmp_path: Path) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "projects").mkdir()
    meta_store = SessionMetaStore()
    return SessionManager(project_root, meta_store)


@pytest.mark.asyncio
async def test_build_options_includes_sandbox_settings(
    session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proj_dir = session_manager.project_root / "projects" / "test_proj"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk", "ARK_API_KEY": ""}

    monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)

    opts = await session_manager._build_options("test_proj")

    assert opts.sandbox is not None
    assert opts.sandbox.get("enabled") is True
    assert opts.sandbox.get("autoAllowBashIfSandboxed") is True
    # 非 Docker 默认 weakerNested=False
    assert opts.sandbox.get("enableWeakerNestedSandbox") is False
    # 网络白名单仅保留 Anthropic + dev 常用域；provider 域名走 in-process MCP tool，不再放行
    # 用 any(==) 显式列表成员比较，避免 CodeQL py/incomplete-url-substring-sanitization 误报
    allowed_domains = opts.sandbox.get("network", {}).get("allowedDomains", [])
    assert any(d == "anthropic.com" for d in allowed_domains)
    assert any(d == "example.com" for d in allowed_domains)
    # provider 域名已下线
    assert not any(d == "*.googleapis.com" for d in allowed_domains)
    assert not any(d == "*.volces.com" for d in allowed_domains)
    # filesystem.denyRead 注入：sandbox profile 内核级文件读拒绝
    deny_read = opts.sandbox.get("filesystem", {}).get("denyRead", [])
    assert isinstance(deny_read, list)


def test_session_manager_wires_env_resolved_roots_into_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SessionManager 负责 env 解析（ARCREEL_LOG_DIR / ARCREEL_PROFILE_DIR /
    projects_root 参数），把 resolve 后的根路径喂给 AgentAccessPolicy——用户把
    日志/数据/profile 目录搬到任意位置（含 repo 外）时，deny 必须跟着指过去。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    external_logs = tmp_path / "external" / "arcreel_logs"
    external_logs.mkdir(parents=True)
    (external_logs / "arcreel.log").write_text("secret\n", encoding="utf-8")
    external_data = tmp_path / "external_data" / "projects"
    external_data.mkdir(parents=True)
    external_profile = tmp_path / "external_profile"
    (external_profile / ".claude").mkdir(parents=True)

    monkeypatch.setenv("ARCREEL_LOG_DIR", str(external_logs))
    monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(external_profile))

    sm = SessionManager(repo, SessionMetaStore(), projects_root=external_data)
    policy = sm.access_policy

    assert policy.log_dir == external_logs.resolve()
    assert policy.agent_profile_root == external_profile.resolve()
    assert policy.projects_root == external_data.resolve()
    assert policy.project_root == repo.resolve()
    # 端到端：env 覆盖后的真实位置被认定为敏感
    assert policy.is_sensitive_path((external_logs / "arcreel.log").resolve())
    assert policy.is_sensitive_path((external_profile / ".claude" / "settings.json").resolve())
    # repo/logs 在此场景下不应被默认 deny（避免误覆盖）
    assert not policy.is_sensitive_path((repo / "logs" / "anything.txt").resolve())


def test_configure_sandbox_runtime_swaps_policy(tmp_path: Path) -> None:
    """startup 期注入平台事实：整体换新 policy 而非戳改私有属性，
    后续 settings 编译 / hook 裁决立即消费新规则。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=True)
    cwd = sm.project_root / "projects" / "demo"
    assert sm.access_policy.build_sandbox_settings(cwd)["enabled"] is True

    sm.configure_sandbox_runtime(in_docker=True, sandbox_enabled=False)

    assert sm.access_policy.sandbox_enabled is False
    assert sm.access_policy.in_docker is True
    assert sm.access_policy.build_sandbox_settings(cwd) == {"enabled": False}


@pytest.mark.asyncio
async def test_bash_env_scrub_hook_wraps_command_with_env_unset(session_manager: SessionManager) -> None:
    """hook 把 policy 的包装结果塞进 ``updatedInput.command``，且不返回
    permissionDecision——PreToolUse hook 是权限链第 1 步，allow 会短路后续所有步骤。
    包装内容（``env -u <keys> sh -c '<orig>'``）由 test_agent_access_policy.py 的
    ``test_wrap_bash_command_unsets_provider_keys`` 覆盖，此处只验接线。"""
    result = await session_manager._options_assembler._bash_env_scrub_hook(
        {"tool_name": "Bash", "tool_input": {"command": "env | grep ANTHROPIC"}},
        None,
        None,
    )

    out = result.get("hookSpecificOutput")
    assert out is not None
    assert out["hookEventName"] == "PreToolUse"
    # 不携带权限决策，让权限链继续走到 allow 规则 / can_use_tool
    assert "permissionDecision" not in out
    # policy 的包装结果被穿进 updatedInput.command（包装形态由 policy 测试断言）
    assert out["updatedInput"]["command"].startswith("env -u ")


@pytest.mark.asyncio
async def test_bash_env_scrub_hook_skips_wrap_when_sandbox_disabled(tmp_path: Path) -> None:
    """Windows 回退：``env -u``/``sh -c`` 是 POSIX 机制，原生 Windows 不可执行；
    hook 不包装也不给权限决策，原始命令落到 _can_use_tool 做白名单匹配
    （包装后命令以 ``env -u`` 开头，会让白名单永远匹配不上）。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    result = await sm._options_assembler._bash_env_scrub_hook(
        {"tool_name": "Bash", "tool_input": {"command": "ffmpeg -i in.mp4 out.mp4"}},
        None,
        None,
    )
    assert result == {"continue_": True}


@pytest.mark.asyncio
async def test_bash_env_scrub_hook_passthrough_when_no_command(session_manager: SessionManager) -> None:
    """空 command 时直接放行，不做包装。"""
    result = await session_manager._options_assembler._bash_env_scrub_hook(
        {"tool_name": "Bash", "tool_input": {}},
        None,
        None,
    )
    assert result == {"continue_": True}


# ============================================================
# Windows 沙箱回退：sandbox_enabled=False 分支（权限链接线）
# ============================================================


def _make_session_manager(tmp_path: Path, *, sandbox_enabled: bool) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir(exist_ok=True)
    (project_root / "projects").mkdir(exist_ok=True)
    return SessionManager(
        project_root,
        SessionMetaStore(),
        sandbox_enabled=sandbox_enabled,
    )


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

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    monkeypatch.setattr("server.agent_runtime.options_assembler.load_provider_env_overrides", fake_env)
    opts = await sm._build_options("test_proj")

    for tool in AgentAccessPolicy.BASH_TOOLS:
        assert (tool in opts.allowed_tools) is sandbox_enabled
    assert "Read" in opts.allowed_tools
    assert "Skill" in opts.allowed_tools
    if not sandbox_enabled:
        assert opts.sandbox == {"enabled": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command,expected",
    [
        (
            "python .claude/skills/compose-video/scripts/compose_video.py scripts/episode_1.json",
            "PermissionResultAllow",
        ),
        ("ffmpeg -i in.mp4 out.mp4", "PermissionResultAllow"),
        ("ffprobe in.mp4", "PermissionResultAllow"),
        # `..` 在文件名内部（非路径段）不触发穿越拦截，合法命令照常放行
        ("ffmpeg -i my..clip.mp4 out.mp4", "PermissionResultAllow"),
        # 归一化容错：带引号的脚本路径、Windows 反斜杠分隔符的合法命令不误拒
        (
            'python ".claude/skills/compose-video/scripts/compose_video.py" scripts/ep.json',
            "PermissionResultAllow",
        ),
        (
            "python .claude\\skills\\compose-video\\scripts\\compose_video.py scripts/ep.json",
            "PermissionResultAllow",
        ),
        ("cat /etc/passwd", "PermissionResultDeny"),
        ("ls -la", "PermissionResultDeny"),
    ],
)
async def test_windows_bash_whitelist_matches_main_behavior(tmp_path: Path, command: str, expected: str) -> None:
    """sandbox 关闭时白名单 prefix 放行，其余拒；deny 文案派生自 WINDOWS_BASH_PREFIX_WHITELIST。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    callback = await sm._build_can_use_tool_callback("test_sid", [None])
    result = await callback("Bash", {"command": command}, None)
    assert type(result).__name__ == expected
    if expected == "PermissionResultDeny":
        assert "Bash 白名单" in result.message
        # deny 文案必须包含所有白名单 prefix（单一真相源）
        for prefix in AgentAccessPolicy.WINDOWS_BASH_PREFIX_WHITELIST:
            assert prefix in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        # 白名单前缀 + metachar 链：尾部命令在 Windows 上无 sandbox denyWrite
        # 兜底，可直写 protected JSON，必须整串拒
        'python .claude/skills/manage-project/scripts/peek_split_point.py; python -c "evil"',
        "ffmpeg -i in.mp4 out.mp4 && python -c \"open('project.json','w')\"",
        "ffprobe in.mp4 | tee scripts/episode_1.json",
        "ffmpeg -i in.mp4 $(evil) out.mp4",
        "ffmpeg -i in.mp4 `evil` out.mp4",
        "ffmpeg -i in.mp4 -f json > scripts/episode_1.json",
        "ffprobe < secret.txt",
        "ffmpeg -i in.mp4 out.mp4\npython -c evil",
        # 命令名前缀碰撞：ffmpegX 以 ffmpeg 开头但不是 ffmpeg
        "ffmpegX --evil",
        "ffprobe2 in.mp4",
        # 路径穿越：满足 python .claude/skills/ 前缀且不含 metachar，但 .. 逃出
        # skills 目录跑任意脚本——Windows 回退无 sandbox 兜底，必须拒
        "python .claude/skills/../../../tmp/evil.py",
        "python .claude/skills/../../arcreel_secrets_dumper.py",
        "ffmpeg -i ../../other_project/secret.mp4 out.mp4",
        # 路径穿越混淆绕过：shell 会把 ".." / .\. 还原成 ..，归一化后必须拒
        'python .claude/skills/dir/".."/".."/evil.py',
        "python .claude/skills/dir/'..'/'..'/evil.py",
        "python .claude/skills/dir/.\\./.\\./evil.py",
        'ffmpeg -i ".."/".."/secret.mp4 out.mp4',
        # Windows 反斜杠分隔符下的 .. 穿越同样要拒（归一化后 ../ 命中）
        "python .claude\\skills\\..\\..\\evil.py",
        # python 入口必须是 <skill>/scripts/<script>.py：skills 目录下任意其它
        # 文件（无 scripts/ 段、非 .py、或直接挂在 skill 根）一律不放行
        "python .claude/skills/evil.py",
        "python .claude/skills/compose-video/compose_video.py scripts/ep.json",
        "python .claude/skills/compose-video/scripts/data.json",
        "python .claude/skills/compose-video/scripts/sub/run.py",
    ],
)
async def test_windows_bash_whitelist_blocks_metachar_chains(tmp_path: Path, command: str) -> None:
    """白名单前缀 + shell metachar（; && | $() ` 重定向 换行）的复合命令必须拒；
    命令名按 token 边界匹配，挡 ffmpegX 这类前缀碰撞；.. 路径穿越整串拒。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    callback = await sm._build_can_use_tool_callback("test_sid", [None])
    result = await callback("Bash", {"command": command}, None)
    assert type(result).__name__ == "PermissionResultDeny"
    assert "Bash 白名单" in result.message


@pytest.mark.asyncio
async def test_windows_bash_management_tools_allowed(tmp_path: Path) -> None:
    """BashOutput / KillBash 是 Bash 管理工具，回退模式下直接放行。"""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    callback = await sm._build_can_use_tool_callback("test_sid", [None])
    for tool in ("BashOutput", "KillBash"):
        result = await callback(tool, {}, None)
        assert type(result).__name__ == "PermissionResultAllow"
