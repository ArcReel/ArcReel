"""用户条目表的装入：读不了或身份对不上的副本跳过，这些条目回到跟随官方。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from arcreel_catalog_core import EntryKey
from lib.catalog.user_entries import load_user_entries
from lib.db.repositories.catalog_user_entry_repo import CatalogUserEntryRepository

_GOOD = {"maker": "acme", "name": "chat", "media_type": "text", "display_name": "Mine", "facts": {}}


async def test_unreadable_or_mismatched_copies_are_skipped(db_factory: async_sessionmaker[AsyncSession]) -> None:
    async with db_factory() as session:
        repo = CatalogUserEntryRepository(session)
        await repo.put(EntryKey("acme", "chat", "text"), _GOOD)
        await repo.put(EntryKey("acme", "broken", "text"), {**_GOOD, "name": "broken", "facts": []})
        await repo.put(EntryKey("acme", "elsewhere", "text"), _GOOD)
        await session.commit()

        entries = await load_user_entries(session)

    assert [(entry.key, entry.display_name) for entry in entries] == [(EntryKey("acme", "chat", "text"), "Mine")]
