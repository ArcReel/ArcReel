"""模型目录 API：按名称与媒体类型搜索模型条目；用户手动填写新条目；把用户改过的条目恢复为官方数据（用户新建的条目
在官方数据收录后改用官方数据，是同一个接口）。"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import CatalogEntry, EntryKey, MediaType
from lib.catalog import query as catalog
from lib.catalog.user_entries import (
    EntryEditRejected,
    create_user_entry,
    discard_user_entry,
    new_entry,
    reload_user_entries,
)
from lib.custom_provider.duration_presets import infer_supported_durations
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


class NewEntryHints(BaseModel):
    """手动填写模型信息时随名字给出的提示：目录里已有的同名模型（有就应直接选择），以及视频模型按名字预设预填的
    可选时长（其他媒体类型为 ``null``）。"""

    existing: CatalogEntryHit | None
    durations: list[int] | None


@router.get("/new-entry-hints", response_model=NewEntryHints)
async def get_new_entry_hints(
    name: Annotated[str, Query(max_length=255, description="准备填写的模型名称")],
    media_type: Annotated[MediaType, Query(description="准备填写的媒体类型")],
) -> NewEntryHints:
    entry = catalog.find_entry(name.strip(), media_type=media_type) if name.strip() else None
    hit = (
        CatalogEntryHit(
            maker=entry.maker,
            name=entry.name,
            media_type=entry.media_type,
            display_name=entry.display_name,
            discontinued=entry.discontinued,
        )
        if entry is not None
        else None
    )
    durations = infer_supported_durations(name.strip()) if media_type == "video" and name.strip() else None
    return NewEntryHints(existing=hit, durations=durations)


class CatalogEntryKey(BaseModel):
    """条目身份：「模型厂商/官方模型名」，两栖模型再以媒体类型区分。"""

    maker: str
    name: str
    media_type: MediaType


class NewEntryBody(CatalogEntryKey):
    """用户手动填写的新条目。``display_name`` 不填用模型名称；``facts`` 缺省的字段取该媒体类型的默认值；
    ``price`` 是目录数据格式的价目表，不填即调用费用记为未定价。"""

    display_name: str | None = None
    facts: dict[str, Any] | None = None
    price: dict[str, Any] | None = None


def build_new_entry(body: NewEntryBody, _t: Translator) -> CatalogEntry:
    """校验并构造用户手动填写的新条目；不合法或目录里已有同名模型时 422。"""
    try:
        return new_entry(
            maker=body.maker,
            name=body.name,
            media_type=body.media_type,
            display_name=body.display_name,
            facts=body.facts,
            price=body.price,
        )
    except EntryEditRejected as exc:
        raise HTTPException(status_code=422, detail=_t(exc.code, **exc.params)) from exc


@router.post("/entries", response_model=CatalogEntryHit, status_code=201)
async def create_catalog_entry(
    body: NewEntryBody,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> CatalogEntryHit:
    """目录中找不到的模型，由用户手动填写新建条目；新建的条目不跟随官方。

    Sheet 里的「手动填写」不走这个接口：新条目随 Sheet 的保存写入（``PATCH /providers/{id}/models/{model}`` 的
    ``new_entry``），取消则不留下条目。这个接口给没有 Sheet 的入口，如添加模型的对话框。

    已有的供应商模型可能从此按名字对应上新条目，能力随之改变，所以与恢复官方数据一样清空 backend 缓存。
    """
    entry = build_new_entry(body, _t)
    await create_user_entry(session, entry)
    await session.commit()
    await reload_user_entries(session)
    await invalidate_provider_caches(request)
    return CatalogEntryHit(
        maker=entry.maker,
        name=entry.name,
        media_type=entry.media_type,
        display_name=entry.display_name,
        discontinued=entry.discontinued,
    )


@router.post("/entries/restore-official", status_code=204)
async def restore_official_entry(
    body: CatalogEntryKey,
    request: Request,
    _t: Translator,
    session: AsyncSession = Depends(get_async_session),
) -> Response:
    """恢复官方数据：丢弃这个条目的用户副本，条目回到跟随官方，所有使用它的供应商一起生效。不能撤销。

    用户改过官方条目时叫「恢复官方数据」；用户新建的条目在官方数据收录了同一身份的条目之后，叫「改用官方数据」，
    是同一个接口。条目没有用户副本时什么都不做；官方数据里还没有这个条目时拒绝。
    """
    try:
        await discard_user_entry(session, EntryKey(body.maker, body.name, body.media_type))
    except EntryEditRejected as exc:
        raise HTTPException(status_code=404 if exc.not_found else 422, detail=_t(exc.code, **exc.params)) from exc
    await session.commit()
    await reload_user_entries(session)
    await invalidate_provider_caches(request)
    return Response(status_code=204)
