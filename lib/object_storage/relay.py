"""Relay copies for provider image retrieval: write user object storage, issue expiring download URLs, and clean up after retention.

Manage relay copies separately from formal material objects: keys stay under :data:`RELAY_PREFIX`;
cleanup follows only ``object_storage_relay_object`` records, without scans or prefix deletion,
so it never affects formal objects.

Retention follows ingest tasks (``docs/adr/0111``). Providers promise no ingest duration, and
CreateAsset may return an asset ID before retrieving the image; therefore:

- After a task resolves (available or failed), retain copies for at least :data:`RELAY_MIN_RETENTION` before deletion;
- Unresolved tasks (processing, uncertain outcome, or unlinked but still processing) retain copies until URL expiry plus
  :data:`RELAY_EXPIRED_GRACE`, after which providers can no longer retrieve through that URL;
- Copies never issued URLs (write failure or process exit mid-write) are cleaned after :data:`RELAY_UNSIGNED_GRACE`.

Deletion failures retry with exponential backoff and recorded reasons. If connections change buckets
or are deleted, defer deletion until the connection returns.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.db.base import utc_now
from lib.db.models.asset_library import ProviderAssetLink
from lib.db.models.object_storage import ObjectStorageConnection, RelayObject
from lib.db.repositories.object_storage_repo import ObjectStorageConnectionRepository
from lib.object_storage.client import ObjectStorageClient, ObjectStorageError
from lib.object_storage.connection import config_from_connection

logger = logging.getLogger(__name__)

#: Relay-copy object-key prefix, separate from formal material objects.
RELAY_PREFIX = "arcreel-relay/"
#: Provider download URL lifetime. Provider docs give no retrieval deadline; one day covers queueing. TOS presigning allows at most 7 days.
RELAY_URL_TTL = timedelta(hours=24)
#: Minimum copy retention after ingest resolves, measured from copy creation.
RELAY_MIN_RETENTION = timedelta(hours=1)
#: Additional retention after download URL expiry.
RELAY_EXPIRED_GRACE = timedelta(hours=1)
#: Retention for copies never issued download URLs.
RELAY_UNSIGNED_GRACE = timedelta(minutes=30)

_RETRY_BASE = timedelta(minutes=5)
_RETRY_CAP = timedelta(hours=6)
_SETTLED_STATES = frozenset({"active", "failed"})


def relay_object_key(suffix: str, *, now: datetime | None = None) -> str:
    """New relay-copy object key: date directories and random filenames, without project/resource names."""
    day = (now or utc_now()).strftime("%Y%m%d")
    return f"{RELAY_PREFIX}{day}/{uuid.uuid4().hex}{suffix.lower()}"


def _retry_delay(attempts: int) -> timedelta:
    # Cap the exponent before multiplying: accumulated retry counts overflow timedelta multiplication before min can clamp it.
    return min(_RETRY_BASE * (2 ** min(max(attempts - 1, 0), 16)), _RETRY_CAP)


def _aware(value: datetime) -> datetime:
    """SQLite reads timestamps without timezone information; supply UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def relay_deletable_at(relay: RelayObject, link: ProviderAssetLink | None) -> datetime:
    """Earliest time a relay copy may be deleted."""
    created = _aware(relay.created_at)
    if relay.url_expires_at is None:
        return created + RELAY_UNSIGNED_GRACE
    return signed_content_retained_until(created, _aware(relay.url_expires_at), link)


def signed_content_retained_until(
    signed_at: datetime, url_expires_at: datetime, link: ProviderAssetLink | None
) -> datetime:
    """Earliest retention deadline for content issued to ingest-task ``link`` (relay copy or directly signed formal object).

    Resolved tasks retain it for at least :data:`RELAY_MIN_RETENTION` from ``signed_at``; unresolved
    or missing task records retain it until URL expiry plus :data:`RELAY_EXPIRED_GRACE`.
    """
    expired = url_expires_at + RELAY_EXPIRED_GRACE
    if link is None:
        return expired
    if link.state in _SETTLED_STATES:
        settled = _aware(link.settled_at) if link.settled_at else signed_at
        return min(max(signed_at + RELAY_MIN_RETENTION, settled), expired)
    return expired


async def record_relay_object(
    session_factory: async_sessionmaker[AsyncSession],
    connection: ObjectStorageConnection,
    object_key: str,
    *,
    link_id: int | None,
) -> int:
    """Register relay copies before writing objects so locators remain recoverable if writes fail midway."""
    async with session_factory() as session:
        relay = RelayObject(
            connection_id=connection.id,
            endpoint=connection.endpoint,
            bucket=connection.bucket,
            object_key=object_key,
            link_id=link_id,
            state="retained",
            delete_attempts=0,
        )
        session.add(relay)
        await session.commit()
        return relay.id


async def mark_relay_signed(
    session_factory: async_sessionmaker[AsyncSession], relay_id: int, expires_at: datetime
) -> None:
    async with session_factory() as session:
        relay = await session.get(RelayObject, relay_id)
        if relay is not None:
            relay.url_expires_at = expires_at
            await session.commit()


def _same_connection(relay: RelayObject, connection: ObjectStorageConnection | None) -> bool:
    return (
        connection is not None
        and connection.id == relay.connection_id
        and connection.endpoint == relay.endpoint
        and connection.bucket == relay.bucket
    )


async def count_retained_relay_objects(session: AsyncSession) -> int:
    """Number of relay copies not yet cleaned up."""
    rows = await session.execute(select(RelayObject.id).where(RelayObject.state == "retained"))
    return len(rows.all())


async def sweep_relay_objects(session_factory: async_sessionmaker[AsyncSession], *, now: datetime | None = None) -> int:
    """Delete expired-retention relay copies and return this round’s deletion count; record failures and retry after backoff."""
    now = now or utc_now()
    async with session_factory() as session:
        stmt = select(RelayObject).where(RelayObject.state == "retained").order_by(RelayObject.id)
        candidates = list((await session.execute(stmt)).scalars())
        connection = await ObjectStorageConnectionRepository(session).get()
        due: list[RelayObject] = []
        for relay in candidates:
            if relay.next_attempt_at is not None and _aware(relay.next_attempt_at) > now:
                continue
            link = await session.get(ProviderAssetLink, relay.link_id) if relay.link_id is not None else None
            if relay_deletable_at(relay, link) <= now:
                due.append(relay)
        if not due:
            return 0
        client = ObjectStorageClient(config_from_connection(connection)) if connection is not None else None
        deleted = 0
        for relay in due:
            relay.delete_attempts += 1
            if client is None or not _same_connection(relay, connection):
                relay.last_error = "connection_changed"
                relay.next_attempt_at = now + _RETRY_CAP
                continue
            try:
                await client.delete(relay.object_key)
            except ObjectStorageError as exc:
                if exc.kind == "not_found":
                    relay.state = "deleted"
                    relay.deleted_at = now
                    relay.last_error = None
                    deleted += 1
                    continue
                relay.last_error = f"{exc.kind}:{exc.code}"[:128]
                relay.next_attempt_at = now + _retry_delay(relay.delete_attempts)
                logger.info("中转副本删除失败，稍后重试 relay=%s reason=%s", relay.id, relay.last_error)
                continue
            relay.state = "deleted"
            relay.deleted_at = now
            relay.last_error = None
            deleted += 1
        await session.commit()
        return deleted


__all__ = [
    "RELAY_EXPIRED_GRACE",
    "RELAY_MIN_RETENTION",
    "RELAY_PREFIX",
    "RELAY_UNSIGNED_GRACE",
    "RELAY_URL_TTL",
    "count_retained_relay_objects",
    "mark_relay_signed",
    "record_relay_object",
    "relay_deletable_at",
    "relay_object_key",
    "signed_content_retained_until",
    "sweep_relay_objects",
]
