from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.project_manager import ProjectManager
from lib.workflow_state import WorkflowRequestError, WorkflowStateService
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.workflow_status import complete_step1_rebuild_tool, get_workflow_status_tool
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
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
async def test_workflow_status_mcp_rejects_invalid_episode_without_calling_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    calls: list[object] = []

    def _fail(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(WorkflowStateService, "get_status", _fail)

    result = await get_workflow_status_tool(ctx).handler({"episode": 0})

    assert result["is_error"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "invalid_episode"
    assert calls == []


@pytest.mark.integration
async def test_complete_step1_rebuild_mcp_forwards_explicit_baseline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    calls: list[tuple[object, ...]] = []

    def _complete(*args: object) -> str:
        calls.append(args)
        return "rebuilt-revision"

    monkeypatch.setattr("server.agent_runtime.sdk_tools.workflow_status.complete_stale_step1_rebuild", _complete)

    result = await complete_step1_rebuild_tool(ctx).handler({"episode": 2, "expected_stale_step1_revision": "baseline"})

    assert result.get("is_error") is not True
    assert json.loads(result["content"][0]["text"]) == {
        "episode": 2,
        "step1_revision": "rebuilt-revision",
    }
    assert calls == [(pm, "demo", 2, "baseline")]


@pytest.mark.integration
async def test_complete_step1_rebuild_mcp_requires_explicit_baseline(tmp_path: Path) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)

    result = await complete_step1_rebuild_tool(ctx).handler({"episode": 1})

    assert result["is_error"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "invalid_request"


@pytest.mark.integration
async def test_workflow_status_mcp_offloads_filesystem_scan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    calls: list[tuple[object, tuple[object, ...]]] = []

    async def _to_thread(fn, *args):
        calls.append((fn, args))
        return fn(*args)

    monkeypatch.setattr("server.agent_runtime.sdk_tools.workflow_status.asyncio.to_thread", _to_thread)

    result = await get_workflow_status_tool(ctx).handler({})

    assert result.get("is_error") is not True
    assert len(calls) == 1
    assert calls[0][1] == ("demo", None)


@pytest.mark.integration
async def test_workflow_status_adapters_treat_corrupt_project_as_server_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)

    def _corrupt(*args: object, **kwargs: object) -> None:
        raise json.JSONDecodeError("broken", "{", 0)

    monkeypatch.setattr(WorkflowStateService, "get_status", _corrupt)

    result = await get_workflow_status_tool(ctx).handler({})

    assert result["is_error"] is True
    assert result["content"][0]["text"].startswith("get_workflow_status 失败:")

    monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/projects/demo/workflow-status")

    assert response.status_code == 500


@pytest.mark.integration
@pytest.mark.parametrize(
    ("error", "expected_status", "expects_request_blame"),
    [
        (WorkflowRequestError("ad workflow only has episode 1"), 400, True),
        (ValueError("scenes must be an array of objects"), 500, False),
    ],
)
async def test_workflow_status_adapters_blame_the_request_only_for_request_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_status: int,
    expects_request_blame: bool,
) -> None:
    pm = _project(tmp_path)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)

    def _raise(*args: object, **kwargs: object) -> None:
        raise error

    monkeypatch.setattr(WorkflowStateService, "get_status", _raise)

    result = await get_workflow_status_tool(ctx).handler({"episode": 2})

    assert result["is_error"] is True
    if expects_request_blame:
        assert json.loads(result["content"][0]["text"])["error"] == "invalid_episode"
    else:
        assert result["content"][0]["text"].startswith("get_workflow_status 失败:")

    monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/projects/demo/workflow-status", params={"episode": 2})

    assert response.status_code == expected_status
