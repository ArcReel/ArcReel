"""供应商素材关联的操作编排：查看图片当前版本的关联与入库状态、上传、解除关联、刷新状态，以及浏览素材库。

Web 路由与 Agent 工具共用这里的编排与结果形状；关联与账号判断在 ``lib.asset_library.links``，
本模块不另做判断。上传与刷新只改记录并唤醒后台循环，立即返回，不等待供应商处理。
结果只含状态事实，不含控制面密钥、中转对象或供应商的签名下载地址。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.artifacts.version_manager import VersionManager
from lib.asset_library import links as link_service
from lib.asset_library.control_plane import (
    AssetLibraryRequestError,
    credentials_of,
    list_assets,
    request_failure_error,
)
from lib.asset_library.ingest import wake_asset_ingest
from lib.db.base import dt_to_iso
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.infra.api_errors import BadRequestError, NotFoundError
from lib.material.storage import CONTENT_DIGEST, CONTENT_VERSION_ID


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


class ProviderAssetResponse(BaseModel):
    id: str
    name: str
    asset_type: str
    status: Literal["active", "processing", "failed", "unknown"]
    error_code: str | None
    created_at: str | None


class ProviderAssetListResponse(BaseModel):
    items: list[ProviderAssetResponse]
    total: int
    page: int
    page_size: int


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


def check_resource_type(resource_type: str) -> None:
    if resource_type not in link_service.UPLOADABLE_RESOURCE_TYPES:
        raise BadRequestError("unsupported_resource_type", resource_type=resource_type)


def _current_record(project_path: Path, resource_type: str, resource_id: str) -> dict[str, Any] | None:
    """资源当前选中的版本记录；只读，不为早期记录补写身份。"""
    info = VersionManager(project_path).get_versions(resource_type, resource_id)
    current = info.get("current_version", 0)
    return next((v for v in info.get("versions", []) if v.get("version") == current), None)


async def load_link_panel(
    session: AsyncSession, *, project_path: Path, project_name: str, resource_type: str, resource_id: str
) -> LinkPanelResponse:
    """图片当前选中版本在各个素材库范围里的关联与入库状态。改选另一版本后，返回的是那个版本的关联。"""
    check_resource_type(resource_type)
    record = await asyncio.to_thread(_current_record, project_path, resource_type, resource_id)
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


async def start_upload(
    session: AsyncSession,
    *,
    project_path: Path,
    project_name: str,
    resource_type: str,
    resource_id: str,
    account_id: int,
) -> LinkResponse:
    """把图片当前选中的版本上传到账号对应的素材库：创建入库任务并提交事务，立即返回任务状态。"""
    check_resource_type(resource_type)
    target = await link_service.resolve_upload_target(project_path, resource_type, resource_id)
    link = await link_service.request_upload(session, project_name=project_name, target=target, account_id=account_id)
    await session.commit()
    wake_asset_ingest()
    return link_response(link)


async def release(session: AsyncSession, *, project_name: str, link_id: int) -> LinkResponse:
    """解除关联：只解除当前关系，不删除 ArcReel 图片或供应商素材。已解除的关联原样返回。"""
    link = await link_service.get_link(session, project_name=project_name, link_id=link_id)
    await link_service.release_link(session, link)
    await session.commit()
    return link_response(link)


async def recheck(session: AsyncSession, *, project_name: str, link_id: int) -> LinkResponse:
    """请后台立即重新查询状态；已可用或已失败的关联原样返回。"""
    link = await link_service.get_link(session, project_name=project_name, link_id=link_id)
    await link_service.request_recheck(session, link)
    await session.commit()
    wake_asset_ingest()
    return link_response(link)


async def browse_provider_assets(
    session: AsyncSession, *, account_id: int, page: int, page_size: int
) -> ProviderAssetListResponse:
    """按账号的站点、区域与项目范围分页列出供应商素材库里的素材，不含签名下载地址。"""
    account = await AssetLibraryRepository(session).get_account(account_id)
    if account is None:
        raise NotFoundError("asset_library_account_not_found")
    try:
        result = await list_assets(credentials_of(account), page_number=page, page_size=page_size)
    except AssetLibraryRequestError as exc:
        raise request_failure_error(exc) from exc
    return ProviderAssetListResponse(
        items=[
            ProviderAssetResponse(
                id=item.id,
                name=item.name,
                asset_type=item.asset_type,
                status=item.status,
                error_code=item.error_code,
                created_at=item.created_at,
            )
            for item in result.items
        ],
        total=result.total,
        page=result.page_number,
        page_size=result.page_size,
    )


__all__ = [
    "AccountSummary",
    "ContentVersionInfo",
    "LinkPanelResponse",
    "LinkResponse",
    "ProviderAssetListResponse",
    "ProviderAssetResponse",
    "browse_provider_assets",
    "check_resource_type",
    "link_response",
    "load_link_panel",
    "recheck",
    "release",
    "start_upload",
]
