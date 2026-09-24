"""ProjectManager global_assets helper."""

from __future__ import annotations

from lib.project.project_manager import ProjectManager


def test_get_global_assets_root_creates_subdirs(tmp_path):
    pm = ProjectManager(tmp_path / "projects")
    root = pm.get_global_assets_root()
    assert root == tmp_path / "projects" / "_global_assets"
    for sub in ("character", "scene", "prop"):
        assert (root / sub).is_dir()
