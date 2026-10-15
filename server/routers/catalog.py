"""模型目录 API：按名称与媒体类型搜索模型条目；把用户改过的条目恢复为官方数据。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import EntryKey, MediaType
from lib.catalog import query as catalog
from lib.catalog.user_entries import EntryEditRejected, discard_user_entry, reload_user_entries
from lib.db import get_async_session
from server.i18n import Translator
from server.routers.custom_providers import invalidate_provider_caches

router = APIRouter(prefix="/catalog", tags=["Catalog"])


class CatalogEntryHit(BaseModel):
    """一个命中的模型条目。``maker``、``name`` 与 ``media_type`` 合起来标识条目，界面只展示显示名。"""

    maker: str
    name: str
    media_type: MediaType
    display_name: str
    discontinued: bool


class CatalogSearchResponse(BaseModel):
    entries: list[CatalogEntryHit]


@router.get("/entries", response_model=CatalogSearchResponse)
async def search_catalog_entries(
    q: Annotated[str, Query(max_length=200, description="按名称搜索；留空列出全部")] = "",
    media_type: Annotated[MediaType | None, Query(description="只列这个媒体类型的条目")] = None,
) -> CatalogSearchResponse:
    hits = catalog.search_entries(q, media_type)
    return CatalogSearchResponse(entries=[CatalogEntryHit.model_validate(vars(hit)) for hit in hits])


class CatalogEntryKey(BaseModel):
    """条目身份：「模型厂商/官方模型名」，两栖模型再以媒体类型区分。"""

    maker: str
    name: str
    media_type: MediaType


@router.post("/entries/restore-official", status_code=204)
async def restore_official_entry(
    body: CatalogEntryKey,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """恢复官方数据：丢弃这个条目的用户副本，条目回到跟随官方，所有使用它的供应商一起生效。不能撤销。

    条目没有用户副本时什么都不做；官方数据里没有这个条目（用户新建的）时拒绝。
    """
    try:
        await discard_user_entry(session, EntryKey(body.maker, body.name, body.media_type))
    except EntryEditRejected as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=_t(exc.code, **exc.params)) from exc
    await session.commit()
    await reload_user_entries(session)
    await invalidate_provider_caches(request)
    return Response(status_code=204)
