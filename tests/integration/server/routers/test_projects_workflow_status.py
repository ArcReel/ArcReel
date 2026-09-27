from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from lib.workflow.workflow_state import WorkflowRequestError, WorkflowStateService
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import projects


def _project(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Ad", "", "ad", target_duration=30)
    return pm


async def test_rest_serializes_the_authoritative_workflow_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    expected = WorkflowStateService(pm).get_status("demo", None).model_dump(mode="json")

    monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    with TestClient(app) as client:
        response = client.get("/api/v1/projects/demo/workflow-status")

    assert response.status_code == 200
    assert response.json() == expected


async def test_workflow_status_rest_treats_corrupt_project_as_server_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)

    def _corrupt(*args: object, **kwargs: object) -> None:
        raise json.JSONDecodeError("broken", "{", 0)

    monkeypatch.setattr(WorkflowStateService, "get_status", _corrupt)

    monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/projects/demo/workflow-status")

    assert response.status_code == 500


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (WorkflowRequestError("ad workflow only has episode 1"), 400),
        (ValueError("scenes must be an array of objects"), 500),
    ],
)
async def test_workflow_status_rest_blames_the_request_only_for_request_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
) -> None:
    pm = _project(tmp_path)

    def _raise(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(WorkflowStateService, "get_status", _raise)

    monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/projects/demo/workflow-status", params={"episode": 2})

    assert response.status_code == expected_status
