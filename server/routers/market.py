"""市场 API：市场源的登记、排序与刷新，以及条目的浏览。

只服务前端、走现有会话鉴权。添加即抓取一次，抓取失败或索引无效即 422 不落库；官方市场源
可禁用、可排序、可改名，删除返回 409。刷新失败不算请求失败：结果落在源的 ``status`` 与
``last_error`` 上。条目列表与详情只读缓存快照；定义原文与 icon 按需经抓取边界取回，上游取不回
或内容不可用时返回 502。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from lib.custom_provider.endpoint_definition import meets_min_app_version, validate_definition
from lib.db import get_async_session
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import OFFICIAL_KIND, MarketSourceRepository
from lib.infra.api_errors import BadGatewayError, BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.market import ENDPOINT_ENTRY_TYPE, check_entry_definition
from lib.market.address import SourceAddressError
from lib.market.entries import (
    MarketAssetFetchError,
    MarketAssetInvalidError,
    MarketEntryNotFoundError,
    MarketEntryService,
    MarketSourceDisabledError,
    MissingTarget,
    SourcedEntry,
    find_entry,
    get_market_entry_service,
    merge_entries,
)
from lib.market.fetch import MarketFetchError
from lib.market.index import MarketIndexEntry
from lib.market.installations import definition_digest, write_installation
from lib.market.issues import MarketIssue, MarketIssueCode
from lib.market.sources import DuplicateSourceError, MarketSourceService, get_market_source_service
from server.i18n import Translator
from server.routers._market_installations import (
    EntryInstallationResponse,
    endpoint_installation,
    entry_installation,
    entry_installations,
)
from server.routers._reorder import full_permutation_error
from server.routers.custom_endpoints import CustomEndpointResponse, endpoint_response
from server.routers.system_config import get_app_version_reader

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/market", tags=["Market"])

Service = Annotated[MarketSourceService, Depends(get_market_source_service)]
EntryService = Annotated[MarketEntryService, Depends(get_market_entry_service)]
AppVersionReader = Annotated[Callable[[], str], Depends(get_app_version_reader)]

#: icon 地址由前端带上条目版本作查询参数，版本变了地址就变，故浏览器可缓存较久。
ICON_CACHE_CONTROL = "private, max-age=86400"
#: SVG 可内嵌脚本：直接打开 icon 地址时也不让它执行、不让它加载外部资源。
ICON_CONTENT_SECURITY_POLICY = "default-src 'none'; style-src 'unsafe-inline'; sandbox"

_MISSING_KEYS = {
    MissingTarget.SOURCE: "market_source_not_found",
    MissingTarget.ENTRY: "market_entry_not_found",
    MissingTarget.ICON: "market_entry_icon_not_found",
}

_ORDER_ERROR_KEYS = {
    "length": "market_source_order_length_mismatch",
    "duplicate": "market_source_order_duplicate_ids",
    "mismatch": "market_source_order_ids_mismatch",
}


# ---------------------------------------------------------------------------
# Pydantic 模型
# ---------------------------------------------------------------------------


class MarketIndexSummary(BaseModel):
    name: str
    description: str | None = None
    homepage: str | None = None


class MarketSourceResponse(BaseModel):
    id: int
    kind: str
    display_name: str
    address: str
    index_url: str
    canonical_key: str
    is_enabled: bool
    position: int
    status: str
    last_error: str | None
    fetched_at: str | None
    created_at: str | None
    updated_at: str | None
    #: 快照中本客户端认识的条目数（未知 ``type`` 不计）。
    entry_count: int
    #: 快照索引的顶层信息；从未成功抓取时为 null。
    index: MarketIndexSummary | None


class MarketSourceListResponse(BaseModel):
    sources: list[MarketSourceResponse]


class MarketEntryResponse(BaseModel):
    source_id: int
    source_display_name: str
    type: str
    slug: str
    path: str
    name: str
    author: str
    version: str
    media_type: str
    description: str | None
    homepage: str | None
    #: 索引里的 icon 相对路径；非 null 时经 ``/market/sources/{id}/entries/{slug}/icon`` 取图。
    icon: str | None
    min_app_version: str | None
    #: 当前应用版本满足 ``min_app_version``；无要求或读不到应用版本时为 true。
    min_app_version_satisfied: bool
    installation: EntryInstallationResponse | None = None


class MarketEntryListResponse(BaseModel):
    entries: list[MarketEntryResponse]
    #: 当前应用版本；读不到时为 null。
    app_version: str | None


class MarketEntrySourceSummary(BaseModel):
    id: int
    kind: str
    display_name: str
    canonical_key: str
    is_enabled: bool
    status: str
    fetched_at: str | None
    index: MarketIndexSummary | None


class MarketEntryDetailResponse(BaseModel):
    entry: MarketEntryResponse
    source: MarketEntrySourceSummary
    app_version: str | None


class MarketEntryDefinitionResponse(BaseModel):
    #: 市场源里的定义文件按 JSON 解析后的原文，未经定义校验。
    definition: Any
    entry_matches_definition: bool
    #: 定义是 JSON 对象时的摘要；安装时须原样带回，证明确认的就是这份定义。
    definition_digest: str | None


class AddMarketSourceRequest(BaseModel):
    address: str = Field(max_length=2048)
    display_name: str | None = Field(default=None, max_length=128)


class UpdateMarketSourceRequest(BaseModel):
    display_name: str | None = Field(default=None, max_length=128)
    is_enabled: bool | None = None


class ReorderMarketSourcesRequest(BaseModel):
    ids: list[int]


def _to_response(source: MarketSource) -> MarketSourceResponse:
    document: Any = source.cached_index
    summary = _index_summary(source)
    entry_count = 0
    if isinstance(document, dict):
        entries = document.get("entries")
        if isinstance(entries, list):
            entry_count = sum(1 for e in entries if isinstance(e, dict) and e.get("type") == ENDPOINT_ENTRY_TYPE)
    return MarketSourceResponse(
        id=source.id,
        kind=source.kind,
        display_name=source.display_name,
        address=source.address,
        index_url=source.index_url,
        canonical_key=source.canonical_key,
        is_enabled=source.is_enabled,
        position=source.position,
        status=source.status,
        last_error=source.last_error,
        fetched_at=_iso_utc(source.fetched_at),
        created_at=_iso_utc(source.created_at),
        updated_at=_iso_utc(source.updated_at),
        entry_count=entry_count,
        index=summary,
    )


def _index_summary(source: MarketSource) -> MarketIndexSummary | None:
    document: Any = source.cached_index
    if not isinstance(document, dict):
        return None
    return MarketIndexSummary(
        name=document.get("name", ""), description=document.get("description"), homepage=document.get("homepage")
    )


def _entry_response(
    sourced: SourcedEntry, app_version: str | None, installation: EntryInstallationResponse | None = None
) -> MarketEntryResponse:
    entry: MarketIndexEntry = sourced.entry
    satisfied = (
        entry.min_app_version is None
        or app_version is None
        or meets_min_app_version(entry.min_app_version, app_version)
    )
    return MarketEntryResponse(
        installation=installation,
        source_id=sourced.source.id,
        source_display_name=sourced.source.display_name,
        type=entry.type,
        slug=entry.slug,
        path=entry.path,
        name=entry.name,
        author=entry.author,
        version=entry.version,
        media_type=entry.media_type,
        description=entry.description,
        homepage=entry.homepage,
        icon=entry.icon,
        min_app_version=entry.min_app_version,
        min_app_version_satisfied=satisfied,
    )


def _app_version(read_app_version: Callable[[], str]) -> str | None:
    try:
        return read_app_version()
    except Exception:
        logger.exception("Failed to read app version")
        return None


def _entry_api_error(
    exc: MarketEntryNotFoundError | MarketSourceDisabledError | MarketAssetFetchError | MarketAssetInvalidError,
) -> NotFoundError | ConflictError | BadGatewayError:
    if isinstance(exc, MarketEntryNotFoundError):
        return NotFoundError(_MISSING_KEYS[exc.target])
    if isinstance(exc, MarketSourceDisabledError):
        return ConflictError("market_source_disabled")
    key = "market_entry_fetch_failed" if isinstance(exc, MarketAssetFetchError) else "market_entry_asset_invalid"
    return BadGatewayError(key, reason=str(exc)).with_diagnostic({"reason": str(exc)})


def _iso_utc(value: datetime | None) -> str | None:
    """SQLite 读回的时间不带时区，库内一律按 UTC 存，序列化时补上。"""
    if value is None:
        return None
    return (value if value.tzinfo is not None else value.replace(tzinfo=UTC)).isoformat()


async def _require_source(repo: MarketSourceRepository, source_id: int) -> MarketSource:
    source = await repo.get(source_id)
    if source is None:
        raise NotFoundError("market_source_not_found")
    return source


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@router.get("/sources", response_model=MarketSourceListResponse)
async def list_sources(session: AsyncSession = Depends(get_async_session)) -> MarketSourceListResponse:
    sources = await MarketSourceRepository(session).list_ordered()
    return MarketSourceListResponse(sources=[_to_response(s) for s in sources])


@router.post("/sources", status_code=201, response_model=MarketSourceResponse)
async def add_source(body: AddMarketSourceRequest, service: Service, _t: Translator) -> MarketSourceResponse:
    try:
        source = await service.add_source(body.address, body.display_name)
    except SourceAddressError as exc:
        raise UnprocessableError(f"market_source_address_{exc.code.value}") from exc
    except DuplicateSourceError as exc:
        raise ConflictError("market_source_duplicate") from exc
    except MarketFetchError as exc:
        raise UnprocessableError(
            "market_source_fetch_failed", status=_t(f"market_source_status_{exc.status.value}"), reason=exc.detail
        ).with_diagnostic({"status": exc.status.value, "reason": exc.detail}) from exc
    return _to_response(source)


@router.put("/sources/order", response_model=MarketSourceListResponse)
async def reorder_sources(
    body: ReorderMarketSourcesRequest, session: AsyncSession = Depends(get_async_session)
) -> MarketSourceListResponse:
    repo = MarketSourceRepository(session)
    existing_ids = [source.id for source in await repo.list_ordered()]
    error_kind = full_permutation_error(existing_ids, body.ids)
    if error_kind is not None:
        raise BadRequestError(_ORDER_ERROR_KEYS[error_kind])
    await repo.apply_order(body.ids)
    await session.commit()
    return MarketSourceListResponse(sources=[_to_response(s) for s in await repo.list_ordered()])


@router.patch("/sources/{source_id}", response_model=MarketSourceResponse)
async def update_source(
    source_id: int, body: UpdateMarketSourceRequest, session: AsyncSession = Depends(get_async_session)
) -> MarketSourceResponse:
    repo = MarketSourceRepository(session)
    source = await _require_source(repo, source_id)
    if body.display_name is not None:
        display_name = body.display_name.strip()
        if not display_name:
            raise UnprocessableError("market_source_display_name_required")
        source.display_name = display_name
    if body.is_enabled is not None:
        source.is_enabled = body.is_enabled
    await session.commit()
    await session.refresh(source)
    return _to_response(source)


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int, session: AsyncSession = Depends(get_async_session)) -> Response:
    repo = MarketSourceRepository(session)
    source = await _require_source(repo, source_id)
    if source.kind == OFFICIAL_KIND:
        raise ConflictError("market_source_official_undeletable")
    await repo.delete(source_id)
    await session.commit()
    return Response(status_code=204)


@router.post("/sources/{source_id}/refresh", response_model=MarketSourceResponse)
async def refresh_source(source_id: int, service: Service) -> MarketSourceResponse:
    source = await service.refresh_source(source_id, manual=True)
    if source is None:
        raise NotFoundError("market_source_not_found")
    return _to_response(source)


@router.post("/refresh", response_model=MarketSourceListResponse)
async def refresh_sources(
    service: Service,
    stale_only: Annotated[
        bool, Query(description="只刷新距上次成功刷新超过 1 小时的启用源（打开市场页时的自动刷新）")
    ] = False,
) -> MarketSourceListResponse:
    """刷新全部启用源，逐源返回刷新后的行；禁用源不刷新、不出现在结果里。"""
    sources = await service.refresh_all(stale_only=stale_only)
    return MarketSourceListResponse(sources=[_to_response(s) for s in sources])


@router.get("/entries", response_model=MarketEntryListResponse)
async def list_entries(
    read_app_version: AppVersionReader,
    session: AsyncSession = Depends(get_async_session),
    entry_type: Annotated[str, Query(alias="type", description="条目类型；首期只有 endpoint")] = ENDPOINT_ENTRY_TYPE,
) -> MarketEntryListResponse:
    """所有启用源缓存快照里的条目：按源顺序、源内按名称排列，不分页、不发请求。"""
    sources = await MarketSourceRepository(session).list_ordered()
    app_version = _app_version(read_app_version)
    merged = merge_entries(sources, entry_type=entry_type)
    installations = await entry_installations(session, merged)
    return MarketEntryListResponse(
        entries=[
            _entry_response(item, app_version, installations.get((item.source.canonical_key, item.entry.slug)))
            for item in merged
        ],
        app_version=app_version,
    )


@router.get("/sources/{source_id}/entries/{slug}", response_model=MarketEntryDetailResponse)
async def get_entry(
    source_id: int,
    slug: str,
    read_app_version: AppVersionReader,
    session: AsyncSession = Depends(get_async_session),
) -> MarketEntryDetailResponse:
    """索引条目与源摘要，只读缓存快照、不抓定义；禁用源的条目同样可查。"""
    source = await _require_source(MarketSourceRepository(session), source_id)
    entry = find_entry(source, slug)
    if entry is None:
        raise NotFoundError("market_entry_not_found")
    app_version = _app_version(read_app_version)
    sourced = SourcedEntry(source=source, entry=entry)
    return MarketEntryDetailResponse(
        entry=_entry_response(sourced, app_version, await entry_installation(session, sourced)),
        source=MarketEntrySourceSummary(
            id=source.id,
            kind=source.kind,
            display_name=source.display_name,
            canonical_key=source.canonical_key,
            is_enabled=source.is_enabled,
            status=source.status,
            fetched_at=_iso_utc(source.fetched_at),
            index=_index_summary(source),
        ),
        app_version=app_version,
    )


@dataclass(frozen=True)
class _CheckedDefinition:
    source: MarketSource
    entry: MarketIndexEntry
    definition: Any
    issues: list[MarketIssue]

    @property
    def matches_entry(self) -> bool:
        return not any(issue.code == MarketIssueCode.PROJECTION_MISMATCH for issue in self.issues)

    @property
    def digest(self) -> str | None:
        return definition_digest(self.definition) if isinstance(self.definition, dict) else None


async def _checked_definition(
    service: MarketEntryService, session: AsyncSession, source_id: int, slug: str
) -> _CheckedDefinition:
    """抓定义后再读当前快照做规则 ④⑤；抓取期间源已刷新、条目或索引地址变了即 409，不拿旧地址的内容对新条目校验。"""
    try:
        fetched = await service.fetch_definition(source_id, slug)
    except (MarketEntryNotFoundError, MarketSourceDisabledError, MarketAssetFetchError, MarketAssetInvalidError) as exc:
        raise _entry_api_error(exc) from exc
    source = await _require_source(MarketSourceRepository(session), source_id)
    entry = find_entry(source, slug)
    if entry is None:
        raise NotFoundError("market_entry_not_found")
    if entry != fetched.entry or source.index_url != fetched.index_url:
        raise ConflictError("market_entry_changed_during_fetch")
    return _CheckedDefinition(
        source=source,
        entry=entry,
        definition=fetched.definition,
        issues=check_entry_definition(entry, fetched.definition, definition_file=entry.path),
    )


@router.get("/sources/{source_id}/entries/{slug}/definition", response_model=MarketEntryDefinitionResponse)
async def get_entry_definition(
    source_id: int, slug: str, service: EntryService, session: AsyncSession = Depends(get_async_session)
) -> MarketEntryDefinitionResponse:
    checked = await _checked_definition(service, session, source_id, slug)
    return MarketEntryDefinitionResponse(
        definition=checked.definition,
        entry_matches_definition=checked.matches_entry,
        definition_digest=checked.digest,
    )


@router.get("/sources/{source_id}/entries/{slug}/icon", response_class=Response)
async def get_entry_icon(source_id: int, slug: str, service: EntryService) -> Response:
    try:
        icon = await service.fetch_icon(source_id, slug)
    except (MarketEntryNotFoundError, MarketSourceDisabledError, MarketAssetFetchError, MarketAssetInvalidError) as exc:
        raise _entry_api_error(exc) from exc
    return Response(
        content=icon.content,
        media_type=icon.media_type,
        headers={
            "Cache-Control": ICON_CACHE_CONTROL,
            "Content-Security-Policy": ICON_CONTENT_SECURITY_POLICY,
            "X-Content-Type-Options": "nosniff",
        },
    )


class InstallMarketEntryRequest(BaseModel):
    #: 确认页展示的定义摘要。安装时重新抓取，内容与用户核对过的不同即拒装。
    definition_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    overwrite_endpoint_id: int | None = Field(default=None, gt=0)


class InstallMarketEntryResponse(BaseModel):
    endpoint: CustomEndpointResponse
    installation: EntryInstallationResponse


@router.post("/sources/{source_id}/entries/{slug}/install", response_model=InstallMarketEntryResponse)
async def install_entry(
    source_id: int,
    slug: str,
    body: InstallMarketEntryRequest,
    service: EntryService,
    read_app_version: AppVersionReader,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> InstallMarketEntryResponse:
    checked = await _checked_definition(service, session, source_id, slug)
    source, entry, definition = checked.source, checked.entry, checked.definition
    if not source.is_enabled:
        raise ConflictError("market_source_disabled")
    if checked.digest is not None and checked.digest != body.definition_digest:
        raise ConflictError("market_entry_definition_changed")
    if not checked.matches_entry:
        raise UnprocessableError("market_entry_definition_mismatch")
    if entry.min_app_version is not None and not meets_min_app_version(
        entry.min_app_version, _app_version(read_app_version) or ""
    ):
        raise UnprocessableError("market_entry_requires_newer_app", version=entry.min_app_version)
    if checked.issues or not isinstance(definition, dict):
        raise UnprocessableError("custom_endpoint_definition_invalid").with_diagnostic(
            validate_definition(definition).to_payload(_t)
        )
    try:
        endpoint = await write_installation(
            session,
            source_key=source.canonical_key,
            slug=slug,
            definition=definition,
            overwrite_endpoint_id=body.overwrite_endpoint_id,
        )
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("market_entry_already_installed") from exc
    from server.services.tasks.generation_context import invalidate_backend_cache

    invalidate_backend_cache()
    await session.refresh(endpoint)
    installation = await entry_installation(session, SourcedEntry(source=source, entry=entry))
    if installation is None:
        raise NotFoundError("custom_endpoint_not_found")
    return InstallMarketEntryResponse(
        endpoint=endpoint_response(endpoint, await endpoint_installation(session, endpoint.id)),
        installation=installation,
    )
