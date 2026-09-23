"""市场源仓储：登记、排序与刷新结果的持久化。"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select

from lib.db.models.market_source import MarketSource
from lib.db.repositories.base import BaseRepository

OFFICIAL_KIND = "official"
CUSTOM_KIND = "custom"


class MarketSourceRepository(BaseRepository):
    """``market_source`` 表的读写；只 flush，提交由调用方负责。"""

    async def list_ordered(self) -> list[MarketSource]:
        stmt = select(MarketSource).order_by(MarketSource.position, MarketSource.id)
        result = await self.session.execute(stmt)
        return list(result.scalars())

    async def get(self, source_id: int) -> MarketSource | None:
        return await self.session.get(MarketSource, source_id)

    async def get_official(self) -> MarketSource | None:
        stmt = select(MarketSource).where(MarketSource.kind == OFFICIAL_KIND).order_by(MarketSource.id).limit(1)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def add(self, source: MarketSource) -> MarketSource:
        self.session.add(source)
        await self.session.flush()
        return source

    async def next_position(self) -> int:
        result = await self.session.execute(select(func.max(MarketSource.position)))
        current = result.scalar_one_or_none()
        return 0 if current is None else current + 1

    async def first_position(self) -> int:
        """排在最前所需的位置：比当前最小位置小 1，空表为 0。"""
        result = await self.session.execute(select(func.min(MarketSource.position)))
        current = result.scalar_one_or_none()
        return 0 if current is None else current - 1

    async def apply_order(self, ordered_ids: Sequence[int]) -> None:
        """按 ``ordered_ids`` 的顺序重写位置；调用方保证它是现有 id 的全排列。"""
        positions = {source_id: position for position, source_id in enumerate(ordered_ids)}
        for source in await self.list_ordered():
            source.position = positions[source.id]
        await self.session.flush()

    async def delete(self, source_id: int) -> None:
        await self.session.execute(delete(MarketSource).where(MarketSource.id == source_id))
        await self.session.flush()
