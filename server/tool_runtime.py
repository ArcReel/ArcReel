"""Host-independent ArcReel tool handlers and their typed call contract."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from lib.config.resolver import ConfigResolver
from lib.project_manager import ProjectManager
from lib.workflow_plan import WorkflowPlan, WorkflowPlanRequest
from lib.workflow_state import WorkflowRequestError
from server.services.video_caps import annotate_reference_unit_tiers
from server.services.workflow_planner import WorkflowPlanner


@dataclass(frozen=True, slots=True)
class ToolRequest[RequestT]:
    value: RequestT


@dataclass(frozen=True, slots=True)
class ProjectScope:
    project_name: str
    projects_root: Path


@dataclass(frozen=True, slots=True)
class CallerContext:
    user_id: str
    source: Literal["embedded", "mcp"]


@dataclass(frozen=True, slots=True)
class Services:
    projects: ProjectManager
    workflow_planner: WorkflowPlanner
    capabilities: ConfigResolver


@dataclass(frozen=True, slots=True)
class ToolProblem:
    code: str
    detail: str


@dataclass(frozen=True, slots=True)
class ToolOutcome[ResultT]:
    value: ResultT | None = None
    problem: ToolProblem | None = None


async def get_workflow_plan(
    request: ToolRequest[WorkflowPlanRequest],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[WorkflowPlan]:
    try:
        plan = await services.workflow_planner.get_plan(scope.project_name, request.value)
    except WorkflowRequestError as exc:
        return ToolOutcome(problem=ToolProblem("invalid_request", str(exc)))
    except Exception as exc:  # noqa: BLE001
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_workflow_plan 失败: {exc}"))
    return ToolOutcome(value=plan)


async def get_video_capabilities(
    _request: ToolRequest[None],
    scope: ProjectScope,
    _caller: CallerContext,
    services: Services,
) -> ToolOutcome[dict[str, Any]]:
    try:
        project = services.projects.load_project(scope.project_name)
        payload = await services.capabilities.video_capabilities_for_project(project)
        await annotate_reference_unit_tiers(payload, project, config_resolver=services.capabilities)
    except FileNotFoundError as exc:
        return ToolOutcome(problem=ToolProblem("project_not_found", f"项目未找到或缺 project.json: {exc}"))
    except ValueError as exc:
        return ToolOutcome(problem=ToolProblem("capabilities_unresolved", f"无法解析视频模型能力: {exc}"))
    except Exception as exc:  # noqa: BLE001
        return ToolOutcome(problem=ToolProblem("internal_error", f"get_video_capabilities 失败: {exc}"))
    return ToolOutcome(value=payload)


__all__ = [
    "CallerContext",
    "ProjectScope",
    "Services",
    "ToolOutcome",
    "ToolProblem",
    "ToolRequest",
    "get_video_capabilities",
    "get_workflow_plan",
]
