"""Web entry point for prompt authoring.

AI authoring / rewriting and the Agent’s ``generate_episode_script`` use the same service command: target selection for
gap filling or explicit rewriting, overwrite confirmation, and draft persistence belong to the service; the route only maps
results to HTTP. Submission immediately returns a generation batch; task events expose progress. Additional instructions
are saved per episode and prefilled the next time the dialog opens."""

from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from lib.infra.api_errors import ConflictError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
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

    instructions: _Instructions | None = Field(
        default=None, description="Original additional instructions; whitespace clears them"
    )


class AuthorPromptsRequest(PromptAuthoringInstructions):
    entry_ids: list[Annotated[str, Field(min_length=1)]] | None = Field(
        default=None,
        min_length=1,
        description="Authoring scope (storyboard / unit IDs); omit for all pending entries; an explicit selection requires at least one entry",
    )
    rewrite: bool = Field(
        default=False, description="Explicitly rewrite all visual layers of entries in scope; omit to fill gaps"
    )
    overwrite_revision: str | None = Field(
        default=None, description="Formal-script revision accepted for overwrite, from prompt_overwrite.revision"
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
    """Save only this episode’s additional instructions for the “Hand to Agent” path, without submitting generation."""
    await run_sync_transaction(
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
    """Save additional instructions and submit prompt authoring, returning a generation batch.

    When explicit rewriting would replace existing visual content and ``overwrite_revision`` is absent or stale, return 409
    without submitting tasks. Diagnostic ``prompt_overwrite`` lists entries and fields to overwrite, rendered ``text``, and
    the acceptance token ``revision``."""
    pm = get_project_manager()
    await run_sync_transaction(
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
