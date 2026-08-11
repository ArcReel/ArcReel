from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.project_manager import ProjectManager
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.workflow_status import get_workflow_status_tool
from server.auth import CurrentUserInfo, get_current_user
from server.routers import projects


def _project(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Ad", "", "ad", target_duration=30)
    return pm


@pytest.mark.integration
async def test_rest_and_mcp_serialize_the_same_workflow_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    mcp_result = await get_workflow_status_tool(ctx).handler({})
    mcp_body = json.loads(mcp_result["content"][0]["text"])

    monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    with TestClient(app) as client:
        response = client.get("/api/v1/projects/demo/workflow-status")

    assert response.status_code == 200
    assert response.json() == mcp_body


@pytest.mark.integration
async def test_workflow_status_mcp_rejects_invalid_episode_without_calling_service(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)

    result = await get_workflow_status_tool(ctx).handler({"episode": 0})

    assert result["is_error"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "invalid_episode"
