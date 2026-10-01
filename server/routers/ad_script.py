"""广告/短片「AI 生成脚本」的 Web 入口。

与 Agent 的 ``generate_episode_script`` 调用同一个服务命令：准入（创作灵感与商品至少一项）、整份重做的
覆盖确认、新增资产的登记都在服务内，路由只负责把结果映射成 HTTP。提交后立即返回生成批次，任务进度
经任务事件呈现；结果直接写成正式脚本，不经过内容确认。附加指令只随本次提交，不保存。
"""

from typing import Annotated

from fastapi import APIRouter
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import ConflictError, UnprocessableError
from lib.project.project_manager import get_project_manager
from lib.script.script_review import overwrite_with_text
from server.agent_toolset.envelope import json_value
from server.auth import CurrentUser
from server.i18n import Translator
from server.text_generation import MAX_INSTRUCTIONS_LEN
from server.tool_runtime import (
    CallerContext,
    GenerateEpisodeScriptRequest,
    ProjectScope,
    Services,
    ToolProblem,
    ToolRequest,
    generate_episode_script,
)

router = APIRouter()


class AdScriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: Annotated[str, Field(max_length=MAX_INSTRUCTIONS_LEN)] | None = Field(
        default=None, description="附加指令原文；只随本次生成，不保存"
    )
    regenerate: bool = Field(default=False, description="整份重做，替换已有的正式脚本")
    overwrite_revision: str | None = Field(
        default=None, description="认可覆盖的正式脚本版本，取自 script_overwrite.revision"
    )


def _raise_problem(problem: ToolProblem, episode: int, _t: Translator) -> None:
    params = problem.params or {}
    if problem.code == "script_overwrite_required":
        overwrite = {key: value for key, value in params["script_overwrite"].items() if key != "text"}
        raise ConflictError("ad_script_overwrite_required").with_diagnostic(
            {"script_overwrite": overwrite_with_text(overwrite, _t)}
        )
    if problem.code == "generation_active_task_conflict":
        raise ConflictError("ad_script_task_active").with_diagnostic(params)
    reason = params.get("reason")
    if problem.code == "operation_not_admitted" and isinstance(reason, str):
        text = _t(f"operation_{reason}", where=_t("operation_episode", episode=episode))
    else:
        text = problem.detail
    raise UnprocessableError("ad_script_refused", reason=text).with_diagnostic(problem.detail)


@router.post("/projects/{project_name}/episodes/{episode}/ad-script")
async def generate_ad_script(
    project_name: str,
    episode: Annotated[int, PathParam(ge=1)],
    req: AdScriptRequest,
    user: CurrentUser,
    _t: Translator,
):
    """提交广告/短片整份生成，返回生成批次。

    ``regenerate`` 时已有正式脚本而未带（或带了过期的）``overwrite_revision`` 返回 409，诊断里的
    ``script_overwrite`` 列出将被移除的条目与产物、渲染好的 ``text`` 和认可令牌 ``revision``，不提交任务。
    """
    pm = get_project_manager()
    outcome = await generate_episode_script(
        ToolRequest(
            GenerateEpisodeScriptRequest(
                episode_id=episode,
                instructions=req.instructions,
                regenerate=req.regenerate,
                overwrite_revision=req.overwrite_revision,
            )
        ),
        ProjectScope(project_name=project_name, data_root=pm.data_root),
        CallerContext(user_id=user.id, source="webui"),
        Services.defaults(pm),
    )
    if outcome.problem is not None:
        _raise_problem(outcome.problem, episode, _t)
    return {"batch": json_value(outcome.value)}
