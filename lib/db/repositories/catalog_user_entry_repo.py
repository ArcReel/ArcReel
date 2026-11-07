"""Read/write access to the user-entry table."""

from __future__ import annotations

from sqlalchemy import delete, select

from arcreel_catalog_core import EntryKey
from lib.db.models.catalog_user_entry import CatalogUserEntry
from lib.db.repositories.base import BaseRepository, rowcount


class CatalogUserEntryRepository(BaseRepository):
    """User-modified entry copies; callers commit transactions."""

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

    async def put(self, key: EntryKey, entry: dict[str, object], *, user_created: bool = False) -> None:
        """Write this entry copy: replace existing copies entirely while preserving whether the entry was user-created."""
        row = await self.get(key)
        if row is None:
            self.session.add(
                CatalogUserEntry(
                    maker=key.maker, name=key.name, media_type=key.media_type, entry=entry, user_created=user_created
                )
            )
        else:
            row.entry = entry
        await self.session.flush()

    async def delete(self, key: EntryKey) -> bool:
        """Delete this entry copy and return whether a row was removed."""
        result = await self.session.execute(
            delete(CatalogUserEntry).where(
                CatalogUserEntry.maker == key.maker,
                CatalogUserEntry.name == key.name,
                CatalogUserEntry.media_type == key.media_type,
            )
        )
        await self.session.flush()
        return rowcount(result) > 0
