"""User modification layer: entry-copy persistence/loading (docs/adr/0099).

Editing model information stores whole-entry copies, stopping official following; restoring official data discards copies. User-entry tables
store catalog-format copies. Callers commit transactions, then :func:`reload_user_entries` loads query copies. All catalog readers
run in the application process, so loading immediately affects facades, capabilities, and pricing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Final, cast

from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import (
    MEDIA_TYPES,
    CatalogEntry,
    CatalogFormatError,
    EntryKey,
    check_entry,
    dump_entry,
    parse_entry,
)
from lib.catalog import query as catalog
from lib.catalog.reload_order import LatestReadWins
from lib.db.repositories.catalog_user_entry_repo import CatalogUserEntryRepository

logger = logging.getLogger(__name__)


class EntryEditRejected(Exception):
    """Rejected entry edits. ``code`` is i18n keys; ``not_found`` distinguishes absence from forbidden operations."""

    def __init__(self, code: str, *, not_found: bool = False, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.not_found = not_found
        self.params = params


@dataclass(frozen=True)
class UserEntries:
    """Loaded user copies; ``created`` holds user-created identities among them."""

    entries: list[CatalogEntry]
    created: frozenset[EntryKey]


async def load_user_entries(session: AsyncSession) -> UserEntries:
    """Read all user copies; skip/log unreadable copies, returning those entries to official following."""
    entries: list[CatalogEntry] = []
    created: set[EntryKey] = set()
    for row in await CatalogUserEntryRepository(session).list_all():
        try:
            entry = parse_entry(row.entry)
        except CatalogFormatError as exc:
            logger.warning("用户修改的条目 %s/%s（%s）无法读入，已跳过：%s", row.maker, row.name, row.media_type, exc)
            continue
        if (entry.maker, entry.name, entry.media_type) != (row.maker, row.name, row.media_type):
            logger.warning("用户修改的条目 %s/%s（%s）与所在行的身份不符，已跳过", row.maker, row.name, row.media_type)
            continue
        entries.append(entry)
        if row.user_created:
            created.add(entry.key)
    return UserEntries(entries, frozenset(created))


_RELOADS = LatestReadWins()


async def reload_user_entries(session: AsyncSession) -> None:
    """Load user copies into queries at startup/after committed writes; concurrent loads apply by read-start order."""
    await _RELOADS.reload(
        lambda: load_user_entries(session),
        lambda loaded: catalog.install_user_entries(loaded.entries, loaded.created),
    )


class _Unset(Enum):
    UNSET = "unset"


#: Omitted ``edited_entry`` ``price`` leaves tables unchanged; ``None`` meaningfully clears them.
UNSET: Final = _Unset.UNSET


def edited_entry(
    current: CatalogEntry,
    *,
    display_name: str | None = None,
    facts: Mapping[str, object] | None = None,
    price: Mapping[str, object] | _Unset | None = UNSET,
) -> CatalogEntry:
    """Edit display names, model facts, and prices into whole-entry copies; preserve aliases/context lengths and all other fields.

    ``facts`` contains changed keys only; ``price`` replaces whole tables, ``None`` clears to unpriced, omission leaves unchanged.
    Validate catalog formats/media types; invalid edits raise :class:`EntryEditRejected`.
    """
    raw = dump_entry(current)
    if display_name is not None:
        raw["display_name"] = display_name.strip()
    if facts is not None:
        raw["facts"] = {**cast("dict[str, object]", raw["facts"]), **facts}
    if price is None:
        raw.pop("price", None)
    elif not isinstance(price, _Unset):
        raw["price"] = price
    issues = check_entry(raw)
    if issues:
        raise EntryEditRejected("catalog_entry_invalid", field=issues[0].path)
    return parse_entry(raw)


async def save_user_entry(session: AsyncSession, entry: CatalogEntry) -> None:
    """Persist user copies, stopping official following."""
    await CatalogUserEntryRepository(session).put(entry.key, dump_entry(entry))


def new_entry(
    *,
    maker: str,
    name: str,
    media_type: str,
    display_name: str | None = None,
    facts: Mapping[str, object] | None = None,
    price: Mapping[str, object] | None = None,
) -> CatalogEntry:
    """User-entered entries: omitted ``facts`` use media defaults; omitted ``price`` means unpriced.

    Empty display names use model names. Validate catalog formats, raising :class:`EntryEditRejected` if invalid; existing same-name entries
    (official names/aliases normalized under ``docs/adr/0105``) raise ``catalog_entry_exists``, prompting direct selection.
    """
    maker, name = maker.strip(), name.strip()
    if media_type not in MEDIA_TYPES:
        raise EntryEditRejected("catalog_entry_invalid", field="media_type")
    if not maker:
        raise EntryEditRejected("catalog_entry_invalid", field="maker")
    if not name:
        raise EntryEditRejected("catalog_entry_invalid", field="name")
    raw: dict[str, object] = {
        "maker": maker,
        "name": name,
        "media_type": media_type,
        "display_name": (display_name or "").strip() or name,
        "facts": dict(facts or {}),
    }
    if price is not None:
        raw["price"] = dict(price)
    if issues := check_entry(raw):
        raise EntryEditRejected("catalog_entry_invalid", field=issues[0].path)
    entry = parse_entry(raw)
    if catalog.find_entry(name, media_type=media_type) is not None or catalog.get_entry(entry.key) is not None:
        raise EntryEditRejected("catalog_entry_exists", name=name)
    return entry


async def create_user_entry(session: AsyncSession, entry: CatalogEntry) -> None:
    """Persist user-created entries without official following; callers first validate through :func:`new_entry`."""
    await CatalogUserEntryRepository(session).put(entry.key, dump_entry(entry), user_created=True)


async def discard_user_entry(session: AsyncSession, key: EntryKey) -> None:
    """Restore official data by discarding copies; reject user-created entries absent from official data; no-op without copies."""
    if catalog.official_entry(key) is None:
        raise EntryEditRejected("catalog_entry_no_official", not_found=catalog.get_entry(key) is None)
    await CatalogUserEntryRepository(session).delete(key)
