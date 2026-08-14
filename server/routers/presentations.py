"""Unified presentation preview and editable-download endpoints."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Query
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from lib.api_errors import ApiError
from lib.project_manager import get_project_manager
from server.services.presentation_bundle import PresentationBundleService
from server.services.presentation_read_model import PresentationReadModelService, PresentationUnavailableError

router = APIRouter()
ResourceType = Literal["videos", "reference_videos"]
Variant = Literal["post_production", "use_tts"]


def get_presentation_read_model() -> PresentationReadModelService:
    return PresentationReadModelService(get_project_manager())


def get_presentation_bundle_service() -> PresentationBundleService:
    manager = get_project_manager()
    return PresentationBundleService(manager)


def _cleanup_bundle(path: str) -> None:
    shutil.rmtree(Path(path).parent, ignore_errors=True)


@router.get("/projects/{project_name}/presentations/{resource_type}/{resource_id}/bundle")
async def download_presentation_bundle(
    project_name: str,
    resource_type: ResourceType,
    resource_id: str,
    variant: Variant = "post_production",
    video_version: int | None = Query(None, ge=1),
    audio_version: int | None = Query(None, ge=1),
):
    try:
        path = await get_presentation_bundle_service().export_unit(
            project_name=project_name,
            resource_type=resource_type,
            resource_id=resource_id,
            variant=variant,
            video_version=video_version,
            audio_version=audio_version,
        )
    except PresentationUnavailableError as exc:
        raise ApiError("presentation_unavailable", status_code=422) from exc
    return FileResponse(
        path,
        media_type="application/zip",
        filename=f"{resource_id}_presentation.zip",
        background=BackgroundTask(_cleanup_bundle, str(path)),
    )


@router.get("/projects/{project_name}/presentations/{resource_type}/{resource_id}")
async def get_presentation(
    project_name: str,
    resource_type: ResourceType,
    resource_id: str,
    variant: Variant = "post_production",
    video_version: int | None = Query(None, ge=1),
    audio_version: int | None = Query(None, ge=1),
):
    try:
        result = await get_presentation_read_model().materialize_unit(
            project_name=project_name,
            resource_type=resource_type,
            resource_id=resource_id,
            variant=variant,
            video_version=video_version,
            audio_version=audio_version,
        )
    except PresentationUnavailableError as exc:
        raise ApiError("presentation_unavailable", status_code=422) from exc
    return result.to_dict()


__all__ = ["get_presentation_bundle_service", "get_presentation_read_model", "router"]
