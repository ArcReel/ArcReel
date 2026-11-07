"""Browse market entries: merge enabled-source entries and fetch definitions/icons on demand.

Browsing reads cached index snapshots only, without requests. Fetch definitions/icons only when
used. Resolve entry ``path`` / ``icon`` relative to the ``index_url`` directory, still applying
proxy prefixes. Recheck icons against extensions and 64 KB before returning; cache valid results
in a process-local LRU. Equal slugs across sources remain separate, never merged.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_market_core.market.fetch import (
    INDEX_MAX_BYTES,
    MarketPayloadNotJsonError,
    MarketPayloadTooLargeError,
    MarketTransportError,
    decode_json_payload,
    fetch_bytes,
)
from arcreel_market_core.market.icon import ICON_FORMATS, ICON_MAX_BYTES, inspect_icon
from arcreel_market_core.market.index import ENDPOINT_ENTRY_TYPE, MarketIndexEntry, MarketIndexError, parse_index
from lib.config.repository import SystemSettingRepository
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import MarketSourceRepository
from lib.infra.httpx_shared import get_http_client

from .sources import PROXY_PREFIX_SETTING

logger = logging.getLogger(__name__)

#: Number of process-cached icons, each capped at 64 KB, bounding total size.
ICON_CACHE_SIZE = 256


@dataclass(frozen=True)
class SourcedEntry:
    """One entry together with its market source."""

    source: MarketSource
    entry: MarketIndexEntry


@dataclass(frozen=True)
class FetchedDefinition:
    """Fetched definition text plus the index URL/entry from the snapshot used to select the fetch URL."""

    definition: Any
    index_url: str
    entry: MarketIndexEntry


@dataclass(frozen=True)
class MarketIcon:
    content: bytes
    media_type: str


class MissingTarget(StrEnum):
    SOURCE = "source"
    ENTRY = "entry"
    ICON = "icon"


class MarketEntryNotFoundError(Exception):
    """Market source, entry, or entry icon does not exist."""

    def __init__(self, target: MissingTarget) -> None:
        super().__init__(f"market {target.value} not found")
        self.target = target


class MarketSourceDisabledError(Exception):
    """Market source is disabled; issue no requests on its behalf."""


class MarketAssetFetchError(Exception):
    """Entry asset could not be fetched: network failure, timeout, unsuccessful status, or excessive size. Message gives the reason."""


class MarketAssetInvalidError(Exception):
    """Fetched entry asset is unusable: definition is not JSON or icon fails format checks. Message gives the reason."""


def snapshot_entries(source: MarketSource) -> tuple[MarketIndexEntry, ...]:
    """Entries recognized by this client in a source’s cached snapshot; empty if never fetched or no longer readable.

    Address entries by slug within sources. Refresh does not validate slug uniqueness (rule ②
    belongs to market-source CI); retain only the first duplicate, keeping lists and slug-based
    details/definitions/icons pointed at the same entry.
    """
    if source.cached_index is None:
        return ()
    try:
        entries = parse_index(source.cached_index).entries
    except MarketIndexError:
        logger.warning("market source %s has an unreadable cached index", source.id)
        return ()
    unique: dict[str, MarketIndexEntry] = {}
    for entry in entries:
        unique.setdefault(entry.slug, entry)
    return tuple(unique.values())


def merge_entries(sources: Iterable[MarketSource], *, entry_type: str = ENDPOINT_ENTRY_TYPE) -> list[SourcedEntry]:
    """Merge enabled-source entries, preserving source order and sorting entry names within sources."""
    merged: list[SourcedEntry] = []
    for source in sources:
        if not source.is_enabled:
            continue
        entries = [entry for entry in snapshot_entries(source) if entry.type == entry_type]
        entries.sort(key=lambda entry: (entry.name.casefold(), entry.name, entry.slug))
        merged.extend(SourcedEntry(source=source, entry=entry) for entry in entries)
    return merged


def find_entry(source: MarketSource, slug: str) -> MarketIndexEntry | None:
    return next((entry for entry in snapshot_entries(source) if entry.slug == slug), None)


def entry_asset_url(index_url: str, relative_path: str) -> str:
    """Resolve relative entry paths under the ``index_url`` directory, escaping reserved characters segment by segment."""
    directory = index_url[: index_url.rfind("/") + 1]
    return directory + quote(relative_path, safe="/")


class MarketEntryService:
    """Fetch entry definitions/icons on demand; production shares :func:`get_market_entry_service`, including its icon cache."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        http_client: Callable[[], httpx.AsyncClient] = get_http_client,
        icon_cache_size: int = ICON_CACHE_SIZE,
    ) -> None:
        self._session_factory = session_factory
        self._http_client = http_client
        self._icon_cache_size = icon_cache_size
        self._icons: OrderedDict[tuple[str, str, str], MarketIcon] = OrderedDict()

    async def fetch_definition(self, source_id: int, slug: str) -> FetchedDefinition:
        """Fetch entry definitions, parse JSON, and return unchanged without definition validation.

        When rereading snapshots after fetch, callers use the returned index URL/entry to detect
        source refreshes during the request.

        Raises:
            MarketEntryNotFoundError: Source or entry is absent.
            MarketSourceDisabledError: Source is disabled.
            MarketAssetFetchError: Fetch did not succeed.
            MarketAssetInvalidError: Fetched content is not JSON.
        """
        index_url, entry, proxy_prefix = await self._locate(source_id, slug)
        content = await self._fetch(entry_asset_url(index_url, entry.path), INDEX_MAX_BYTES, proxy_prefix)
        try:
            definition = decode_json_payload(content)
        except MarketPayloadNotJsonError as exc:
            raise MarketAssetInvalidError(f"definition {exc}") from exc
        return FetchedDefinition(definition=definition, index_url=index_url, entry=entry)

    async def fetch_icon(self, source_id: int, slug: str) -> MarketIcon:
        """Read entry icons from process cache; on misses, fetch and recheck extension, size, and format.

        Cache keys include entry versions, forcing refetch after upgrades.

        Raises:
            MarketEntryNotFoundError: Source, entry, or icon is absent.
            MarketSourceDisabledError: Source is disabled.
            MarketAssetFetchError: Fetch failed, including size over 64 KB.
            MarketAssetInvalidError: Format checks failed.
        """
        index_url, entry, proxy_prefix = await self._locate(source_id, slug)
        if entry.icon is None:
            raise MarketEntryNotFoundError(MissingTarget.ICON)
        key = (index_url, entry.icon, entry.version)
        cached = self._icons.get(key)
        if cached is not None:
            self._icons.move_to_end(key)
            return cached

        icon_format = ICON_FORMATS.get(PurePosixPath(entry.icon).suffix.lower())
        if icon_format is None:
            raise MarketAssetInvalidError(f"icon extension is not one of {', '.join(ICON_FORMATS)}")
        _raster_format, media_type = icon_format
        content = await self._fetch(entry_asset_url(index_url, entry.icon), ICON_MAX_BYTES, proxy_prefix)
        issues = inspect_icon(entry.icon, content)
        if issues:
            raise MarketAssetInvalidError(f"icon rejected: {issues[0].code.value}")

        icon = MarketIcon(content=content, media_type=media_type)
        self._icons[key] = icon
        while len(self._icons) > self._icon_cache_size:
            self._icons.popitem(last=False)
        return icon

    async def _locate(self, source_id: int, slug: str) -> tuple[str, MarketIndexEntry, str]:
        async with self._session_factory() as session:
            source = await MarketSourceRepository(session).get(source_id)
            if source is None:
                raise MarketEntryNotFoundError(MissingTarget.SOURCE)
            entry = find_entry(source, slug)
            if entry is None:
                raise MarketEntryNotFoundError(MissingTarget.ENTRY)
            if not source.is_enabled:
                raise MarketSourceDisabledError(source_id)
            proxy_prefix = await SystemSettingRepository(session).get(PROXY_PREFIX_SETTING)
            return source.index_url, entry, proxy_prefix

    async def _fetch(self, url: str, max_bytes: int, proxy_prefix: str) -> bytes:
        try:
            response = await fetch_bytes(self._http_client(), url, max_bytes=max_bytes, proxy_prefix=proxy_prefix)
        except (MarketTransportError, MarketPayloadTooLargeError) as exc:
            raise MarketAssetFetchError(str(exc)) from exc
        if response.status_code == 304:
            raise MarketAssetFetchError("HTTP 304 without If-None-Match")
        return response.content


_service: MarketEntryService | None = None


def get_market_entry_service() -> MarketEntryService:
    """Process-shared service instance, required to share the icon cache."""
    global _service
    if _service is None:
        from lib.db import async_session_factory

        _service = MarketEntryService(async_session_factory)
    return _service
