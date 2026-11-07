"""Web entry points for AI episode planning and replanning.

Planning calls :func:`server.tool_runtime.start_episode_planning`, sharing Agent ``plan_episodes`` admission: whole-source
text exists and no unresolved replan candidate exists. Plan window by window from the ledger-derived starting point
through source end, each window a queued text task. Submission immediately returns the first window’s batch; task
events/project snapshots expose progress. Additional instructions travel with tasks and are not saved in the project.

Replanning from an episode calls :func:`server.tool_runtime.start_episode_replan`, generating a candidate window by
window without changing the episode ledger. Interrupted generation can continue through
:func:`server.tool_runtime.continue_episode_replan`. See :mod:`lib.episode.episode_replan` for adoption/discard."""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from lib.episode.episode_replan import (
    ReplanAdoptionResult,
    ReplanConfirmationRequired,
    ReplanError,
    ReplanScope,
    adopt_replan_candidate,
    discard_replan_candidate,
    render_replan_adoption_text,
)
from lib.infra.api_errors import ApiError, ConflictError, NotFoundError, UnprocessableError
from lib.infra.async_thread import run_sync_transaction
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import ProjectManager, get_project_manager
from server.agent_toolset.envelope import json_value
from server.auth import CurrentUser
from server.dependencies import require_project_migration_ok
from server.i18n import Translator
from server.routers.episode_management import episode_has_active_tasks
from server.text_generation import MAX_INSTRUCTIONS_LEN
from server.tool_runtime import (
    CallerContext,
    PlanEpisodesRequest,
    ProjectScope,
    Services,
    ToolProblem,
    ToolRequest,
    continue_episode_replan,
    episode_planning_active,
    start_episode_planning,
    start_episode_replan,
    stop_episode_planning,
)

logger = logging.getLogger(__name__)

router = APIRouter()


class PlanningGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file: str
    end: Annotated[int, Field(gt=0)]


class EpisodePlanningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instructions: Annotated[str, Field(max_length=MAX_INSTRUCTIONS_LEN)] | None = Field(
        default=None, description="Original additional instructions; whitespace counts as omitted"
    )
    gap: PlanningGap | None = Field(
        default=None,
        description="Plan only the unsplit source range ending at this point; omit to plan from the planning start through the whole source’s end",
    )


#: Replan rejection reason → status; unlisted reasons use 409.
_REPLAN_STATUS: dict[str, int] = {
    "episode_not_found": 404,
    "candidate_not_found": 404,
    "not_cut_episode": 422,
    "whole_source_missing": 422,
    "episode_not_placed": 422,
    "ledger_invalid": 422,
    "candidate_invalid": 422,
    "candidate_empty": 422,
}


def _replan_http_error(code: str, _t: Translator) -> HTTPException:
    return HTTPException(status_code=_REPLAN_STATUS.get(code, 409), detail=_t(f"episode_replan_{code}"))


def _raise_problem(problem: ToolProblem, _t: Translator) -> None:
    params = problem.params or {}
    if problem.code == "generation_active_task_conflict":
        raise ConflictError("episode_planning_task_active").with_diagnostic(params)
    if problem.code == "episode_replan_refused" and isinstance(params.get("reason"), str):
        raise _replan_http_error(params["reason"], _t)
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
    """Submit AI planning from the planning start through whole-source end, window by window, returning the first window’s batch.

    With ``gap``, plan only that unsplit source range through its ending and insert new episodes by source position."""
    scope, caller, services = _context(project_name, user.id)
    outcome = await start_episode_planning(
        ToolRequest(PlanEpisodesRequest(instructions=req.instructions)),
        scope,
        caller,
        services,
        gap=None if req.gap is None else (req.gap.source_file, req.gap.end),
    )
    if outcome.problem is not None:
        _raise_problem(outcome.problem, _t)
    return {"batch": json_value(outcome.value)}


@router.post("/projects/{project_name}/episode-planning/stop")
async def stop_planning(project_name: str, user: CurrentUser):
    """Stop planning by cancelling queued windows; the running window finishes normally and existing planned episodes remain."""
    scope, caller, services = _context(project_name, user.id)
    result = await stop_episode_planning(scope, caller, services)
    return {"cancelled": result.cancelled, "running": result.running}


class EpisodeReplanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Episode ID from which to replan.
    episode: Annotated[int, Field(gt=0)]
    instructions: Annotated[str, Field(max_length=MAX_INSTRUCTIONS_LEN)] | None = Field(
        default=None, description="Original additional instructions; whitespace counts as omitted"
    )
    dry_run: bool = Field(default=False, description="Return only the replanning range without starting replanning")


@router.post("/projects/{project_name}/episode-replan", dependencies=[Depends(require_project_migration_ok)])
async def start_replan(project_name: str, req: EpisodeReplanRequest, user: CurrentUser, _t: Translator):
    """Replan from an episode by generating a candidate window by window without changing the ledger; return the first window’s batch.

    ``dry_run`` returns ``status=preview`` with extracted episodes to replace in ``replaced`` and those already in production in ``started``."""
    scope, caller, services = _context(project_name, user.id)
    outcome = await start_episode_replan(
        ToolRequest(PlanEpisodesRequest(instructions=req.instructions)),
        scope,
        caller,
        services,
        episode=req.episode,
        dry_run=req.dry_run,
    )
    if outcome.problem is not None:
        _raise_problem(outcome.problem, _t)
    if isinstance(outcome.value, ReplanScope):
        return {"status": "preview", **asdict(outcome.value)}
    return {"batch": json_value(outcome.value)}


class ReplanContinueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str


@router.post("/projects/{project_name}/episode-replan/continue", dependencies=[Depends(require_project_migration_ok)])
async def continue_replan(project_name: str, req: ReplanContinueRequest, user: CurrentUser, _t: Translator):
    """Continue an interrupted replan from the candidate ending through whole-source end, window by window, reusing original additional instructions.

    Return the first window’s batch. Reject completed, stale or absent candidates by reason code; return 409 while episode planning is active."""
    scope, caller, services = _context(project_name, user.id)
    outcome = await continue_episode_replan(req.candidate_id, scope, caller, services)
    if outcome.problem is not None:
        _raise_problem(outcome.problem, _t)
    return {"batch": json_value(outcome.value)}


class ReplanAdoptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str
    #: ``revision`` of the change list seen by the creator; absent/mismatched revisions return the list without adopting.
    revision: str | None = None
    #: Also delete retired episodes that already entered production and became sourceless.
    delete_retired: bool = False


class ReplanDiscardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str


async def _run_replan[T](name: str, user_id: str, _t: Translator, action: Callable[[ProjectManager], T]) -> T:
    """Reject while candidate generation is active; otherwise execute in a thread and map ``ReplanError`` to its status/message."""
    scope, caller, services = _context(name, user_id)
    if not await asyncio.to_thread(services.projects.project_exists, name):
        raise NotFoundError("project_not_found", name=name)
    if await episode_planning_active(scope, caller, services):
        raise HTTPException(status_code=409, detail=_t("episode_replan_generating"))

    def _sync() -> T:
        with project_change_source("webui"):
            return action(services.projects)

    try:
        return await run_sync_transaction(_sync)
    except ReplanError as exc:
        raise _replan_http_error(exc.code, _t) from exc
    except (HTTPException, ApiError):
        raise
    except Exception as exc:
        logger.exception("请求处理失败")
        raise HTTPException(status_code=500, detail=_t("internal_server_error")) from exc


@router.post("/projects/{project_name}/episode-replan/adopt", dependencies=[Depends(require_project_migration_ok)])
async def adopt_replan(project_name: str, req: ReplanAdoptRequest, user: CurrentUser, _t: Translator) -> dict[str, Any]:
    """Adopt the new episode plan.

    First omit ``revision`` for ``status=confirmation_required``, server-written adoption consequences in ``impact.text`` and
    content lost by selecting deletion in ``impact.delete_text``. After confirmation, resubmit with ``impact.revision``.
    If lists change between calls, request confirmation again without adopting."""
    if req.revision is not None:
        # Share manual-split checks: reject active tasks in retiring/removing episodes so tasks cannot persist into replaced episodes.
        preview = await _run_replan(
            project_name,
            user.id,
            _t,
            lambda manager: adopt_replan_candidate(manager.get_project_path(project_name), req.candidate_id),
        )
        if isinstance(preview, ReplanConfirmationRequired):
            for episode in (*preview.impact.retired, *preview.impact.removed):
                if await episode_has_active_tasks(project_name, episode):
                    raise HTTPException(status_code=409, detail=_t("episode_replan_tasks_active"))

    def _adopt(manager: ProjectManager) -> dict[str, Any]:
        outcome = adopt_replan_candidate(
            manager.get_project_path(project_name),
            req.candidate_id,
            revision=req.revision,
            delete_retired=req.delete_retired,
        )
        if isinstance(outcome, ReplanAdoptionResult):
            return {"status": "adopted", "episodes": outcome.episodes, "deleted": outcome.deleted}
        impact = outcome.impact.to_dict()
        texts = render_replan_adoption_text(impact, manager.load_project(project_name), _t)
        return {"status": "confirmation_required", "impact": {**impact, **texts}}

    return await _run_replan(project_name, user.id, _t, _adopt)


@router.post("/projects/{project_name}/episode-replan/discard", dependencies=[Depends(require_project_migration_ok)])
async def discard_replan(project_name: str, req: ReplanDiscardRequest, user: CurrentUser, _t: Translator):
    """Discard the new episode plan without changing the ledger."""

    def _discard(manager: ProjectManager) -> dict[str, Any]:
        discard_replan_candidate(manager.get_project_path(project_name), req.candidate_id)
        return {"status": "discarded"}

    return await _run_replan(project_name, user.id, _t, _discard)
