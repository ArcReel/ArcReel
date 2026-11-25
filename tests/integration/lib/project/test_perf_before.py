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


@pytest.mark.parametrize("sqlite", [False, True])
def test_workflow_before(tmp_path, sqlite):
    pm = ProjectManager(tmp_path)
    with ZipFile(FIXTURES / "workflow-v2.zip") as archive:
        root = pm.projects_dir / "demo"
        archive.extractall(root)
        if sqlite:
            manager = VersionManager(root)
            manager.versions_file.unlink()
            manager.enable_sqlite_history(json.loads(archive.read("versions/versions.json")))
    status = WorkflowStateService(pm).get_status("demo")
    expected_status = json.loads((FIXTURES / "wf_status.json").read_bytes())
    assert status.model_dump(mode="json") == {**expected_status, "schema_version": 3}
    plan = build_workflow_plan(status)
    expected_plan = json.loads((FIXTURES / "wf_plan.json").read_bytes())
    expected_plan["schema_version"] = expected_plan["status"]["schema_version"] = 3
    assert plan.model_dump(mode="json") == expected_plan


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
    assert VersionManager(path).sqlite.is_enabled()
    assert not VersionManager(path).versions_file.exists()
    assert (path / "products/Cup.png").read_bytes() == b"products-v3"
    records = VersionManager(path).get_versions("products", "Cup")["versions"]
    expected_versions = [1, 2, 3] if scope == "full" else []
    assert [row["version"] for row in records] == expected_versions
    archive, _ = ProjectArchiveService(pm).export_project(result.project_name, scope=scope)
    try:
        with ZipFile(archive) as package:
            assert not any(
                "history.sqlite3" in name or ".history-undo" in name or ".bak.v" in name for name in package.namelist()
            )
            assert json.loads(package.read(f"{path.name}/project.json"))["schema_version"] == 21
        fresh = ProjectManager(tmp_path / "restored")
        imported = ProjectArchiveService(fresh).import_project_archive(archive)
        restored = fresh.get_project_path(imported.project_name)
        assert (restored / "products/Cup.png").read_bytes() == b"products-v3"
        assert VersionManager(restored).get_versions("products", "Cup") == VersionManager(path).get_versions(
            "products", "Cup"
        )
    finally:
        archive.unlink(missing_ok=True)
