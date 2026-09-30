"""剪辑时间线的 HTTP 入口：列表、读取、按脚本机械新建，以及成片的提交、现状与下载地址。

行为全部在 lib 层剪辑时间线命令与成片服务里；成片渲染作为 ``render`` 车道任务入队。
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from fastapi import Path as PathParam
from pydantic import BaseModel, ConfigDict, Field

from lib.edit_timeline import (
    EditTimelineError,
    EditTimelineReadout,
    EditTimelineService,
    RevisionAuthor,
    TimelineSummary,
)
from lib.final_cut.basis import DEFAULT_VARIANT
from lib.final_cut.errors import FinalCutError
from lib.final_cut.service import FinalCutService, FinalCutStatus
from lib.generation.generation_queue import ActiveTaskRequestConflict, GenerationQueue, get_generation_queue
from lib.infra.api_errors import ApiError
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser
from server.dependencies import require_project_migration_ok
from server.media_tools.final_cuts import final_cut_download_url
from server.services.tasks.render_tasks import final_cut_task_request

router = APIRouter(dependencies=[Depends(require_project_migration_ok)])


def get_edit_timeline_service() -> EditTimelineService:
    return EditTimelineService(get_project_manager())


EditTimelineServiceDep = Annotated[EditTimelineService, Depends(get_edit_timeline_service)]


def get_final_cut_service() -> FinalCutService:
    return FinalCutService(get_project_manager())


FinalCutServiceDep = Annotated[FinalCutService, Depends(get_final_cut_service)]
GenerationQueueDep = Annotated[GenerationQueue, Depends(get_generation_queue)]

_ERROR_STATUS: dict[str, tuple[str, int]] = {
    "project_not_found": ("project_not_found", 404),
    "episode_not_found": ("episode_not_found", 404),
    "timeline_not_found": ("edit_timeline_not_found", 404),
    "revision_not_found": ("edit_timeline_revision_not_found", 404),
    "timeline_name_conflict": ("edit_timeline_name_conflict", 409),
    "timeline_name_invalid": ("edit_timeline_name_invalid", 422),
    "script_invalid": ("edit_timeline_script_invalid", 422),
    "timeline_invalid": ("edit_timeline_invalid", 422),
}


def edit_timeline_api_error(exc: EditTimelineError) -> ApiError:
    key, status_code = _ERROR_STATUS[exc.code]
    params = dict(exc.params)
    if "project" in params:
        params["name"] = params.pop("project")
    return ApiError(key, status_code=status_code, **params)


_FINAL_CUT_STATUS: dict[str, int] = {
    "final_cut_variant_unsupported": 422,
    "final_cut_blocked": 409,
    "final_cut_content_unsupported": 422,
    "final_cut_empty": 422,
    "final_cut_ffmpeg_unavailable": 503,
    "final_cut_render_failed": 500,
    "final_cut_acceptance_failed": 500,
}


def final_cut_api_error(exc: FinalCutError) -> ApiError:
    """成片错误的摘要只列出阻断的视频单元；结构化的问题清单与片段 ID 挂在诊断上。"""
    params = exc.params
    issues = params.get("issues") or []
    units = "、".join(dict.fromkeys(str(issue.get("unit_id")) for issue in issues if issue.get("unit_id")))
    error = ApiError(exc.code, status_code=_FINAL_CUT_STATUS[exc.code], units=units)
    if params:
        error.with_diagnostic(params)
    return error


class CreateEditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    source: Literal["script"] = Field(alias="from")
    name: str


class RenderFinalCutBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int | None = Field(default=None, ge=1)


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
    user: CurrentUser,
) -> EditTimelineReadout:
    try:
        return await service.create_from_script(
            project_name,
            episode=episode,
            name=body.name,
            author=RevisionAuthor(kind="creator", user_id=user.id),
        )
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


@router.post("/projects/{project_name}/edit-timelines/{timeline_id}/final-cut", status_code=202)
async def render_final_cut(
    project_name: str,
    timeline_id: str,
    service: FinalCutServiceDep,
    queue: GenerationQueueDep,
    user: CurrentUser,
    body: RenderFinalCutBody | None = None,
) -> FinalCutSubmission:
    """先检查阻断问题再入队；``revision`` 省略时渲染任务开始时的最新修订。"""
    revision = body.revision if body is not None else None
    try:
        check = await service.check(project_name, timeline_id, revision=revision, variant=DEFAULT_VARIANT)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    except FinalCutError as exc:
        raise final_cut_api_error(exc) from exc
    request = final_cut_task_request(
        episode=check.episode, timeline_id=check.timeline_id, revision=revision, variant=DEFAULT_VARIANT
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
) -> FinalCutStatusResponse:
    """成片现状；stale 的成片仍可下载，``download_url`` 只在文件存在时给出。"""
    try:
        status = await service.status(project_name, timeline_id, variant=DEFAULT_VARIANT)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    download_url = (
        final_cut_download_url(project_name, status.artifact_path, status.version)
        if status.version is not None
        else None
    )
    return FinalCutStatusResponse(**status.model_dump(), download_url=download_url)
