"""提示词编写的 Web 入口。

「AI 编写 / AI 重写」与 Agent 的 ``generate_episode_script`` 调用同一个服务命令：补缺与显式重写的
对象选择、覆盖确认与草稿落盘都在服务内，路由只负责把结果映射成 HTTP。提交后立即返回生成批次，
任务进度经任务事件呈现。附加指令按集保存，下次打开弹窗时预填。
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import ConflictError, UnprocessableError
from lib.project.project_manager import get_project_manager
from lib.script.prompt_authoring_scope import prompt_overwrite_with_text
from server.agent_toolset.envelope import json_value
from server.auth import CurrentUser
from server.i18n import Translator
from server.services.project.episode_instructions import EpisodeInstructionKind, save_episode_instructions
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

_Instructions = Annotated[str, Field(max_length=MAX_INSTRUCTIONS_LEN)]


class PromptAuthoringInstructions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: _Instructions | None = Field(default=None, description="附加指令原文；空白视同清除")


class AuthorPromptsRequest(PromptAuthoringInstructions):
    entry_ids: list[Annotated[str, Field(min_length=1)]] | None = Field(
        default=None, min_length=1, description="编写范围（分镜 / 单元 id）；省略时为全部待编写条目，给出时至少一条"
    )
    rewrite: bool = Field(default=False, description="显式重写范围内条目的全部视觉层；省略时补缺")
    overwrite_revision: str | None = Field(
        default=None, description="认可覆盖的正式脚本版本，取自 prompt_overwrite.revision"
    )


def _raise_problem(problem: ToolProblem, episode: int, _t: Translator) -> None:
    params = problem.params or {}
    if problem.code == "prompt_overwrite_required":
        overwrite = {key: value for key, value in params["prompt_overwrite"].items() if key != "text"}
        raise ConflictError("prompt_overwrite_required").with_diagnostic(
            {"prompt_overwrite": prompt_overwrite_with_text(overwrite, _t)}
        )
    if problem.code == "generation_active_task_conflict":
        raise ConflictError("prompt_authoring_task_active").with_diagnostic(params)
    reason = params.get("reason")
    if problem.code == "operation_not_admitted" and isinstance(reason, str):
        text = _t(f"operation_{reason}", where=_t("operation_episode", episode=episode))
    else:
        text = problem.detail
    raise UnprocessableError("prompt_authoring_refused", reason=text).with_diagnostic(problem.detail)


@router.put("/projects/{project_name}/episodes/{episode}/prompt-authoring/instructions")
async def save_prompt_authoring_instructions(project_name: str, episode: int, req: PromptAuthoringInstructions):
    """只保存本集的附加指令（「交给 Agent」路径用），不提交生成。"""
    await asyncio.to_thread(
        save_episode_instructions,
        get_project_manager(),
        project_name,
        episode,
        EpisodeInstructionKind.PROMPT_AUTHORING,
        req.instructions,
    )
    return {"success": True}


@router.post("/projects/{project_name}/episodes/{episode}/prompt-authoring")
async def author_prompts(
    project_name: str,
    episode: int,
    req: AuthorPromptsRequest,
    user: CurrentUser,
    _t: Translator,
):
    """保存附加指令并提交提示词编写，返回生成批次。

    显式重写会替换已有视觉层内容而未带（或带了过期的）``overwrite_revision`` 时 409，诊断里的
    ``prompt_overwrite`` 列出将被覆盖的条目与字段、渲染好的 ``text`` 和认可令牌 ``revision``，不提交任务。
    """
    pm = get_project_manager()
    await asyncio.to_thread(
        save_episode_instructions,
        pm,
        project_name,
        episode,
        EpisodeInstructionKind.PROMPT_AUTHORING,
        req.instructions,
    )
    outcome = await generate_episode_script(
        ToolRequest(
            GenerateEpisodeScriptRequest(
                episode_id=episode,
                instructions=req.instructions,
                entry_ids=req.entry_ids,
                rewrite=req.rewrite,
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
