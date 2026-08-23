from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.narration_delivery import POST_PRODUCTION
from lib.project_manager import ProjectManager
from lib.workflow_plan import WorkflowPlanRequest, build_workflow_plan
from lib.workflow_state import (
    WorkflowActionType,
    WorkflowNextAction,
    WorkflowProject,
    WorkflowRequestError,
    WorkflowStatus,
    WorkflowTarget,
)
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.workflow_plan import get_workflow_plan_tool
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import projects
from server.services import workflow_planner


def _project(tmp_path: Path) -> ProjectManager:
    pm = ProjectManager(tmp_path / "projects")
    pm.create_project("demo")
    pm.create_project_metadata("demo", "Ad", "", "ad", target_duration=30)
    return pm


def _status() -> WorkflowStatus:
    return WorkflowStatus.model_validate(
        {
            "project_revision": "sha256-v1:project",
            "source_revision": None,
            "project": WorkflowProject(content_mode="ad", generation_mode="storyboard", grid_storyboard=False),
            "target": WorkflowTarget(
                episode=1,
                script="scripts/episode_1.json",
                script_filename="episode_1.json",
                source="source/episode_1.txt",
            ),
            "state": "VIDEO",
            "blockers": [],
            "gates": {"step1_review": {"state": "not_applicable", "revision": None}},
            "artifacts": {
                "asset_inventory": {"state": "not_applicable"},
                "asset_sheets": {},
                "step1": {"state": "not_applicable"},
                "script": {"state": "current"},
                "storyboards": {"current_ids": ["E1S01"], "stale_ids": [], "missing_ids": []},
                "videos": {"current_ids": [], "stale_ids": [], "missing_ids": ["E1S01"]},
                "audio": {"state": "not_applicable", "current_ids": [], "stale_ids": [], "missing_ids": []},
            },
            "next_action": WorkflowNextAction(
                type=WorkflowActionType.GENERATE_VIDEOS,
                requested_ids=["E1S01"],
                reason="video missing",
            ),
        }
    )


class _Planner:
    def __init__(self):
        self.calls: list[tuple[str, WorkflowPlanRequest]] = []

    async def get_plan(self, project_name: str, request: WorkflowPlanRequest):
        self.calls.append((project_name, request))
        return build_workflow_plan(_status(), narration_delivery=request.narration_delivery)


async def test_rest_and_mcp_serialize_the_same_workflow_plan(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pm = _project(tmp_path)
    planner = _Planner()
    monkeypatch.setattr(workflow_planner, "get_workflow_planner", lambda _pm=None: planner)
    monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
    payload = {
        "episode": 1,
        "narration_delivery": POST_PRODUCTION,
        "confirmed_request_durations": {"E1S01": 5},
    }

    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    mcp_result = await get_workflow_plan_tool(ctx).handler(payload)
    mcp_body = json.loads(mcp_result["content"][0]["text"])

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    with TestClient(app) as client:
        response = client.post("/api/v1/projects/demo/workflow-plan", json=payload)

    assert response.status_code == 200
    assert response.json() == mcp_body
    assert planner.calls == [
        ("demo", WorkflowPlanRequest.model_validate(payload)),
        ("demo", WorkflowPlanRequest.model_validate(payload)),
    ]


async def test_workflow_plan_mcp_rejects_invalid_transient_choice_before_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    calls: list[object] = []

    def _planner(*args: object, **kwargs: object) -> None:
        calls.append((args, kwargs))

    monkeypatch.setattr(workflow_planner, "get_workflow_planner", _planner)
    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)

    result = await get_workflow_plan_tool(ctx).handler({"narration_delivery": "persist_this_choice"})

    assert result["is_error"] is True
    assert json.loads(result["content"][0]["text"])["error"] == "invalid_request"
    assert calls == []


class _FailingPlanner:
    def __init__(self, error: Exception):
        self._error = error

    async def get_plan(self, project_name: str, request: WorkflowPlanRequest):
        raise self._error


def _adapter_app(pm: ProjectManager, monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    monkeypatch.setattr(projects, "get_project_manager", lambda: pm)
    app = FastAPI()
    register_error_handlers(app)
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    return app


async def test_workflow_plan_adapters_blame_the_request_only_for_request_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    planner = _FailingPlanner(WorkflowRequestError("ad workflow only has episode 1"))
    monkeypatch.setattr(workflow_planner, "get_workflow_planner", lambda _pm=None: planner)

    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    mcp_result = await get_workflow_plan_tool(ctx).handler({"episode": 2})

    assert mcp_result["is_error"] is True
    assert json.loads(mcp_result["content"][0]["text"])["error"] == "invalid_request"

    with TestClient(_adapter_app(pm, monkeypatch), raise_server_exceptions=False) as client:
        response = client.post("/api/v1/projects/demo/workflow-plan", json={"episode": 2})

    assert response.status_code == 400


async def test_workflow_plan_adapters_report_corrupt_script_as_server_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    planner = _FailingPlanner(ValueError("segments must be an array of objects"))
    monkeypatch.setattr(workflow_planner, "get_workflow_planner", lambda _pm=None: planner)

    ctx = ToolContext(project_name="demo", projects_root=tmp_path / "projects", pm=pm)
    mcp_result = await get_workflow_plan_tool(ctx).handler({"episode": 1})

    assert mcp_result["is_error"] is True
    assert mcp_result["content"][0]["text"].startswith("get_workflow_plan 失败:")

    with TestClient(_adapter_app(pm, monkeypatch), raise_server_exceptions=False) as client:
        response = client.post("/api/v1/projects/demo/workflow-plan", json={"episode": 1})

    assert response.status_code == 500
