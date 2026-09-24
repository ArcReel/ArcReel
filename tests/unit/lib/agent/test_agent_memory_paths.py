"""项目记忆目录派生与用户 id 校验：纯函数、零 I/O，目录不存在也照常派生。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.agent.agent_memory_paths import is_valid_memory_user_id, project_memory_dir


def test_project_memory_dir_is_arcreel_memory_under_project() -> None:
    project_dir = Path("/nonexistent/data/projects/demo")
    assert project_memory_dir(project_dir) == project_dir / ".arcreel" / "memory"


def test_derivation_does_no_io(tmp_path: Path) -> None:
    """派生不建目录、不要求目录存在。"""
    assert not project_memory_dir(tmp_path / "demo").exists()


@pytest.mark.parametrize("user_id", ["", ".", "..", "../other", "a/b", "a\\b", "\x00", "a\x00b", "C:", "iss:sub"])
def test_is_valid_memory_user_id_rejects_non_segment_user_id(user_id: str) -> None:
    assert not is_valid_memory_user_id(user_id)


def test_is_valid_memory_user_id_accepts_ordinary_ids() -> None:
    assert is_valid_memory_user_id("default")
    assert is_valid_memory_user_id("user-42")
