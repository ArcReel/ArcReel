"""Data-root layout is pure derivation with zero I/O; deriving roots/projects is inverse."""

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
    """Derivation neither creates nor requires existing directories."""
    layout = DataRootLayout(tmp_path / "data")
    layout.user_memory_dir("default")
    _ = (layout.projects_dir, layout.global_assets_dir, layout.trial_runs_dir, layout.log_dir)
    assert not layout.root.exists()


@pytest.mark.parametrize("user_id", ["", ".", "..", "../other", "a/b", "a\\b", "\x00", "a\x00b", "C:", "iss:sub"])
def test_user_memory_dir_rejects_non_segment_user_id(user_id: str) -> None:
    """user_id directly names directories; nonsingle segments escape data roots and expand fence allowances arbitrarily.
    NUL-derived paths fail every filesystem call, leaving unusable fenced directories.
    ``:`` produces Windows drive-relative paths; apply the intersection of platform rules."""
    with pytest.raises(ValueError, match="user_id"):
        DataRootLayout(_ROOT).user_memory_dir(user_id)
