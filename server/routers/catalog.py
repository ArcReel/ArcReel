"""模型目录 API：按名称与媒体类型搜索模型条目。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel

from arcreel_catalog_core import MediaType
from lib.catalog import query as catalog

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
