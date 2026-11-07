"""Installation-record response projections shared by market and endpoint APIs."""

from collections.abc import Iterable
from datetime import UTC
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import ColumnElement, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_market_core.market.address import source_identity
from arcreel_market_core.market.index import MarketIndexEntry
from lib.custom_provider import make_endpoint_key
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.market_installation import MarketInstallation
from lib.db.models.market_source import MarketSource
from lib.market.entries import SourcedEntry
from lib.market.installations import InstallationState, InstallationStatus, available_entries, installation_status


class EntryInstallationResponse(BaseModel):
    endpoint_id: int
    endpoint_key: str
    endpoint_display_name: str
    installed_version: str
    #: The entry is present, so the market axis is only current / update_available.
    state: Literal["current", "update_available"]
    modified: bool


class EndpointInstallationResponse(BaseModel):
    source_key: str
    source_id: int | None
    source_display_name: str | None
    #: Empty when the source has been deleted.
    source_enabled: bool | None
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
    """Index installation records for supplied entries by their source’s ``(canonical_key, slug)``; omit entries without records.

    Records belong to source identity; deleting and readding a source with differently cased GitHub addresses still reconnects them."""
    by_identity = {
        (source_identity(item.source.canonical_key), item.entry.slug): (item.source.canonical_key, item.entry)
        for item in entries
    }
    if not by_identity:
        return {}
    rows = await session.execute(
        select(MarketInstallation, CustomEndpoint)
        .join(CustomEndpoint)
        .where(MarketInstallation.slug.in_({slug for _, slug in by_identity}))
    )
    installations: dict[tuple[str, str], EntryInstallationResponse] = {}
    for record, endpoint in rows:
        matched = by_identity.get((source_identity(record.source_key), record.slug))
        if matched is None:
            continue
        canonical_key, entry = matched
        status = _status(record, endpoint, entry)
        installations[(canonical_key, record.slug)] = EntryInstallationResponse(
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
    """Index by endpoint ID; omit ``where`` to fetch all records. Deleted sources have empty source fields and an unavailable market axis."""
    rows = (
        await session.execute(
            select(MarketInstallation, CustomEndpoint).join(CustomEndpoint).where(true() if where is None else where)
        )
    ).all()
    if not rows:
        return {}
    sources = {source_identity(source.canonical_key): source for source in await session.scalars(select(MarketSource))}
    installations: dict[int, EndpointInstallationResponse] = {}
    snapshots: dict[str, dict[str, MarketIndexEntry]] = {}
    for record, endpoint in rows:
        identity = source_identity(record.source_key)
        source = sources.get(identity)
        if identity not in snapshots:
            snapshots[identity] = available_entries(source)
        status = _status(record, endpoint, snapshots[identity].get(record.slug))
        installations[record.custom_endpoint_id] = EndpointInstallationResponse(
            source_key=record.source_key,
            source_id=source.id if source else None,
            source_display_name=source.display_name if source else None,
            source_enabled=source.is_enabled if source else None,
            slug=record.slug,
            installed_version=record.installed_version,
            # SQLite returns timezone-naive timestamps; all database timestamps are stored as UTC.
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
