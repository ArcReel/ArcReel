"""Web entry point for ad / short-film AI script generation.

Use the same service command as the Agent’s ``generate_episode_script``: admission (at least creative inspiration or products),
overwrite confirmation for full redos, and new asset registration belong to the service; the route only maps results to HTTP.
Submission immediately returns a generation batch; task events expose progress. Results become formal scripts directly,
without content confirmation. Additional instructions apply only to this submission and are not saved."""

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
        default=None, description="Original additional instructions; apply only to this generation, without saving"
    )
    regenerate: bool = Field(default=False, description="Redo the whole script, replacing the existing formal script")
    overwrite_revision: str | None = Field(
        default=None, description="Formal-script revision accepted for overwrite, from script_overwrite.revision"
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
    """Submit full ad / short-film generation, returning a generation batch.

    If ``regenerate`` encounters an existing formal script and ``overwrite_revision`` is absent or stale, return 409 without
    submitting tasks. Diagnostic ``script_overwrite`` lists entries and artifacts to remove, rendered ``text``, and the
    acceptance token ``revision``."""
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
