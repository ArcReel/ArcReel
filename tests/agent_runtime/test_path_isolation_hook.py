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
        "agent_runtime_profile/.claude/settings.json",
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


def test_arcreel_db_not_in_sensitive_list(sm: SessionManager) -> None:
    """skill 脚本通过 generation_queue_client 直连 SQLite 入队，.arcreel.db 必须放行。"""
    cwd = sm.project_root / "projects" / "selfproj"
    db = sm.project_root / "projects" / ".arcreel.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    db.write_bytes(b"sqlite-fake")
    allowed, _ = sm._is_path_allowed(str(db), "Read", cwd)
    assert allowed


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
