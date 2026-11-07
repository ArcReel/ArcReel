"""Provider asset-link API: inspect current-image links and ingestion state, upload, select existing library images, unlink and refresh.

Uploads only create ingestion jobs and return immediately. The background loop (``lib.asset_library.ingest``) handles relay
uploads, submission and state queries; pages poll returned state rather than waiting for provider review. Orchestration and
result shapes live in ``server.services.project.provider_asset_links``, shared with Agent tools; routes map only parameters/responses."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.db import get_async_session
from lib.project.project_manager import get_project_manager
from server.services.project import provider_asset_links as link_ops
from server.services.project.provider_asset_import import import_provider_asset
from server.services.project.provider_asset_links import (
    ContentVersionInfo,
    LinkPanelResponse,
    LinkResponse,
    link_response,
)

router = APIRouter(prefix="/projects/{project_name}/provider-asset-links", tags=["Provider Asset Links"])


class UploadRequest(BaseModel):
    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=512)
    account_id: int


class ImportRequest(UploadRequest):
    asset_id: str = Field(min_length=1, max_length=256)


class ImportResponse(BaseModel):
    content_version: ContentVersionInfo
    link: LinkResponse


async def _project_path(project_name: str) -> Path:
    return await asyncio.to_thread(get_project_manager().get_project_path, project_name)


@router.get("", response_model=LinkPanelResponse)
async def get_links(
    project_name: str,
    resource_type: str = Query(min_length=1, max_length=64),
    resource_id: str = Query(min_length=1, max_length=512),
    session: AsyncSession = Depends(get_async_session),
) -> LinkPanelResponse:
    """Links and ingestion state of the image’s selected version within each library scope; selecting another version returns that version’s links."""
    link_ops.check_resource_type(resource_type)
    return await link_ops.load_link_panel(
        session,
        project_path=await _project_path(project_name),
        project_name=project_name,
        resource_type=resource_type,
        resource_id=resource_id,
    )


@router.post("", status_code=202, response_model=LinkResponse)
async def upload_to_asset_library(
    project_name: str, body: UploadRequest, session: AsyncSession = Depends(get_async_session)
) -> LinkResponse:
    """Upload the image’s currently selected version to the account’s library. Return the ingestion job immediately, then poll its state."""
    link_ops.check_resource_type(body.resource_type)
    return await link_ops.start_upload(
        session,
        project_path=await _project_path(project_name),
        project_name=project_name,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        account_id=body.account_id,
    )


@router.post("/import", status_code=201, response_model=ImportResponse)
async def import_from_asset_library(
    project_name: str, body: ImportRequest, session: AsyncSession = Depends(get_async_session)
) -> ImportResponse:
    """Select an available library image: retrieve the original, save a new asset-image version, and link that version.

    If retrieval or saving fails, create no link and retain the previously selected version."""
    imported = await import_provider_asset(
        session,
        project_name=project_name,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        account_id=body.account_id,
        asset_id=body.asset_id,
    )
    content_version = imported.content_version
    return ImportResponse(
        content_version=ContentVersionInfo(
            id=content_version.id, version=content_version.version, digest=content_version.digest
        ),
        link=link_response(imported.link),
    )


@router.delete("/{link_id}", status_code=204)
async def unlink_provider_asset(
    project_name: str, link_id: int, session: AsyncSession = Depends(get_async_session)
) -> Response:
    """Unlink only the current relationship, without deleting the ArcReel image or provider asset."""
    await link_ops.release(session, project_name=project_name, link_id=link_id)
    return Response(status_code=204)


@router.post("/{link_id}/refresh", response_model=LinkResponse)
async def refresh(project_name: str, link_id: int, session: AsyncSession = Depends(get_async_session)) -> LinkResponse:
    """Ask the background worker to recheck state immediately; return available or failed links unchanged."""
    return await link_ops.recheck(session, project_name=project_name, link_id=link_id)
