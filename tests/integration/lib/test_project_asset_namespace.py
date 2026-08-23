from __future__ import annotations

import json
import unicodedata
from pathlib import Path

import pytest

from lib.asset_types import ProjectAssetNameConflictError
from lib.data_validator import DataValidator
from lib.project_manager import ProjectManager


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


def _write_corrupt_v6_project(pm: ProjectManager, *, with_episode: bool = False) -> Path:
    project = pm.load_project("demo")
    project["characters"] = {"Shared": {"description": "character"}}
    project["scenes"] = {"Shared": {"description": "scene"}}
    if with_episode:
        project["episodes"] = [{"episode": 1, "script_file": "scripts/episode_1.json", "title": "Episode 1"}]
    project_file = pm.get_project_path("demo") / "project.json"
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return project_file


def test_rename_rejects_a_corrupt_v6_namespace_before_writing(pm: ProjectManager) -> None:
    project_file = _write_corrupt_v6_project(pm)
    project = json.loads(project_file.read_text(encoding="utf-8"))
    project["props"] = {"Old": {"description": "prop"}}
    project_file.write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    before = project_file.read_bytes()

    with pytest.raises(ProjectAssetNameConflictError):
        pm.rename_asset("demo", "props", "Old", "Renamed")

    assert project_file.read_bytes() == before


def test_locked_episode_write_rejects_a_corrupt_v6_namespace_before_writing(pm: ProjectManager) -> None:
    project_file = _write_corrupt_v6_project(pm, with_episode=True)
    script_file = pm.get_project_path("demo") / "scripts" / "episode_1.json"
    script_file.write_text('{"episode": 1}', encoding="utf-8")
    before_project = project_file.read_bytes()
    before_script = script_file.read_bytes()

    with pytest.raises(ProjectAssetNameConflictError):
        with pm.locked_episode_script("demo", lambda project: project["episodes"][0]["script_file"]):
            pytest.fail("corrupt namespace must fail before yielding the script")

    assert project_file.read_bytes() == before_project
    assert script_file.read_bytes() == before_script
