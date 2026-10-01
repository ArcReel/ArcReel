"""AI 规划脚本的 Web 入口。

「AI 规划脚本」与 Agent 的 ``generate_script_plan`` 调用同一个服务命令：准入（本集有集原文）、按项目
模式选择规划变体与草稿落盘都在服务内，路由只负责把结果映射成 HTTP。提交后立即返回生成批次，
任务进度经任务事件呈现；产出的脚本规划进入内容确认。附加指令按集保存，重新生成时预填。
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import ConflictError, UnprocessableError
from lib.project.project_manager import get_project_manager
from server.agent_toolset.envelope import json_value
from server.auth import CurrentUser
from server.i18n import Translator
from server.services.project.episode_instructions import EpisodeInstructionKind, save_episode_instructions
from server.text_generation import MAX_INSTRUCTIONS_LEN
from server.tool_runtime import (
    CallerContext,
    GenerateScriptPlanRequest,
    ProjectScope,
    Services,
    ToolProblem,
    ToolRequest,
    generate_script_plan,
)

router = APIRouter()

_Instructions = Annotated[str, Field(max_length=MAX_INSTRUCTIONS_LEN)]


class ScriptPlanInstructions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: _Instructions | None = Field(default=None, description="附加指令原文；空白视同清除")


def _raise_problem(problem: ToolProblem, episode: int, _t: Translator) -> None:
    params = problem.params or {}
    if problem.code == "generation_active_task_conflict":
        raise ConflictError("script_plan_task_active").with_diagnostic(params)
    reason = params.get("reason")
    if problem.code == "operation_not_admitted" and isinstance(reason, str):
        text = _t(f"operation_{reason}", where=_t("operation_episode", episode=episode))
    else:
        text = problem.detail
    raise UnprocessableError("script_plan_refused", reason=text).with_diagnostic(problem.detail)


async def _save(project_name: str, episode: int, instructions: str | None) -> None:
    await asyncio.to_thread(
        save_episode_instructions,
        get_project_manager(),
        project_name,
        episode,
        EpisodeInstructionKind.SCRIPT_PLAN,
        instructions,
    )


@router.put("/projects/{project_name}/episodes/{episode}/script-plan/instructions")
async def save_script_plan_instructions(project_name: str, episode: int, req: ScriptPlanInstructions):
    """只保存本集的附加指令（「交给 Agent」路径用），不提交生成。"""
    await _save(project_name, episode, req.instructions)
    return {"success": True}


@router.post("/projects/{project_name}/episodes/{episode}/script-plan")
async def plan_script(
    project_name: str,
    episode: int,
    req: ScriptPlanInstructions,
    user: CurrentUser,
    _t: Translator,
):
    """保存附加指令并提交 AI 规划脚本，返回生成批次。

    新的脚本规划整份替换本集现有的规划与草稿；是否先向用户确认由调用方负责。
    """
    await _save(project_name, episode, req.instructions)
    pm = get_project_manager()
    outcome = await generate_script_plan(
        ToolRequest(GenerateScriptPlanRequest(episode_id=episode, instructions=req.instructions)),
        ProjectScope(project_name=project_name, data_root=pm.data_root),
        CallerContext(user_id=user.id, source="webui"),
        Services.defaults(pm),
    )
    if outcome.problem is not None:
        _raise_problem(outcome.problem, episode, _t)
    return {"batch": json_value(outcome.value)}
