"""_is_path_allowed 四规则：敏感文件拒 + 跨项目读拒 + cwd 外写拒 + 代码扩展名拒。"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.agent_runtime.session_manager import SessionManager
from server.agent_runtime.session_store import SessionMetaStore


@pytest.fixture
def sm(tmp_path: Path) -> SessionManager:
    project_root = tmp_path / "repo"
    project_root.mkdir()
    (project_root / "projects").mkdir()
    (project_root / "projects" / "selfproj").mkdir()
    (project_root / "projects" / "other").mkdir()
    (project_root / "lib").mkdir()
    return SessionManager(project_root, tmp_path / "data", SessionMetaStore())


def test_read_cwd_internal_passes(sm: SessionManager, tmp_path: Path) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, _ = sm._is_path_allowed(str(cwd / "data.json"), "Read", cwd)
    assert allowed


def test_read_other_project_denied(sm: SessionManager) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, reason = sm._is_path_allowed(str(sm.project_root / "projects" / "other" / "x.json"), "Read", cwd)
    assert not allowed
    assert "跨项目" in reason or "项目" in reason


def test_read_lib_passes(sm: SessionManager) -> None:
    """cwd 外的非 projects 路径允许读（用于 agent 查 docs/lib 等参考资料）。"""
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, _ = sm._is_path_allowed(str(sm.project_root / "lib" / "foo.py"), "Read", cwd)
    assert allowed


def test_write_cwd_external_denied(sm: SessionManager) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, reason = sm._is_path_allowed(str(sm.project_root / "lib" / "foo.json"), "Write", cwd)
    assert not allowed
    assert "项目目录之外" in reason or "cwd" in reason or "项目" in reason


def test_write_cwd_internal_code_ext_denied(sm: SessionManager) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    for ext in (".py", ".js", ".ts", ".tsx", ".sh", ".yaml", ".yml", ".toml"):
        allowed, reason = sm._is_path_allowed(str(cwd / f"test{ext}"), "Write", cwd)
        assert not allowed, f"扩展名 {ext} 应被拒"
        assert "代码" in reason or "扩展名" in reason


def test_write_cwd_internal_data_ext_allowed(sm: SessionManager) -> None:
    cwd = sm.project_root / "projects" / "selfproj"
    for ext in (".json", ".md", ".txt", ".html", ".csv"):
        allowed, _ = sm._is_path_allowed(str(cwd / f"data{ext}"), "Write", cwd)
        assert allowed, f"扩展名 {ext} 应允许"


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize("relative", ["scripts/episode_1.json", "scripts/episode_10.json", "project.json"])
def test_write_protected_project_json_denied(sm: SessionManager, tool: str, relative: str) -> None:
    """scripts/*.json 与 project.json 不可用 Write/Edit 直改，报错指向 MCP 工具。"""
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, reason = sm._is_path_allowed(str(cwd / relative), tool, cwd)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason and "patch_episode_script" in reason or "patch_project" in (reason or "")


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_scripts_dir_itself_denied(sm: SessionManager, tool: str) -> None:
    """`scripts/` 目录路径本身（不带 trailing sep）也该拒：defense-in-depth，
    不依赖 OS 兜底 agent 把目录名当文件路径的 typo。"""
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, reason = sm._is_path_allowed(str(cwd / "scripts"), tool, cwd)
    assert not allowed
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    ["scripts/episode_1.bak", "scripts/notes.md", "scripts/.tmp", "scripts/subdir/anything.txt"],
)
def test_write_protected_scripts_non_json_denied(sm: SessionManager, tool: str, relative: str) -> None:
    """`scripts/` 下任意文件类型都该拒（不只 .json）：sandbox denyWrite 把整个 scripts/ 列入
    内核级 deny，hook 层须保持一致，避免 agent 用 Write 污染剧本目录。"""
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, reason = sm._is_path_allowed(str(cwd / relative), tool, cwd)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(
    "relative",
    ["PROJECT.JSON", "Project.Json", "scripts/EPISODE_1.JSON", "Scripts/episode_1.json"],
)
def test_write_protected_case_variants_denied(sm: SessionManager, tool: str, relative: str) -> None:
    """大小写变体（PROJECT.JSON / Scripts/x.json）在 Windows NTFS / macOS APFS 默认卷
    上指向同一物理文件，Path 字符串比较 case-sensitive 会漏判——`_is_protected_project_json`
    用 casefold 比较后这类变体也应被拒，否则 agent 可改大小写绕过收口。"""
    cwd = sm.project_root / "projects" / "selfproj"
    allowed, reason = sm._is_path_allowed(str(cwd / relative), tool, cwd)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_via_symlink_project_json_denied(sm: SessionManager, tool: str) -> None:
    """`project.json` 本身被做成项目内 symlink（指向另一个项目内文件）时，仍须拒——
    防止"把入口换成 symlink"绕过 protected 区判定。仅靠 resolve 后路径比较会失配。"""
    cwd = sm.project_root / "projects" / "selfproj"
    real = cwd / "other.json"
    real.write_text("{}", encoding="utf-8")
    link = cwd / "project.json"
    link.symlink_to(real)
    allowed, reason = sm._is_path_allowed(str(link), tool, cwd)
    assert not allowed, "symlink 形态的 project.json 写入应被拒"
    assert reason and ("patch_project" in reason or "patch_episode_script" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_via_symlink_scripts_dir_denied(sm: SessionManager, tool: str) -> None:
    """`scripts/` 整个目录被做成项目内 symlink 时，对其下 .json 的写入仍须拒。"""
    cwd = sm.project_root / "projects" / "selfproj"
    real_dir = cwd / "data"
    real_dir.mkdir()
    link_dir = cwd / "scripts"
    link_dir.symlink_to(real_dir)
    target = link_dir / "episode_1.json"
    allowed, reason = sm._is_path_allowed(str(target), tool, cwd)
    assert not allowed, "symlink 形态的 scripts/ 下 .json 写入应被拒"
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


@pytest.mark.parametrize("tool", ["Write", "Edit"])
def test_write_protected_with_symlinked_project_cwd_denied(sm: SessionManager, tmp_path: Path, tool: str) -> None:
    """project_cwd 本身是个 symlink 指向真实项目目录时(macOS /var↔/private/var、Linux
    symlinked 项目根),`_is_protected_project_json` 要把 base 也按 resolve 一次再拼接 protected
    路径,避免 resolved target 与 raw base 字符串不等 → bypass。"""
    # 真实项目目录在 tmp 根的另一处,通过 symlink 暴露
    real_root = tmp_path / "real_data"
    (real_root / "projects" / "selfproj").mkdir(parents=True)
    link_cwd = sm.project_root / "projects" / "selfproj_link"
    link_cwd.symlink_to(real_root / "projects" / "selfproj")

    # caller 把 symlinked cwd 传入,_is_path_allowed 内 logical.resolve() 会展开 symlink,
    # 然后 _check_write_access 把 resolved target 与原始 link_cwd 比较——若不把 base 也
    # resolve,就会因为字符串不等漏判。
    allowed, reason = sm._is_path_allowed(str(link_cwd / "project.json"), tool, link_cwd)
    assert not allowed, "symlinked project_cwd 下 project.json 写入应被拒"
    assert reason and ("patch_project" in reason or "patch_episode_script" in reason)

    allowed, reason = sm._is_path_allowed(str(link_cwd / "scripts" / "episode_1.json"), tool, link_cwd)
    assert not allowed, "symlinked project_cwd 下 scripts/*.json 写入应被拒"
    assert reason and ("patch_episode_script" in reason or "patch_project" in reason)


def test_protected_json_predicate_normalizes_nfd_and_case() -> None:
    """NFC/NFD 与大小写混合形式都须命中：macOS HFS+ 按 NFD 存储文件名，resolve
    返回的 target 与 NFC 形式的 base 即使 casefold 后仍是不同字符串——受保护
    比对须先做 NFC 归一化，再做大小写不敏感比较。"""
    base_nfc = Path("/data/projects/caf\u00e9")  # café（NFC 单码位）
    target_nfd = Path("/data/projects/cafe\u0301/project.json")  # café（NFD 组合字符）
    assert SessionManager._is_protected_project_json(target_nfd, [base_nfc])

    # 大小写变体 + NFD 叠加
    target_mixed = Path("/data/projects/CAFE\u0301/SCRIPTS/EPISODE_1.JSON")
    assert SessionManager._is_protected_project_json(target_mixed, [base_nfc])

    # 反向：base 是 NFD（HFS+ 磁盘形式）、target 是 NFC（用户输入形式）
    base_nfd = Path("/data/projects/cafe\u0301")
    target_nfc = Path("/data/projects/caf\u00e9/scripts/episode_1.json")
    assert SessionManager._is_protected_project_json(target_nfc, [base_nfd])

    # 归一化不引入 over-match：其他项目路径不受影响
    other = Path("/data/projects/cafe_other/project.json")
    assert not SessionManager._is_protected_project_json(other, [base_nfc])


def test_normalize_path_for_protected_compare_strips_windows_extended_prefix() -> None:
    """Windows ``\\\\?\\`` 扩展长度前缀（resolve 在长路径/UNC 下返回）与常规形态
    须归一化为同一比较键，否则 bases 混入两种形式时 startswith 失配。
    helper 级单测；实机 Windows 端到端验证另行跟踪。"""
    norm = SessionManager._normalize_path_for_protected_compare
    assert norm("\\\\?\\C:\\data\\projects\\demo") == norm("C:\\data\\projects\\demo")
    assert norm("\\\\?\\UNC\\server\\share\\proj") == norm("\\\\server\\share\\proj")
    # 常规路径不受影响
    assert norm("/data/projects/demo") == norm("/data/projects/demo")


def test_write_drafts_and_source_still_allowed(sm: SessionManager) -> None:
    """合法的草稿/源文件写入不受影响（drafts/*.md、source/*.txt、scripts 外的 .json）。"""
    cwd = sm.project_root / "projects" / "selfproj"
    for relative in ("drafts/episode_1/step1_segments.md", "source/episode_1.txt", "config_data.json"):
        allowed, _ = sm._is_path_allowed(str(cwd / relative), "Write", cwd)
        assert allowed, f"{relative} 应允许"


def test_build_sandbox_settings_denies_write_to_project_json(sm: SessionManager) -> None:
    """sandbox 启用时 denyWrite 覆盖 scripts/ 与 project.json（Bash 子进程内核级封堵）。"""
    sm._sandbox_enabled = True
    cwd = sm.project_root / "projects" / "selfproj"
    settings = sm._build_sandbox_settings(cwd)
    deny_write = settings["filesystem"]["denyWrite"]
    assert str(cwd / "scripts") in deny_write
    assert str(cwd / "project.json") in deny_write


def test_build_sandbox_settings_deny_write_includes_resolved_paths(sm: SessionManager, tmp_path: Path) -> None:
    """project_cwd 是 symlink 入口时（macOS /var↔/private/var、Linux symlinked
    项目根），denyWrite 须同时枚举 raw 与 resolved 两种形式——sandbox 实现若按
    字符串路径比对，仅注册 raw 形式会在 Bash 子进程经 symlink 解析后写 resolved
    路径时失配。与 _check_write_access 的 bases 同口径。"""
    real_root = tmp_path / "real_data"
    (real_root / "projects" / "selfproj").mkdir(parents=True)
    link_cwd = sm.project_root / "projects" / "selfproj_link"
    link_cwd.symlink_to(real_root / "projects" / "selfproj")

    sm._sandbox_enabled = True
    settings = sm._build_sandbox_settings(link_cwd)
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
def test_sensitive_file_denied(sm: SessionManager, tool: str, relative: str) -> None:
    """敏感文件无论 Read 还是 Write 一律拒，且报错信息包含"敏感文件"。"""
    cwd = sm.project_root / "projects" / "selfproj"
    # 文件实际存在与否不影响 deny 判断（resolve() 对不存在路径仍返回绝对路径）
    target = sm.project_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    allowed, reason = sm._is_path_allowed(str(target), tool, cwd)
    assert not allowed, f"{tool} {relative} 应被拒"
    assert reason and "敏感文件" in reason


@pytest.mark.parametrize("tool", ["Read", "Write", "Edit", "Glob", "Grep"])
def test_agent_profile_settings_denied(sm: SessionManager, tool: str) -> None:
    """``ARCREEL_PROFILE_DIR`` 由 conftest autouse 锁到 ``tmp_path/agent_runtime_profile``，
    SessionManager 用同一份解析得到 ``_agent_profile_root``——所以敏感判断必须
    对准 env-aware 路径而不是源码根的硬编码路径。"""
    cwd = sm.project_root / "projects" / "selfproj"
    target = sm._agent_profile_root / ".claude" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    allowed, reason = sm._is_path_allowed(str(target), tool, cwd)
    assert not allowed, f"{tool} agent_profile settings.json 应被拒"
    assert reason and "敏感文件" in reason


def test_arcreel_db_in_sensitive_list(sm: SessionManager) -> None:
    """入队链路已迁到 in-process MCP tool (issue #519)，sandbox 内 agent 不再需要直读 db。"""
    cwd = sm.project_root / "projects" / "selfproj"
    db = sm.project_root / "projects" / ".arcreel.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"sqlite-fake")
    allowed, reason = sm._is_path_allowed(str(db), "Read", cwd)
    assert not allowed
    assert reason and "敏感文件" in reason


def test_read_host_file_outside_project_root_denied(sm: SessionManager, tmp_path: Path) -> None:
    """project_root 外的 host 文件（~/.ssh、/etc 等）不允许 Read/Glob/Grep。"""
    cwd = sm.project_root / "projects" / "selfproj"
    # tmp_path 在 sm.project_root 之外（project_root = tmp_path / "repo"）
    outside = tmp_path / "host_fake_ssh"
    outside.mkdir()
    (outside / "id_rsa").write_text("secret", encoding="utf-8")
    for tool in ("Read", "Glob", "Grep"):
        allowed, reason = sm._is_path_allowed(str(outside / "id_rsa"), tool, cwd)
        assert not allowed, f"{tool} 不应允许读 project_root 外的 host 文件"
        assert reason and "项目根外" in reason


def test_sensitive_glob_pattern_does_not_overmatch(sm: SessionManager, tmp_path: Path) -> None:
    """`.env.*` 不能误伤 `.environment` 这种命名的合法目录/文件。"""
    cwd = sm.project_root / "projects" / "selfproj"
    legal = sm.project_root / ".environment"
    legal.parent.mkdir(parents=True, exist_ok=True)
    allowed, _ = sm._is_path_allowed(str(legal), "Read", cwd)
    assert allowed, ".environment 是合法文件，不应被 `.env.*` glob 误伤"
