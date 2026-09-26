"""仅当前版本导出：包里的版本记录与快照文件一一对应，导入后版本面板与还原可用。"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from lib.artifacts.version_manager import VersionManager
from lib.project.asset_types import DERIVATIVES_FIELD
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.project.resource_paths import (
    CHARACTER_DERIVATIVE_RESOURCE_TYPE,
    RESOURCE_TYPES,
    resource_relative_path,
)
from server.routers import versions as versions_router
from server.services.project.project_archive import CURRENT_EXPORT_VERSION_RETENTION, ProjectArchiveService

_DERIVATIVE_ID = "Hero/Armor"
_PRODUCT_ID = "Cup"
_RESOURCE_IDS = {
    CHARACTER_DERIVATIVE_RESOURCE_TYPE: _DERIVATIVE_ID,
    "products": _PRODUCT_ID,
    "grids": "grid_0123456789ab",
}


def _resource_id(resource_type: str) -> str:
    return _RESOURCE_IDS.get(resource_type, "E1S01")


def _project(pm: ProjectManager) -> Path:
    """启用宫格、带商品与角色衍生登记的项目；各资源的正式文件由用例自行写入。"""

    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    pm.update_project("demo", lambda project: project.update(generation_mode="storyboard", grid_storyboard=True))
    pm.add_product("demo", _PRODUCT_ID, "白色马克杯")
    pm.update_product_sheet("demo", _PRODUCT_ID, resource_relative_path("products", _PRODUCT_ID))
    pm.add_character("demo", "Hero", "少年")

    def _register(entry: dict) -> None:
        entry[DERIVATIVES_FIELD] = {
            "Armor": {
                "description": "换上重甲",
                "character_sheet": resource_relative_path(CHARACTER_DERIVATIVE_RESOURCE_TYPE, _DERIVATIVE_ID),
            }
        }

    pm.update_asset_entry("character", "demo", "Hero", _register)
    return pm.get_project_path("demo")


def _generate_versions(project_dir: Path, resource_type: str, resource_id: str, count: int) -> list[bytes]:
    """按顺序生成 ``count`` 个版本，最后一个为选中的当前内容；返回各版本字节。"""

    current = project_dir / resource_relative_path(resource_type, resource_id)
    current.parent.mkdir(parents=True, exist_ok=True)
    manager = VersionManager(project_dir)
    contents = []
    for number in range(1, count + 1):
        content = f"{resource_type}-v{number}".encode()
        current.write_bytes(content)
        manager.add_version(resource_type, resource_id, f"prompt {number}", source_file=current)
        contents.append(content)
    return contents


def _packed_history(archive_path: Path) -> tuple[set[str], set[str], dict]:
    """返回（包内快照文件、versions.json 记录指向的文件、versions.json）。路径相对项目根。"""

    with zipfile.ZipFile(archive_path) as archive:
        names = archive.namelist()
        payload = json.loads(archive.read("demo/versions/versions.json"))
    snapshots = {
        name.removeprefix("demo/")
        for name in names
        if name.startswith("demo/versions/") and not name.endswith("/") and name != "demo/versions/versions.json"
    }
    recorded = {
        record["file"] for bucket in payload.values() for resource in bucket.values() for record in resource["versions"]
    }
    return snapshots, recorded, payload


def test_every_resource_type_has_a_current_export_retention():
    assert set(CURRENT_EXPORT_VERSION_RETENTION) == set(RESOURCE_TYPES)


@pytest.mark.parametrize("resource_type", RESOURCE_TYPES)
def test_current_package_packs_exactly_the_snapshots_its_version_records_name(tmp_path, resource_type):
    pm = ProjectManager(tmp_path / "projects")
    project_dir = _project(pm)
    resource_id = _resource_id(resource_type)
    exported_content = _generate_versions(project_dir, resource_type, resource_id, 3)[-1]
    service = ProjectArchiveService(pm)

    archive_path, _ = service.export_project("demo", scope="current")

    snapshots, recorded, payload = _packed_history(archive_path)
    assert snapshots == recorded
    assert all(len(resource["versions"]) <= 1 for bucket in payload.values() for resource in bucket.values())

    shutil.rmtree(project_dir)
    service.import_project_archive(archive_path, uploaded_filename="demo.zip")

    imported_dir = pm.get_project_path("demo")
    assert (imported_dir / resource_relative_path(resource_type, resource_id)).read_bytes() == exported_content
    history = VersionManager(imported_dir).get_versions(resource_type, resource_id)
    assert all((imported_dir / record["file"]).is_file() for record in history["versions"])


@pytest.mark.parametrize("resource_type", ["products", "grids", CHARACTER_DERIVATIVE_RESOURCE_TYPE])
async def test_imported_current_package_can_regenerate_and_restore_the_exported_content(resource_type):
    pm = get_project_manager()
    project_dir = _project(pm)
    resource_id = _resource_id(resource_type)
    exported_content = _generate_versions(project_dir, resource_type, resource_id, 2)[-1]
    service = ProjectArchiveService(pm)
    archive_path, _ = service.export_project("demo", scope="current")
    shutil.rmtree(project_dir)
    service.import_project_archive(archive_path, uploaded_filename="demo.zip")
    imported_dir = pm.get_project_path("demo")
    current = imported_dir / resource_relative_path(resource_type, resource_id)

    staged = current.with_name(f".staged{current.suffix}")
    staged.write_bytes(b"regenerated")
    VersionManager(imported_dir).commit_staged_version(
        resource_type, resource_id, "regenerate", staged_file=staged, current_file=current
    )
    panel = await versions_router.read_resource_versions("demo", resource_type, resource_id)
    assert all((imported_dir / record["file"]).is_file() for record in panel["versions"])
    [exported_version] = [
        record["version"]
        for record in panel["versions"]
        if (imported_dir / record["file"]).read_bytes() == exported_content
    ]

    await versions_router.restore_resource_version("demo", resource_type, resource_id, exported_version)

    assert current.read_bytes() == exported_content
