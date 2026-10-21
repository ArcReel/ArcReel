"""官方同步：从目录数据仓库取回更新的数据集，整批应用到目录查询（docs/adr/0103）。

应用启动时检查一次，之后每天一次。只经 raw 地址抓取，复用市场的 GitHub raw 代理前缀与抓取工具（只接受 https、
限制体积与耗时），请求不带实例标识。清单文件的数据集版本比本地官方数据新时才取数据文件，核对哈希、整份读入后
存进官方同步表并装入目录查询；整份无法解析或抓取失败时这一次作废，本地目录不变，等下一轮。

停止跟随官方的条目由目录合成跳过（用户副本照旧替换官方条目）；上游删除的条目保留本地最后一个版本；内置供应商的
供应商模型清单整份取新数据。官方数据收录了用户添加的同名模型时，目录查询改用官方版本，只保留启用状态。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
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
from lib.market.sources import PROXY_PREFIX_SETTING

logger = logging.getLogger(__name__)

_REPO_RAW = "https://raw.githubusercontent.com/ArcReel/arcreel-catalog/main"
MANIFEST_URL = f"{_REPO_RAW}/{MANIFEST_FILENAME}"
CATALOG_URL = f"{_REPO_RAW}/{CATALOG_FILENAME}"

SYNC_INTERVAL_SECONDS = 24 * 60 * 60
_MANIFEST_MAX_BYTES = 64 * 1024
#: 数据文件目前约 340 KB，留出十倍以上余量。
_CATALOG_MAX_BYTES = 8 * 1024 * 1024
_FETCH_DEADLINE_SECONDS = 60.0
_ERROR_MAX_CHARS = 1000
_ROW_ID = 1


@dataclass(frozen=True)
class SyncStatus:
    """供系统诊断信息读取的同步状态。"""

    last_success_at: datetime | None = None
    last_error: str | None = None


# 进程内的同步状态，与官方同步表一致；诊断信息在线程里同步读取，不查库。单元素列表当信箱。
_status: list[SyncStatus] = [SyncStatus()]


def _status_of(row: CatalogOfficialSync) -> SyncStatus:
    at = row.last_success_at
    # SQLite 读回的时间不带时区，存的都是 UTC
    return SyncStatus(at.replace(tzinfo=UTC) if at is not None and at.tzinfo is None else at, row.last_error)


def sync_status() -> SyncStatus:
    return _status[0]


def reset_for_tests() -> None:
    _status[0] = SyncStatus()
    catalog.install_synced_data(None)


def _parse_stored(content: bytes) -> CatalogData:
    return parse_catalog(json.loads(content))


async def load_synced_data(session: AsyncSession) -> None:
    """应用启动时装入已存的同步数据与同步状态。存的数据读不进来时按没有同步数据处理，等下一轮同步。"""
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
    """读入新数据集（核对哈希与版本），补上本地官方数据里有、新数据集没有的条目，返回装入用的数据与存库的内容。"""
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
    """检查一次：数据集版本比本地新时取回并整批应用。失败只记下原因，本地目录不变。"""
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
        # CatalogFormatError 是 ValueError 的子类：整份数据无法解析时拒收这一次
        logger.info("官方同步检查失败，等下一轮：%s", exc)
        await _record(session_factory, error=str(exc)[:_ERROR_MAX_CHARS] or type(exc).__name__)
        return
    await _record(session_factory, error=None, applied=(data.dataset_version, stored))
    catalog.install_synced_data(data)
    logger.info("官方同步已应用数据集 %s", data.dataset_version)


async def _sync_loop(session_factory: async_sessionmaker[AsyncSession]) -> None:
    while True:
        try:
            await sync_once(session_factory)
        except Exception:
            logger.warning("官方同步异常，等下一轮", exc_info=True)
        await asyncio.sleep(SYNC_INTERVAL_SECONDS)


def start_official_sync(session_factory: async_sessionmaker[AsyncSession]) -> asyncio.Task[None]:
    """启动后台同步循环：立即检查一次，之后每天一次。"""
    return asyncio.get_running_loop().create_task(_sync_loop(session_factory), name="catalog-official-sync")


async def stop_official_sync(task: asyncio.Task[None]) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
