"""用户修改层：模型条目的用户副本的读写与装入（docs/adr/0099）。

用户修改模型信息时存下整个条目的副本，这个条目从此停止跟随官方；恢复官方数据即丢弃副本。副本存在用户条目表里，
格式与目录数据相同。写入后由调用方提交事务，再调用 :func:`reload_user_entries` 把副本装入目录查询；读目录的代码
都在应用进程里，装入即对门面、能力与计价全部生效。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from arcreel_catalog_core import CatalogEntry, CatalogFormatError, EntryKey, check_entry, dump_entry, parse_entry
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


async def load_user_entries(session: AsyncSession) -> list[CatalogEntry]:
    """读出全部用户副本；读不了的副本跳过并记日志，这个条目回到跟随官方。"""
    entries: list[CatalogEntry] = []
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
    return entries


async def reload_user_entries(session: AsyncSession) -> None:
    """从用户条目表装入用户副本，交给目录查询。应用启动时与每次写入提交之后调用。"""
    catalog.install_user_entries(await load_user_entries(session))


def edited_entry(
    current: CatalogEntry, *, display_name: str | None = None, facts: Mapping[str, object] | None = None
) -> CatalogEntry:
    """在当前条目上改显示名与模型事实，得到新的整条副本；其余字段（别名、上下文长度、价目表等）原样带上。

    ``facts`` 只给要改的键，按目录数据的格式与媒体类型校验；不合法时抛 :class:`EntryEditRejected`。
    """
    raw = dump_entry(current)
    if display_name is not None:
        raw["display_name"] = display_name.strip()
    if facts is not None:
        raw["facts"] = {**cast("dict[str, object]", raw["facts"]), **facts}
    issues = check_entry(raw)
    if issues:
        raise EntryEditRejected("catalog_entry_invalid", field=issues[0].path)
    return parse_entry(raw)


async def save_user_entry(session: AsyncSession, entry: CatalogEntry) -> None:
    """存下条目的用户副本；这个条目从此停止跟随官方。"""
    await CatalogUserEntryRepository(session).put(entry.key, dump_entry(entry))


async def discard_user_entry(session: AsyncSession, key: EntryKey) -> None:
    """恢复官方数据：丢弃用户副本。官方数据里没有这个条目（用户新建的）时拒绝；没有副本时什么都不做。"""
    if catalog.official_entry(key) is None:
        raise EntryEditRejected("catalog_entry_no_official", not_found=catalog.get_entry(key) is None)
    await CatalogUserEntryRepository(session).delete(key)
