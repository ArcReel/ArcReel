"""安装记录的响应投影，供市场与端点 API 共用。"""

from datetime import UTC
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from lib.custom_provider import make_endpoint_key
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.market_installation import MarketInstallation
from lib.db.models.market_source import MarketSource


class EntryInstallationResponse(BaseModel):
    endpoint_id: int
    endpoint_key: str
    endpoint_display_name: str
    installed_version: str
    state: Literal["current"] = "current"
    modified: bool = False


class EndpointInstallationResponse(BaseModel):
    source_key: str
    source_id: int | None
    source_display_name: str | None
    slug: str
    installed_version: str
    installed_at: str
    state: Literal["current"] = "current"
    modified: bool = False


async def entry_installations(
    session: AsyncSession, where: ColumnElement[bool] | None = None
) -> dict[tuple[str, str], EntryInstallationResponse]:
    """按 ``(source_key, slug)`` 索引；``where`` 缺省时取全部记录。"""
    rows = await session.execute(
        select(MarketInstallation, CustomEndpoint).join(CustomEndpoint).where(true() if where is None else where)
    )
    return {
        (record.source_key, record.slug): EntryInstallationResponse(
            endpoint_id=endpoint.id,
            endpoint_key=make_endpoint_key(endpoint.id),
            endpoint_display_name=endpoint.display_name,
            installed_version=record.installed_version,
        )
        for record, endpoint in rows
    }


async def entry_installation(session: AsyncSession, source_key: str, slug: str) -> EntryInstallationResponse | None:
    installations = await entry_installations(
        session, (MarketInstallation.source_key == source_key) & (MarketInstallation.slug == slug)
    )
    return installations.get((source_key, slug))


async def endpoint_installations(
    session: AsyncSession, where: ColumnElement[bool] | None = None
) -> dict[int, EndpointInstallationResponse]:
    """按端点 id 索引；``where`` 缺省时取全部记录。源被删除时来源字段为空。"""
    rows = await session.execute(
        select(MarketInstallation, MarketSource)
        .outerjoin(MarketSource, MarketSource.canonical_key == MarketInstallation.source_key)
        .where(true() if where is None else where)
    )
    return {
        record.custom_endpoint_id: EndpointInstallationResponse(
            source_key=record.source_key,
            source_id=source.id if source else None,
            source_display_name=source.display_name if source else None,
            slug=record.slug,
            installed_version=record.installed_version,
            # SQLite 读回的时间不带时区，库内一律按 UTC 存。
            installed_at=(
                record.installed_at if record.installed_at.tzinfo else record.installed_at.replace(tzinfo=UTC)
            ).isoformat(),
        )
        for record, source in rows
    }


async def endpoint_installation(session: AsyncSession, endpoint_id: int) -> EndpointInstallationResponse | None:
    installations = await endpoint_installations(session, MarketInstallation.custom_endpoint_id == endpoint_id)
    return installations.get(endpoint_id)
