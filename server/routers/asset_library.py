"""Provider asset-library API: control-plane accounts, explicit bindings to inference credentials, and read-only asset browsing.

Only these endpoints write control-plane credentials; reads return masked values. Control-plane requests go only to registered
official site hosts. Browsing lists asset names, types and availability, without signed provider download URLs or importing assets."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import AfterValidator, BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.asset_library.bindings import (
    BindingStatus,
    CredentialType,
    confirm_binding,
    get_bindable_credential,
    list_bindable_credentials,
    list_binding_views,
)
from lib.asset_library.sites import DEFAULT_PROJECT_NAME, SITES, AssetLibrarySite, control_plane_host
from lib.config.repository import mask_secret
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.asset_library import AssetLibraryAccount
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.infra.api_errors import NotFoundError, UnprocessableError
from server.services.project.provider_asset_links import ProviderAssetListResponse, browse_provider_assets

router = APIRouter(prefix="/asset-library", tags=["Asset Library"])


def _strip(value: str | None) -> str | None:
    return value.strip() if isinstance(value, str) else value


_Name = Annotated[str, AfterValidator(_strip), Field(min_length=1, max_length=128)]
_Secret = Annotated[str, AfterValidator(_strip), Field(max_length=2048)]


class SiteInfo(BaseModel):
    id: str
    regions: list[str]


class AccountResponse(BaseModel):
    id: int
    site: str
    name: str
    region: str
    project_name: str
    access_key_id_masked: str
    secret_access_key_masked: str
    created_at: str


class AccountListResponse(BaseModel):
    sites: list[SiteInfo]
    default_project_name: str
    accounts: list[AccountResponse]


class CreateAccountRequest(BaseModel):
    site: AssetLibrarySite
    name: _Name
    region: str | None = None
    project_name: _Name = DEFAULT_PROJECT_NAME
    access_key_id: _Secret
    secret_access_key: _Secret


class UpdateAccountRequest(BaseModel):
    """Omitted fields remain unchanged; leaving either secret field empty preserves its stored value."""

    name: _Name | None = None
    site: AssetLibrarySite | None = None
    region: str | None = None
    project_name: _Name | None = None
    access_key_id: _Secret | None = None
    secret_access_key: _Secret | None = None


class BindingInfo(BaseModel):
    account_id: int
    status: BindingStatus


class BindableCredentialResponse(BaseModel):
    credential_type: CredentialType
    credential_id: int
    provider: str
    name: str
    api_key_masked: str | None
    endpoint: str
    binding: BindingInfo | None


class BindingListResponse(BaseModel):
    credentials: list[BindableCredentialResponse]


class ConfirmBindingRequest(BaseModel):
    account_id: int


def _account_response(account: AssetLibraryAccount) -> AccountResponse:
    return AccountResponse(
        id=account.id,
        site=account.site,
        name=account.name,
        region=account.region,
        project_name=account.project_name,
        access_key_id_masked=mask_secret(account.access_key_id),
        secret_access_key_masked=mask_secret(account.secret_access_key),
        created_at=dt_to_iso(account.created_at) or "",
    )


def _resolve_region(site: str, region: str | None) -> str:
    resolved = region or SITES[site].default_region
    if control_plane_host(site, resolved) is None:
        raise UnprocessableError("asset_library_region_unsupported", region=resolved)
    return resolved


async def _get_account_or_404(repo: AssetLibraryRepository, account_id: int) -> AssetLibraryAccount:
    account = await repo.get_account(account_id)
    if account is None:
        raise NotFoundError("asset_library_account_not_found")
    return account


# ── Accounts ──────────────────────────────────────────────────────────


@router.get("/accounts", response_model=AccountListResponse)
async def list_accounts(session: AsyncSession = Depends(get_async_session)) -> AccountListResponse:
    accounts = await AssetLibraryRepository(session).list_accounts()
    return AccountListResponse(
        sites=[SiteInfo(id=site, regions=spec.regions) for site, spec in SITES.items()],
        default_project_name=DEFAULT_PROJECT_NAME,
        accounts=[_account_response(a) for a in accounts],
    )


@router.post("/accounts", status_code=201, response_model=AccountResponse)
async def create_account(
    body: CreateAccountRequest, session: AsyncSession = Depends(get_async_session)
) -> AccountResponse:
    if not body.access_key_id or not body.secret_access_key:
        raise UnprocessableError("asset_library_keys_required")
    account = await AssetLibraryRepository(session).create_account(
        site=body.site,
        name=body.name,
        region=_resolve_region(body.site, body.region),
        project_name=body.project_name,
        access_key_id=body.access_key_id,
        secret_access_key=body.secret_access_key,
    )
    await session.commit()
    return _account_response(account)


@router.patch("/accounts/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int, body: UpdateAccountRequest, session: AsyncSession = Depends(get_async_session)
) -> AccountResponse:
    """Update an account. Changes to site, region, project or Access Key ID require reconfirming all its bindings."""
    account = await _get_account_or_404(AssetLibraryRepository(session), account_id)
    if body.name is not None:
        account.name = body.name
    if body.site is not None or body.region is not None:
        site = body.site or account.site
        account.region = _resolve_region(site, body.region)
        account.site = site
    if body.project_name is not None:
        account.project_name = body.project_name
    if body.access_key_id:
        account.access_key_id = body.access_key_id
    if body.secret_access_key:
        account.secret_access_key = body.secret_access_key
    await session.commit()
    return _account_response(account)


@router.delete("/accounts/{account_id}", status_code=204)
async def delete_account(account_id: int, session: AsyncSession = Depends(get_async_session)) -> Response:
    """Delete the account and all its bindings, without affecting assets in the provider library."""
    repo = AssetLibraryRepository(session)
    account = await _get_account_or_404(repo, account_id)
    await repo.delete_account(account)
    await session.commit()
    return Response(status_code=204)


# ── Bindings ──────────────────────────────────────────────────────────


@router.get("/bindings", response_model=BindingListResponse)
async def list_bindings(session: AsyncSession = Depends(get_async_session)) -> BindingListResponse:
    """All bindable inference credentials and their current binding states."""
    views = {(v.credential.credential_type, v.credential.credential_id): v for v in await list_binding_views(session)}
    items: list[BindableCredentialResponse] = []
    for credential in await list_bindable_credentials(session):
        view = views.get((credential.credential_type, credential.credential_id))
        items.append(
            BindableCredentialResponse(
                credential_type=credential.credential_type,
                credential_id=credential.credential_id,
                provider=credential.provider,
                name=credential.name,
                api_key_masked=mask_secret(credential.api_key) if credential.api_key else None,
                endpoint=credential.endpoint,
                binding=BindingInfo(account_id=view.account_id, status=view.status) if view else None,
            )
        )
    return BindingListResponse(credentials=items)


@router.put("/bindings/{credential_type}/{credential_id}", response_model=BindingInfo)
async def put_binding(
    credential_type: CredentialType,
    credential_id: int,
    body: ConfirmBindingRequest,
    session: AsyncSession = Depends(get_async_session),
) -> BindingInfo:
    """Confirm a binding using current inference-credential and account contents; reconfirmation and rebinding use the same endpoint."""
    credential = await get_bindable_credential(session, credential_type, credential_id)
    if credential is None:
        raise NotFoundError("asset_library_credential_not_found")
    account = await _get_account_or_404(AssetLibraryRepository(session), body.account_id)
    await confirm_binding(session, account, credential)
    await session.commit()
    return BindingInfo(account_id=account.id, status="confirmed")


@router.delete("/bindings/{credential_type}/{credential_id}", status_code=204)
async def delete_binding(
    credential_type: CredentialType, credential_id: int, session: AsyncSession = Depends(get_async_session)
) -> Response:
    repo = AssetLibraryRepository(session)
    binding = await repo.get_binding_for_credential(credential_type, credential_id)
    if binding is None:
        raise NotFoundError("asset_library_binding_not_found")
    await repo.delete_binding(binding)
    await session.commit()
    return Response(status_code=204)


# ── Browsing ──────────────────────────────────────────────────────────


@router.get("/accounts/{account_id}/assets", response_model=ProviderAssetListResponse)
async def browse_assets(
    account_id: int,
    page: Annotated[int, Query(ge=1, le=200)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderAssetListResponse:
    """Page through provider-library assets within the account’s site, region and project scope."""
    return await browse_provider_assets(session, account_id=account_id, page=page, page_size=page_size)
