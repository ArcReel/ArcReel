"""安装记录的响应投影，供市场与端点 API 共用。"""

from collections.abc import Iterable
from datetime import UTC
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from lib.custom_provider import make_endpoint_key
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.market_installation import MarketInstallation
from lib.db.models.market_source import MarketSource
from lib.market.entries import SourcedEntry
from lib.market.index import MarketIndexEntry
from lib.market.installations import InstallationState, InstallationStatus, available_entries, installation_status


class EntryInstallationResponse(BaseModel):
    endpoint_id: int
    endpoint_key: str
    endpoint_display_name: str
    installed_version: str
    #: 条目就在眼前，市场轴只会是 current / update_available。
    state: Literal["current", "update_available"]
    modified: bool


class EndpointInstallationResponse(BaseModel):
    source_key: str
    source_id: int | None
    source_display_name: str | None
    slug: str
    installed_version: str
    installed_at: str
    state: Literal["current", "update_available", "unavailable"]
    modified: bool


def _status(record: MarketInstallation, endpoint: CustomEndpoint, entry: MarketIndexEntry | None) -> InstallationStatus:
    return installation_status(
        installed_version=record.installed_version,
        installed_digest=record.installed_digest,
        definition=endpoint.definition,
        entry=entry,
    )


async def entry_installations(
    session: AsyncSession, entries: Iterable[SourcedEntry]
) -> dict[tuple[str, str], EntryInstallationResponse]:
    """按 ``(source_key, slug)`` 索引给定条目的安装记录；没有记录的条目不出现。"""
    by_key = {(item.source.canonical_key, item.entry.slug): item.entry for item in entries}
    if not by_key:
        return {}
    rows = await session.execute(
        select(MarketInstallation, CustomEndpoint)
        .join(CustomEndpoint)
        .where(MarketInstallation.source_key.in_({source_key for source_key, _ in by_key}))
    )
    installations: dict[tuple[str, str], EntryInstallationResponse] = {}
    for record, endpoint in rows:
        entry = by_key.get((record.source_key, record.slug))
        if entry is None:
            continue
        status = _status(record, endpoint, entry)
        installations[(record.source_key, record.slug)] = EntryInstallationResponse(
            endpoint_id=endpoint.id,
            endpoint_key=make_endpoint_key(endpoint.id),
            endpoint_display_name=endpoint.display_name,
            installed_version=record.installed_version,
            state="update_available" if status.state is InstallationState.UPDATE_AVAILABLE else "current",
            modified=status.modified,
        )
    return installations


async def entry_installation(session: AsyncSession, sourced: SourcedEntry) -> EntryInstallationResponse | None:
    installations = await entry_installations(session, [sourced])
    return installations.get((sourced.source.canonical_key, sourced.entry.slug))


async def endpoint_installations(
    session: AsyncSession, where: ColumnElement[bool] | None = None
) -> dict[int, EndpointInstallationResponse]:
    """按端点 id 索引；``where`` 缺省时取全部记录。源被删除时来源字段为空、市场轴为 unavailable。"""
    rows = await session.execute(
        select(MarketInstallation, CustomEndpoint, MarketSource)
        .join(CustomEndpoint)
        .outerjoin(MarketSource, MarketSource.canonical_key == MarketInstallation.source_key)
        .where(true() if where is None else where)
    )
    installations: dict[int, EndpointInstallationResponse] = {}
    snapshots: dict[str, dict[str, MarketIndexEntry]] = {}
    for record, endpoint, source in rows:
        if record.source_key not in snapshots:
            snapshots[record.source_key] = available_entries(source)
        status = _status(record, endpoint, snapshots[record.source_key].get(record.slug))
        installations[record.custom_endpoint_id] = EndpointInstallationResponse(
            source_key=record.source_key,
            source_id=source.id if source else None,
            source_display_name=source.display_name if source else None,
            slug=record.slug,
            installed_version=record.installed_version,
            # SQLite 读回的时间不带时区，库内一律按 UTC 存。
            installed_at=(
                record.installed_at if record.installed_at.tzinfo else record.installed_at.replace(tzinfo=UTC)
            ).isoformat(),
            state=status.state.value,
            modified=status.modified,
        )
    return installations


async def endpoint_installation(session: AsyncSession, endpoint_id: int) -> EndpointInstallationResponse | None:
    installations = await endpoint_installations(session, MarketInstallation.custom_endpoint_id == endpoint_id)
    return installations.get(endpoint_id)
