from __future__ import annotations

from pathlib import Path

import pytest

from lib.project.data_validator import DataValidator
from lib.project.project_manager import ProjectManager


@pytest.fixture
def aliases_pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    return manager


@pytest.mark.parametrize("table", ["characters", "scenes", "props"])
def test_rename_records_the_old_name_as_an_alias(aliases_pm: ProjectManager, table: str) -> None:
    aliases_pm.upsert_assets("demo", table, {"阿离": {"description": "少女"}})

    aliases_pm.rename_asset("demo", table, "阿离", "离儿")
    aliases_pm.rename_asset("demo", table, "离儿", "姜离")

    assert aliases_pm.load_project("demo")[table]["姜离"]["aliases"] == ["阿离", "离儿"]


def test_renaming_to_an_alias_moves_it_out_of_the_aliases(aliases_pm: ProjectManager) -> None:
    aliases_pm.upsert_assets("demo", "characters", {"阿离": {"description": "少女", "aliases": ["离儿"]}})

    aliases_pm.rename_asset("demo", "characters", "阿离", "离儿")

    assert aliases_pm.load_project("demo")["characters"]["离儿"]["aliases"] == ["阿离"]


@pytest.mark.parametrize("table", ["characters", "scenes", "props"])
def test_aliases_must_be_a_list_of_strings(aliases_pm: ProjectManager, table: str) -> None:
    project = aliases_pm.load_project("demo")
    project[table] = {"阿离": {"description": "少女", "aliases": "离儿"}}

    errors = DataValidator(str(aliases_pm.projects_dir)).validate_project_payload(project).errors

    assert any("aliases" in str(error) for error in errors)
