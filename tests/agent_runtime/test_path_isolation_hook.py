"""新版 _is_path_allowed 三规则：跨项目读拒 + cwd 外写拒 + 代码扩展名拒。"""

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
