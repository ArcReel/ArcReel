"""Agent 记忆两级目录派生：纯函数、零 I/O，目录不存在也照常派生。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.agent_memory_paths import is_valid_memory_user_id, project_memory_dir, user_memory_dir


def test_project_memory_dir_is_arcreel_memory_under_project() -> None:
    project_dir = Path("/nonexistent/data/projects/demo")
    assert project_memory_dir(project_dir) == project_dir / ".arcreel" / "memory"


def test_user_memory_dir_is_namespaced_under_data_root() -> None:
    """用户记忆走 ``.arcreel/users/``：数据根下 ``_`` 前缀是共享素材库语义，不复用。"""
    projects_root = Path("/nonexistent/data/projects")
    assert user_memory_dir(projects_root, "default") == projects_root / ".arcreel" / "users" / "default" / "memory"


def test_derivation_does_no_io(tmp_path: Path) -> None:
    """派生不建目录、不要求目录存在。"""
    assert not project_memory_dir(tmp_path / "demo").exists()
    assert not user_memory_dir(tmp_path, "default").exists()


def test_distinct_users_get_distinct_dirs() -> None:
    projects_root = Path("/nonexistent/data/projects")
    assert user_memory_dir(projects_root, "alice") != user_memory_dir(projects_root, "bob")


@pytest.mark.parametrize("user_id", ["", ".", "..", "../other", "a/b", "a\\b"])
def test_user_memory_dir_rejects_non_segment_user_id(user_id: str) -> None:
    """user_id 直接构成目录名，非单段值会让目录逃出数据根、把围栏放行范围扩到任意路径。"""
    assert not is_valid_memory_user_id(user_id)
    with pytest.raises(ValueError, match="user_id"):
        user_memory_dir(Path("/nonexistent/data/projects"), user_id)


def test_is_valid_memory_user_id_accepts_ordinary_ids() -> None:
    assert is_valid_memory_user_id("default")
    assert is_valid_memory_user_id("user-42")
