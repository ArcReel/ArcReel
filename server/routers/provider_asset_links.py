"""供应商素材关联 API：查看图片当前版本的关联与入库状态、上传到素材库、从素材库选择已有图片、解除关联与刷新状态。

上传只创建入库任务并立即返回，中转上传、提交与状态查询由后台循环推进（``lib.asset_library.ingest``），
页面按返回的状态轮询，不等待供应商审核。领域规则在 ``lib.asset_library.links``，本路由只做参数与响应映射。
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.artifacts.version_manager import VersionManager
from lib.asset_library import links as link_service
from lib.asset_library.ingest import wake_asset_ingest
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import BadRequestError
from lib.material.storage import CONTENT_DIGEST, CONTENT_VERSION_ID
from lib.project.project_manager import get_project_manager
from server.services.project.provider_asset_import import import_provider_asset

router = APIRouter(prefix="/projects/{project_name}/provider-asset-links", tags=["Provider Asset Links"])


class LinkResponse(BaseModel):
    id: int
    content_version_id: str
    version: int
    account_id: int
    site: str
    region: str
    provider_project_name: str
    state: str
    selected: bool
    usable: bool
    provider_asset_id: str | None
    error_code: str | None
    error_message: str | None
    check_error: str | None
    #: 后台仍会自动查询；为 false 且状态未定时，由用户手动刷新。
    polling: bool
    created_at: str | None
    submitted_at: str | None
    settled_at: str | None


class AccountSummary(BaseModel):
    id: int
    name: str
    site: str
    region: str
    project_name: str


class ContentVersionInfo(BaseModel):
    id: str | None
    version: int
    digest: str | None


class LinkPanelResponse(BaseModel):
    content_version: ContentVersionInfo | None
    object_storage_configured: bool
    accounts: list[AccountSummary]
    links: list[LinkResponse]


class UploadRequest(BaseModel):
    resource_type: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=512)
    account_id: int


class ImportRequest(UploadRequest):
    asset_id: str = Field(min_length=1, max_length=256)


class ImportResponse(BaseModel):
    content_version: ContentVersionInfo
    link: LinkResponse


def link_response(link: ProviderAssetLink) -> LinkResponse:
    return LinkResponse(
        id=link.id,
        content_version_id=link.content_version_id,
        version=link.version,
        account_id=link.account_id,
        site=link.site,
        region=link.region,
        provider_project_name=link.provider_project_name,
        state=link.state,
        selected=link.selected,
        usable=link_service.is_usable(link),
        provider_asset_id=link.provider_asset_id,
        error_code=link.error_code,
        error_message=link.error_message,
        check_error=link.check_error,
        polling=link.next_check_at is not None,
        created_at=dt_to_iso(link.created_at),
        submitted_at=dt_to_iso(link.submitted_at),
        settled_at=dt_to_iso(link.settled_at),
    )


def _current_record(project_name: str, resource_type: str, resource_id: str) -> dict[str, Any] | None:
    """资源当前选中的版本记录；只读，不为早期记录补写身份。"""
    manager = VersionManager(get_project_manager().get_project_path(project_name))
    info = manager.get_versions(resource_type, resource_id)
    current = info.get("current_version", 0)
    return next((v for v in info.get("versions", []) if v.get("version") == current), None)


def _check_resource_type(resource_type: str) -> None:
    if resource_type not in link_service.UPLOADABLE_RESOURCE_TYPES:
        raise BadRequestError("unsupported_resource_type", resource_type=resource_type)


@router.get("", response_model=LinkPanelResponse)
async def get_links(
    project_name: str,
    resource_type: str = Query(min_length=1, max_length=64),
    resource_id: str = Query(min_length=1, max_length=512),
    session: AsyncSession = Depends(get_async_session),
) -> LinkPanelResponse:
    """图片当前选中版本在各个素材库范围里的关联与入库状态。改选另一版本后，返回的是那个版本的关联。"""
    _check_resource_type(resource_type)
    record = await asyncio.to_thread(_current_record, project_name, resource_type, resource_id)
    content_version: ContentVersionInfo | None = None
    links: list[LinkResponse] = []
    if record is not None:
        cv_id = record.get(CONTENT_VERSION_ID)
        digest = record.get(CONTENT_DIGEST)
        content_version = ContentVersionInfo(
            id=cv_id if isinstance(cv_id, str) else None,
            version=int(record["version"]),
            digest=digest if isinstance(digest, str) else None,
        )
        if content_version.id is not None:
            links = [
                link_response(link)
                for link in await link_service.selected_links(
                    session, project_name=project_name, content_version_id=content_version.id
                )
            ]
    accounts = await AssetLibraryRepository(session).list_accounts()
    return LinkPanelResponse(
        content_version=content_version,
        object_storage_configured=await ObjectStorageConnectionRepository(session).get() is not None,
        accounts=[
            AccountSummary(id=a.id, name=a.name, site=a.site, region=a.region, project_name=a.project_name)
            for a in accounts
        ],
        links=links,
    )


@router.post("", status_code=202, response_model=LinkResponse)
async def upload_to_asset_library(
    project_name: str, body: UploadRequest, session: AsyncSession = Depends(get_async_session)
) -> LinkResponse:
    """把图片当前选中的版本上传到账号对应的素材库。立即返回入库任务，之后按状态轮询。"""
    _check_resource_type(body.resource_type)
    project_path = await asyncio.to_thread(get_project_manager().get_project_path, project_name)
    target = await link_service.resolve_upload_target(project_path, body.resource_type, body.resource_id)
    link = await link_service.request_upload(
        session, project_name=project_name, target=target, account_id=body.account_id
    )
    await session.commit()
    wake_asset_ingest()
    return link_response(link)


@router.post("/import", status_code=201, response_model=ImportResponse)
async def import_from_asset_library(
    project_name: str, body: ImportRequest, session: AsyncSession = Depends(get_async_session)
) -> ImportResponse:
    """从素材库选择一张可用的图片：取回原图保存为资产图的新版本，并为这个版本建立关联。

    取不回内容或保存失败时不建立关联，原选中版本不变。
    """
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
    """解除关联：只解除当前关系，不删除 ArcReel 图片或供应商素材。"""
    link = await link_service.get_link(session, project_name=project_name, link_id=link_id)
    await link_service.release_link(session, link)
    await session.commit()
    return Response(status_code=204)


@router.post("/{link_id}/refresh", response_model=LinkResponse)
async def refresh(project_name: str, link_id: int, session: AsyncSession = Depends(get_async_session)) -> LinkResponse:
    """请后台立即重新查询状态；已可用或已失败的关联原样返回。"""
    link = await link_service.get_link(session, project_name=project_name, link_id=link_id)
    await link_service.request_recheck(session, link)
    await session.commit()
    wake_asset_ingest()
    return link_response(link)
