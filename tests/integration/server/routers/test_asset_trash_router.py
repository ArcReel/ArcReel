"""资产进出项目回收站：删除、衍生删除与资产合并成为删除记录，恢复按落地规则加回，名字被占用时走改名级联加后缀。"""

from __future__ import annotations

import errno
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from lib.artifacts.version_manager import VersionManager
from lib.generation.deleted_target import GenerationTargetDeletedError, trash_result_if_target_deleted
from lib.project import asset_trash, project_trash, trash_restore
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.legacy_shapes import normalize_legacy_shapes
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
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


def _record_of(asset_client: TestClient, **match) -> dict:
    records = asset_client.get(TRASH).json()["records"]
    found = [r for r in records if all(r["object"].get(k) == v for k, v in match.items())]
    assert len(found) == 1, records
    return found[0]


def _record_ids(asset_client: TestClient) -> set[str]:
    return {record["id"] for record in asset_client.get(TRASH).json()["records"]}


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


def _with_reference_files(asset_pm: ProjectManager, asset_dir: Path, name: str) -> dict[str, str]:
    """给角色装上自己名下的参考图与参考音频，返回字段 → 路径。"""
    fields = {"reference_image": f"characters/refs/{name}.png", "reference_audio": f"characters/refs_audio/{name}.wav"}
    for field, rel in fields.items():
        (asset_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (asset_dir / rel).write_bytes(f"{name}:{field}".encode())
    asset_pm.update_asset_entry("character", "demo", name, lambda entry: entry.update(fields))
    return fields


def test_reference_files_cannot_point_at_files_of_another_asset(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    shared = _with_reference_files(asset_pm, asset_dir, "阿岚")
    _create(asset_client, "阿青")

    for field, rel in shared.items():
        response = asset_client.patch(f"{CHARACTERS}/阿青", json={field: rel})
        assert response.status_code == 422, response.text
        assert "阿青" in response.json()["detail"]
    created = asset_client.post(CHARACTERS, json={"name": "阿紫", "description": "", **shared})
    assert created.status_code == 422, created.text

    project = _project(asset_dir)
    assert "阿紫" not in project["characters"]
    assert (project["characters"]["阿青"]["reference_image"], project["characters"]["阿青"]["reference_audio"]) == (
        "",
        "",
    )
    own = asset_client.patch(f"{CHARACTERS}/阿青", json={"reference_audio": "characters/refs_audio/阿青.mp3"})
    assert own.status_code == 200, own.text
    # 已经指着的值原样提交不算新写入：只改描述的请求带着整份条目也能保存。
    unchanged = asset_client.patch(f"{CHARACTERS}/阿岚", json={"description": "新的", **shared})
    assert unchanged.status_code == 200, unchanged.text


def test_an_asset_sheet_cannot_point_at_files_of_another_asset(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"sheet")
    _create(asset_client, "阿青")

    for rel in ("characters/阿岚.png", "characters/refs/阿岚.png", "characters/../characters/阿岚.png"):
        response = asset_client.patch(f"{CHARACTERS}/阿青", json={"character_sheet": rel})
        assert response.status_code == 422, response.text
        assert "阿青" in response.json()["detail"]
    created = asset_client.post(
        CHARACTERS, json={"name": "阿紫", "description": "", "character_sheet": "characters/阿岚.png"}
    )
    assert created.status_code == 422, created.text

    project = _project(asset_dir)
    assert "阿紫" not in project["characters"]
    assert project["characters"]["阿青"]["character_sheet"] == ""
    own = asset_client.patch(f"{CHARACTERS}/阿青", json={"character_sheet": "characters/阿青.png"})
    assert own.status_code == 200, own.text
    # 已经指着的值原样提交不算新写入：只改描述的请求带着整份条目也能保存。
    unchanged = asset_client.patch(
        f"{CHARACTERS}/阿岚", json={"description": "新的", "character_sheet": "characters/阿岚.png"}
    )
    assert unchanged.status_code == 200, unchanged.text


def test_deleting_an_asset_whose_sheet_another_shared_leaves_the_other_sheet_in_place(
    asset_client, asset_pm, asset_dir
):
    """升级前「阿青」的资产图指着「阿岚」的图（旧版 PATCH 接受任意路径）；升级时它得到自己的一份。

    之后删除「阿岚」只带走它名下的图，「阿青」的资产图不悬空；恢复「阿岚」后两边各指自己的图。
    """
    _create(asset_client, "阿岚")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"sheet")
    _create(asset_client, "阿青")
    asset_pm.update_asset_entry(
        "character", "demo", "阿青", lambda entry: entry.update(character_sheet="characters/阿岚.png")
    )
    normalize_legacy_shapes(asset_dir, schema_version=CURRENT_PROJECT_SCHEMA_VERSION - 1, record=[])

    assert asset_client.delete(f"{CHARACTERS}/阿岚").status_code == 200

    sheet = _project(asset_dir)["characters"]["阿青"]["character_sheet"]
    assert sheet == "characters/阿青.png"
    assert (asset_dir / sheet).read_bytes() == b"sheet"
    assert not (asset_dir / "characters/阿岚.png").exists()

    restored = asset_client.post(f"{TRASH}/{_only_record(asset_client)['id']}/restore")

    assert restored.status_code == 200, restored.text
    characters = _project(asset_dir)["characters"]
    for name in ("阿岚", "阿青"):
        assert characters[name]["character_sheet"] == f"characters/{name}.png"
        assert (asset_dir / f"characters/{name}.png").read_bytes() == b"sheet"


def test_deleting_a_character_takes_its_own_reference_files(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    files = _with_reference_files(asset_pm, asset_dir, "阿岚")

    assert asset_client.delete(f"{CHARACTERS}/阿岚").status_code == 200

    assert set(files.values()) <= set(_only_record(asset_client)["files"])
    assert not any((asset_dir / rel).exists() for rel in files.values())


def test_a_character_restored_under_a_new_name_points_at_files_that_exist(asset_client, asset_pm, asset_dir):
    """删除、新建同名、再恢复：换名恢复的资产，每个文件字段都指向随它改名加回的文件。

    升级前「阿青」共用「阿岚」的参考图与参考音频（旧版 PATCH 接受任意路径）；升级时它得到自己的一份，
    删除「阿岚」于是带走它名下的全部文件，换名恢复时随改名级联一起改名加回。
    """
    _create(asset_client, "阿岚", "旧的")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"sheet")
    shared = _with_reference_files(asset_pm, asset_dir, "阿岚")
    _create(asset_client, "阿青")
    asset_pm.update_asset_entry("character", "demo", "阿青", lambda entry: entry.update(shared))
    normalize_legacy_shapes(asset_dir, schema_version=CURRENT_PROJECT_SCHEMA_VERSION - 1, record=[])
    asset_client.delete(f"{CHARACTERS}/阿岚")
    record = _only_record(asset_client)["id"]
    _create(asset_client, "阿岚", "新的")

    restored = asset_client.post(f"{TRASH}/{record}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["object"]["name"] == "阿岚 (1)"
    characters = _project(asset_dir)["characters"]
    entry = characters["阿岚 (1)"]
    assert (entry["character_sheet"], entry["reference_image"], entry["reference_audio"]) == (
        "characters/阿岚 (1).png",
        "characters/refs/阿岚 (1).png",
        "characters/refs_audio/阿岚 (1).wav",
    )
    assert (asset_dir / entry["character_sheet"]).read_bytes() == b"sheet"
    for field in shared:
        assert (asset_dir / entry[field]).read_bytes() == f"阿岚:{field}".encode()
        assert (asset_dir / characters["阿青"][field]).read_bytes() == f"阿岚:{field}".encode()


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


@pytest.mark.parametrize("target_first", [False, True])
def test_restoring_a_deleted_asset_before_its_late_generation_keeps_the_original_name(
    asset_client, asset_pm, asset_dir, tmp_path, target_first
):
    _create(asset_client, "阿岚")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"old")
    asset_client.delete(f"{CHARACTERS}/阿岚")
    asset_record = _only_record(asset_client)
    staged = tmp_path / "generated.png"
    staged.write_bytes(b"late-generation")
    with pytest.raises(GenerationTargetDeletedError) as deleted:
        trash_result_if_target_deleted(asset_dir, resource_type="characters", resource_id="阿岚", staged_file=staged)
    generation_id = deleted.value.record.id
    order = [asset_record["id"], generation_id] if target_first else [generation_id, asset_record["id"]]
    results = {}
    for record_id in order:
        restored = asset_client.post(f"{TRASH}/{record_id}/restore")
        assert restored.status_code == 200, restored.text
        results[record_id] = restored.json()

    name = "阿岚" if target_first else "阿岚 (1)"
    assert results[asset_record["id"]]["object"]["name"] == name
    entry = _project(asset_dir)["characters"][name]
    assert (asset_dir / entry["character_sheet"]).read_bytes() == b"old"
    [result_file] = results[generation_id]["restored"]
    assert result_file["as_history"] is target_first
    assert (asset_dir / result_file["path"]).read_bytes() == b"late-generation"
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


def _with_derivative_sheet(asset_client, asset_dir) -> Path:
    asset_client.post(f"{CHARACTERS}/阿岚/derivatives", json={"name": "战甲", "description": "穿战甲"})
    sheet = asset_dir / "characters/derivatives/阿岚/战甲.png"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"armor")
    VersionManager(asset_dir).add_version("character_derivatives", "阿岚/战甲", "p", source_file=sheet)
    return sheet


def test_a_derivative_deleted_before_its_owner_restores_only_after_the_owner_is_back(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    sheet = _with_derivative_sheet(asset_client, asset_dir)
    asset_client.delete(f"{CHARACTERS}/阿岚/derivatives/战甲")
    derivative = _record_of(asset_client, kind="asset_derivative")["id"]
    asset_client.delete(f"{CHARACTERS}/阿岚")
    owner = _record_of(asset_client, kind="asset")["id"]

    # 本体还在项目回收站里：衍生只能随本体恢复，不降级成未登记的文件。
    too_early = asset_client.post(f"{TRASH}/{derivative}/restore")
    assert too_early.status_code == 409
    assert "阿岚" in too_early.json()["detail"]
    assert derivative in _record_ids(asset_client)

    assert asset_client.post(f"{TRASH}/{owner}/restore").status_code == 200
    restored = asset_client.post(f"{TRASH}/{derivative}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["degraded"] is False
    assert _project(asset_dir)["characters"]["阿岚"]["derivatives"]["战甲"]["description"] == "穿战甲"
    assert sheet.read_bytes() == b"armor"
    assert VersionManager(asset_dir).has_versions("character_derivatives", "阿岚/战甲")


def test_a_derivative_whose_owner_was_purged_restores_only_its_files(asset_client, asset_pm, asset_dir):
    _create(asset_client, "阿岚")
    sheet = _with_derivative_sheet(asset_client, asset_dir)
    asset_client.delete(f"{CHARACTERS}/阿岚/derivatives/战甲")
    derivative = _record_of(asset_client, kind="asset_derivative")["id"]
    asset_client.delete(f"{CHARACTERS}/阿岚")
    assert asset_client.delete(f"{TRASH}/{_record_of(asset_client, kind='asset')['id']}").status_code == 200

    restored = asset_client.post(f"{TRASH}/{derivative}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["degraded"] is True
    assert sheet.read_bytes() == b"armor"


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


def test_a_suffixed_restore_lands_even_when_the_snapshot_already_held_the_suffixed_name(
    asset_client, asset_pm, asset_dir
):
    """删除第二个「Alice」时项目里有「Alice (1)」（条目与历史）；之后它也被删，恢复时这个名字只在临时项目里被占。"""
    _create(asset_client, "Alice", "first")
    _with_sheet(asset_pm, asset_dir, "Alice", b"first")
    asset_client.delete(f"{CHARACTERS}/Alice")
    first = _record_of(asset_client, name="Alice")["id"]
    _create(asset_client, "Alice", "second")
    _with_sheet(asset_pm, asset_dir, "Alice", b"second")
    assert asset_client.post(f"{TRASH}/{first}/restore").json()["object"]["name"] == "Alice (1)"
    asset_client.delete(f"{CHARACTERS}/Alice")
    second = _record_of(asset_client, name="Alice")["id"]
    asset_client.delete(f"{CHARACTERS}/Alice (1)")
    _create(asset_client, "Alice", "third")

    restored = asset_client.post(f"{TRASH}/{second}/restore")

    assert restored.status_code == 200, restored.text
    landed = restored.json()["object"]["name"]
    assert _project(asset_dir)["characters"][landed]["description"] == "second"
    assert (asset_dir / f"characters/{landed}.png").read_bytes() == b"second"
    assert len(VersionManager(asset_dir).get_versions("characters", landed)["versions"]) == 2


def test_a_suffixed_restore_lands_when_the_snapshot_holds_only_leftover_history_of_the_suffixed_name(
    asset_client, asset_pm, asset_dir
):
    _create(asset_client, "Alice", "old")
    _with_sheet(asset_pm, asset_dir, "Alice", b"old")
    leftover = asset_dir / "characters" / "leftover.png"
    leftover.write_bytes(b"x")
    VersionManager(asset_dir).add_version("characters", "Alice (1)", "p", source_file=leftover)
    leftover.unlink()
    asset_client.delete(f"{CHARACTERS}/Alice")
    record = _record_of(asset_client, name="Alice")["id"]
    # 当前项目里清掉这份残留历史，「Alice (1)」只在临时项目里被占
    VersionManager(asset_dir).detach_resources([("characters", "Alice (1)")])
    _create(asset_client, "Alice", "new")

    restored = asset_client.post(f"{TRASH}/{record}/restore")

    assert restored.status_code == 200, restored.text


def _deleted_with_sheet(asset_client, asset_pm, asset_dir) -> str:
    _create(asset_client, "阿岚")
    _with_sheet(asset_pm, asset_dir, "阿岚", b"sheet")
    asset_client.delete(f"{CHARACTERS}/阿岚")
    return _only_record(asset_client)["id"]


def _disk_full(*_args, **_kwargs):
    raise OSError(errno.ENOSPC, "No space left on device")


@pytest.mark.parametrize(
    "failing_step",
    [
        (trash_restore, "_stage"),
        (trash_restore, "migrate_project_dir"),
        (asset_trash, "_read_project"),
        (trash_restore.Landing, "place"),
    ],
    ids=["staging", "migration", "take-rule", "placing"],
)
def test_an_io_error_keeps_the_record_for_a_retry(asset_client, asset_pm, asset_dir, monkeypatch, failing_step):
    """磁盘满等读写故障排除后重试就能按原结构恢复：哪一步出错都不降级、不消耗记录。"""
    record_id = _deleted_with_sheet(asset_client, asset_pm, asset_dir)

    with monkeypatch.context() as patched:
        patched.setattr(*failing_step, _disk_full)
        with TestClient(asset_client.app, raise_server_exceptions=False) as failing:
            assert failing.post(f"{TRASH}/{record_id}/restore").status_code == 500

    assert _only_record(asset_client)["has_snapshot"] is True
    assert "阿岚" not in _project(asset_dir).get("characters", {})
    restored = asset_client.post(f"{TRASH}/{record_id}/restore")
    assert restored.status_code == 200, restored.text
    assert restored.json()["degraded"] is False
    assert (asset_dir / "characters/阿岚.png").read_bytes() == b"sheet"


def test_a_missing_file_during_migration_still_degrades_to_restoring_the_files(
    asset_client, asset_pm, asset_dir, monkeypatch
):
    """记录里的文件缺失是永久状态，重试也不会成功：仍降级，至少把还在的文件加回。"""
    record_id = _deleted_with_sheet(asset_client, asset_pm, asset_dir)

    def missing(*_args, **_kwargs):
        raise FileNotFoundError(errno.ENOENT, "No such file or directory")

    monkeypatch.setattr(trash_restore, "migrate_project_dir", missing)
    restored = asset_client.post(f"{TRASH}/{record_id}/restore")

    assert restored.status_code == 200, restored.text
    assert restored.json()["degraded"] is True
    assert (asset_dir / "characters/阿岚.png").read_bytes() == b"sheet"
