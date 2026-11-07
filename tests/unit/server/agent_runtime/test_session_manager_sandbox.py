"""SessionManager sandbox wiring tests: option assembly, hook return shape and permission-chain order.

Pure rule assertions (path decisions / settings compilation / allowlist predicates / secret stripping) moved to tests/agent_runtime/test_agent_access_policy.py; this file tests only SDK-wrapper wiring."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from lib.agent.agent_memory_paths import project_memory_dir
from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.agent_runtime.session_manager import ManagedSession, SessionManager
from server.agent_runtime.session_store import SessionMetaStore
from tests.fakes import use_provider_env

#: Current user ID supplied per call (production obtains it from SessionManager CurrentUser context).
_USER_ID = "default"


@pytest.fixture
def fs_session_manager(tmp_path: Path) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "projects").mkdir()
    meta_store = SessionMetaStore()
    return SessionManager(project_root, meta_store)


@pytest.mark.asyncio
async def test_build_options_includes_sandbox_settings(
    fs_session_manager: SessionManager, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    proj_dir = fs_session_manager.layout.projects_dir / "test_proj"
    proj_dir.mkdir(parents=True)
    (proj_dir / "project.json").write_text('{"title": "t"}', encoding="utf-8")

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk", "ARK_API_KEY": ""}

    use_provider_env(monkeypatch, fake_env)

    opts = await fs_session_manager._build_options("test_proj")

    assert opts.sandbox is not None
    assert opts.sandbox.get("enabled") is True
    assert opts.sandbox.get("autoAllowBashIfSandboxed") is True
    # Outside Docker, weakerNested defaults to False.
    assert opts.sandbox.get("enableWeakerNestedSandbox") is False
    # Explicitly allow all outbound domains: omitting network gives zero preapprovals, so unattended sessions deny new domains.
    # Loopback has its own switch: skill scripts must use it to call the same-origin ArcReel API;
    # allowedDomains covers neither * nor 127.0.0.1 as loopback permission.
    assert opts.sandbox["network"] == {"allowedDomains": ["*"], "allowLocalBinding": True}
    # Unsandboxed fallback remains forbidden and file boundaries remain intact when network access opens.
    assert opts.sandbox.get("allowUnsandboxedCommands") is False
    # filesystem.denyRead injection: kernel-level file-read denial in the sandbox profile.
    deny_read = opts.sandbox.get("filesystem", {}).get("denyRead", [])
    assert isinstance(deny_read, list)
    assert str(proj_dir / "project.json") in opts.sandbox["filesystem"]["denyWrite"]


def test_session_manager_wires_env_resolved_roots_into_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """SessionManager resolves environment (ARCREEL_PROFILE_DIR / data_root arguments) and supplies resolved roots to AgentAccessPolicy. Denial must follow relocated data/profile directories, even outside the repository. Logs are at ``<数据根>/logs``; ARCREEL_LOG_DIR does not change denial scope."""
    repo = tmp_path / "repo"
    repo.mkdir()
    external_data = tmp_path / "external_data" / "projects"
    data_logs = external_data / "logs"
    data_logs.mkdir(parents=True)
    (data_logs / "arcreel.log").write_text("secret\n", encoding="utf-8")
    stale_logs = tmp_path / "stale_logs"
    stale_logs.mkdir()
    external_profile = tmp_path / "external_profile"
    (external_profile / ".claude").mkdir(parents=True)

    monkeypatch.setenv("ARCREEL_LOG_DIR", str(stale_logs))
    monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(external_profile))

    sm = SessionManager(repo, SessionMetaStore(), data_root=external_data, sandbox_enabled=True)
    policy = sm.access_policy

    assert policy.agent_profile_root == external_profile.resolve()
    assert policy.data_root == external_data.resolve()
    assert policy.project_root == repo.resolve()
    # End-to-end: data-root logs are unreadable to built-in read tools and Bash (kernel denyRead).
    cwd = sm.layout.projects_dir / "demo"
    allowed, _ = policy.check_path_access(str(data_logs / "arcreel.log"), "Read", cwd, user_id=_USER_ID)
    assert not allowed
    deny_read = policy.build_sandbox_settings(cwd)["filesystem"]["denyRead"]
    assert str(data_logs.resolve()) in deny_read
    assert policy.is_sensitive_path((external_profile / ".claude" / "settings.json").resolve())
    # Neither ARCREEL_LOG_DIR nor repo/logs is the log location; exclude both from denyRead.
    assert str(stale_logs.resolve()) not in deny_read
    assert str((repo / "logs").resolve()) not in deny_read


def test_configure_sandbox_runtime_swaps_policy(tmp_path: Path) -> None:
    """Inject platform facts during startup by replacing the entire policy instead of mutating private attributes; subsequent settings compilation and hook decisions immediately use the new rules."""
    sm = _make_session_manager(tmp_path, sandbox_enabled=True)
    cwd = sm.layout.projects_dir / "demo"
    assert sm.access_policy.build_sandbox_settings(cwd)["enabled"] is True

    sm.configure_sandbox_runtime(in_docker=True, sandbox_enabled=False)

    assert sm.access_policy.sandbox_enabled is False
    assert sm.access_policy.in_docker is True
    assert sm.access_policy.build_sandbox_settings(cwd) == {"enabled": False}


@pytest.mark.asyncio
async def test_bash_env_scrub_hook_wraps_command_with_env_unset(fs_session_manager: SessionManager) -> None:
    """The hook puts the policy wrapper into ``updatedInput.command`` without permissionDecision: PreToolUse is permission-chain step 1 and allow would short-circuit all later steps. ``test_wrap_bash_command_unsets_provider_keys`` in test_agent_access_policy.py covers wrapper contents (``env -u <keys> sh -c '<orig>'``); only verify wiring here."""
    result = await fs_session_manager._options_assembler._bash_env_scrub_hook(
        {"tool_name": "Bash", "tool_input": {"command": "env | grep ANTHROPIC"}},
        None,
        None,
    )

    out = result.get("hookSpecificOutput")
    assert out is not None
    assert out["hookEventName"] == "PreToolUse"
    # Return no permission decision so the permission chain continues to allow rules / can_use_tool.
    assert "permissionDecision" not in out
    # Forward the policy wrapper into updatedInput.command (policy tests assert its exact shape).
    assert out["updatedInput"]["command"].startswith("env -u ")


@pytest.mark.asyncio
async def test_bash_env_scrub_hook_skips_wrap_when_sandbox_disabled(tmp_path: Path) -> None:
    """Windows fallback: ``env -u``/``sh -c`` are POSIX mechanisms unavailable on native Windows. The hook neither wraps nor decides permissions; _can_use_tool matches the raw command against its allowlist. Wrapping would start with ``env -u`` and never match."""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    result = await sm._options_assembler._bash_env_scrub_hook(
        {"tool_name": "Bash", "tool_input": {"command": "python .claude/skills/x/scripts/run.py"}},
        None,
        None,
    )
    assert result == {"continue_": True}


@pytest.mark.asyncio
async def test_bash_env_scrub_hook_passthrough_when_no_command(fs_session_manager: SessionManager) -> None:
    """Allow empty commands directly without wrapping."""
    result = await fs_session_manager._options_assembler._bash_env_scrub_hook(
        {"tool_name": "Bash", "tool_input": {}},
        None,
        None,
    )
    assert result == {"continue_": True}


# ============================================================
# Windows sandbox fallback: sandbox_enabled=False branch (permission-chain wiring).
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
    """Strip Bash/BashOutput/KillBash when sandbox is disabled; retain them when enabled."""
    sm = _make_session_manager(tmp_path, sandbox_enabled=sandbox_enabled)
    proj_dir = sm.layout.projects_dir / "test_proj"
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "project.json").write_text('{"title":"t"}', encoding="utf-8")

    async def fake_env():
        return {"ANTHROPIC_API_KEY": "sk"}

    use_provider_env(monkeypatch, fake_env)
    opts = await sm._build_options("test_proj")

    for tool in AgentAccessPolicy.BASH_TOOLS:
        assert (tool in opts.allowed_tools) is sandbox_enabled
    assert "Read" in opts.allowed_tools
    assert "Skill" in opts.allowed_tools
    assert "WebFetch" in opts.allowed_tools
    if not sandbox_enabled:
        assert opts.sandbox == {"enabled": False}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        (
            "python .claude/skills/adapt-custom-endpoint/scripts/custom_endpoint.py validate",
            "PermissionResultAllow",
        ),
        # `..` inside a filename, rather than a path segment, does not trigger traversal blocking; allow legitimate commands.
        ("python .claude/skills/adapt-custom-endpoint/scripts/my..script.py validate", "PermissionResultAllow"),
        # Normalization tolerance: do not reject legitimate quoted script paths or Windows backslash separators.
        (
            'python ".claude/skills/adapt-custom-endpoint/scripts/custom_endpoint.py" validate',
            "PermissionResultAllow",
        ),
        (
            "python .claude\\skills\\adapt-custom-endpoint\\scripts\\custom_endpoint.py validate",
            "PermissionResultAllow",
        ),
        ("cat /etc/passwd", "PermissionResultDeny"),
        ("ls -la", "PermissionResultDeny"),
        # ffmpeg / ffprobe are not allowlisted: Agents cannot run them through Bash; use the toolset for media processing.
        ("ffmpeg -i in.mp4 out.mp4", "PermissionResultDeny"),
        ("ffprobe in.mp4", "PermissionResultDeny"),
        ("ffmpeg", "PermissionResultDeny"),
    ],
)
async def test_windows_bash_whitelist_matches_main_behavior(tmp_path: Path, command: str, expected: str) -> None:
    """With sandbox disabled, allow allowlisted prefixes and deny others; derive denial wording from WINDOWS_BASH_PREFIX_WHITELIST."""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    callback = await sm._build_can_use_tool_callback("test_sid", [None])
    result = await callback("Bash", {"command": command}, None)
    assert type(result).__name__ == expected
    if expected == "PermissionResultDeny":
        assert "Bash 白名单" in result.message
        # Denial wording must include every allowlist prefix (single source of truth).
        for prefix in AgentAccessPolicy.WINDOWS_BASH_PREFIX_WHITELIST:
            assert prefix in result.message


def test_windows_bash_whitelist_has_no_media_tool_prefixes() -> None:
    assert AgentAccessPolicy.WINDOWS_BASH_PREFIX_WHITELIST == ("python .claude/skills/",)
    deny_message = AgentAccessPolicy.format_bash_whitelist_deny_message("ffmpeg -i in.mp4 out.mp4")
    assert "  - ffmpeg" not in deny_message
    assert "  - ffprobe" not in deny_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "command",
    [
        # Allowlisted prefix plus metacharacter chain: Windows has no sandbox denyWrite fallback for the trailing command,
        # which could directly write protected JSON; reject the whole string.
        'python .claude/skills/manage-project/scripts/peek_split_point.py; python -c "evil"',
        "python .claude/skills/x/scripts/run.py && python -c \"open('project.json','w')\"",
        "python .claude/skills/x/scripts/run.py | tee scripts/episode_1.json",
        "python .claude/skills/x/scripts/run.py $(evil)",
        "python .claude/skills/x/scripts/run.py `evil`",
        "python .claude/skills/x/scripts/run.py > scripts/episode_1.json",
        "python .claude/skills/x/scripts/run.py < secret.txt",
        "python .claude/skills/x/scripts/run.py\npython -c evil",
        # Command-name prefix collision: python3 is not python.
        "python3 .claude/skills/x/scripts/run.py",
        # Path traversal: matches python .claude/skills/ with no metacharacters, but .. escapes
        # the skills directory to run arbitrary scripts. Windows fallback has no sandbox protection, so reject it.
        "python .claude/skills/../../../tmp/evil.py",
        "python .claude/skills/../../arcreel_secrets_dumper.py",
        "python .claude/skills/x/scripts/run.py ../../other_project/secret.json",
        # Obfuscated traversal: shells restore ".." / .\. to ..; reject after normalization.
        'python .claude/skills/dir/".."/".."/evil.py',
        "python .claude/skills/dir/'..'/'..'/evil.py",
        "python .claude/skills/dir/.\\./.\\./evil.py",
        'python .claude/skills/x/scripts/run.py ".."/".."/secret.json',
        # Likewise reject .. traversal with Windows backslash separators (normalized ../ matches).
        "python .claude\\skills\\..\\..\\evil.py",
        # Python entry points must be <skill>/scripts/<script>.py; reject all other files under skills,
        # including paths lacking scripts/, non-.py files, or scripts directly under the skill root.
        "python .claude/skills/evil.py",
        "python .claude/skills/adapt-custom-endpoint/custom_endpoint.py validate",
        "python .claude/skills/adapt-custom-endpoint/scripts/data.json",
        "python .claude/skills/adapt-custom-endpoint/scripts/sub/run.py",
    ],
)
async def test_windows_bash_whitelist_blocks_metachar_chains(tmp_path: Path, command: str) -> None:
    """Reject compound commands combining allowlisted prefixes with shell metacharacters (; && | $() ` redirection newline). Match command names at token boundaries to block collisions such as python3; reject entire strings containing .. traversal."""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    callback = await sm._build_can_use_tool_callback("test_sid", [None])
    result = await callback("Bash", {"command": command}, None)
    assert type(result).__name__ == "PermissionResultDeny"
    assert "Bash 白名单" in result.message


@pytest.mark.asyncio
async def test_windows_bash_management_tools_allowed(tmp_path: Path) -> None:
    """BashOutput / KillBash manage Bash and are directly allowed in fallback mode."""
    sm = _make_session_manager(tmp_path, sandbox_enabled=False)
    callback = await sm._build_can_use_tool_callback("test_sid", [None])
    for tool in ("BashOutput", "KillBash"):
        result = await callback(tool, {}, None)
        assert type(result).__name__ == "PermissionResultAllow"


def _init_message(auto_dir: str | None) -> dict[str, object]:
    """Minimal CLI init system-message shape; ``memory_paths`` belongs in SystemMessage.data."""
    data: dict[str, object] = {"subtype": "init", "session_id": "sdk-1"}
    if auto_dir is not None:
        data["memory_paths"] = {"auto": auto_dir}
    return {"type": "system", "subtype": "init", "data": data}


def _managed_for(project_name: str) -> ManagedSession:
    return ManagedSession(session_id="sess-1", actor=None, status="running", project_name=project_name)


def test_init_auto_memory_mismatch_logs_error_and_keeps_session(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """If CLI init reports unexpected auto-memory directories, log an error without rewriting the message or preventing session startup."""
    sm = _make_session_manager(tmp_path, sandbox_enabled=True)
    proj_dir = sm.layout.projects_dir / "demo"
    proj_dir.mkdir(parents=True, exist_ok=True)
    managed = _managed_for("demo")
    msg = _init_message(str(tmp_path / "elsewhere" / "memory"))

    with caplog.at_level(logging.ERROR, logger="server.agent_runtime.session_manager"):
        sm._handle_special_message(managed, msg)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert [getattr(r, "expected_auto_memory_dir", None) for r in errors] == [str(project_memory_dir(proj_dir))]
    assert managed.status == "running"
    assert msg == _init_message(str(tmp_path / "elsewhere" / "memory"))


@pytest.mark.parametrize(
    "auto_dir_factory",
    [
        pytest.param(lambda proj_dir: str(project_memory_dir(proj_dir)), id="exact"),
        # CLI 2.1.259 reports trailing slashes; compare resolved paths, not strings.
        pytest.param(lambda proj_dir: f"{project_memory_dir(proj_dir)}/", id="trailing-slash"),
        pytest.param(lambda _proj_dir: None, id="field-absent"),
    ],
)
def test_init_auto_memory_match_logs_nothing(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, auto_dir_factory
) -> None:
    """No warning for matching paths or absent CLI memory_paths (older versions / auto memory disabled)."""
    sm = _make_session_manager(tmp_path, sandbox_enabled=True)
    proj_dir = sm.layout.projects_dir / "demo"
    proj_dir.mkdir(parents=True, exist_ok=True)

    with caplog.at_level(logging.ERROR, logger="server.agent_runtime.session_manager"):
        sm._handle_special_message(_managed_for("demo"), _init_message(auto_dir_factory(proj_dir)))

    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == []
