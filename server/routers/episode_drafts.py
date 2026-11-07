"""Web endpoints for episode drafts: list, read, save manual repairs (adopting when violations clear), repair with AI, and discard.

Four draft types share these endpoints: reference-video, drama and narration/commentary script-planning drafts, plus reference-video
prompt-authoring drafts. ``doc_type`` values match Agent draft tools. Routes only forward to ``EpisodeDraftService``; AI repair
uses ``server.tool_runtime.repair_draft`` like the Agent, submitting a queued text task and immediately returning a generation batch.
Task events expose progress."""

import logging
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Body
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from lib.infra.api_errors import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from server.agent_toolset.envelope import json_value
from server.auth import CurrentUser
from server.draft_workflow import DraftWorkflowError
from server.i18n import DisplayNamesCatalog, Translator
from server.routers.script_review import localize_draft_view
from server.services.project.episode_drafts import EpisodeDraftService
from server.text_generation import MAX_INSTRUCTIONS_LEN
from server.tool_runtime import (
    CallerContext,
    ProjectScope,
    RepairDraftRequest,
    Services,
    ToolProblem,
    ToolRequest,
    repair_draft,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_CONFLICT_KEYS: dict[str, str] = {
    "revision_conflict": "draft_revision_conflict",
    "formal_revision_conflict": "draft_formal_revision_conflict",
    "draft_agent_owned": "draft_agent_owned",
    "script_plan_confirmed": "script_review_script_plan_confirmed",
}


def _raise_draft_error(exc: DraftWorkflowError, episode: int) -> NoReturn:
    """Map draft-command error codes to HTTP responses; attach Agent-facing ``detail`` only for diagnostics."""
    if exc.code == "draft_not_found":
        raise NotFoundError("draft_not_found", episode=episode) from exc
    if exc.code in _CONFLICT_KEYS:
        raise ConflictError(_CONFLICT_KEYS[exc.code]).with_diagnostic({"code": exc.code}) from exc
    if exc.code in {"invalid_request", "doc_type_not_applicable"}:
        raise BadRequestError("draft_doc_type_not_applicable").with_diagnostic({"code": exc.code}) from exc
    if exc.failure is not None:
        # Video-model configuration blocked revalidation after draft writeback; use the problem code to explain causes and required settings.
        failure = exc.failure
        raise UnprocessableError(failure.code, **failure.parameters()).with_diagnostic(
            {"code": failure.code, "params": failure.parameters()}
        ) from exc
    if exc.notice_key is not None:
        # Draft writeback succeeded but formal-script units blocked adoption; explain using UI unit names, retaining draft_invalid.
        raise UnprocessableError(exc.notice_key, **exc.notice_params).with_diagnostic({"code": exc.code}) from exc
    if exc.code == "draft_repair_failed":
        logger.warning("草稿 AI 修复失败 episode=%s：%s", episode, exc.detail)
        raise UnprocessableError("draft_repair_failed").with_diagnostic({"code": exc.code}) from exc
    # Agent-facing detail may contain server paths: log it only and return just the error code.
    logger.warning("草稿命令失败 episode=%s code=%s：%s", episode, exc.code, exc.detail)
    raise UnprocessableError("draft_save_failed").with_diagnostic({"code": exc.code}) from exc


class SaveDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: dict[str, Any] = Field(description="Complete revised draft content")
    base_revision: str = Field(description="Revision obtained when reading the draft")


class RepairDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_revision: str = Field(description="Revision obtained when reading the draft")
    instructions: Annotated[str, Field(max_length=MAX_INSTRUCTIONS_LEN)] | None = Field(
        default=None, description="Additional repair instructions, not saved; whitespace counts as omitted"
    )


@router.get("/projects/{project_name}/episodes/{episode}/drafts")
async def list_episode_drafts(project_name: str, episode: int):
    """Summaries of existing drafts for this episode."""
    try:
        drafts = await EpisodeDraftService(get_project_manager()).list_drafts(project_name, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return {"episode": episode, "drafts": drafts}


@router.get("/projects/{project_name}/episodes/{episode}/drafts/{doc_type}")
async def get_episode_draft(project_name: str, episode: int, doc_type: str, _t: Translator, names: DisplayNamesCatalog):
    """A draft presentation view with violations and degradation hints located per item; editable Agent drafts omit the body."""
    try:
        view = await EpisodeDraftService(get_project_manager()).get_draft(project_name, episode, doc_type)
    except DraftWorkflowError as exc:
        _raise_draft_error(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    return localize_draft_view(view, _t, names)


@router.put("/projects/{project_name}/episodes/{episode}/drafts/{doc_type}")
async def save_episode_draft(
    project_name: str,
    episode: int,
    doc_type: str,
    _t: Translator,
    names: DisplayNamesCatalog,
    req: SaveDraftRequest = Body(...),
):
    """Save manual repairs: the server fully revalidates, adopting as formal content when violations clear; otherwise return the refreshed draft view."""
    try:
        # Broadcast the user’s own save as webui so the frontend does not notify or navigate as if it were an external change.
        with project_change_source("webui"):
            result = await EpisodeDraftService(get_project_manager()).save_draft(
                project_name, episode, doc_type, req.content, req.base_revision
            )
    except DraftWorkflowError as exc:
        _raise_draft_error(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
    result["draft"] = localize_draft_view(result["draft"], _t, names)
    return result


def _raise_repair_problem(problem: ToolProblem, episode: int) -> NoReturn:
    if problem.code == "generation_active_task_conflict":
        raise ConflictError("draft_repair_task_active").with_diagnostic(problem.params or {})
    if problem.code in {"draft_not_found", "invalid_request", "doc_type_not_applicable", *_CONFLICT_KEYS}:
        _raise_draft_error(DraftWorkflowError(problem.code, problem.detail), episode)
    # Other submission failures, such as queue unavailability, leave drafts unchanged.
    logger.warning("草稿 AI 修复未能提交 episode=%s code=%s：%s", episode, problem.code, problem.detail)
    raise UnprocessableError("draft_repair_failed").with_diagnostic({"code": problem.code})


@router.post("/projects/{project_name}/episodes/{episode}/drafts/{doc_type}/repair")
async def repair_episode_draft(
    project_name: str,
    episode: int,
    doc_type: str,
    user: CurrentUser,
    req: RepairDraftBody = Body(...),
):
    """Submit AI repair and return a generation batch: modify only items with violations, revalidate normally, and adopt once violations clear."""
    pm = get_project_manager()
    try:
        command = RepairDraftRequest.model_validate(
            {
                "episode_id": episode,
                "doc_type": doc_type,
                "base_revision": req.base_revision,
                "instructions": (req.instructions or "").strip() or None,
            }
        )
    except ValidationError as exc:
        raise BadRequestError("draft_doc_type_not_applicable") from exc
    outcome = await repair_draft(
        ToolRequest(command),
        ProjectScope(project_name=project_name, data_root=pm.data_root),
        CallerContext(user_id=user.id, source="webui"),
        Services.defaults(pm),
    )
    if outcome.problem is not None:
        _raise_repair_problem(outcome.problem, episode)
    return {"batch": json_value(outcome.value)}


@router.delete("/projects/{project_name}/episodes/{episode}/drafts/{doc_type}")
async def discard_episode_draft(project_name: str, episode: int, doc_type: str, base_revision: str = ""):
    """Discard the draft and return to formal content."""
    try:
        with project_change_source("webui"):
            return await EpisodeDraftService(get_project_manager()).discard_draft(
                project_name, episode, doc_type, base_revision
            )
    except DraftWorkflowError as exc:
        _raise_draft_error(exc, episode)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
