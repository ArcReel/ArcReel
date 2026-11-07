"""Provider asset-link orchestration: inspect links and ingestion state for an image’s current version, upload, unlink, refresh state, and browse the library.

Web routes and Agent tools share this orchestration and result shape. ``lib.asset_library.links`` handles link/account decisions;
this module does not duplicate them. Upload and refresh only update records and wake the background loop, returning immediately
without waiting for provider processing. Results contain only state facts, never control-plane secrets, relay objects or signed provider download URLs."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from lib.artifacts.version_manager import VersionManager
from lib.asset_library import links as link_service
from lib.asset_library.bindings import account_scope, scope_key
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
    #: Call scope at submission/import; it matches a scope only when equal to the same field in :class:`AccountSummary`.
    scope_key: str
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
    #: Background polling remains automatic; when false and the state is unresolved, the user refreshes manually.
    polling: bool
    created_at: str | None
    submitted_at: str | None
    settled_at: str | None


class AccountSummary(BaseModel):
    id: int
    #: Scope covered by current account content; changing to another cloud account’s Access Key changes it, so old links no longer match.
    scope_key: str
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
        scope_key=scope_key(link_service.scope_of(link)),
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
    """The resource’s currently selected version record; read only, without backfilling identities for early records."""
    info = VersionManager(project_path).get_versions(resource_type, resource_id)
    current = info.get("current_version", 0)
    return next((v for v in info.get("versions", []) if v.get("version") == current), None)


async def load_link_panel(
    session: AsyncSession, *, project_path: Path, project_name: str, resource_type: str, resource_id: str
) -> LinkPanelResponse:
    """Links and ingestion state of the image’s selected version within each library scope; selecting another version returns that version’s links."""
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
            AccountSummary(
                id=a.id,
                scope_key=scope_key(account_scope(a)),
                name=a.name,
                site=a.site,
                region=a.region,
                project_name=a.project_name,
            )
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
    """Upload the image’s selected version to the account’s library: create an ingestion job, commit the transaction, and immediately return job state."""
    check_resource_type(resource_type)
    target = await link_service.resolve_upload_target(project_path, resource_type, resource_id)
    link = await link_service.request_upload(session, project_name=project_name, target=target, account_id=account_id)
    await session.commit()
    wake_asset_ingest()
    return link_response(link)


async def release(session: AsyncSession, *, project_name: str, link_id: int) -> LinkResponse:
    """Unlink only the current relationship without deleting the ArcReel image or provider asset. Return already unlinked relationships unchanged."""
    link = await link_service.get_link(session, project_name=project_name, link_id=link_id)
    await link_service.release_link(session, link)
    await session.commit()
    return link_response(link)


async def recheck(session: AsyncSession, *, project_name: str, link_id: int) -> LinkResponse:
    """Ask the background worker to recheck state immediately; return available or failed links unchanged."""
    link = await link_service.get_link(session, project_name=project_name, link_id=link_id)
    await link_service.request_recheck(session, link)
    await session.commit()
    wake_asset_ingest()
    return link_response(link)


async def browse_provider_assets(
    session: AsyncSession, *, account_id: int, page: int, page_size: int
) -> ProviderAssetListResponse:
    """Page through provider-library assets within the account’s site, region and project scope, excluding signed download URLs."""
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
