"""带上传版本记录的资产图：迁移补录与各种归档导入得出同一时效结论，无上传记录的存量结论不变。

归档导入的校验要求资产描述非空，描述为空的资产只出现在补录用例里。
"""

import json
import shutil
import zipfile
from pathlib import Path

import pytest

from lib.artifacts.artifact_activation import ArtifactCurrencyResolver
from lib.artifacts.artifact_manifest import MANIFEST_FILENAME, ArtifactKey
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations.runner import migrate_project_dir
from server.services.currency.upload_finalize import install_manual_asset_sheet_upload
from server.services.project.project_archive import ARCHIVE_MANIFEST_NAME, ProjectArchiveService
from tests.integration.server.derivative_sheet_support import solid_png_bytes

#: Alice 描述为空时上传；Bob 上传后改了描述；Carol 与 Dave 的资产图没有上传版本记录。
_BACKFILL_EXPECTED = {"Alice": "current", "Bob": "current", "Carol": "current", "Dave": "missing"}
_ARCHIVE_EXPECTED = {"Bob": "current", "Carol": "current"}


def _statuses(project_dir: Path, names) -> dict[str, str]:
    resolver = ArtifactCurrencyResolver(project_dir)
    return {
        name: resolver.compare(
            ArtifactKey.asset_sheet("character", name), artifact_path=f"characters/{name}.png"
        ).status.value
        for name in names
    }


def _backfilled_project(pm: ProjectManager, *, with_empty_descriptions: bool) -> Path:
    """建项目、上传资产图、放无记录的资产图，再退回 v7 走整条迁移链补录。"""

    pm.create_project("demo")
    pm.create_project_metadata("demo", "Demo", "Anime", "narration")
    project_dir = pm.get_project_path("demo")
    pm.add_character("demo", "Bob", "银发少女")
    pm.add_character("demo", "Carol", "存量角色")
    uploads = [("Bob", (10, 200, 10))]
    unrecorded = [("Carol", (10, 10, 200))]
    if with_empty_descriptions:
        pm.add_character("demo", "Alice", "")
        pm.add_character("demo", "Dave", "")
        uploads.append(("Alice", (200, 10, 10)))
        unrecorded.append(("Dave", (90, 90, 90)))
    for name, color in uploads:
        install_manual_asset_sheet_upload(
            project_manager=pm,
            project_name="demo",
            asset_type="character",
            name=name,
            sheet_path=f"characters/{name}.png",
            content=solid_png_bytes(color),
            original_filename=f"{name}.png",
        )
    for name, color in unrecorded:
        (project_dir / "characters" / f"{name}.png").write_bytes(solid_png_bytes(color))

    def _legacy(project: dict) -> None:
        for name, _color in unrecorded:
            project["characters"][name]["character_sheet"] = f"characters/{name}.png"
        project["characters"]["Bob"]["description"] = "黑发少年"
        project["schema_version"] = 7

    pm.update_project("demo", _legacy)
    (project_dir / MANIFEST_FILENAME).unlink()
    assert migrate_project_dir(project_dir) is True
    return project_dir


def _strip_manifest_envelope(archive_path: Path, target_path: Path) -> None:
    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(target_path, "w") as target:
        for member in source.infolist():
            content = source.read(member)
            if member.filename == f"demo/{ARCHIVE_MANIFEST_NAME}":
                envelope = json.loads(content)
                envelope.pop("artifact_manifest")
                content = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
            target.writestr(member, content)


def test_backfill_claims_uploaded_sheets_by_the_upload_and_leaves_unrecorded_sheets_unchanged(tmp_path):
    project_dir = _backfilled_project(ProjectManager(tmp_path / "projects"), with_empty_descriptions=True)

    assert _statuses(project_dir, _BACKFILL_EXPECTED) == _BACKFILL_EXPECTED


@pytest.mark.parametrize(
    ("scope", "envelope"),
    [("full", True), ("current", True), ("full", False), ("current", False)],
)
def test_archive_import_reaches_the_backfill_conclusion(tmp_path, scope, envelope):
    pm = ProjectManager(tmp_path / "projects")
    project_dir = _backfilled_project(pm, with_empty_descriptions=False)
    service = ProjectArchiveService(pm)
    archive_path, _ = service.export_project("demo", scope=scope)
    if not envelope:
        stripped = tmp_path / "stripped.zip"
        _strip_manifest_envelope(archive_path, stripped)
        archive_path = stripped
    shutil.rmtree(project_dir)

    service.import_project_archive(archive_path, uploaded_filename="demo.zip")

    imported_dir = pm.get_project_path("demo")
    assert _statuses(imported_dir, _ARCHIVE_EXPECTED) == _ARCHIVE_EXPECTED

    def _edit(project: dict) -> None:
        project["characters"]["Bob"]["description"] = "白发老者"
        project["characters"]["Carol"]["description"] = "改写后的角色"
        project["style_description"] = "胶片颗粒"

    pm.update_project("demo", _edit)
    assert _statuses(imported_dir, _ARCHIVE_EXPECTED) == {"Bob": "current", "Carol": "stale"}
