"""从资产库应用的资产图与衍生资产图：迁移补录与各种归档导入得出与源项目相同的时效结论。

归档导入的校验要求资产描述非空，描述为空的本体只出现在补录用例里。
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lib.artifacts.artifact_manifest import MANIFEST_FILENAME, ArtifactStatus
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.project.asset_derivatives import derivative_sheet_relative_path
from lib.project.asset_types import DERIVATIVES_FIELD
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import migrate_project_dir
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from server.services.project.project_archive import ARCHIVE_MANIFEST_NAME, ProjectArchiveService
from tests.integration.server.asset_library_support import (
    LIBRARY_SHEETS_ALL_CURRENT,
    character_sheet_statuses,
    store_character_with_derivatives,
)
from tests.integration.server.derivative_sheet_support import solid_png_bytes

_ALL_CURRENT = LIBRARY_SHEETS_ALL_CURRENT


@pytest.fixture
def library_client(assets_env) -> tuple[TestClient, ProjectManager]:
    return assets_env["client"], assets_env["pm"]


def _applied_project(
    client: TestClient, pm: ProjectManager, *, owner_description: str, conflict_policy: str = "skip"
) -> Path:
    """把带两个衍生资产图的库角色应用到项目 demo；demo 不存在时新建。"""

    asset_id = store_character_with_derivatives(client, pm, owner_description=owner_description)
    if not pm.project_exists("demo"):
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    applied = client.post(
        "/api/v1/assets/apply-to-project",
        json={"asset_ids": [asset_id], "target_project": "demo", "conflict_policy": conflict_policy},
    )
    assert applied.status_code == 200, applied.text
    return pm.get_project_path("demo")


def _statuses(pm: ProjectManager) -> dict[str, ArtifactStatus]:
    return character_sheet_statuses(pm, "demo", "王")


def _edit_descriptions_and_style(pm: ProjectManager) -> None:
    def _edit(project: dict) -> None:
        entry = project["characters"]["王"]
        entry["description"] = "黑发老者"
        for derivative in entry[DERIVATIVES_FIELD].values():
            derivative["description"] = "改写后的衍生"
        project["style_description"] = "胶片颗粒"

    pm.update_project("demo", _edit)


@pytest.mark.parametrize("owner_description", ["", "白衣少年"])
def test_backfill_claims_applied_sheets_by_the_image(library_client, owner_description):
    client, pm = library_client
    project_dir = _applied_project(client, pm, owner_description=owner_description)
    pm.update_project("demo", lambda project: project.__setitem__("schema_version", 7))
    (project_dir / MANIFEST_FILENAME).unlink()

    assert migrate_project_dir(project_dir) is True

    assert _statuses(pm) == _ALL_CURRENT
    _edit_descriptions_and_style(pm)
    assert _statuses(pm) == _ALL_CURRENT


def _strip_manifest_envelope(archive_path: Path, target_path: Path) -> None:
    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(target_path, "w") as target:
        for member in source.infolist():
            content = source.read(member)
            if member.filename == f"demo/{ARCHIVE_MANIFEST_NAME}":
                envelope = json.loads(content)
                envelope.pop("artifact_manifest")
                content = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            target.writestr(member, content)


@pytest.mark.parametrize(
    ("scope", "envelope"),
    [("full", True), ("current", True), ("full", False), ("current", False)],
)
@pytest.mark.parametrize("owner_description", ["", "白衣少年"])
def test_archive_import_reaches_the_source_conclusion(library_client, tmp_path, scope, envelope, owner_description):
    client, pm = library_client
    project_dir = _applied_project(client, pm, owner_description=owner_description)
    service = ProjectArchiveService(pm)
    archive_path, _ = service.export_project("demo", scope=scope)
    if not envelope:
        stripped = tmp_path / "stripped.zip"
        _strip_manifest_envelope(archive_path, stripped)
        archive_path = stripped
    shutil.rmtree(project_dir)

    service.import_project_archive(archive_path, uploaded_filename="demo.zip")

    assert _statuses(pm) == _ALL_CURRENT
    _edit_descriptions_and_style(pm)
    assert _statuses(pm) == _ALL_CURRENT


def test_current_package_carries_only_the_selected_applied_derivatives_and_keeps_the_source_conclusion(
    library_client,
):
    """项目里已有生成的衍生「战斗装」与「旧装」，覆盖应用后战斗装选中带入的版本，旧装仍是生成的。"""

    client, pm = library_client
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.add_character("demo", "王", "项目里的王")
    project_dir = pm.get_project_path("demo")
    table = {}
    for derivative_name in ("战斗装", "旧装"):
        relative = derivative_sheet_relative_path("王", derivative_name)
        (project_dir / relative).parent.mkdir(parents=True, exist_ok=True)
        (project_dir / relative).write_bytes(solid_png_bytes((90, 90, 90)))
        VersionManager(project_dir).add_version(
            CHARACTER_DERIVATIVE_RESOURCE_TYPE, f"王/{derivative_name}", "生成", source_file=project_dir / relative
        )
        table[derivative_name] = {"description": "旧甲", "character_sheet": relative}
    pm.update_asset_entry("character", "demo", "王", lambda entry: entry.__setitem__(DERIVATIVES_FIELD, table))

    _applied_project(client, pm, owner_description="白衣少年", conflict_policy="overwrite")
    source_statuses = _statuses(pm)
    history = json.loads((project_dir / "versions" / "versions.json").read_text(encoding="utf-8"))
    applied = {
        resource_id: next(record for record in resource["versions"] if record["version"] == resource["current_version"])
        for resource_id, resource in history[CHARACTER_DERIVATIVE_RESOURCE_TYPE].items()
        if resource_id != "王/旧装"
    }
    assert {resource_id: record["source"] for resource_id, record in applied.items()} == {
        "王/战斗装": MANUAL_UPLOAD_VERSION_SOURCE,
        "王/便装": MANUAL_UPLOAD_VERSION_SOURCE,
    }
    service = ProjectArchiveService(pm)

    archive_path, _ = service.export_project("demo", scope="current")

    with zipfile.ZipFile(archive_path) as archive:
        packed = {
            name.removeprefix("demo/")
            for name in archive.namelist()
            if name.startswith("demo/versions/characters/derivatives/") and not name.endswith("/")
        }
        payload = json.loads(archive.read("demo/versions/versions.json"))
    assert packed == {record["file"] for record in applied.values()}
    assert payload[CHARACTER_DERIVATIVE_RESOURCE_TYPE] == {
        resource_id: {"current_version": record["version"], "versions": [record]}
        for resource_id, record in applied.items()
    }

    shutil.rmtree(project_dir)
    service.import_project_archive(archive_path, uploaded_filename="demo.zip")
    assert _statuses(pm) == source_statuses
    assert {name: source_statuses[name] for name in _ALL_CURRENT} == _ALL_CURRENT
    _edit_descriptions_and_style(pm)
    assert {name: status for name, status in _statuses(pm).items() if name in _ALL_CURRENT} == _ALL_CURRENT
