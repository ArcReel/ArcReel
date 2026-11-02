"""供应商素材库 API：控制面账号、与推理凭证的显式绑定，以及只读的素材浏览。

控制面凭证只经本接口写入，读取返回掩码；控制面请求只发往站点登记的官方主机。浏览只列出供应商素材的
名称、类型与可用状态，不下发供应商的签名下载地址，也不导入任何素材。
"""

from __future__ import annotations

from typing import Annotated, Literal

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
from lib.asset_library.control_plane import (
    AssetLibraryRequestError,
    credentials_of,
    list_assets,
    request_failure_error,
)
from lib.asset_library.sites import DEFAULT_PROJECT_NAME, SITES, AssetLibrarySite, control_plane_host
from lib.config.repository import mask_secret
from lib.db import get_async_session
from lib.db.base import dt_to_iso
from lib.db.models.asset_library import AssetLibraryAccount
from lib.db.repositories.asset_library_repo import AssetLibraryRepository
from lib.infra.api_errors import NotFoundError, UnprocessableError

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
    """省略的字段保持不变；两个密钥字段留空表示保留已保存的值。"""

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


# ── 账号 ──────────────────────────────────────────────────────────


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
    """修改账号。站点、区域、项目或 Access Key ID 变化后，该账号上的绑定都需要重新确认。"""
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
    """删除账号与它上面的全部绑定；不影响供应商素材库里的任何素材。"""
    repo = AssetLibraryRepository(session)
    account = await _get_account_or_404(repo, account_id)
    await repo.delete_account(account)
    await session.commit()
    return Response(status_code=204)


# ── 绑定 ──────────────────────────────────────────────────────────


@router.get("/bindings", response_model=BindingListResponse)
async def list_bindings(session: AsyncSession = Depends(get_async_session)) -> BindingListResponse:
    """全部可绑定的推理凭证及其当前绑定状态。"""
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
    """按推理凭证与账号的当前内容确认绑定；重新确认与改绑都走这里。"""
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


# ── 浏览 ──────────────────────────────────────────────────────────


@router.get("/accounts/{account_id}/assets", response_model=ProviderAssetListResponse)
async def browse_assets(
    account_id: int,
    page: Annotated[int, Query(ge=1, le=200)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    session: AsyncSession = Depends(get_async_session),
) -> ProviderAssetListResponse:
    """按账号的站点、区域与项目范围分页列出供应商素材库里的素材。"""
    account = await _get_account_or_404(AssetLibraryRepository(session), account_id)
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
