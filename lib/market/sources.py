"""Market-source registration/refresh.

The official source is a ``kind = official`` row seeded at startup without fetching. Adding
third-party sources fetches once and persists only success. Deduplicate before/after fetch;
serialize post-fetch deduplication/persistence per instance. Refresh yields one of five states:
success (including 304) updates ``fetched_at`` and clears errors; failure records only ``status`` /
``last_error``, preserving the last successful snapshot. Concurrent same-source refreshes share
one request, but manual refreshes never merge into in-flight automatic refreshes. Hold no DB
session during network requests.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_market_core.market.address import resolve_source_address, source_identity
from arcreel_market_core.market.fetch import MarketFetchError, SourceStatus, fetch_index
from lib.config.repository import SystemSettingRepository
from lib.db.base import utc_now
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import CUSTOM_KIND, OFFICIAL_KIND, MarketSourceRepository
from lib.i18n import _
from lib.infra.httpx_shared import get_http_client

logger = logging.getLogger(__name__)

OFFICIAL_SOURCE_ADDRESS = "ArcReel/arcreel-market"
OFFICIAL_SOURCE_DISPLAY_NAME = "ArcReel Market"
#: System setting: GitHub raw proxy prefix, with empty strings using direct connections.
PROXY_PREFIX_SETTING = "market_github_proxy_prefix"
#: On market-page entry, automatically refresh enabled sources only after this interval since last success.
AUTO_REFRESH_AFTER = timedelta(hours=1)


class DuplicateSourceError(Exception):
    """A market source with the same canonical key is already registered."""


@dataclass(frozen=True)
class _InflightRefresh:
    task: asyncio.Task[MarketSource | None]
    manual: bool


async def seed_official_source(session: AsyncSession, *, address: str = OFFICIAL_SOURCE_ADDRESS) -> None:
    """Ensure official source exists at the current URL constant, then commit without fetching.

    Existing rows synchronize URLs. Changed index URLs/canonical keys also discard old snapshots
    and refresh states, since old relative paths belong to the old URL. Preserve user-edited
    display names, enabled states, and ordering.
    """
    resolved = resolve_source_address(address)
    repo = MarketSourceRepository(session)
    official = await repo.get_official()
    if official is None:
        await repo.add(
            MarketSource(
                kind=OFFICIAL_KIND,
                display_name=OFFICIAL_SOURCE_DISPLAY_NAME,
                address=address,
                index_url=resolved.index_url,
                canonical_key=resolved.canonical_key,
                is_enabled=True,
                position=await repo.first_position(),
                status=SourceStatus.NEVER_FETCHED.value,
            )
        )
    else:
        official.address = address
        if official.canonical_key != resolved.canonical_key or official.index_url != resolved.index_url:
            official.index_url = resolved.index_url
            official.canonical_key = resolved.canonical_key
            official.cached_index = None
            official.fetched_at = None
            official.etag = None
            official.status = SourceStatus.NEVER_FETCHED.value
            official.last_error = None
    await session.commit()


def is_stale(source: MarketSource, now: datetime) -> bool:
    if source.fetched_at is None:
        return True
    fetched_at = source.fetched_at if source.fetched_at.tzinfo else source.fetched_at.replace(tzinfo=UTC)
    return now - fetched_at > AUTO_REFRESH_AFTER


class MarketSourceService:
    """Add/refresh sources; serialization and refresh deduplication are instance-local. Production shares :func:`get_market_source_service`."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        http_client: Callable[[], httpx.AsyncClient] = get_http_client,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session_factory = session_factory
        self._http_client = http_client
        self._clock = clock
        self._inflight: dict[int, _InflightRefresh] = {}
        self._add_lock = asyncio.Lock()

    async def add_source(self, address: str, display_name: str | None = None) -> MarketSource:
        """Parse URL, fetch/judge the entire index, and persist only success.

        Raises:
            SourceAddressError: URL shape is unacceptable.
            DuplicateSourceError: Canonical key already registered.
            MarketFetchError: Fetch failed or index is unusable.
        """
        text = address.strip()
        resolved = resolve_source_address(text)
        async with self._session_factory() as session:
            await _ensure_unregistered(MarketSourceRepository(session), resolved.canonical_key)
            proxy_prefix = await SystemSettingRepository(session).get(PROXY_PREFIX_SETTING)

        now = self._clock()
        result = await fetch_index(
            self._http_client(),
            resolved.index_url,
            proxy_prefix=proxy_prefix,
            cache_bust=_cache_bust_token(now),
            translate=_last_error_translate,
        )
        assert result.index is not None

        async with self._add_lock, self._session_factory() as session:
            repo = MarketSourceRepository(session)
            await _ensure_unregistered(repo, resolved.canonical_key)
            source = MarketSource(
                kind=CUSTOM_KIND,
                display_name=(display_name or "").strip() or result.index.name,
                address=text,
                index_url=resolved.index_url,
                canonical_key=resolved.canonical_key,
                is_enabled=True,
                position=await repo.next_position(),
                cached_index=result.document,
                fetched_at=now,
                etag=result.etag,
                status=SourceStatus.OK.value,
                last_error=None,
            )
            try:
                await repo.add(source)
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise DuplicateSourceError(resolved.canonical_key) from exc
            return source

    async def refresh_source(self, source_id: int, *, manual: bool) -> MarketSource | None:
        """Refresh an enabled source and return its updated row; absent returns None, disabled returns unchanged.

        Manual refreshes omit ETag and bypass edge caches; automatic refreshes send ``If-None-Match``.
        Await existing same-source refreshes, except manual calls following automatic refreshes
        queue another manual refresh afterward.
        """
        inflight = self._inflight.get(source_id)
        if inflight is None or (manual and not inflight.manual):
            previous = inflight.task if inflight is not None else None
            inflight = _InflightRefresh(
                task=asyncio.create_task(self._refresh_after(previous, source_id, manual=manual)), manual=manual
            )
            self._inflight[source_id] = inflight
            inflight.task.add_done_callback(self._forget_inflight(source_id, inflight))
        return await asyncio.shield(inflight.task)

    def _forget_inflight(self, source_id: int, inflight: _InflightRefresh) -> Callable[[asyncio.Task[Any]], None]:
        def forget(_task: asyncio.Task[Any]) -> None:
            if self._inflight.get(source_id) is inflight:
                del self._inflight[source_id]

        return forget

    async def _refresh_after(
        self, previous: asyncio.Task[MarketSource | None] | None, source_id: int, *, manual: bool
    ) -> MarketSource | None:
        if previous is not None:
            await asyncio.wait([previous])
        return await self._refresh(source_id, manual=manual)

    async def refresh_all(self, *, stale_only: bool) -> list[MarketSource]:
        """Refresh enabled sources concurrently, returning refreshed rows in source order.

        ``stale_only`` requests automatic refresh, touching only sources beyond :data:`AUTO_REFRESH_AFTER` since last success.
        """
        async with self._session_factory() as session:
            sources = await MarketSourceRepository(session).list_ordered()
        now = self._clock()
        targets = [s.id for s in sources if s.is_enabled and (not stale_only or is_stale(s, now))]
        results = await asyncio.gather(*(self.refresh_source(sid, manual=not stale_only) for sid in targets))
        return [source for source in results if source is not None]

    async def _refresh(self, source_id: int, *, manual: bool) -> MarketSource | None:
        async with self._session_factory() as session:
            source = await MarketSourceRepository(session).get(source_id)
            if source is None:
                return None
            if not source.is_enabled:
                return source
            index_url = source.index_url
            etag = source.etag if not manual and source.cached_index is not None else None
            proxy_prefix = await SystemSettingRepository(session).get(PROXY_PREFIX_SETTING)

        now = self._clock()
        try:
            result = await fetch_index(
                self._http_client(),
                index_url,
                proxy_prefix=proxy_prefix,
                etag=etag,
                cache_bust=_cache_bust_token(now) if manual else None,
                translate=_last_error_translate,
            )
        except MarketFetchError as exc:
            logger.info("market source %s refresh failed: %s", source_id, exc)
            failure: MarketFetchError | None = exc
            result = None
        else:
            failure = None

        async with self._session_factory() as session:
            source = await MarketSourceRepository(session).get(source_id)
            if source is None or source.index_url != index_url:
                # Source deleted during fetch and its ID reused, or official URL moved: the result no longer belongs to this row.
                return source
            if failure is not None:
                source.status = failure.status.value
                source.last_error = failure.detail
            elif result is not None:
                if not result.not_modified:
                    source.cached_index = result.document
                    source.etag = result.etag
                source.fetched_at = now
                source.status = SourceStatus.OK.value
                source.last_error = None
            await session.commit()
            return source


async def _ensure_unregistered(repo: MarketSourceRepository, canonical_key: str) -> None:
    """Deduplicate via :func:`source_identity`; DB uniqueness matches exact canonical keys only, so block case variants here."""
    registered = await repo.list_ordered()
    identity = source_identity(canonical_key)
    if any(source_identity(source.canonical_key) == identity for source in registered):
        raise DuplicateSourceError(canonical_key)


def _last_error_translate(key: str, **params: Any) -> str:
    """``last_error`` stores technical reasons independent of UI language, always rendered in English."""
    return _(key, locale="en", **params)


def _cache_bust_token(now: datetime) -> int:
    return int(now.timestamp() * 1000)


_service: MarketSourceService | None = None


def get_market_source_service() -> MarketSourceService:
    """Process-shared service instance, required for concurrent-refresh deduplication."""
    global _service
    if _service is None:
        from lib.db import async_session_factory

        _service = MarketSourceService(async_session_factory)
    return _service
