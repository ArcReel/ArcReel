"""Official synchronization: retrieve updated catalog datasets and apply whole batches to queries (docs/adr/0103).

Check at startup and daily thereafter. Fetch raw URLs only, reusing market GitHub raw proxy prefixes/tools with https-only,
size/time limits, and no instance identifiers. Fetch data files only for newer manifest dataset versions, verifying hashes and parsing whole files
before storing/loading synchronized tables. Parsing/fetch failures discard this attempt, preserving local catalogs until next checks.

Composition skips official following for user-owned copies, still replacing official entries; retain last local versions of upstream-deleted entries.
Replace built-in provider manifests entirely. When official data lists same-name user-added provider models, queries use official versions, retaining enabled state only.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_catalog_core import (
    CATALOG_FILENAME,
    MANIFEST_FILENAME,
    CatalogData,
    dump_entry,
    load_catalog,
    parse_catalog,
    parse_dataset_version,
    parse_manifest,
)
from arcreel_market_core.market.fetch import MarketPayloadTooLargeError, MarketTransportError, fetch_bytes
from lib.catalog import query as catalog
from lib.catalog.composition import retain_entries
from lib.config.repository import SystemSettingRepository
from lib.db.base import utc_now
from lib.db.models.catalog_official_sync import CatalogOfficialSync
from lib.infra.httpx_shared import get_http_client
from lib.infra.task_cancel import raise_if_cancel_absorbed
from lib.market.sources import PROXY_PREFIX_SETTING

logger = logging.getLogger(__name__)

_REPO_RAW = "https://raw.githubusercontent.com/ArcReel/arcreel-catalog/main"
MANIFEST_URL = f"{_REPO_RAW}/{MANIFEST_FILENAME}"
CATALOG_URL = f"{_REPO_RAW}/{CATALOG_FILENAME}"

SYNC_INTERVAL_SECONDS = 24 * 60 * 60
_MANIFEST_MAX_BYTES = 64 * 1024
#: Generously above actual dataset size for growth; release snapshot fetching shares this maximum.
_CATALOG_MAX_BYTES = 8 * 1024 * 1024
_FETCH_DEADLINE_SECONDS = 60.0
_ERROR_MAX_CHARS = 1000
_ROW_ID = 1


@dataclass(frozen=True)
class SyncStatus:
    """Synchronization status for system diagnostics."""

    last_success_at: datetime | None = None
    last_error: str | None = None


# Process sync state matches official-sync tables; diagnostics read synchronously in threads without DB, via a single-element mailbox list.
_status: list[SyncStatus] = [SyncStatus()]


def _status_of(row: CatalogOfficialSync) -> SyncStatus:
    at = row.last_success_at
    # SQLite timestamps lack timezone; all stored values are UTC.
    return SyncStatus(at.replace(tzinfo=UTC) if at is not None and at.tzinfo is None else at, row.last_error)


def sync_status() -> SyncStatus:
    return _status[0]


def reset_for_tests() -> None:
    _status[0] = SyncStatus()
    catalog.install_synced_data(None)


def _parse_stored(content: bytes) -> CatalogData:
    return parse_catalog(json.loads(content))


async def load_synced_data(session: AsyncSession) -> None:
    """Load persisted sync data/status at startup; unreadable stored data means no synchronized data until next checks."""
    row = await session.get(CatalogOfficialSync, _ROW_ID)
    if row is None:
        return
    _status[0] = _status_of(row)
    if row.catalog is None:
        return
    try:
        data = await asyncio.to_thread(_parse_stored, row.catalog)
    except ValueError:
        logger.warning("已存的官方同步数据无法读入，暂用出厂快照", exc_info=True)
        return
    catalog.install_synced_data(data)


def _with_retained(manifest: bytes, content: bytes, current: CatalogData) -> tuple[CatalogData, bytes]:
    """Read new datasets, verifying hash/version; retain local official entries absent from new datasets, returning loadable data/persisted content."""
    data = load_catalog(manifest, content)
    merged = retain_entries(data, current)
    if merged is data:
        return data, content
    raw = json.loads(content)
    raw["entries"].extend(dump_entry(entry) for entry in merged.entries[len(data.entries) :])
    return merged, json.dumps(raw, ensure_ascii=False).encode()


async def _fetch(client: httpx.AsyncClient, url: str, max_bytes: int, proxy_prefix: str) -> bytes:
    response = await fetch_bytes(
        client, url, max_bytes=max_bytes, proxy_prefix=proxy_prefix, deadline_seconds=_FETCH_DEADLINE_SECONDS
    )
    raise_if_cancel_absorbed()
    if response.status_code == 304:
        raise MarketTransportError("HTTP 304 without If-None-Match")
    return response.content


async def _record(
    session_factory: async_sessionmaker[AsyncSession], *, error: str | None, applied: tuple[str, bytes] | None = None
) -> None:
    async with session_factory() as session:
        row = await session.get(CatalogOfficialSync, _ROW_ID)
        if row is None:
            row = CatalogOfficialSync(id=_ROW_ID)
            session.add(row)
        if applied is not None:
            row.dataset_version, row.catalog = applied
        if error is None:
            row.last_success_at = utc_now()
        row.last_error = error
        await session.commit()
        _status[0] = _status_of(row)


async def sync_once(
    session_factory: async_sessionmaker[AsyncSession],
    http_client: Callable[[], httpx.AsyncClient] = get_http_client,
) -> None:
    """Check once, fetching/applying whole batches only for newer versions. Failures record reasons, preserving local catalogs."""
    async with session_factory() as session:
        proxy_prefix = await SystemSettingRepository(session).get(PROXY_PREFIX_SETTING)
    try:
        manifest = await _fetch(http_client(), MANIFEST_URL, _MANIFEST_MAX_BYTES, proxy_prefix)
        version = parse_manifest(manifest).dataset_version
        current = catalog.official_data()
        if parse_dataset_version(version) <= parse_dataset_version(current.dataset_version):
            await _record(session_factory, error=None)
            return
        content = await _fetch(http_client(), CATALOG_URL, _CATALOG_MAX_BYTES, proxy_prefix)
        data, stored = await asyncio.to_thread(_with_retained, manifest, content, current)
    except (MarketTransportError, MarketPayloadTooLargeError, ValueError) as exc:
        # CatalogFormatError subclasses ValueError: reject whole unparseable datasets.
        logger.info("官方同步检查失败，等下一轮：%s", exc)
        await _record(session_factory, error=str(exc)[:_ERROR_MAX_CHARS] or type(exc).__name__)
        return
    await _record(session_factory, error=None, applied=(data.dataset_version, stored))
    catalog.install_synced_data(data)
    logger.info("官方同步已应用数据集 %s", data.dataset_version)


async def _sync_loop(
    session_factory: async_sessionmaker[AsyncSession], http_client: Callable[[], httpx.AsyncClient]
) -> None:
    while True:
        try:
            await sync_once(session_factory, http_client)
        except Exception as exc:
            logger.warning("官方同步异常，等下一轮", exc_info=True)
            # Update process state only: errors may originate in DB; diagnostics still expose the failed attempt.
            _status[0] = replace(_status[0], last_error=(str(exc) or type(exc).__name__)[:_ERROR_MAX_CHARS])
        # anyio may swallow shutdown cancellation during connection setup and finish the round; apply it here rather than sleeping an entire day with pending cancellation.
        raise_if_cancel_absorbed()
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def start_official_sync(
    session_factory: async_sessionmaker[AsyncSession],
    http_client: Callable[[], httpx.AsyncClient] = get_http_client,
) -> asyncio.Task[None] | None:
    """Check immediately, then daily; ARCREEL_SKIP_CATALOG_SYNC=1 skips network sync for offline recording."""
    if os.getenv("ARCREEL_SKIP_CATALOG_SYNC") == "1":
        logger.info("ARCREEL_SKIP_CATALOG_SYNC=1，跳过官方目录同步")
        return None
    return asyncio.get_running_loop().create_task(
        _sync_loop(session_factory, http_client), name="catalog-official-sync"
    )


async def stop_official_sync(task: asyncio.Task[None] | None) -> None:
    """Cancel loops and wait for complete cleanup, including interrupted DB sessions, before returning."""
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
