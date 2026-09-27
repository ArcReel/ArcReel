"""ProjectManager 列项目：只有项目目录下名字合法且带 project.json 的目录是项目。"""

from __future__ import annotations

from lib.project.project_manager import ProjectManager


def test_list_projects_only_lists_named_dirs_with_project_json(tmp_path):
    pm = ProjectManager(tmp_path / "data")
    pm.create_project("demo")
    pm.create_project("draft", publish=False)
    (pm.projects_dir / "no-project-json").mkdir()
    bad_name = pm.projects_dir / "bad_name"
    bad_name.mkdir()
    (bad_name / "project.json").write_text("{}")
    (pm.projects_dir / "loose-file").write_text("x")

    assert pm.list_projects() == ["demo"]


def test_list_projects_excludes_data_root_system_entries(tmp_path):
    pm = ProjectManager(tmp_path / "data")
    pm.create_project("demo")
    pm.get_global_assets_root()
    pm.layout.trial_runs_dir.mkdir(parents=True)
    pm.layout.user_memory_dir("default").mkdir(parents=True)
    pm.layout.generation_admission_locks_dir.mkdir(parents=True)
    pm.layout.sqlite_db_path.write_text("")

    assert pm.list_projects() == ["demo"]
