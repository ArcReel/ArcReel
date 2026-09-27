from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from lib.project.project_manager import ProjectManager
from lib.speech.narration_delivery import POST_PRODUCTION
from lib.workflow.workflow_plan import WorkflowPlanRequest, build_workflow_plan
from lib.workflow.workflow_state import (
    WorkflowActionType,
    WorkflowNextAction,
    WorkflowProject,
    WorkflowRequestError,
    WorkflowStatus,
    WorkflowTarget,
)
from server.agent_toolset.envelope import json_value
from server.agent_toolset.orientation import GET_WORKFLOW_PLAN
from server.auth import CurrentUserInfo, get_current_user
from server.error_handlers import register_error_handlers
from server.routers import projects
from server.services.project import workflow_planner
from server.tool_runtime import ToolOutcome
from tests.integration.server.agent_tool_support import ToolHarness, run_declared_tool


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
            "gates": {"script_plan_review": {"state": "not_applicable", "revision": None}},
            "artifacts": {
                "asset_inventory": {"state": "not_applicable"},
                "asset_sheets": {},
                "script_plan": {"state": "not_applicable"},
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


async def _agent_plan(pm: ProjectManager, tmp_path: Path, arguments: dict[str, Any]) -> ToolOutcome[Any]:
    """经 Agent 工具声明的共享入口读取制作计划（两宿主同一入口）。"""
    ctx = ToolHarness(project_name="demo", data_root=tmp_path / "projects", pm=pm)
    return await run_declared_tool(GET_WORKFLOW_PLAN, ctx, arguments)


class _Planner:
    def __init__(self):
        self.calls: list[tuple[str, WorkflowPlanRequest, str]] = []

    async def get_plan(
        self,
        project_name: str,
        request: WorkflowPlanRequest,
        *,
        user_id: str,
        queue=None,
        config_resolver=None,
    ):
        self.calls.append((project_name, request, user_id))
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

    agent_plan = await _agent_plan(pm, tmp_path, payload)

    app = FastAPI()
    app.dependency_overrides[get_current_user] = lambda: CurrentUserInfo(id="u1", sub="tester")
    app.include_router(projects.router, prefix="/api/v1", dependencies=[Depends(get_current_user)])
    with TestClient(app) as client:
        response = client.post("/api/v1/projects/demo/workflow-plan", json=payload)

    assert response.status_code == 200
    assert json_value(agent_plan.value) == response.json()
    assert planner.calls == [
        ("demo", WorkflowPlanRequest.model_validate(payload), "default"),
        ("demo", WorkflowPlanRequest.model_validate(payload), "u1"),
    ]


async def test_workflow_plan_mcp_rejects_invalid_transient_choice_before_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    planner = _Planner()
    monkeypatch.setattr(workflow_planner, "get_workflow_planner", lambda _pm=None: planner)

    outcome = await _agent_plan(pm, tmp_path, {"narration_delivery": "persist_this_choice"})

    assert outcome.problem is not None
    assert outcome.problem.code == "invalid_request"
    assert planner.calls == []


class _FailingPlanner:
    def __init__(self, error: Exception):
        self._error = error

    async def get_plan(
        self,
        project_name: str,
        request: WorkflowPlanRequest,
        *,
        user_id: str,
        queue=None,
        config_resolver=None,
    ):
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

    outcome = await _agent_plan(pm, tmp_path, {"episode": 2})

    assert outcome.problem is not None
    assert outcome.problem.code == "invalid_request"

    with TestClient(_adapter_app(pm, monkeypatch), raise_server_exceptions=False) as client:
        response = client.post("/api/v1/projects/demo/workflow-plan", json={"episode": 2})

    assert response.status_code == 400


async def test_workflow_plan_adapters_report_corrupt_script_as_server_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pm = _project(tmp_path)
    planner = _FailingPlanner(ValueError("segments must be an array of objects"))
    monkeypatch.setattr(workflow_planner, "get_workflow_planner", lambda _pm=None: planner)

    outcome = await _agent_plan(pm, tmp_path, {"episode": 1})

    assert outcome.problem is not None
    assert outcome.problem.model_dump() == {
        "code": "internal_error",
        "detail": "get_workflow_plan 失败: segments must be an array of objects",
    }

    with TestClient(_adapter_app(pm, monkeypatch), raise_server_exceptions=False) as client:
        response = client.post("/api/v1/projects/demo/workflow-plan", json={"episode": 1})

    assert response.status_code == 500
