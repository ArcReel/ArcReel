"""市场条目的浏览：合并启用源的条目，按需抓取定义原文与 icon。

浏览只读各源缓存的索引快照，不发请求；定义与 icon 在用到时才经抓取边界取回。条目的 ``path`` /
``icon`` 相对 ``index_url`` 所在目录解析，抓取同样走代理前缀。icon 回传前按扩展名与 64 KB 二次
把关，合规结果进程内 LRU 缓存。跨源同 slug 的条目并列，不归并。
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

from lib.config.repository import SystemSettingRepository
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import MarketSourceRepository
from lib.httpx_shared import get_http_client

from .fetch import (
    INDEX_MAX_BYTES,
    MarketPayloadNotJsonError,
    MarketPayloadTooLargeError,
    MarketTransportError,
    decode_json_payload,
    fetch_bytes,
)
from .icon import ICON_FORMATS, ICON_MAX_BYTES, inspect_icon
from .index import ENDPOINT_ENTRY_TYPE, MarketIndexEntry, MarketIndexError, parse_index
from .sources import PROXY_PREFIX_SETTING

logger = logging.getLogger(__name__)

#: 进程内缓存的 icon 份数；单份上限 64 KB，总量有界。
ICON_CACHE_SIZE = 256


@dataclass(frozen=True)
class SourcedEntry:
    """一个条目连同它所在的市场源。"""

    source: MarketSource
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
    """市场源、条目或条目的 icon 不存在。"""

    def __init__(self, target: MissingTarget) -> None:
        super().__init__(f"market {target.value} not found")
        self.target = target


class MarketSourceDisabledError(Exception):
    """市场源已禁用：不代它发请求。"""


class MarketAssetFetchError(Exception):
    """条目资产没有取回：网络失败、超时、非成功状态码或体积超限。消息即原因。"""


class MarketAssetInvalidError(Exception):
    """取回的条目资产不可用：定义不是 JSON，或 icon 未过格式把关。消息即原因。"""


def snapshot_entries(source: MarketSource) -> tuple[MarketIndexEntry, ...]:
    """市场源缓存快照里本客户端认识的条目；从未抓取或快照已读不懂时为空。

    条目在源内以 slug 寻址，客户端刷新不校验 slug 唯一（规则 ② 由市场源 CI 负责）；同 slug 只保留
    第一条，列表与按 slug 取详情、定义、icon 始终指向同一条。
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
    """合并启用源的条目：保持传入的源顺序，源内按条目名称排序。"""
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
    """把条目内的相对路径解析为 ``index_url`` 所在目录下的地址；路径中的保留字符逐段转义。"""
    directory = index_url[: index_url.rfind("/") + 1]
    return directory + quote(relative_path, safe="/")


class MarketEntryService:
    """按需抓取条目的定义原文与 icon。生产环境经 :func:`get_market_entry_service` 共享一个实例（icon 缓存随之共享）。"""

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

    async def fetch_definition(self, source_id: int, slug: str) -> Any:
        """抓取条目定义并按 JSON 解析后原样返回，不做定义校验。

        Raises:
            MarketEntryNotFoundError: 源或条目不存在。
            MarketSourceDisabledError: 源已禁用。
            MarketAssetFetchError: 没有取回。
            MarketAssetInvalidError: 取回的内容不是 JSON。
        """
        index_url, entry, proxy_prefix = await self._locate(source_id, slug)
        content = await self._fetch(entry_asset_url(index_url, entry.path), INDEX_MAX_BYTES, proxy_prefix)
        try:
            return decode_json_payload(content)
        except MarketPayloadNotJsonError as exc:
            raise MarketAssetInvalidError(f"definition {exc}") from exc

    async def fetch_icon(self, source_id: int, slug: str) -> MarketIcon:
        """取条目 icon：先查进程内缓存，未命中再抓取并二次把关扩展名、体积与格式。

        缓存键含条目版本，条目升版即重新抓取。

        Raises:
            MarketEntryNotFoundError: 源、条目或 icon 不存在。
            MarketSourceDisabledError: 源已禁用。
            MarketAssetFetchError: 没有取回（含超过 64 KB）。
            MarketAssetInvalidError: 未过格式把关。
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
    """进程内共享的服务实例：icon 缓存依赖同一个实例。"""
    global _service
    if _service is None:
        from lib.db import async_session_factory

        _service = MarketEntryService(async_session_factory)
    return _service
