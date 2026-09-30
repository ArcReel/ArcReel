"""剪辑时间线的 HTTP 入口：列表、读取与按脚本机械新建；行为全部在 lib 层剪辑时间线命令里。"""

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
from lib.infra.api_errors import ApiError
from lib.project.project_manager import get_project_manager
from server.auth import CurrentUser
from server.dependencies import require_project_migration_ok

router = APIRouter(dependencies=[Depends(require_project_migration_ok)])


def get_edit_timeline_service() -> EditTimelineService:
    return EditTimelineService(get_project_manager())


EditTimelineServiceDep = Annotated[EditTimelineService, Depends(get_edit_timeline_service)]

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


class CreateEditTimelineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, populate_by_name=True)

    source: Literal["script"] = Field(alias="from")
    name: str


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
