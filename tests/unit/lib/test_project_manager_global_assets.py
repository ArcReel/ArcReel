"""ProjectManager global_assets helper."""

from __future__ import annotations

from lib.project_manager import ProjectManager


def test_get_global_assets_root_creates_subdirs(tmp_path):
    pm = ProjectManager(tmp_path / "projects")
    root = pm.get_global_assets_root()
    assert root == tmp_path / "projects" / "_global_assets"
    for sub in ("character", "scene", "prop"):
        assert (root / sub).is_dir()


def test_list_projects_skips_global_assets(tmp_path):
    pm = ProjectManager(tmp_path / "projects")
    pm.get_global_assets_root()  # 生成 _global_assets
    (pm.projects_root / "my_project").mkdir()
    assert pm.list_projects() == ["my_project"]
