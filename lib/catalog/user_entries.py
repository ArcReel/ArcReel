"""用户修改层：模型条目的用户副本的读写与装入（docs/adr/0099）。

用户修改模型信息时存下整个条目的副本，这个条目从此停止跟随官方；恢复官方数据即丢弃副本。副本存在用户条目表里，
格式与目录数据相同。写入后由调用方提交事务，再调用 :func:`reload_user_entries` 把副本装入目录查询；读目录的代码
都在应用进程里，装入即对门面、能力与计价全部生效。
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
from lib.db.repositories.catalog_user_entry_repo import CatalogUserEntryRepository

logger = logging.getLogger(__name__)


class EntryEditRejected(Exception):
    """对模型条目的修改被拒绝。``code`` 是错误文案的 i18n key，``not_found`` 区分「不存在」与「不允许」。"""

    def __init__(self, code: str, *, not_found: bool = False, **params: object) -> None:
        super().__init__(code)
        self.code = code
        self.not_found = not_found
        self.params = params


@dataclass(frozen=True)
class UserEntries:
    """读出的用户副本；``created`` 是其中用户新建的条目身份。"""

    entries: list[CatalogEntry]
    created: frozenset[EntryKey]


async def load_user_entries(session: AsyncSession) -> UserEntries:
    """读出全部用户副本；读不了的副本跳过并记日志，这个条目回到跟随官方。"""
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


async def reload_user_entries(session: AsyncSession) -> None:
    """从用户条目表装入用户副本，交给目录查询。应用启动时与每次写入提交之后调用。"""
    loaded = await load_user_entries(session)
    catalog.install_user_entries(loaded.entries, loaded.created)


class _Unset(Enum):
    UNSET = "unset"


#: ``edited_entry`` 的 ``price`` 缺省值：不改价目表。``None`` 是有意义的取值（清除价目表）。
UNSET: Final = _Unset.UNSET


def edited_entry(
    current: CatalogEntry,
    *,
    display_name: str | None = None,
    facts: Mapping[str, object] | None = None,
    price: Mapping[str, object] | _Unset | None = UNSET,
) -> CatalogEntry:
    """在当前条目上改显示名、模型事实与价目表，得到新的整条副本；其余字段（别名、上下文长度等）原样带上。

    ``facts`` 只给要改的键；``price`` 是整份价目表，``None`` 表示清除（调用费用记为未定价），不传表示不改。
    都按目录数据的格式与媒体类型校验；不合法时抛 :class:`EntryEditRejected`。
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
    """存下条目的用户副本；这个条目从此停止跟随官方。"""
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
    """用户手动填写的新条目：``facts`` 缺省的字段取该媒体类型的默认值，``price`` 不填即未定价。

    显示名为空时用模型名。按目录数据的格式校验，不合法时抛 :class:`EntryEditRejected`；目录里已有同名的模型
    （官方模型名与模型别名按 ``docs/adr/0105`` 归一化后相同）时抛 ``catalog_entry_exists``，请用户直接选择。
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
    """存下用户新建的条目；这个条目不跟随官方。调用方先用 :func:`new_entry` 校验。"""
    await CatalogUserEntryRepository(session).put(entry.key, dump_entry(entry), user_created=True)


async def discard_user_entry(session: AsyncSession, key: EntryKey) -> None:
    """恢复官方数据：丢弃用户副本。官方数据里没有这个条目（用户新建的）时拒绝；没有副本时什么都不做。"""
    if catalog.official_entry(key) is None:
        raise EntryEditRejected("catalog_entry_no_official", not_found=catalog.get_entry(key) is None)
    await CatalogUserEntryRepository(session).delete(key)
