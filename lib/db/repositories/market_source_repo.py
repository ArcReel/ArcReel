"""Market-source repository: persist registration, ordering and refresh results."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import delete, func, select

from lib.db.models.market_source import MarketSource
from lib.db.repositories.base import BaseRepository

OFFICIAL_KIND = "official"
CUSTOM_KIND = "custom"


class MarketSourceRepository(BaseRepository):
    """Read/write access to ``market_source``; flush only, with commits owned by callers."""

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
        """Position needed to sort first: current minimum minus 1, or 0 for an empty table."""
        result = await self.session.execute(select(func.min(MarketSource.position)))
        current = result.scalar_one_or_none()
        return 0 if current is None else current - 1

    async def apply_order(self, ordered_ids: Sequence[int]) -> None:
        """Rewrite positions in ``ordered_ids`` order; callers ensure a full permutation of existing IDs."""
        positions = {source_id: position for position, source_id in enumerate(ordered_ids)}
        for source in await self.list_ordered():
            source.position = positions[source.id]
        await self.session.flush()

    async def delete(self, source_id: int) -> None:
        await self.session.execute(delete(MarketSource).where(MarketSource.id == source_id))
        await self.session.flush()
