"""数据根布局：纯派生、零 I/O；由项目目录求数据根与由数据根求项目目录互逆。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.infra.data_root_layout import DataRootLayout

_ROOT = Path("/nonexistent/data")


def test_for_project_dir_recovers_the_layout_of_its_data_root() -> None:
    layout = DataRootLayout(_ROOT)
    assert DataRootLayout.for_project_dir(layout.projects_dir / "demo") == layout


def test_user_memory_dirs_are_per_user_inside_the_data_root() -> None:
    layout = DataRootLayout(_ROOT)
    alice = layout.user_memory_dir("alice")
    assert alice.is_relative_to(_ROOT)
    assert alice != layout.user_memory_dir("bob")


def test_derivation_does_no_io(tmp_path: Path) -> None:
    """派生不建目录、不要求目录存在。"""
    layout = DataRootLayout(tmp_path / "data")
    layout.user_memory_dir("default")
    _ = (layout.projects_dir, layout.global_assets_dir, layout.trial_runs_dir, layout.log_dir)
    assert not layout.root.exists()


@pytest.mark.parametrize("user_id", ["", ".", "..", "../other", "a/b", "a\\b", "\x00", "a\x00b", "C:", "iss:sub"])
def test_user_memory_dir_rejects_non_segment_user_id(user_id: str) -> None:
    """user_id 直接构成目录名，非单段值会让目录逃出数据根、把围栏放行范围扩到任意路径；
    含 NUL 的值派生出的路径任何文件系统调用都会抛错，围栏登记的是个用不了的目录；
    含 ``:`` 的值在 Windows 上拼成驱动器相对路径，判据取所有平台的交集。"""
    with pytest.raises(ValueError, match="user_id"):
        DataRootLayout(_ROOT).user_memory_dir(user_id)
