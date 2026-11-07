"""Editing-timeline HTTP entry points: list, read, preview material layers, create, copy, rename, revision history, restore,
delete and episode editing summaries, plus final-cut/Jianying-draft submission, state and download.

Behavior lives entirely in lib timeline commands, final-cut services and Jianying-draft services; enqueue rendering in the ``render`` lane."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Annotated, Any, Literal

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi import Path as PathParam
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask

from lib.edit_timeline import (
    EditTimelineError,
    EditTimelineReadout,
    EditTimelineService,
    EditTimelineWriteResult,
    RevisionAuthor,
    RevisionHistory,
    TimelineSummary,
)
from lib.edit_timeline.errors import edit_timeline_message
from lib.final_cut.basis import SubtitleMode
from lib.final_cut.errors import FinalCutError
from lib.final_cut.overview import EpisodeEditOverview, episode_edit_overview
from lib.final_cut.service import FinalCutService, FinalCutStatus
from lib.generation.generation_queue import ActiveTaskRequestConflict, GenerationQueue, get_generation_queue
from lib.infra.api_errors import ApiError, UnprocessableError
from lib.jianying_draft.basis import DraftNarration
from lib.jianying_draft.errors import JianyingDraftError
from lib.jianying_draft.results import JianyingDraftStatus
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from lib.workflow.operation_admission import AdmissionState
from lib.workflow.workflow_state import WorkflowActionType, WorkflowRequestError, WorkflowStateService
from server.auth import CurrentUser, verify_download_token
from server.dependencies import require_project_migration_ok
from server.i18n import Translator
from server.media_tools.final_cuts import final_cut_download_url
from server.services.presentation.timeline_preview import TimelinePreviewMedia, TimelinePreviewService
from server.services.tasks.render_tasks import (
    final_cut_task_request,
    jianying_draft_task_request,
    timeline_render_resource_ids,
)

if TYPE_CHECKING:
    from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService

router = APIRouter(dependencies=[Depends(require_project_migration_ok)])

# Native browser downloads cannot send Authorization headers; validate short-lived download tokens in endpoints (docs/adr/0071).
self_auth_router = APIRouter()


def get_edit_timeline_service() -> EditTimelineService:
    return EditTimelineService(get_project_manager())


EditTimelineServiceDep = Annotated[EditTimelineService, Depends(get_edit_timeline_service)]


def get_timeline_preview_service() -> TimelinePreviewService:
    return TimelinePreviewService(get_project_manager())


TimelinePreviewServiceDep = Annotated[TimelinePreviewService, Depends(get_timeline_preview_service)]


def get_final_cut_service() -> FinalCutService:
    return FinalCutService(get_project_manager())


FinalCutServiceDep = Annotated[FinalCutService, Depends(get_final_cut_service)]
EpisodeEditOverviewReader = Callable[[str, int], Awaitable[EpisodeEditOverview]]


def get_episode_edit_overview() -> EpisodeEditOverviewReader:
    return partial(episode_edit_overview, get_project_manager())


EpisodeEditOverviewDep = Annotated[EpisodeEditOverviewReader, Depends(get_episode_edit_overview)]


def get_workflow_state_service() -> WorkflowStateService:
    return WorkflowStateService(get_project_manager())


WorkflowStateServiceDep = Annotated[WorkflowStateService, Depends(get_workflow_state_service)]
GenerationQueueDep = Annotated[GenerationQueue, Depends(get_generation_queue)]


def get_jianying_draft_service() -> TimelineJianyingDraftService:
    from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService

    return TimelineJianyingDraftService(get_project_manager())


# Concrete types appear only under TYPE_CHECKING; pyJianYingDraft is heavy and imported lazily at runtime.
JianyingDraftServiceDep = Annotated[Any, Depends(get_jianying_draft_service)]

_ERROR_STATUS: dict[str, int] = {
    "project_not_found": 404,
    "episode_not_found": 404,
    "timeline_not_found": 404,
    "revision_not_found": 404,
    "revision_unchanged": 409,
    "timeline_name_conflict": 409,
    "timeline_name_invalid": 422,
    "script_invalid": 422,
    "timeline_invalid": 422,
}


def edit_timeline_api_error(exc: EditTimelineError) -> ApiError:
    message = edit_timeline_message(exc.code, exc.params)
    if message is None:
        raise exc
    key, params = message
    return ApiError(key, status_code=_ERROR_STATUS[exc.code], **params)


_FINAL_CUT_STATUS: dict[str, int] = {
    "final_cut_narration_unavailable": 422,
    "final_cut_blocked": 409,
    "final_cut_video_version_corrupted": 409,
    "final_cut_narration_version_corrupted": 409,
    "final_cut_empty": 422,
    "final_cut_ffmpeg_unavailable": 503,
    "final_cut_render_failed": 500,
    "final_cut_acceptance_failed": 500,
}


def final_cut_api_error(exc: FinalCutError) -> ApiError:
    """Final-cut error summaries list only blocking video units; attach structured issues and clip IDs to diagnostics."""
    params = exc.params
    issues = params.get("issues") or []
    units = "、".join(dict.fromkeys(str(issue.get("unit_id")) for issue in issues if issue.get("unit_id")))
    values = {key: value for key, value in params.items() if key != "issues"}
    error = ApiError(exc.code, status_code=_FINAL_CUT_STATUS[exc.code], **values, units=units)
    if params:
        error.with_diagnostic(params)
    return error


class CreateEditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    source: Literal["script"] = Field(alias="from")
    name: str


class CopyEditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    revision: int | None = Field(
        default=None, ge=1, description="Revision number to copy; omit to copy the latest revision"
    )


class RenameEditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str


class RestoreEditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(ge=1, description="Revision number to restore")


class RenderFinalCutBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int | None = Field(default=None, ge=1)
    # Omitted values use project defaults: TTS includes narration, others omit it; subtitles default to burned in.
    narration: DraftNarration | None = None
    subtitles: SubtitleMode | None = None


class FinalCutSubmission(BaseModel):
    task_id: str
    deduped: bool
    artifact_path: str


class FinalCutStatusResponse(FinalCutStatus):
    download_url: str | None


class EditTimelineListResponse(BaseModel):
    timelines: tuple[TimelineSummary, ...]


@router.get("/projects/{project_name}/edit-timelines")
async def list_edit_timelines(
    project_name: str,
    service: EditTimelineServiceDep,
    episode: int | None = Query(None, ge=1),
) -> EditTimelineListResponse:
    try:
        return EditTimelineListResponse(timelines=await service.list_timelines(project_name, episode=episode))
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


@router.post("/projects/{project_name}/episodes/{episode}/edit-timelines", status_code=201)
async def create_edit_timeline(
    project_name: str,
    episode: Annotated[int, PathParam(ge=1)],
    body: CreateEditTimelineRequest,
    service: EditTimelineServiceDep,
    workflow: WorkflowStateServiceDep,
    user: CurrentUser,
    _t: Translator,
) -> EditTimelineReadout:
    """Mechanically create from the script; admission shares the production-state predicate in ``operations.create_edit_timeline``: at least one usable video in the episode.

    If admission cannot be read, such as a missing project/episode, do not reject here; let the command report the appropriate domain error."""
    try:
        status = await asyncio.to_thread(workflow.get_status, project_name, episode)
    except (FileNotFoundError, WorkflowRequestError):
        status = None
    admission = None
    if status is not None and status.target is not None and status.target.episode == episode:
        admission = status.operations.get(WorkflowActionType.CREATE_EDIT_TIMELINE)
    if admission is not None and admission.state is AdmissionState.REFUSED:
        reason = _t(f"operation_{admission.reason}", where=_t("operation_episode", episode=episode))
        raise UnprocessableError("edit_timeline_refused", reason=reason)
    try:
        with project_change_source("webui"):
            return await service.create_from_script(
                project_name,
                episode=episode,
                name=body.name,
                author=RevisionAuthor(kind="creator", user_id=user.id),
            )
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


@router.get("/projects/{project_name}/episodes/{episode}/edit-overview")
async def read_episode_edit_overview(
    project_name: str,
    episode: Annotated[int, PathParam(ge=1)],
    read_overview: EpisodeEditOverviewDep,
) -> EpisodeEditOverview:
    """Episode editing summary: timeline count, issue count for the most recently modified timeline, and timelines whose final cuts are stale."""
    try:
        return await read_overview(project_name, episode)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


@router.get("/projects/{project_name}/edit-timelines/{timeline_id}")
async def read_edit_timeline(
    project_name: str,
    timeline_id: str,
    service: EditTimelineServiceDep,
    revision: int | None = Query(None, ge=1),
) -> EditTimelineReadout:
    try:
        return await service.read(project_name, timeline_id, revision=revision)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


@router.post("/projects/{project_name}/edit-timelines/{timeline_id}/copy", status_code=201)
async def copy_edit_timeline(
    project_name: str,
    timeline_id: str,
    body: CopyEditTimelineRequest,
    service: EditTimelineServiceDep,
    user: CurrentUser,
) -> EditTimelineReadout:
    """Copy the specified revision, defaulting to latest, into a new timeline in the same episode and return its first revision."""
    try:
        with project_change_source("webui"):
            return await service.copy(
                project_name,
                timeline_id,
                name=body.name,
                revision=body.revision,
                author=RevisionAuthor(kind="creator", user_id=user.id),
            )
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


@router.patch("/projects/{project_name}/edit-timelines/{timeline_id}")
async def rename_edit_timeline(
    project_name: str,
    timeline_id: str,
    body: RenameEditTimelineRequest,
    service: EditTimelineServiceDep,
) -> TimelineSummary:
    """Rename the display name without creating a revision or making final cuts/Jianying drafts stale."""
    try:
        with project_change_source("webui"):
            return await service.rename(project_name, timeline_id, name=body.name)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


@router.get("/projects/{project_name}/edit-timelines/{timeline_id}/revisions")
async def list_edit_timeline_revisions(
    project_name: str,
    timeline_id: str,
    service: EditTimelineServiceDep,
) -> RevisionHistory:
    try:
        return await service.list_revisions(project_name, timeline_id)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


@router.post("/projects/{project_name}/edit-timelines/{timeline_id}/restore")
async def restore_edit_timeline_revision(
    project_name: str,
    timeline_id: str,
    body: RestoreEditTimelineRequest,
    service: EditTimelineServiceDep,
    user: CurrentUser,
) -> EditTimelineWriteResult:
    """Restore by appending a new revision with old content, without rewriting history."""
    try:
        with project_change_source("webui"):
            return await service.restore(
                project_name,
                timeline_id,
                revision=body.revision,
                author=RevisionAuthor(kind="creator", user_id=user.id),
            )
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


@router.delete("/projects/{project_name}/edit-timelines/{timeline_id}", status_code=204)
async def delete_edit_timeline(
    project_name: str,
    timeline_id: str,
    service: EditTimelineServiceDep,
    queue: GenerationQueueDep,
    user: CurrentUser,
) -> Response:
    """Delete the timeline and its final cuts/Jianying drafts; reject while render tasks are queued or running, allowing deletion after completion."""
    for task_type, resource_ids in timeline_render_resource_ids(timeline_id).items():
        active = await queue.get_active_tasks_for_resources(
            project_name=project_name, task_type=task_type, resource_ids=resource_ids, user_id=user.id
        )
        if active:
            raise ApiError("edit_timeline_render_in_progress", status_code=409, task_id=active[0]["task_id"])
    try:
        with project_change_source("webui"):
            await service.delete(project_name, timeline_id)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    return Response(status_code=204)


@router.get("/projects/{project_name}/edit-timelines/{timeline_id}/preview-media")
async def read_edit_timeline_preview_media(
    project_name: str,
    timeline_id: str,
    service: TimelinePreviewServiceDep,
) -> TimelinePreviewMedia:
    try:
        return await service.media(project_name, timeline_id)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


@router.post("/projects/{project_name}/edit-timelines/{timeline_id}/final-cut", status_code=202)
async def render_final_cut(
    project_name: str,
    timeline_id: str,
    service: FinalCutServiceDep,
    queue: GenerationQueueDep,
    user: CurrentUser,
    body: RenderFinalCutBody | None = None,
) -> FinalCutSubmission:
    """Check blocking issues for the selected variant before enqueueing; omitted ``revision`` renders the latest revision at submission time."""
    request_body = body or RenderFinalCutBody()
    try:
        check = await service.check(
            project_name,
            timeline_id,
            revision=request_body.revision,
            narration=request_body.narration,
            subtitles=request_body.subtitles,
        )
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    except FinalCutError as exc:
        raise final_cut_api_error(exc) from exc
    request = final_cut_task_request(
        episode=check.episode, timeline_id=check.timeline_id, revision=check.revision, variant=check.variant
    )
    try:
        enqueued = await queue.enqueue_task(
            project_name=project_name, **request.enqueue_fields(), source="webui", user_id=user.id
        )
    except ActiveTaskRequestConflict as exc:
        raise ApiError("final_cut_render_in_progress", status_code=409, task_id=exc.existing_task_id) from exc
    return FinalCutSubmission(
        task_id=enqueued["task_id"], deduped=bool(enqueued.get("deduped", False)), artifact_path=request.artifact_path
    )


@router.get("/projects/{project_name}/edit-timelines/{timeline_id}/final-cut")
async def read_final_cut(
    project_name: str,
    timeline_id: str,
    service: FinalCutServiceDep,
    narration: DraftNarration | None = Query(None, description="Narration variant; omit to use the project default"),
    subtitles: SubtitleMode | None = Query(None, description="Subtitle mode; omit to burn subtitles in"),
) -> FinalCutStatusResponse:
    """Final-cut state; stale cuts remain downloadable, with ``download_url`` supplied only if the file exists."""
    try:
        status = await service.status(project_name, timeline_id, narration=narration, subtitles=subtitles)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    download_url = (
        final_cut_download_url(project_name, status.artifact_path, status.version)
        if status.version is not None
        else None
    )
    return FinalCutStatusResponse(**status.model_dump(), download_url=download_url)


class ExportJianyingDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int | None = Field(default=None, ge=1)
    # Omit to use the project narration default: included for TTS projects, excluded otherwise.
    narration: DraftNarration | None = None


class JianyingDraftSubmission(BaseModel):
    task_id: str
    deduped: bool
    artifact_path: str


_JIANYING_DRAFT_STATUS: dict[str, int] = {
    "jianying_draft_narration_unavailable": 422,
    "jianying_draft_blocked": 409,
    "jianying_draft_empty": 422,
    "jianying_draft_not_exported": 404,
    "jianying_draft_invalid": 409,
}


def jianying_draft_api_error(exc: JianyingDraftError) -> ApiError:
    """Jianying-draft error summaries list only blocking video units; attach structured issues to diagnostics."""
    params = dict(exc.params)
    issues = params.pop("issues", None)
    if issues is not None:
        params["units"] = "、".join(
            dict.fromkeys(str(issue.get("unit_id")) for issue in issues if issue.get("unit_id"))
        )
    error = ApiError(exc.code, status_code=_JIANYING_DRAFT_STATUS[exc.code], **params)
    if issues is not None:
        error.with_diagnostic({"issues": issues})
    return error


@router.post("/projects/{project_name}/edit-timelines/{timeline_id}/jianying-draft", status_code=202)
async def export_jianying_draft(
    project_name: str,
    timeline_id: str,
    service: JianyingDraftServiceDep,
    queue: GenerationQueueDep,
    user: CurrentUser,
    body: ExportJianyingDraftBody | None = None,
) -> JianyingDraftSubmission:
    """Check blocking issues before enqueueing; omitted ``revision`` exports the latest revision at submission time."""
    request_body = body or ExportJianyingDraftBody()
    try:
        check = await service.check(
            project_name, timeline_id, narration=request_body.narration, revision=request_body.revision
        )
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    except JianyingDraftError as exc:
        raise jianying_draft_api_error(exc) from exc
    request = jianying_draft_task_request(
        episode=check.episode,
        timeline_id=check.timeline_id,
        revision=check.revision,
        narration=check.narration,
    )
    try:
        enqueued = await queue.enqueue_task(
            project_name=project_name, **request.enqueue_fields(), source="webui", user_id=user.id
        )
    except ActiveTaskRequestConflict as exc:
        raise ApiError("jianying_draft_export_in_progress", status_code=409, task_id=exc.existing_task_id) from exc
    return JianyingDraftSubmission(
        task_id=enqueued["task_id"], deduped=bool(enqueued.get("deduped", False)), artifact_path=request.artifact_path
    )


@router.get("/projects/{project_name}/edit-timelines/{timeline_id}/jianying-draft")
async def read_jianying_draft(
    project_name: str,
    timeline_id: str,
    service: JianyingDraftServiceDep,
    narration: DraftNarration | None = Query(None, description="Narration variant; omit to use the project default"),
) -> JianyingDraftStatus:
    """Jianying-draft state: current, stale (behind the timeline but still downloadable), or missing (never exported)."""
    try:
        return await service.status(project_name, timeline_id, narration=narration)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc


def _draft_root(value: str, _t: Translator) -> str:
    if not value or not value.strip():
        raise HTTPException(status_code=422, detail=_t("jianying_path_invalid"))
    if len(value) > 1024:
        raise HTTPException(status_code=422, detail=_t("jianying_path_too_long"))
    if any(ord(character) < 32 for character in value):
        raise HTTPException(status_code=422, detail=_t("jianying_path_illegal"))
    return value.strip()


@self_auth_router.get("/projects/{project_name}/edit-timelines/{timeline_id}/jianying-draft/download")
async def download_jianying_draft(
    project_name: str,
    timeline_id: str,
    _t: Translator,
    service: JianyingDraftServiceDep,
    draft_path: str = Query(..., description="Jianying draft directory on the user’s local machine"),
    download_token: str = Query(..., description="Download token"),
    jianying_version: Literal["5", "6"] = Query("6", description="Jianying version: 6 means 6 or later; 5 means 5.x"),
    narration: DraftNarration | None = Query(None, description="Narration variant; omit to use the project default"),
) -> FileResponse:
    """Download a registered Jianying draft, substituting the local draft directory and Jianying version here; stale drafts remain downloadable."""
    try:
        verify_download_token(download_token, project_name)
    except jwt.ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail=_t("download_expired")) from exc
    except ValueError as exc:
        raise HTTPException(status_code=403, detail=_t("download_token_mismatch")) from exc
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail=_t("download_token_invalid")) from exc
    root = _draft_root(draft_path, _t)
    try:
        package, name = await service.package_download(
            project_name,
            timeline_id,
            narration=narration,
            draft_root=root,
            jianying_version=jianying_version,
            translate=_t,
        )
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    except JianyingDraftError as exc:
        raise jianying_draft_api_error(exc) from exc
    # hard-delete: temp -- temporary Jianying draft download-package directory
    return FileResponse(
        path=str(package),
        media_type="application/zip",
        filename=f"{name}.zip",
        background=BackgroundTask(shutil.rmtree, str(package.parent), ignore_errors=True),
    )
