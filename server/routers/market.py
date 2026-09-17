"""市场 API：市场源的登记、排序与刷新。

只服务前端、走现有会话鉴权。添加即抓取一次，抓取失败或索引无效即 422 不落库；官方市场源
可禁用、可排序、可改名，删除返回 409。刷新失败不算请求失败：结果落在源的 ``status`` 与
``last_error`` 上。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from lib.api_errors import BadRequestError, ConflictError, NotFoundError, UnprocessableError
from lib.db import get_async_session
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import OFFICIAL_KIND, MarketSourceRepository
from lib.i18n import Translator
from lib.market import ENDPOINT_ENTRY_TYPE
from lib.market.address import SourceAddressError
from lib.market.fetch import MarketFetchError
from lib.market.sources import DuplicateSourceError, MarketSourceService, get_market_source_service
from server.routers._reorder import full_permutation_error

router = APIRouter(prefix="/market", tags=["Market"])

Service = Annotated[MarketSourceService, Depends(get_market_source_service)]

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
    summary: MarketIndexSummary | None = None
    entry_count = 0
    if isinstance(document, dict):
        summary = MarketIndexSummary(
            name=document.get("name", ""),
            description=document.get("description"),
            homepage=document.get("homepage"),
        )
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
