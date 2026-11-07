"""Installation records atomically save validated definitions/records (unique constraints guard concurrent installs) and judge two installed-endpoint state axes.

The market axis compares only installation-record/index-entry version strings, ignoring local
``meta.version``. Disabled/deleted sources or removed entries mean ``unavailable``. The local-change
axis compares only current-definition and installation digests. Neither axis affects the other.
"""

import hashlib
import json
from collections.abc import Mapping
from enum import StrEnum
from typing import Any, NamedTuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_market_core.market.address import source_identity
from arcreel_market_core.market.index import MarketIndexEntry
from lib.custom_provider.endpoint_resolution import derive_mirror_columns
from lib.db.base import utc_now
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.market_installation import MarketInstallation
from lib.db.models.market_source import MarketSource
from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
from lib.infra.api_errors import ConflictError, NotFoundError

from .entries import snapshot_entries


class InstallationState(StrEnum):
    CURRENT = "current"
    UPDATE_AVAILABLE = "update_available"
    UNAVAILABLE = "unavailable"


class InstallationStatus(NamedTuple):
    state: InstallationState
    modified: bool


def definition_digest(definition: Mapping[str, Any]) -> str:
    """Digest shared by installation records/local-change checks: UTF-8 JSON with sorted keys, compact separators, and unescaped Unicode."""
    return hashlib.sha256(
        json.dumps(definition, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


def available_entries(source: MarketSource | None) -> dict[str, MarketIndexEntry]:
    """Index installed records’ still-available market entries by slug; source must exist, be enabled, and still list the slug."""
    if source is None or not source.is_enabled:
        return {}
    return {entry.slug: entry for entry in snapshot_entries(source)}


def installation_status(
    *, installed_version: str, installed_digest: str, definition: Mapping[str, Any], entry: MarketIndexEntry | None
) -> InstallationStatus:
    """None ``entry`` means unavailable in the market; older index versions also count as updatable."""
    if entry is None:
        state = InstallationState.UNAVAILABLE
    elif entry.version != installed_version:
        state = InstallationState.UPDATE_AVAILABLE
    else:
        state = InstallationState.CURRENT
    return InstallationStatus(state=state, modified=definition_digest(definition) != installed_digest)


def _author_and_name(definition: Mapping[str, Any]) -> tuple[object, object]:
    meta = definition.get("meta")
    return (meta.get("author"), meta.get("name")) if isinstance(meta, Mapping) else (None, None)


async def write_installation(
    session: AsyncSession,
    *,
    source_key: str,
    slug: str,
    definition: dict[str, Any],
    overwrite_endpoint_id: int | None,
) -> CustomEndpoint:
    """Callers validate/commit; any failure rolls back both endpoint and record.

    Overwrite only endpoints holding this entry’s installation record, or unrecorded endpoints
    sharing the definition author/name. Source ownership follows
    :func:`~arcreel_market_core.market.address.source_identity`, matching market-source deduplication.
    """
    repo = CustomEndpointRepository(session)
    mirror = derive_mirror_columns(definition)
    identity = source_identity(source_key)
    existing = next(
        (
            record
            for record in await session.scalars(select(MarketInstallation).where(MarketInstallation.slug == slug))
            if source_identity(record.source_key) == identity
        ),
        None,
    )
    if overwrite_endpoint_id is None:
        if existing is not None:
            raise ConflictError("market_entry_already_installed")
        endpoint = await repo.create(
            definition=definition,
            kind=mirror.kind,
            schema_version=mirror.schema_version,
            media_type=mirror.media_type,
            display_name=mirror.display_name,
        )
    else:
        target = await session.scalar(
            select(CustomEndpoint).where(CustomEndpoint.id == overwrite_endpoint_id).with_for_update()
        )
        if target is None:
            raise NotFoundError("custom_endpoint_not_found")
        if existing is not None and existing.custom_endpoint_id != overwrite_endpoint_id:
            raise ConflictError("market_entry_already_installed")
        target_record = await session.get(MarketInstallation, overwrite_endpoint_id)
        if target_record is not None and target_record is not existing:
            raise ConflictError("market_endpoint_already_installed")
        if target_record is None and _author_and_name(target.definition) != _author_and_name(definition):
            raise ConflictError("market_overwrite_target_not_duplicate")
        endpoint = await repo.update(
            overwrite_endpoint_id,
            definition=definition,
            kind=mirror.kind,
            schema_version=mirror.schema_version,
            media_type=mirror.media_type,
            display_name=mirror.display_name,
        )
        if endpoint is None:
            raise NotFoundError("custom_endpoint_not_found")
    record = existing or MarketInstallation(custom_endpoint_id=endpoint.id, slug=slug)
    # If a deleted source is readded under differently cased addresses, move records to its current canonical key.
    record.source_key = source_key
    record.installed_version = definition["meta"]["version"]
    record.installed_digest = definition_digest(definition)
    record.installed_at = utc_now()
    session.add(record)
    await session.flush()
    return endpoint
