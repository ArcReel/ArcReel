import json
from pathlib import Path
from zipfile import ZipFile

import pytest

from lib.artifacts.version_manager import VersionManager
from lib.project.project_manager import ProjectManager
from lib.project.project_migrations import run_project_migrations
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from lib.workflow.workflow_plan import build_workflow_plan
from lib.workflow.workflow_state import WorkflowStateService
from server.services.project.project_archive import ProjectArchiveService

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures/perf_before"


def test_workflow_before(tmp_path):
    pm = ProjectManager(tmp_path)
    with ZipFile(FIXTURES / "workflow-v2.zip") as archive:
        archive.extractall(pm.projects_dir / "demo")
    status = WorkflowStateService(pm).get_status("demo")
    assert status.model_dump(mode="json") == json.loads((FIXTURES / "wf_status.json").read_bytes())
    plan = build_workflow_plan(status)
    assert plan.model_dump(mode="json") == json.loads((FIXTURES / "wf_plan.json").read_bytes())


def test_legacy_before(tmp_path):
    project = tmp_path / "legacy"
    with ZipFile(FIXTURES / "legacy-v1.zip") as archive:
        archive.extractall(project)
    assert run_project_migrations(tmp_path).migrated == ["legacy"]
    assert json.loads((project / "project.json").read_bytes())["schema_version"] == CURRENT_PROJECT_SCHEMA_VERSION


@pytest.mark.parametrize("scope", ["full", "current"])
def test_archive_before(tmp_path, scope):
    pm = ProjectManager(tmp_path)
    result = ProjectArchiveService(pm).import_project_archive(FIXTURES / f"{scope}.zip")
    path = pm.get_project_path(result.project_name)
    assert (path / "products/Cup.png").read_bytes() == b"products-v3"
    records = VersionManager(path).get_versions("products", "Cup")["versions"]
    expected_versions = [1, 2, 3] if scope == "full" else []
    assert [row["version"] for row in records] == expected_versions
    archive, _ = ProjectArchiveService(pm).export_project(result.project_name, scope=scope)
    try:
        fresh = ProjectManager(tmp_path / "restored")
        imported = ProjectArchiveService(fresh).import_project_archive(archive)
        restored = fresh.get_project_path(imported.project_name)
        assert (restored / "products/Cup.png").read_bytes() == b"products-v3"
        assert VersionManager(restored).get_versions("products", "Cup") == VersionManager(path).get_versions(
            "products", "Cup"
        )
    finally:
        archive.unlink(missing_ok=True)


@pytest.mark.parametrize(
    ("kind", "key"), [("character_derivatives", "Hero/Armor"), ("characters", "Café"), ("characters", "Cafe\u0301")]
)
def test_version_allocation_before(tmp_path, kind, key):
    pm = ProjectManager(tmp_path)
    pm.create_project("demo")
    path = pm.get_project_path("demo")
    manager = VersionManager(path)
    assert manager.get_versions(kind, key) == {"versions": [], "current_version": 0}
    current = path / "current.png"
    current.write_bytes(b"image")
    assert manager.add_version(kind, key, "one", current) == 1
    assert manager.add_version(kind, key, "two", current) == 2
    manager.restore_version(kind, key, 1, current)
    second = manager.get_versions(kind, key)["versions"][1]
    assert key in second["file"]
    manager.delete_history_version(second["file"])
    assert manager.add_version(kind, key, "replacement", current) == 2


def test_legacy_missing_selection(tmp_path):
    manager = VersionManager(tmp_path)
    manager.versions_file.parent.mkdir(parents=True)
    manager.versions_file.write_text('{"characters":{"Hero":{"versions":[]}}}', encoding="utf-8")
    assert manager.get_versions("characters", "Hero") == {"versions": [], "current_version": 0}


def test_append_with_legacy_missing_pointer(tmp_path):
    manager = VersionManager(tmp_path)
    manager.versions_file.parent.mkdir(parents=True)
    history = {"characters": {"Hero": {"versions": [{"version": 7, "file": "versions/characters/Hero_v7_old.png"}]}}}
    manager.versions_file.write_text(json.dumps(history), encoding="utf-8")
    current = tmp_path / "current.png"
    current.write_bytes(b"image")
    assert manager.add_version("characters", "Hero", "", current) == 8
    assert manager.get_current_version("characters", "Hero") == 8


def test_unicode_resource_ids_remain_distinct(tmp_path):
    manager = VersionManager(tmp_path)
    current = tmp_path / "current.png"
    current.write_bytes(b"image")
    for name in ("Café", "Cafe\u0301"):
        assert manager.add_version("characters", name, "", current) == 1
    assert manager.add_version("characters", "Café", "", current) == 2
    assert manager.get_current_version("characters", "Cafe\u0301") == 1
