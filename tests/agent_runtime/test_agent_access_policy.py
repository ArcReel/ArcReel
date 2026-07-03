"""AgentAccessPolicy 纯规则测试：构造参数喂入，断言 allow/deny，无 env/私有方法 monkeypatch。

路径裁决四规则：敏感文件拒 + 跨项目读拒 + cwd 外写拒 + 代码扩展名拒。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from server.agent_runtime.agent_access_policy import AgentAccessPolicy


def _make_policy(tmp_path: Path, **overrides: object) -> AgentAccessPolicy:
    """以 tmp 根路径纯构造 policy：repo 布局与旧 SessionManager fixture 一致。"""
    project_root = (tmp_path / "repo").resolve()
    kwargs: dict[str, object] = {
        "project_root": project_root,
        "projects_root": project_root / "projects",
        "agent_profile_root": (tmp_path / "agent_runtime_profile").resolve(),
        "log_dir": project_root / "logs",
    }
    kwargs.update(overrides)
    return AgentAccessPolicy(**kwargs)  # type: ignore[arg-type]


@pytest.fixture
def policy(tmp_path: Path) -> AgentAccessPolicy:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "projects").mkdir()
    (project_root / "projects" / "selfproj").mkdir()
    (project_root / "projects" / "other").mkdir()
    (project_root / "lib").mkdir()
    return _make_policy(tmp_path)


def _cwd(policy: AgentAccessPolicy) -> Path:
    return policy.projects_root / "selfproj"


# ============================================================
# 构造纯度：假根路径可构造，不 import SDK
# ============================================================


def test_pure_construction_with_fake_roots() -> None:
    """假根路径（磁盘上不存在）+ sandbox_enabled 即可纯构造，裁决照常工作。"""
    fake = Path("/nonexistent/fake-root")
    policy = AgentAccessPolicy(
        project_root=fake / "repo",
        projects_root=fake / "repo" / "projects",
        agent_profile_root=fake / "profile",
        log_dir=fake / "logs",
        sandbox_enabled=False,
        claude_projects_dir=fake / "claude" / "projects",
    )
    assert policy.sandbox_enabled is False
    cwd = fake / "repo" / "projects" / "demo"
    allowed, _ = policy.check_path_access(str(cwd / "data.json"), "Read", cwd)
    assert allowed
    allowed, reason = policy.check_path_access(str(fake / "repo" / ".env"), "Read", cwd)
    assert not allowed
    assert reason and "敏感文件" in reason


def test_policy_module_does_not_import_sdk_types() -> None:
    """规则真相源不 import SDK 类型——SDK 封皮（权限结果类型、hook 签名）留在 adapter。"""
    module = sys.modules[AgentAccessPolicy.__module__]
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "claude_agent_sdk" not in source


# ============================================================
# 路径读写裁决（原 test_path_isolation_hook.py 断言搬家）
# ============================================================


def test_read_cwd_internal_passes(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    allowed, _ = policy.check_path_access(str(cwd / "data.json"), "Read", cwd)
    assert allowed


def test_read_other_project_denied(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(policy.projects_root / "other" / "x.json"), "Read", cwd)
    assert not allowed
    assert "跨项目" in reason or "项目" in reason


def test_read_lib_passes(policy: AgentAccessPolicy) -> None:
    """cwd 外的非 projects 路径允许读（用于 agent 查 docs/lib 等参考资料）。"""
    cwd = _cwd(policy)
    allowed, _ = policy.check_path_access(str(policy.project_root / "lib" / "foo.py"), "Read", cwd)
    assert allowed


def test_write_cwd_external_denied(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(policy.project_root / "lib" / "foo.json"), "Write", cwd)
    assert not allowed
    assert "项目目录之外" in reason or "cwd" in reason or "项目" in reason


def test_write_cwd_internal_code_ext_denied(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    for ext in (".py", ".js", ".ts", ".tsx", ".sh", ".yaml", ".yml", ".toml"):
        allowed, reason = policy.check_path_access(str(cwd / f"test{ext}"), "Write", cwd)
        assert not allowed, f"扩展名 {ext} 应被拒"
        assert "代码" in reason or "扩展名" in reason


def test_write_cwd_internal_data_ext_allowed(policy: AgentAccessPolicy) -> None:
    cwd = _cwd(policy)
    for ext in (".json", ".md", ".txt", ".html", ".csv"):
        allowed, _ = policy.check_path_access(str(cwd / f"data{ext}"), "Write", cwd)
        assert allowed, f"扩展名 {ext} 应允许"


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", ["scripts/episode_1.json", "scripts/episode_10.json", "project.json"])
def test_write_protected_project_json_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """scripts/*.json 与 project.json 不可用 Write/Edit 直改，报错指向 MCP 工具。"""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason and "patch_episode_script" in reason or "patch_project" in (reason or "")


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_scripts_dir_itself_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """`scripts/` 目录路径本身（不带 trailing sep）也该拒：defense-in-depth，
    不依赖 OS 兜底 agent 把目录名当文件路径的 typo。"""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / "scripts"), tool, cwd)
    assert not allowed
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    ["scripts/episode_1.bak", "scripts/notes.md", "scripts/.tmp", "scripts/subdir/anything.txt"],
)
def test_write_protected_scripts_non_json_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """`scripts/` 下任意文件类型都该拒（不只 .json）：sandbox denyWrite 把整个 scripts/ 列入
    内核级 deny，hook 层须保持一致，避免 agent 用 Write 污染剧本目录。"""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    ["PROJECT.JSON", "Project.Json", "scripts/EPISODE_1.JSON", "Scripts/episode_1.json"],
)
def test_write_protected_case_variants_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """大小写变体（PROJECT.JSON / Scripts/x.json）在 Windows NTFS / macOS APFS 默认卷
    上指向同一物理文件，Path 字符串比较 case-sensitive 会漏判——`_is_protected_project_json`
    用 casefold 比较后这类变体也应被拒，否则 agent 可改大小写绕过收口。"""
    cwd = _cwd(policy)
    allowed, reason = policy.check_path_access(str(cwd / relative), tool, cwd)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_via_symlink_project_json_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """`project.json` 本身被做成项目内 symlink（指向另一个项目内文件）时，仍须拒——
    防止"把入口换成 symlink"绕过 protected 区判定。仅靠 resolve 后路径比较会失配。"""
    cwd = _cwd(policy)
    real = cwd / "other.json"
    real.write_text("{}", encoding="utf-8")
    link = cwd / "project.json"
    link.symlink_to(real)
    allowed, reason = policy.check_path_access(str(link), tool, cwd)
    assert not allowed, "symlink 形态的 project.json 写入应被拒"
    assert reason and ("patch_project" in reason or "patch_episode_script" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_via_symlink_scripts_dir_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """`scripts/` 整个目录被做成项目内 symlink 时，对其下 .json 的写入仍须拒。"""
    cwd = _cwd(policy)
    real_dir = cwd / "data"
    real_dir.mkdir()
    link_dir = cwd / "scripts"
    link_dir.symlink_to(real_dir)
    target = link_dir / "episode_1.json"
    allowed, reason = policy.check_path_access(str(target), tool, cwd)
    assert not allowed, "symlink 形态的 scripts/ 下 .json 写入应被拒"
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_with_symlinked_project_cwd_denied(
    policy: AgentAccessPolicy, tmp_path: Path, tool: str
) -> None:
    """project_cwd 本身是个 symlink 指向真实项目目录时(macOS /var↔/private/var、Linux
    symlinked 项目根),`_is_protected_project_json` 要把 base 也按 resolve 一次再拼接 protected
    路径,避免 resolved target 与 raw base 字符串不等 → bypass。"""
    # 真实项目目录在 tmp 根的另一处,通过 symlink 暴露
    real_root = tmp_path / "real_data"
    (real_root / "projects" / "selfproj").mkdir(parents=True)
    link_cwd = policy.projects_root / "selfproj_link"
    link_cwd.symlink_to(real_root / "projects" / "selfproj")

    # caller 把 symlinked cwd 传入,check_path_access 内 logical.resolve() 会展开 symlink,
    # 然后 _check_write_access 把 resolved target 与原始 link_cwd 比较——若不把 base 也
    # resolve,就会因为字符串不等漏判。
    allowed, reason = policy.check_path_access(str(link_cwd / "project.json"), tool, link_cwd)
    assert not allowed, "symlinked project_cwd 下 project.json 写入应被拒"
    assert reason and ("patch_project" in reason or "patch_episode_script" in reason)

    allowed, reason = policy.check_path_access(str(link_cwd / "scripts" / "episode_1.json"), tool, link_cwd)
    assert not allowed, "symlinked project_cwd 下 scripts/*.json 写入应被拒"
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


def test_protected_json_predicate_normalizes_nfd_and_case() -> None:
    """NFC/NFD 与大小写混合形式都须命中：macOS HFS+ 按 NFD 存储文件名，resolve
    返回的 target 与 NFC 形式的 base 即使 casefold 后仍是不同字符串——受保护
    比对须先做 NFC 归一化，再做大小写不敏感比较。"""
    base_nfc = Path("/data/projects/café")  # café（NFC 单码位）
    target_nfd = Path("/data/projects/café/project.json")  # café（NFD 组合字符）
    assert AgentAccessPolicy._is_protected_project_json(target_nfd, [base_nfc])

    # 大小写变体 + NFD 叠加
    target_mixed = Path("/data/projects/CAFÉ/SCRIPTS/EPISODE_1.JSON")
    assert AgentAccessPolicy._is_protected_project_json(target_mixed, [base_nfc])

    # 反向：base 是 NFD（HFS+ 磁盘形式）、target 是 NFC（用户输入形式）
    base_nfd = Path("/data/projects/café")
    target_nfc = Path("/data/projects/café/scripts/episode_1.json")
    assert AgentAccessPolicy._is_protected_project_json(target_nfc, [base_nfd])

    # 归一化不引入 over-match：其他项目路径不受影响
    other = Path("/data/projects/cafe_other/project.json")
    assert not AgentAccessPolicy._is_protected_project_json(other, [base_nfc])


def test_normalize_path_for_protected_compare_strips_windows_extended_prefix() -> None:
    """Windows ``\\\\?\\`` 扩展长度前缀（resolve 在长路径/UNC 下返回）与常规形态
    须归一化为同一比较键，否则 bases 混入两种形式时 startswith 失配。
    helper 级单测；实机 Windows 端到端验证另行跟踪。"""
    norm = AgentAccessPolicy._normalize_path_for_protected_compare
    assert norm("\\\\?\\C:\\data\\projects\\demo") == norm("C:\\data\\projects\\demo")
    assert norm("\\\\?\\UNC\\server\\share\\proj") == norm("\\\\server\\share\\proj")
    # 常规路径不受影响
    assert norm("/data/projects/demo") == norm("/data/projects/demo")


def test_write_drafts_and_source_still_allowed(policy: AgentAccessPolicy) -> None:
    """合法的草稿/源文件写入不受影响（drafts/*.md、source/*.txt、scripts 外的 .json）。"""
    cwd = _cwd(policy)
    for relative in ("drafts/episode_1/step1_segments.md", "source/episode_1.txt", "config_data.json"):
        allowed, _ = policy.check_path_access(str(cwd / relative), "Write", cwd)
        assert allowed, f"{relative} 应允许"


@pytest.mark.parametrize(
    "relative",
    [
        ".env",
        ".env.local",
        ".env.production",
        "vertex_keys/key.json",
        "vertex_keys/nested/secret.json",
        "projects/.system_config.json",
        "projects/.system_config.json.bak",
    ],
)
@pytest.mark.parametrize("tool", ["Read", "Write", "Edit", "Glob", "Grep"])
def test_sensitive_file_denied(policy: AgentAccessPolicy, tool: str, relative: str) -> None:
    """敏感文件无论 Read 还是 Write 一律拒，且报错信息包含"敏感文件"。"""
    cwd = _cwd(policy)
    # 文件实际存在与否不影响 deny 判断（resolve() 对不存在路径仍返回绝对路径）
    target = policy.project_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    allowed, reason = policy.check_path_access(str(target), tool, cwd)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason and "敏感文件" in reason


@pytest.mark.parametrize("tool", ["Read", "Write", "Edit", "Glob", "Grep"])
def test_agent_profile_settings_denied(policy: AgentAccessPolicy, tool: str) -> None:
    """敏感判断对准构造时传入的 agent_profile_root，而不是源码根的硬编码路径。"""
    cwd = _cwd(policy)
    target = policy.agent_profile_root / ".claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    allowed, reason = policy.check_path_access(str(target), tool, cwd)
    assert not allowed, f"{tool} agent_profile settings.json 应被拒"
    assert reason and "敏感文件" in reason


def test_arcreel_db_in_sensitive_list(policy: AgentAccessPolicy) -> None:
    """入队链路已迁到 in-process MCP tool，sandbox 内 agent 不再需要直读 db。"""
    cwd = _cwd(policy)
    db = policy.projects_root / ".arcreel.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"sqlite-fake")
    allowed, reason = policy.check_path_access(str(db), "Read", cwd)
    assert not allowed
    assert reason and "敏感文件" in reason


def test_read_host_file_outside_project_root_denied(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """project_root 外的 host 文件（~/.ssh、/etc 等）不允许 Read/Glob/Grep。"""
    cwd = _cwd(policy)
    # tmp_path 在 policy.project_root 之外（project_root = tmp_path / "repo"）
    outside = tmp_path / "host_fake_ssh"
    outside.mkdir()
    (outside / "id_rsa").write_text("secret", encoding="utf-8")
    for tool in ("Read", "Glob", "Grep"):
        allowed, reason = policy.check_path_access(str(outside / "id_rsa"), tool, cwd)
        assert not allowed, f"{tool} 不应允许读 project_root 外的 host 文件"
        assert reason and "项目根外" in reason


def test_sensitive_glob_pattern_does_not_overmatch(policy: AgentAccessPolicy) -> None:
    """`.env.*` 不能误伤 `.environment` 这种命名的合法目录/文件。"""
    cwd = _cwd(policy)
    legal = policy.project_root / ".environment"
    legal.parent.mkdir(parents=True, exist_ok=True)
    allowed, _ = policy.check_path_access(str(legal), "Read", cwd)
    assert allowed, ".environment 是合法文件，不应被 `.env.*` glob 误伤"


# ============================================================
# 日志目录敏感前缀（log_dir 构造参数喂入）
# ============================================================


def test_logs_dir_is_sensitive_prefix(tmp_path: Path) -> None:
    """log_dir 必须落在 sensitive prefixes 里，agent 不能 Read/Grep 全局日志。

    背景：服务器日志含 HTTP 请求路径、provider 探测、异常栈；_check_read_access
    的 "仓库根内参考资料放行" 分支会把 repo 内的全局日志当成参考资料放给 agent。
    规则 0 的 sensitive-path 拒绝必须在前面截住，所以 log_dir 要进 prefixes。
    """
    root = tmp_path / "repo"
    root.mkdir()
    logs_dir = root / "logs"
    logs_dir.mkdir()
    (logs_dir / "arcreel.log").write_text("payload\n", encoding="utf-8")
    (logs_dir / "arcreel.log.2026-05-20").write_text("rotated\n", encoding="utf-8")

    policy = _make_policy(tmp_path, log_dir=logs_dir.resolve())

    # 当前 + 历史 log 文件都被认定为敏感
    assert policy.is_sensitive_path((logs_dir / "arcreel.log").resolve())
    assert policy.is_sensitive_path((logs_dir / "arcreel.log.2026-05-20").resolve())
    # 整目录本身也是敏感（Glob/listdir 拒）
    assert policy.is_sensitive_path(logs_dir.resolve())


# ============================================================
# 内核 settings 编译投影（原 sandbox settings 测试断言搬家）
# ============================================================


def test_build_sandbox_settings_disabled_returns_only_enabled_false(tmp_path: Path) -> None:
    """sandbox_enabled=False（Windows 回退）时只返回 {"enabled": False}。"""
    policy = _make_policy(tmp_path, sandbox_enabled=False)
    cwd = policy.projects_root / "demo"
    assert policy.build_sandbox_settings(cwd) == {"enabled": False}


def test_build_sandbox_settings_enabled_returns_full_config(tmp_path: Path) -> None:
    """sandbox_enabled=True（默认）依然返回完整 dict（含 network / filesystem）。"""
    policy = _make_policy(tmp_path, sandbox_enabled=True)
    cwd = policy.projects_root / "demo"
    settings = policy.build_sandbox_settings(cwd)
    assert settings["enabled"] is True
    assert settings["autoAllowBashIfSandboxed"] is True
    assert settings["allowUnsandboxedCommands"] is False
    assert "allowedDomains" in settings["network"]
    assert "denyRead" in settings["filesystem"]
    assert str(cwd / "project.json") in settings["filesystem"]["denyWrite"]


def test_build_sandbox_settings_in_docker_enables_weaker_nested(tmp_path: Path) -> None:
    """in_docker 透传到 enableWeakerNestedSandbox；非 Docker 默认 False。"""
    cwd = _make_policy(tmp_path).projects_root / "demo"
    assert _make_policy(tmp_path).build_sandbox_settings(cwd)["enableWeakerNestedSandbox"] is False
    assert _make_policy(tmp_path, in_docker=True).build_sandbox_settings(cwd)["enableWeakerNestedSandbox"] is True


def test_build_sandbox_settings_denies_write_to_project_json(policy: AgentAccessPolicy) -> None:
    """sandbox 启用时 denyWrite 覆盖 scripts/ 与 project.json（Bash 子进程内核级封堵）。"""
    cwd = _cwd(policy)
    settings = policy.build_sandbox_settings(cwd)
    deny_write = settings["filesystem"]["denyWrite"]
    assert str(cwd / "scripts") in deny_write
    assert str(cwd / "project.json") in deny_write


def test_build_sandbox_settings_deny_write_includes_resolved_paths(policy: AgentAccessPolicy, tmp_path: Path) -> None:
    """project_cwd 是 symlink 入口时（macOS /var↔/private/var、Linux symlinked
    项目根），denyWrite 须同时枚举 raw 与 resolved 两种形式——sandbox 实现若按
    字符串路径比对，仅注册 raw 形式会在 Bash 子进程经 symlink 解析后写 resolved
    路径时失配。与 _check_write_access 的 bases 同口径。"""
    real_root = tmp_path / "real_data"
    (real_root / "projects" / "selfproj").mkdir(parents=True)
    link_cwd = policy.projects_root / "selfproj_link"
    link_cwd.symlink_to(real_root / "projects" / "selfproj")

    settings = policy.build_sandbox_settings(link_cwd)
    deny_write = settings["filesystem"]["denyWrite"]
    resolved_cwd = link_cwd.resolve()
    assert resolved_cwd != link_cwd
    # raw 与 resolved 两种形式都注册
    assert str(link_cwd / "scripts") in deny_write
    assert str(link_cwd / "project.json") in deny_write
    assert str(resolved_cwd / "scripts") in deny_write
    assert str(resolved_cwd / "project.json") in deny_write
    # raw == resolved 的常规路径不重复注册
    assert len(deny_write) == len(set(deny_write))


def test_build_sensitive_abs_paths_includes_existing_files(tmp_path: Path) -> None:
    """枚举实际存在的敏感文件，跳过不存在项。"""
    root = tmp_path / "repo"
    root.mkdir()
    (root / ".env").write_text("X=1", encoding="utf-8")
    (root / ".env.local").write_text("Y=2", encoding="utf-8")
    (root / "projects").mkdir()
    (root / "projects" / ".arcreel.db").write_bytes(b"sqlite-fake")
    (root / "projects" / ".arcreel.db-shm").write_bytes(b"shm")
    profile_dir = tmp_path / "agent_runtime_profile"
    (profile_dir / ".claude").mkdir(parents=True)
    (profile_dir / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    (root / "vertex_keys").mkdir()

    policy = _make_policy(tmp_path)
    paths = policy._build_sensitive_abs_paths()

    # 必须命中真实存在的关键路径
    assert str(root.resolve() / ".env") in paths
    assert str(root.resolve() / ".env.local") in paths
    assert str(profile_dir.resolve() / ".claude" / "settings.json") in paths
    assert str(root.resolve() / "vertex_keys") in paths

    # 不存在的 system_config.json 不应出现（SDK 会跳过 non-existent path）
    assert all(".system_config.json" not in p for p in paths)
    # .arcreel.db + WAL 辅助文件在敏感清单（入队走 MCP tool，agent 不直读 db）
    assert str(root.resolve() / "projects" / ".arcreel.db") in paths
    assert str(root.resolve() / "projects" / ".arcreel.db-shm") in paths


def test_build_sensitive_abs_paths_follows_constructed_roots(tmp_path: Path) -> None:
    """数据/profile 目录被搬到项目外（构造参数指向新位置）时，denyRead 必须
    跟着指到新位置——否则源码根下的硬编码清单实际什么都护不到。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    # 数据目录搬到 repo 之外
    external_data = tmp_path / "external_data" / "projects"
    external_data.mkdir(parents=True)
    (external_data / ".arcreel.db").write_bytes(b"db")
    (external_data / ".arcreel.db-wal").write_bytes(b"wal")
    (external_data / ".system_config.json").write_text("{}", encoding="utf-8")
    (external_data.parent / "vertex_keys").mkdir()
    # profile 目录搬到 repo 之外
    external_profile = tmp_path / "external_profile"
    (external_profile / ".claude").mkdir(parents=True)
    (external_profile / ".claude" / "settings.json").write_text("{}", encoding="utf-8")

    policy = _make_policy(
        tmp_path,
        projects_root=external_data.resolve(),
        agent_profile_root=external_profile.resolve(),
    )
    paths = policy._build_sensitive_abs_paths()

    assert str(external_data / ".arcreel.db") in paths
    assert str(external_data / ".arcreel.db-wal") in paths
    assert str(external_data / ".system_config.json") in paths
    assert str(external_data.parent / "vertex_keys") in paths
    assert str(external_profile.resolve() / ".claude" / "settings.json") in paths
    # 旧的 ``repo/projects/.arcreel.db`` 路径已不复存在 — 不再误指 deny 到空位置
    assert not any(str(repo) + "/projects/" in p for p in paths)

    # is_sensitive_path 也必须能识别新位置
    assert policy.is_sensitive_path((external_data / ".arcreel.db").resolve())
    assert policy.is_sensitive_path((external_profile / ".claude" / "settings.json").resolve())
    assert policy.is_sensitive_path((external_data.parent / "vertex_keys" / "k.json").resolve())


def test_build_sensitive_abs_paths_includes_log_dir(tmp_path: Path) -> None:
    """log_dir 整目录必须进 denyRead 清单（内核级封锁与 hook 层同源）。"""
    root = tmp_path / "repo"
    logs_dir = root / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "arcreel.log").write_text("payload\n", encoding="utf-8")

    policy = _make_policy(tmp_path, log_dir=logs_dir.resolve())
    assert str(logs_dir.resolve()) in policy._build_sensitive_abs_paths()


def test_filter_allowed_tools_strips_bash_family_when_sandbox_disabled(tmp_path: Path) -> None:
    """sandbox 关闭时剥离 Bash/BashOutput/KillBash（落到 can_use_tool 白名单），启用时保留。"""
    base = ["Skill", "Bash", "BashOutput", "KillBash", "Read"]
    enabled = _make_policy(tmp_path, sandbox_enabled=True)
    assert enabled.filter_allowed_tools(base) == base
    disabled = _make_policy(tmp_path, sandbox_enabled=False)
    assert disabled.filter_allowed_tools(base) == ["Skill", "Read"]


# ============================================================
# Bash 密钥剥离（env scrub）纯变换
# ============================================================


def test_env_scrub_collects_pattern_matched_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """unset 清单除了固定名单还要动态命中 *_API_KEY / *_AUTH_TOKEN 等模式。"""
    monkeypatch.setenv("GEMINI_CLI_IDE_AUTH_TOKEN", "abc")
    monkeypatch.setenv("RANDOM_VENDOR_API_KEY", "def")
    monkeypatch.setenv("PATH", "/usr/bin")  # 不应命中

    AgentAccessPolicy._collect_env_keys_to_scrub.cache_clear()
    AgentAccessPolicy._env_scrub_wrap_prefix.cache_clear()
    try:
        keys = AgentAccessPolicy._collect_env_keys_to_scrub()
        assert "GEMINI_CLI_IDE_AUTH_TOKEN" in keys
        assert "RANDOM_VENDOR_API_KEY" in keys
        assert "PATH" not in keys
        # 固定清单
        assert "ANTHROPIC_API_KEY" in keys
        assert "ARK_API_KEY" in keys
    finally:
        AgentAccessPolicy._collect_env_keys_to_scrub.cache_clear()
        AgentAccessPolicy._env_scrub_wrap_prefix.cache_clear()


def test_wrap_bash_command_unsets_provider_keys(tmp_path: Path) -> None:
    """POSIX（sandbox 启用）：command 包装成 ``env -u ANTHROPIC_* sh -c '<orig>'``。"""
    from lib.config.env_keys import ANTHROPIC_ENV_KEYS

    policy = _make_policy(tmp_path)
    wrapped = policy.wrap_bash_command_for_env_scrub("env | grep ANTHROPIC")

    assert wrapped is not None
    # 每个 ANTHROPIC_* key 都被 unset
    for key in ANTHROPIC_ENV_KEYS:
        assert f"-u {key}" in wrapped
    # 原命令被 shlex.quote 包到 sh -c 内
    assert "sh -c " in wrapped
    assert "'env | grep ANTHROPIC'" in wrapped


def test_wrap_bash_command_skips_when_sandbox_disabled(tmp_path: Path) -> None:
    """Windows 回退：``env -u``/``sh -c`` 是 POSIX 机制，原生 Windows 不可执行；
    且包装后的命令以 ``env -u`` 开头，会让白名单永远匹配不上——返回 None 表示
    不包装，原始命令落到 can_use_tool 做白名单匹配。"""
    policy = _make_policy(tmp_path, sandbox_enabled=False)
    assert policy.wrap_bash_command_for_env_scrub("ffmpeg -i in.mp4 out.mp4") is None


def test_wrap_bash_command_handles_single_quotes(tmp_path: Path) -> None:
    """命令含单引号时不能破坏 shell 引号闭合。"""
    policy = _make_policy(tmp_path)
    wrapped = policy.wrap_bash_command_for_env_scrub("echo 'hello world'")
    assert wrapped is not None
    # shlex.quote 把 'hello world' 转义为 'echo '"'"'hello world'"'"''
    assert wrapped.endswith("'\"'\"'hello world'\"'\"''")


def test_wrap_bash_command_passthrough_when_no_command(tmp_path: Path) -> None:
    """空 command 时不做包装。"""
    policy = _make_policy(tmp_path)
    assert policy.wrap_bash_command_for_env_scrub(None) is None
    assert policy.wrap_bash_command_for_env_scrub("   ") is None


def test_logs_dir_outside_repo_is_sensitive(tmp_path: Path) -> None:
    """log_dir 在 repo 外（用户自定义日志位置）时，敏感前缀必须跟着指过去——
    硬编码 repo/logs 会让 agent 仍能 Read/Grep 真实 log_dir 下的日志。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    external_logs = tmp_path / "external" / "arcreel_logs"
    external_logs.mkdir(parents=True)
    (external_logs / "arcreel.log").write_text("secret\n", encoding="utf-8")

    policy = _make_policy(tmp_path, log_dir=external_logs.resolve())

    # repo 外的自定义 log_dir 也要被 deny
    assert policy.is_sensitive_path((external_logs / "arcreel.log").resolve())
    assert policy.is_sensitive_path(external_logs.resolve())
    # repo/logs 在此场景下不应被默认 deny（避免误覆盖）
    assert not policy.is_sensitive_path((repo / "logs" / "anything.txt").resolve())
