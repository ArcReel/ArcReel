"""Web entry point for AI script planning.

AI script planning and the Agent’s ``generate_script_plan`` use the same service command: admission (episode source text exists),
planning-variant selection by project mode, and draft persistence belong to the service; the route only maps results to HTTP.
Submission immediately returns a generation batch; task events expose progress. The resulting script plan proceeds to content
confirmation. Additional instructions are saved per episode and prefilled for regeneration."""

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import ConflictError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
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

    instructions: _Instructions | None = Field(
        default=None, description="Original additional instructions; whitespace clears them"
    )


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
    await run_sync_transaction(
        save_episode_instructions,
        get_project_manager(),
        project_name,
        episode,
        EpisodeInstructionKind.SCRIPT_PLAN,
        instructions,
    )


@router.put("/projects/{project_name}/episodes/{episode}/script-plan/instructions")
async def save_script_plan_instructions(project_name: str, episode: int, req: ScriptPlanInstructions):
    """Save only this episode’s additional instructions for the “Hand to Agent” path, without submitting generation."""
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
    """Save additional instructions and submit AI script planning, returning a generation batch.

    The new script plan replaces this episode’s entire existing plan and draft; the caller is responsible for deciding whether to confirm with the user first."""
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
