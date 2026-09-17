"""市场源的登记与刷新。

官方市场源是表里 ``kind = official`` 的一行，服务启动时 seed、不抓取。添加第三方源即抓取一次，
失败不落库。刷新落五态之一：成功（含 304）更新 ``fetched_at`` 并清错误；失败只记 ``status`` 与
``last_error``，保留上次成功的快照。同一源的并发刷新共享一次请求。网络请求期间不持有数据库会话。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from lib.config.repository import SystemSettingRepository
from lib.db.base import utc_now
from lib.db.models.market_source import MarketSource
from lib.db.repositories.market_source_repo import CUSTOM_KIND, OFFICIAL_KIND, MarketSourceRepository
from lib.httpx_shared import get_http_client

from .address import resolve_source_address, same_source_identity
from .fetch import MarketFetchError, SourceStatus, fetch_index

logger = logging.getLogger(__name__)

OFFICIAL_SOURCE_ADDRESS = "ArcReel/arcreel-market"
OFFICIAL_SOURCE_DISPLAY_NAME = "ArcReel Market"
#: 系统设置：GitHub raw 代理前缀，空串即直连。
PROXY_PREFIX_SETTING = "market_github_proxy_prefix"
#: 打开市场页时，距上次成功刷新超过该时长的启用源才自动刷新。
AUTO_REFRESH_AFTER = timedelta(hours=1)


class DuplicateSourceError(Exception):
    """同一规范键的市场源已登记。"""


async def seed_official_source(session: AsyncSession, *, address: str = OFFICIAL_SOURCE_ADDRESS) -> None:
    """确保官方市场源存在并指向当前地址常量，然后提交；不抓取。

    已存在时只同步地址、索引地址与规范键，用户改过的显示名、启用状态与顺序保留。
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
    elif (
        official.address != address
        or official.canonical_key != resolved.canonical_key
        or official.index_url != resolved.index_url
    ):
        official.address = address
        official.index_url = resolved.index_url
        official.canonical_key = resolved.canonical_key
        official.etag = None
    await session.commit()


def is_stale(source: MarketSource, now: datetime) -> bool:
    if source.fetched_at is None:
        return True
    fetched_at = source.fetched_at if source.fetched_at.tzinfo else source.fetched_at.replace(tzinfo=UTC)
    return now - fetched_at > AUTO_REFRESH_AFTER


class MarketSourceService:
    """添加与刷新市场源。刷新去重按实例维持，生产环境经 :func:`get_market_source_service` 共享一个实例。"""

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
        self._inflight: dict[int, asyncio.Task[MarketSource | None]] = {}

    async def add_source(self, address: str, display_name: str | None = None) -> MarketSource:
        """解析地址、抓取并整份判定索引，成功才落库。

        Raises:
            SourceAddressError: 地址形态不被接受。
            DuplicateSourceError: 同规范键已登记。
            MarketFetchError: 抓取失败或索引不可用。
        """
        text = address.strip()
        resolved = resolve_source_address(text)
        async with self._session_factory() as session:
            registered = await MarketSourceRepository(session).list_ordered()
            if any(same_source_identity(source.canonical_key, resolved.canonical_key) for source in registered):
                raise DuplicateSourceError(resolved.canonical_key)
            proxy_prefix = await SystemSettingRepository(session).get(PROXY_PREFIX_SETTING)

        now = self._clock()
        result = await fetch_index(
            self._http_client(), resolved.index_url, proxy_prefix=proxy_prefix, cache_bust=_cache_bust_token(now)
        )
        assert result.index is not None

        async with self._session_factory() as session:
            repo = MarketSourceRepository(session)
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
        """刷新一个启用源，返回刷新后的行；源不存在时返回 None，禁用时原样返回。

        手动刷新不带 ETag 并绕过边缘缓存；自动刷新带 ``If-None-Match``。同一源已有刷新在途时
        直接等待那一次的结果。
        """
        task = self._inflight.get(source_id)
        if task is None:
            task = asyncio.create_task(self._refresh(source_id, manual=manual))
            self._inflight[source_id] = task
            task.add_done_callback(lambda _: self._inflight.pop(source_id, None))
        return await asyncio.shield(task)

    async def refresh_all(self, *, stale_only: bool) -> list[MarketSource]:
        """并行刷新全部启用源，按源顺序返回刷新过的行。

        ``stale_only`` 为自动刷新：只刷距上次成功刷新超过 :data:`AUTO_REFRESH_AFTER` 的源。
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
            )
        except MarketFetchError as exc:
            logger.info("market source %s refresh failed: %s", source_id, exc)
            failure: MarketFetchError | None = exc
            result = None
        else:
            failure = None

        async with self._session_factory() as session:
            source = await MarketSourceRepository(session).get(source_id)
            if source is None:
                return None
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


def _cache_bust_token(now: datetime) -> int:
    return int(now.timestamp() * 1000)


_service: MarketSourceService | None = None


def get_market_source_service() -> MarketSourceService:
    """进程内共享的服务实例：并发刷新去重依赖同一个实例。"""
    global _service
    if _service is None:
        from lib.db import async_session_factory

        _service = MarketSourceService(async_session_factory)
    return _service
