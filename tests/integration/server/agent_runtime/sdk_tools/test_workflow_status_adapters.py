from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.project_manager import ProjectManager
from lib.workflow_state import WorkflowRequestError, WorkflowStateService
from server.agent_runtime.sdk_tools.workflow_status import complete_script_plan_rebuild_tool
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.media_tools.context import ToolContext
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


async def test_complete_script_plan_rebuild_mcp_forwards_explicit_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    calls: list[tuple[object, ...]] = []

    def _complete(*args: object) -> str:
        calls.append(args)
        return "rebuilt-revision"

    monkeypatch.setattr("server.agent_runtime.sdk_tools.workflow_status.complete_stale_script_plan_rebuild", _complete)

    result = await complete_script_plan_rebuild_tool(ctx).handler(
        {"episode": 2, "expected_stale_script_plan_revision": "baseline"}
    )

    assert result.get("is_error") is not True
    assert json.loads(result["content"][0]["text"])["script_plan_rebuild"] == {
        "episode": 2,
        "script_plan_revision": "rebuilt-revision",
    }
    assert calls == [(pm, "demo", 2, "baseline")]


async def test_complete_script_plan_rebuild_mcp_requires_explicit_baseline(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)

    result = await complete_script_plan_rebuild_tool(ctx).handler({"episode": 1})

    assert result["is_error"] is True
    assert json.loads(result["content"][0]["text"])["problem"]["code"] == "invalid_request"


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
