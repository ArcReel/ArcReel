"""用户条目表的读写。"""

from __future__ import annotations

from sqlalchemy import delete, select

from arcreel_catalog_core import EntryKey
from lib.db.models.catalog_user_entry import CatalogUserEntry
from lib.db.repositories.base import BaseRepository, rowcount


class CatalogUserEntryRepository(BaseRepository):
    """用户修改的条目副本；调用方负责提交事务。"""

    async def list_all(self) -> list[CatalogUserEntry]:
        result = await self.session.execute(select(CatalogUserEntry).order_by(CatalogUserEntry.id))
        return list(result.scalars())

    async def get(self, key: EntryKey) -> CatalogUserEntry | None:
        stmt = select(CatalogUserEntry).where(
            CatalogUserEntry.maker == key.maker,
            CatalogUserEntry.name == key.name,
            CatalogUserEntry.media_type == key.media_type,
        )
        return (await self.session.execute(stmt)).scalar_one_or_none()

    async def put(self, key: EntryKey, entry: dict[str, object]) -> None:
        """写入这个条目的副本：已有副本时整条替换。"""
        row = await self.get(key)
        if row is None:
            self.session.add(CatalogUserEntry(maker=key.maker, name=key.name, media_type=key.media_type, entry=entry))
        else:
            row.entry = entry
        await self.session.flush()

    async def delete(self, key: EntryKey) -> bool:
        """删除这个条目的副本，返回是否删掉了一行。"""
        result = await self.session.execute(
            delete(CatalogUserEntry).where(
                CatalogUserEntry.maker == key.maker,
                CatalogUserEntry.name == key.name,
                CatalogUserEntry.media_type == key.media_type,
            )
        )
        await self.session.flush()
        return rowcount(result) > 0
