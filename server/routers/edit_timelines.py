"""剪辑时间线的 HTTP 入口：列表、读取、新建、复制、改名、修订历史、回滚、删除、一集的剪辑概况，以及成片与剪映草稿的提交、现状与下载。

行为全部在 lib 层剪辑时间线命令、成片服务与剪映草稿服务里；渲染作为 ``render`` 车道任务入队。
"""

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
from lib.final_cut.basis import DEFAULT_VARIANT
from lib.final_cut.errors import FinalCutError
from lib.final_cut.overview import EpisodeEditOverview, episode_edit_overview
from lib.final_cut.service import FinalCutService, FinalCutStatus
from lib.generation.generation_queue import ActiveTaskRequestConflict, GenerationQueue, get_generation_queue
from lib.infra.api_errors import ApiError, UnprocessableError
from lib.jianying_draft.basis import DraftNarration
from lib.jianying_draft.errors import JianyingDraftError
from lib.jianying_draft.results import JianyingDraftStatus
from lib.project.project_manager import get_project_manager
from lib.workflow.operation_admission import AdmissionState
from lib.workflow.workflow_state import WorkflowActionType, WorkflowRequestError, WorkflowStateService
from server.auth import CurrentUser, verify_download_token
from server.dependencies import require_project_migration_ok
from server.i18n import Translator
from server.media_tools.final_cuts import final_cut_download_url
from server.services.tasks.render_tasks import (
    final_cut_task_request,
    jianying_draft_task_request,
    timeline_render_resource_ids,
)

if TYPE_CHECKING:
    from server.services.presentation.timeline_jianying_draft import TimelineJianyingDraftService

router = APIRouter(dependencies=[Depends(require_project_migration_ok)])

# 浏览器原生下载带不了 Authorization header，端点内校验短时效下载 token（见 docs/adr/0071）。
self_auth_router = APIRouter()


def get_edit_timeline_service() -> EditTimelineService:
    return EditTimelineService(get_project_manager())


EditTimelineServiceDep = Annotated[EditTimelineService, Depends(get_edit_timeline_service)]


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


# 具体类型只在 TYPE_CHECKING 下可见：pyJianYingDraft 是重依赖，运行期按需惰性导入。
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


class CopyEditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    revision: int | None = Field(default=None, ge=1, description="被复制的修订号；省略时复制最新修订")


class RenameEditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str


class RestoreEditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(ge=1, description="要回滚到的修订号")


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
    workflow: WorkflowStateServiceDep,
    user: CurrentUser,
    _t: Translator,
) -> EditTimelineReadout:
    """按脚本机械新建；准入与制作状态 ``operations.create_edit_timeline`` 同一份谓词：本集至少有一个可用视频。

    读不出准入（项目或集不存在等）时不在这里拒绝，由命令报对应的领域错误。
    """
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
    """一集的剪辑概况：剪辑时间线条数、最近修改那条的问题数，以及成片已落后的几条。"""
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
    """把指定修订（缺省为最新修订）复制成同一集的新剪辑时间线，返回新时间线的第一个修订。"""
    try:
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
    """改显示名：不产生修订，成片与剪映草稿不因此过期。"""
    try:
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
    """回滚：以旧修订的内容追加一个新修订，历史不改写。"""
    try:
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
    """删除剪辑时间线及其成片与剪映草稿；仍有渲染任务在排队或执行时拒绝，等任务结束后再删。"""
    for task_type, resource_ids in timeline_render_resource_ids(timeline_id).items():
        active = await queue.get_active_tasks_for_resources(
            project_name=project_name, task_type=task_type, resource_ids=resource_ids, user_id=user.id
        )
        if active:
            raise ApiError("edit_timeline_render_in_progress", status_code=409, task_id=active[0]["task_id"])
    try:
        await service.delete(project_name, timeline_id)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    return Response(status_code=204)


@router.post("/projects/{project_name}/edit-timelines/{timeline_id}/final-cut", status_code=202)
async def render_final_cut(
    project_name: str,
    timeline_id: str,
    service: FinalCutServiceDep,
    queue: GenerationQueueDep,
    user: CurrentUser,
    body: RenderFinalCutBody | None = None,
) -> FinalCutSubmission:
    """先检查阻断问题再入队；``revision`` 省略时渲染提交时的最新修订。"""
    revision = body.revision if body is not None else None
    try:
        check = await service.check(project_name, timeline_id, revision=revision, variant=DEFAULT_VARIANT)
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    except FinalCutError as exc:
        raise final_cut_api_error(exc) from exc
    request = final_cut_task_request(
        episode=check.episode, timeline_id=check.timeline_id, revision=check.revision, variant=DEFAULT_VARIANT
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


class ExportJianyingDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int | None = Field(default=None, ge=1)
    # 省略时按项目取默认旁白版本：TTS 配音项目带旁白，其余不带旁白。
    narration: DraftNarration | None = None


class JianyingDraftSubmission(BaseModel):
    task_id: str
    deduped: bool
    artifact_path: str


_JIANYING_DRAFT_STATUS: dict[str, int] = {
    "jianying_draft_narration_unavailable": 422,
    "jianying_draft_blocked": 409,
    "jianying_draft_content_unsupported": 422,
    "jianying_draft_empty": 422,
    "jianying_draft_not_exported": 404,
    "jianying_draft_invalid": 409,
}


def jianying_draft_api_error(exc: JianyingDraftError) -> ApiError:
    """剪映草稿错误的摘要只列出阻断的视频单元；结构化的问题清单挂在诊断上。"""
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
    """先检查阻断问题再入队；``revision`` 省略时导出提交时的最新修订。"""
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
    narration: DraftNarration | None = Query(None, description="旁白版本；省略时按项目取默认版本"),
) -> JianyingDraftStatus:
    """剪映草稿现状：current、stale（已落后于剪辑时间线，仍可下载）或 missing（还没导出过）。"""
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
    draft_path: str = Query(..., description="用户本机的剪映草稿目录"),
    download_token: str = Query(..., description="下载 token"),
    jianying_version: Literal["5", "6"] = Query("6", description="剪映版本：6 表示 6 及以上，5 表示 5.x"),
    narration: DraftNarration | None = Query(None, description="旁白版本；省略时按项目取默认版本"),
) -> FileResponse:
    """下载已登记的剪映草稿：本机草稿目录与剪映版本在此代入，过期的草稿照常可下载。"""
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
            project_name, timeline_id, narration=narration, draft_root=root, jianying_version=jianying_version
        )
    except EditTimelineError as exc:
        raise edit_timeline_api_error(exc) from exc
    except JianyingDraftError as exc:
        raise jianying_draft_api_error(exc) from exc
    return FileResponse(
        path=str(package),
        media_type="application/zip",
        filename=f"{name}.zip",
        background=BackgroundTask(shutil.rmtree, str(package.parent), ignore_errors=True),
    )
