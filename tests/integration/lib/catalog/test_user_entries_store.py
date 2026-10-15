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

        loaded = await load_user_entries(session)

    assert [(entry.key, entry.display_name) for entry in loaded.entries] == [(EntryKey("acme", "chat", "text"), "Mine")]
    assert loaded.created == frozenset()


async def test_created_flag_is_kept_on_replace_and_loaded(db_factory: async_sessionmaker[AsyncSession]) -> None:
    key = EntryKey("acme", "chat", "text")
    async with db_factory() as session:
        repo = CatalogUserEntryRepository(session)
        await repo.put(key, _GOOD, user_created=True)
        # 后续对副本的修改整条替换，不改变条目由用户新建这件事
        await repo.put(key, {**_GOOD, "display_name": "Renamed"})
        await session.commit()

        loaded = await load_user_entries(session)

    assert [entry.display_name for entry in loaded.entries] == ["Renamed"]
    assert loaded.created == {key}
