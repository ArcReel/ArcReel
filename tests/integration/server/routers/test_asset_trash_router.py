"""资产进出项目回收站：删除、衍生删除与资产合并成为删除记录，恢复按落地规则加回，名字被占用时走改名级联加后缀。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.version_manager import VersionManager
from lib.project import project_trash
from lib.project.project_manager import ProjectManager
from server.error_handlers import register_error_handlers
from server.routers import characters, scenes
from server.routers import project_trash as trash_router
from tests.auth_deps import AUTH_DEPENDENCIES, override_auth
from tests.fakes import use_project_manager

CHARACTERS = "/api/v1/projects/demo/characters"
TRASH = "/api/v1/projects/demo/trash"


@pytest.fixture
def asset_pm(tmp_path: Path) -> ProjectManager:
    manager = ProjectManager(tmp_path / "data")
    manager.create_project("demo")
    return manager


@pytest.fixture
def asset_dir(asset_pm: ProjectManager) -> Path:
    return asset_pm.get_project_path("demo")


@pytest.fixture
def asset_client(asset_pm: ProjectManager, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    use_project_manager(monkeypatch, asset_pm)
    monkeypatch.setattr(scenes, "get_project_manager", lambda: asset_pm)
    app = FastAPI()
    for module in (characters, scenes, trash_router):
        app.include_router(module.router, prefix="/api/v1", dependencies=AUTH_DEPENDENCIES)
    register_error_handlers(app)
    override_auth(app)
    return TestClient(app)


def _create(asset_client: TestClient, name: str, description: str = "少女") -> dict:
    response = asset_client.post(CHARACTERS, json={"name": name, "description": description})
    assert response.status_code == 200, response.text
    return response.json()


def _with_sheet(asset_pm: ProjectManager, asset_dir: Path, name: str, content: bytes) -> None:
    """给角色装一张资产图，并留下两个版本（第二个是当前版本）。"""
    sheet = asset_dir / "characters" / f"{name}.png"
    sheet.parent.mkdir(parents=True, exist_ok=True)
    versions = VersionManager(asset_dir)
    for revision in (b"-v1", b""):
        sheet.write_bytes(content + revision)
        versions.add_version("characters", name, f"prompt{revision.decode()}", source_file=sheet, aspect_ratio="3:4")
    asset_pm.update_asset_entry(
        "character", "demo", name, lambda entry: entry.update(character_sheet=f"characters/{name}.png")
    )


def _project(asset_dir: Path) -> dict:
    return json.loads((asset_dir / "project.json").read_text(encoding="utf-8"))


def _only_record(asset_client: TestClient) -> dict:
    records = asset_client.get(TRASH).json()["records"]
    assert len(records) == 1, records
    return records[0]


def test_deleting_an_asset_moves_its_sheet_and_history_into_one_record(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"sheet")

    assert asset_client.delete(f"{CHARACTERS}/阿岚").status_code == 200

    record = _only_record(asset_client)
    assert record["object"] == {"kind": "asset", "asset_type": "character", "name": "阿岚"}
    assert record["has_snapshot"] is True
    assert "characters/阿岚.png" in record["files"]
    assert sum(rel.startswith("versions/characters/") for rel in record["files"]) == 2
    assert not (asset_dir / "characters/阿岚.png").exists()
    assert not VersionManager(asset_dir).has_versions("characters", "阿岚")
    assert "阿岚" not in _project(asset_dir)["characters"]


def test_restore_with_free_name_brings_back_sheet_history_and_name(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"sheet")
    asset_client.delete(f"{CHARACTERS}/阿岚")

    restored = asset_client.post(f"{TRASH}/{_only_record(asset_client)['id']}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["object"]["name"] == "阿岚"
    entry = _project(asset_dir)["characters"]["阿岚"]
    assert (entry["description"], entry["character_sheet"]) == ("少女", "characters/阿岚.png")
    assert (asset_dir / "characters/阿岚.png").read_bytes() == b"sheet"
    history = VersionManager(asset_dir).get_versions("characters", "阿岚")
    assert (history["current_version"], len(history["versions"])) == (2, 2)
    assert history["versions"][0]["aspect_ratio"] == "3:4"
    assert asset_client.get(TRASH).json()["records"] == []


def test_recreating_a_deleted_name_points_at_the_trash_and_restore_takes_a_suffix(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"old")
    asset_client.delete(f"{CHARACTERS}/阿岚")
    record_id = _only_record(asset_client)["id"]

    recreated = _create(asset_client, "阿岚", description="新的阿岚")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"new")
    assert recreated["trash_record_id"] == record_id
    current_before = VersionManager(asset_dir).get_versions("characters", "阿岚")

    restored = asset_client.post(f"{TRASH}/{record_id}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["object"]["name"] == "阿岚 (1)"
    bucket = _project(asset_dir)["characters"]
    assert bucket["阿岚"]["description"] == "新的阿岚"
    assert (asset_dir / "characters/阿岚.png").read_bytes() == b"new"
    assert VersionManager(asset_dir).get_versions("characters", "阿岚") == current_before
    # 改名级联：条目、资产图、版本历史一并换成新名，原名不记成别名
    renamed = bucket["阿岚 (1)"]
    assert (renamed["description"], renamed["character_sheet"]) == ("少女", "characters/阿岚 (1).png")
    assert "阿岚" not in renamed.get("aliases", [])
    assert (asset_dir / "characters/阿岚 (1).png").read_bytes() == b"old"
    history = VersionManager(asset_dir).get_versions("characters", "阿岚 (1)")
    assert len(history["versions"]) == 2
    assert all((asset_dir / item["file"]).is_file() for item in history["versions"])
    assert all(Path(item["file"]).name.startswith("阿岚 (1)_v") for item in history["versions"])


def test_a_name_taken_by_another_asset_type_or_leftover_history_also_takes_a_suffix(asset_client, asset_pm, asset_dir):
    _create(asset_client, "Alan")
    asset_client.delete(f"{CHARACTERS}/Alan")
    record_id = _only_record(asset_client)["id"]
    assert asset_client.post("/api/v1/projects/demo/scenes", json={"name": "alan"}).status_code == 200

    restored = asset_client.post(f"{TRASH}/{record_id}/restore")

    assert restored.json()["object"]["name"] == "Alan (1)"
    assert "Alan (1)" in _project(asset_dir)["characters"]


def test_deleting_a_derivative_is_one_record_and_restores_onto_its_owner(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    added = asset_client.post(f"{CHARACTERS}/阿岚/derivatives", json={"name": "战甲", "description": "穿战甲"})
    assert added.status_code == 200, added.text
    sheet = asset_dir / "characters/derivatives/阿岚/战甲.png"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"armor")
    VersionManager(asset_dir).add_version("character_derivatives", "阿岚/战甲", "p", source_file=sheet)

    assert asset_client.delete(f"{CHARACTERS}/阿岚/derivatives/战甲").status_code == 200
    record = _only_record(asset_client)
    assert record["object"]["kind"] == "asset_derivative"
    assert not sheet.exists()
    assert "战甲" not in _project(asset_dir)["characters"]["阿岚"]["derivatives"]

    restored = asset_client.post(f"{TRASH}/{record['id']}/restore")

    assert restored.status_code == 200, restored.text
    assert _project(asset_dir)["characters"]["阿岚"]["derivatives"]["战甲"]["description"] == "穿战甲"
    assert sheet.read_bytes() == b"armor"
    assert VersionManager(asset_dir).has_versions("character_derivatives", "阿岚/战甲")


def test_deleting_an_asset_takes_its_derivatives_into_the_same_record(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    asset_client.post(f"{CHARACTERS}/阿岚/derivatives", json={"name": "战甲", "description": "穿战甲"})
    sheet = asset_dir / "characters/derivatives/阿岚/战甲.png"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"armor")

    asset_client.delete(f"{CHARACTERS}/阿岚")
    record = _only_record(asset_client)
    assert "characters/derivatives/阿岚/战甲.png" in record["files"]
    assert not sheet.parent.exists()

    asset_client.post(f"{TRASH}/{record['id']}/restore")
    assert sheet.read_bytes() == b"armor"
    assert "战甲" in _project(asset_dir)["characters"]["阿岚"]["derivatives"]


def test_merging_moves_the_merged_asset_into_the_trash_and_it_restores_beside_the_target(
    asset_client, asset_pm, asset_dir
):
    _create(asset_client, "阿岚")
    _create(asset_client, "小岚", description="别名")
    _with_sheet(asset_pm, asset_dir, "小岚", b"merged")

    merged = asset_client.post(f"{CHARACTERS}/小岚/merge", json={"target": "阿岚"})
    assert merged.status_code == 200, merged.text
    record = _only_record(asset_client)
    assert record["object"]["merged_into"] == "阿岚"
    assert not (asset_dir / "characters/小岚.png").exists()
    assert not VersionManager(asset_dir).has_versions("characters", "小岚")

    restored = asset_client.post(f"{TRASH}/{record['id']}/restore")

    assert restored.json()["object"]["name"] == "小岚"
    assert (asset_dir / "characters/小岚.png").read_bytes() == b"merged"
    assert "小岚" in _project(asset_dir)["characters"]["阿岚"]["aliases"]


def test_a_failed_record_leaves_the_asset_in_place(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"sheet")
    before = VersionManager(asset_dir).get_versions("characters", "阿岚")
    # 回收站目录的位置被一个文件占着：删除记录建不起来
    project_trash.trash_dir(asset_dir).parent.mkdir(parents=True, exist_ok=True)
    project_trash.trash_dir(asset_dir).write_text("blocked")

    with TestClient(asset_client.app, raise_server_exceptions=False) as failing:
        assert failing.delete(f"{CHARACTERS}/阿岚").status_code == 500

    assert "阿岚" in _project(asset_dir)["characters"]
    assert (asset_dir / "characters/阿岚.png").read_bytes() == b"sheet"
    assert VersionManager(asset_dir).get_versions("characters", "阿岚") == before
