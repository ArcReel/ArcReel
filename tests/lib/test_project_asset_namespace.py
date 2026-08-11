from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from lib.asset_types import ProjectAssetNameConflictError
from lib.data_validator import DataValidator
from lib.project_manager import ProjectManager

pytestmark = pytest.mark.unit


@pytest.fixture
def pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(str(tmp_path))
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo", "Anime", "narration")
    return manager


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("character", "scene"),
        ("character", "prop"),
        ("character", "product"),
        ("scene", "prop"),
        ("scene", "product"),
        ("prop", "product"),
    ],
)
def test_every_asset_type_pair_shares_one_namespace(pm: ProjectManager, first: str, second: str) -> None:
    table = {"character": "characters", "scene": "scenes", "prop": "props", "product": "products"}
    pm.upsert_assets("demo", table[first], {"Shared": {"description": "first"}})
    with pytest.raises(ProjectAssetNameConflictError, match="Shared") as exc_info:
        pm.upsert_assets("demo", table[second], {"Shared": {"description": "second"}})
    assert exc_info.value.requested_asset_type == second
    assert exc_info.value.existing.asset_type == first
    assert pm.load_project("demo").get(table[second], {}) == {}


def test_namespace_uses_strip_nfc_and_is_case_sensitive(pm: ProjectManager) -> None:
    nfd = unicodedata.normalize("NFD", "café")
    pm.add_character("demo", " café ", "first")
    with pytest.raises(ProjectAssetNameConflictError):
        pm.add_project_scene("demo", nfd, "second")
    assert pm.add_project_scene("demo", "CAFÉ", "case differs") is True


def test_batch_conflict_is_atomic(pm: ProjectManager) -> None:
    pm.add_character("demo", "Taken", "character")
    with pytest.raises(ProjectAssetNameConflictError):
        pm.add_scenes_batch(
            "demo",
            {"Fresh": {"description": "would be valid"}, "Taken": {"description": "conflict"}},
        )
    assert pm.load_project("demo")["scenes"] == {}


def test_cross_type_rename_conflict_is_rejected(pm: ProjectManager) -> None:
    pm.add_character("demo", "Character", "character")
    pm.add_project_scene("demo", "Scene", "scene")
    with pytest.raises(ProjectAssetNameConflictError):
        pm.rename_asset("demo", "scenes", "Scene", "Character")
    project = pm.load_project("demo")
    assert list(project["scenes"]) == ["Scene"]


def test_validator_reports_cross_type_and_equivalent_duplicates(pm: ProjectManager) -> None:
    project = pm.load_project("demo")
    nfd = unicodedata.normalize("NFD", "café")
    project["characters"] = {" café ": {"description": "a"}}
    project["products"] = {nfd: {"description": "b"}}

    result = DataValidator(str(pm.projects_root)).validate_project_payload(project)

    assert any(message.key == "val_asset_name_duplicate" for message in result.error_messages)
