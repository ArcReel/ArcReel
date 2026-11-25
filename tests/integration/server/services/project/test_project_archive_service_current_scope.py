"""仅当前版本导出：包里的版本记录与快照文件一一对应，导入后版本面板与还原可用。"""

import json
import shutil
import zipfile
from hashlib import sha256
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver, activate_artifact_target_state
from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactStatus, ProjectArtifactManifestAdapter
from lib.artifacts.version_manager import MANUAL_UPLOAD_VERSION_SOURCE, VersionManager
from lib.artifacts.visual_artifact_provenance import build_uploaded_storyboard_basis
from lib.project.asset_types import DERIVATIVES_FIELD
from lib.project.project_manager import ProjectManager, get_project_manager
from lib.project.resource_paths import (
    CHARACTER_DERIVATIVE_RESOURCE_TYPE,
    RESOURCE_TYPES,
    resource_relative_path,
)
from server.routers import versions as versions_router
from server.services.project.project_archive import CURRENT_EXPORT_VERSION_RETENTION, ProjectArchiveService
from tests.integration.server.derivative_sheet_support import solid_png_bytes

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


def test_current_package_keeps_selected_storyboard_upload_but_drops_generated_history(tmp_path):
    source = ProjectManager(tmp_path / "source")
    source.create_project("demo")
    source.create_project_metadata("demo", "Demo", "Anime", "narration")
    project_dir = source.get_project_path("demo")
    versions = VersionManager(project_dir)
    upload_id, generated_id = "E1S01", "E1S02"
    upload_path = resource_relative_path("storyboards", upload_id)
    generated_path = resource_relative_path("storyboards", generated_id)
    uploaded = solid_png_bytes((200, 30, 10))
    generated = solid_png_bytes((10, 30, 200))
    for shot_id, path, history in (
        (upload_id, upload_path, [(generated, {}), (uploaded, {"source": MANUAL_UPLOAD_VERSION_SOURCE})]),
        (generated_id, generated_path, [(uploaded, {"source": MANUAL_UPLOAD_VERSION_SOURCE}), (generated, {})]),
    ):
        current = project_dir / path
        current.parent.mkdir(parents=True, exist_ok=True)
        for content, metadata in history:
            current.write_bytes(content)
            versions.add_version("storyboards", shot_id, "林间小路", source_file=current, **metadata)

    script_file = "episode_1.json"
    source.save_script(
        "demo",
        {
            "episode": 1,
            "title": "E1",
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": shot_id,
                    "novel_text": "她走过树林。",
                    "duration_seconds": 5,
                    "characters_in_segment": [],
                    "scenes": [],
                    "props": [],
                    "image_prompt": "林间小路",
                    "video_prompt": "镜头缓慢推进",
                    "generated_assets": {"storyboard_image": path},
                }
                for shot_id, path in ((upload_id, upload_path), (generated_id, generated_path))
            ],
        },
        script_file,
        validate=False,
    )
    activate_artifact_target_state(project_dir, bump_schema=False)
    upload_history = versions.get_versions("storyboards", upload_id)
    [selected_upload] = [record for record in upload_history["versions"] if record["is_current"]]
    snapshot = selected_upload["file"]

    archive_path, _ = ProjectArchiveService(source).export_project("demo", scope="current")
    target = ProjectManager(tmp_path / "target")
    ProjectArchiveService(target).import_project_archive(archive_path, uploaded_filename="demo.zip")

    imported_dir = target.get_project_path("demo")
    imported_versions = VersionManager(imported_dir)
    assert imported_versions.get_versions("storyboards", upload_id) == {
        "current_version": selected_upload["version"],
        "versions": [selected_upload],
    }
    assert imported_versions.get_versions("storyboards", generated_id)["versions"] == []
    snapshots, recorded, _ = _packed_history(archive_path)
    assert snapshots == recorded == {snapshot}
    assert (imported_dir / snapshot).read_bytes() == (imported_dir / upload_path).read_bytes() == uploaded
    assert (imported_dir / generated_path).read_bytes() == generated

    upload_key = ArtifactKey.episode_storyboard(1, upload_id)
    entry = ProjectArtifactManifestAdapter(imported_dir).get_entry(upload_key)
    assert entry is not None
    assert entry.basis_digest == build_uploaded_storyboard_basis(content_digest=sha256(uploaded).hexdigest()).digest

    generated_key = ArtifactKey.episode_storyboard(1, generated_id)
    resolver = ArtifactCurrencyResolver(imported_dir)
    assert resolver.compare(upload_key, artifact_path=upload_path).status is ArtifactStatus.CURRENT
    assert resolver.compare(generated_key, artifact_path=generated_path).status is ArtifactStatus.CURRENT

    with target.locked_script("demo", script_file, validate=False) as script:
        for segment in script["segments"]:
            segment["image_prompt"] = "黄昏的街角"

    resolver = ArtifactCurrencyResolver(imported_dir)
    assert resolver.compare(upload_key, artifact_path=upload_path).status is ArtifactStatus.CURRENT
    assert resolver.compare(generated_key, artifact_path=generated_path).status is ArtifactStatus.STALE


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


def test_current_package_drops_a_malformed_selected_bucket_and_stays_importable(tmp_path):
    pm = ProjectManager(tmp_path / "projects")
    project_dir = _project(pm)
    _generate_versions(project_dir, "videos", "E1S01", 2)
    versions_path = project_dir / "versions" / "versions.json"
    payload = json.loads(versions_path.read_text(encoding="utf-8"))
    payload["audio"] = []
    versions_path.write_text(json.dumps(payload), encoding="utf-8")
    service = ProjectArchiveService(pm)

    archive_path, _ = service.export_project("demo", scope="current")

    shutil.rmtree(project_dir)
    service.import_project_archive(archive_path, uploaded_filename="demo.zip")

    imported = json.loads((pm.get_project_path("demo") / "versions" / "versions.json").read_text(encoding="utf-8"))
    assert "audio" not in imported
    assert "videos" in imported
