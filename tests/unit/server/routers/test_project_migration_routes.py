"""项目数据升级的 Web 入口：确认升级报告后制作状态不再给出它。"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from lib.project.project_migration_report import (
    MigrationAdjustment,
    build_migration_report,
    visible_migration_report,
    write_migration_report,
)
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import project_migration


def _client(monkeypatch, pm: ProjectManager) -> TestClient:
    monkeypatch.setattr(project_migration, "get_project_manager", lambda: pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="default", sub="tester", role="admin")
    app.include_router(project_migration.router, dependencies=[Depends(get_current_user)])
    register_error_handlers(app)
    return TestClient(app)


def test_acknowledging_the_report_hides_it(tmp_path: Path, monkeypatch) -> None:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")
    project_dir = pm.get_project_path("demo")
    write_migration_report(
        project_dir,
        build_migration_report(
            None,
            from_schema_version=14,
            to_schema_version=18,
            adjusted=[MigrationAdjustment(code="placeholder_asset_added", params={"asset_type": "prop", "name": "伞"})],
        ),
    )

    shown = visible_migration_report(project_dir)
    assert shown is not None

    response = _client(monkeypatch, pm).post(
        "/projects/demo/migration-report/acknowledge", json={"migrated_at": shown.migrated_at}
    )

    assert response.status_code == 200
    assert response.json()["acknowledged"] is True
    assert visible_migration_report(project_dir) is None


def test_a_report_other_than_the_one_shown_stays_unacknowledged(tmp_path: Path, monkeypatch) -> None:
    """页面还停在旧报告上时，之后又一次升级写下的新报告不会被一并确认掉。"""
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")
    project_dir = pm.get_project_path("demo")
    write_migration_report(
        project_dir,
        build_migration_report(
            None,
            from_schema_version=14,
            to_schema_version=18,
            adjusted=[MigrationAdjustment(code="placeholder_asset_added", params={"asset_type": "prop", "name": "伞"})],
        ),
    )

    response = _client(monkeypatch, pm).post(
        "/projects/demo/migration-report/acknowledge", json={"migrated_at": "2000-01-01T00:00:00Z"}
    )

    assert response.status_code == 200
    assert response.json()["acknowledged"] is False
    assert visible_migration_report(project_dir) is not None


def test_acknowledging_without_a_report_succeeds(tmp_path: Path, monkeypatch) -> None:
    pm = ProjectManager(str(tmp_path))
    pm.create_project("demo")

    response = _client(monkeypatch, pm).post(
        "/projects/demo/migration-report/acknowledge", json={"migrated_at": "2000-01-01T00:00:00Z"}
    )

    assert response.status_code == 200


def test_acknowledging_an_unknown_project_is_not_found(tmp_path: Path, monkeypatch) -> None:
    response = _client(monkeypatch, ProjectManager(str(tmp_path))).post(
        "/projects/nope/migration-report/acknowledge", json={"migrated_at": "2000-01-01T00:00:00Z"}
    )

    assert response.status_code == 404
