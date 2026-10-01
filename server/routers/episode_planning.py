"""AI 分集规划的 Web 入口。

「AI 规划分集」调用 :func:`server.tool_runtime.start_episode_planning`：准入（有整本源文）与 Agent 的
``plan_episodes`` 同一个谓词，从账本推导的规划起点逐窗规划到整本源文结尾，每一窗是一个排队的文本任务。
提交后立即返回首窗的生成批次，进度经任务事件与项目快照呈现。附加指令随任务传递，不写进项目。
"""

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import ConflictError, UnprocessableError
from lib.project.project_manager import get_project_manager
from server.agent_toolset.envelope import json_value
from server.auth import CurrentUser
from server.i18n import Translator
from server.text_generation import MAX_INSTRUCTIONS_LEN
from server.tool_runtime import (
    CallerContext,
    PlanEpisodesRequest,
    ProjectScope,
    Services,
    ToolProblem,
    ToolRequest,
    start_episode_planning,
    stop_episode_planning,
)

router = APIRouter()


class EpisodePlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: Annotated[str, Field(max_length=MAX_INSTRUCTIONS_LEN)] | None = Field(
        default=None, description="附加指令原文；空白视同未传"
    )


def _raise_problem(problem: ToolProblem, _t: Translator) -> None:
    params = problem.params or {}
    if problem.code == "generation_active_task_conflict":
        raise ConflictError("episode_planning_task_active").with_diagnostic(params)
    reason = params.get("reason")
    if problem.code == "operation_not_admitted" and isinstance(reason, str):
        text = _t(f"operation_{reason}", where=_t("operation_project"))
    else:
        text = problem.detail
    raise UnprocessableError("episode_planning_refused", reason=text).with_diagnostic(problem.detail)


def _context(project_name: str, user_id: str) -> tuple[ProjectScope, CallerContext, Services]:
    pm = get_project_manager()
    return (
        ProjectScope(project_name=project_name, data_root=pm.data_root),
        CallerContext(user_id=user_id, source="webui"),
        Services.defaults(pm),
    )


@router.post("/projects/{project_name}/episode-planning")
async def plan_episodes_to_end(project_name: str, req: EpisodePlanningRequest, user: CurrentUser, _t: Translator):
    """提交 AI 分集规划，从规划起点逐窗规划到整本源文结尾，返回首窗的生成批次。"""
    scope, caller, services = _context(project_name, user.id)
    outcome = await start_episode_planning(
        ToolRequest(PlanEpisodesRequest(instructions=req.instructions)), scope, caller, services
    )
    if outcome.problem is not None:
        _raise_problem(outcome.problem, _t)
    return {"batch": json_value(outcome.value)}


@router.post("/projects/{project_name}/episode-planning/stop")
async def stop_planning(project_name: str, user: CurrentUser):
    """停止分集规划：取消排队中的窗口；执行中的那一窗照常完成，已切出的集保留。"""
    scope, caller, services = _context(project_name, user.id)
    result = await stop_episode_planning(scope, caller, services)
    return {"cancelled": result.cancelled, "running": result.running}
