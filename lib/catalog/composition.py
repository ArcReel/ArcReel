"""目录合成：官方数据与用户修改合成运行时读到的模型目录（docs/adr/0099、0103）。

- 官方数据：出厂快照与官方同步中，数据集版本较新的一份（:func:`newest_official`）。官方同步取回的数据接入时，
  作为候选与出厂快照一起交给它比较。
- 用户修改：整个条目的副本。有副本的条目停止跟随官方，只用副本；丢弃副本即恢复跟随官方。用户新建的条目（官方
  数据里没有它时手动填写的）从一开始就不跟随官方；日后官方数据收录了同一身份的条目，它仍是用户新建的，同时能看到
  官方的值，用户可以改用官方数据。

本模块只做纯计算；副本的存储与装入见 ``lib.catalog.user_entries``，进程内的缓存见 ``lib.catalog.query``。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from arcreel_catalog_core import CatalogData, CatalogEntry, EntryKey, parse_dataset_version

#: 条目由谁维护：``official`` 跟随官方数据；``modified`` 用户改过官方条目；``user_created`` 用户新建、由用户填写。
EntrySource = Literal["official", "modified", "user_created"]


def newest_official(candidates: Sequence[CatalogData]) -> CatalogData:
    """官方数据的候选中数据集版本最新的一份；版本相同时取靠前的。``candidates`` 不能为空。"""
    newest = candidates[0]
    for candidate in candidates[1:]:
        if parse_dataset_version(candidate.dataset_version) > parse_dataset_version(newest.dataset_version):
            newest = candidate
    return newest


@dataclass(frozen=True)
class ComposedCatalog:
    """合成后的目录。``data`` 是运行时读的目录数据：条目已换成用户副本，供应商清单与数据集版本取官方数据。"""

    official: CatalogData
    data: CatalogData
    _official_by_key: Mapping[EntryKey, CatalogEntry]
    _user_keys: frozenset[EntryKey]
    _created_keys: frozenset[EntryKey]

    def source_of(self, key: EntryKey) -> EntrySource:
        if key not in self._user_keys:
            return "official"
        return "user_created" if key in self._created_keys or key not in self._official_by_key else "modified"

    def official_entry(self, key: EntryKey) -> CatalogEntry | None:
        """官方数据里同一身份的条目；用户新建的条目在官方数据收录它之前没有。"""
        return self._official_by_key.get(key)


def compose_catalog(
    official: CatalogData, user_entries: Iterable[CatalogEntry], created: Iterable[EntryKey] = ()
) -> ComposedCatalog:
    """用户副本按身份替换官方条目（保持官方顺序），官方数据里没有的副本排在最后。``created`` 是用户新建的条目身份；
    官方数据里没有的副本无论是否在其中，都按用户新建处理。"""
    copies: dict[EntryKey, CatalogEntry] = {}
    for entry in user_entries:
        copies.setdefault(entry.key, entry)
    official_by_key: dict[EntryKey, CatalogEntry] = {}
    for entry in official.entries:
        official_by_key.setdefault(entry.key, entry)
    entries = tuple(copies.get(entry.key, entry) for entry in official.entries)
    entries += tuple(entry for key, entry in copies.items() if key not in official_by_key)
    data = replace(official, entries=entries) if copies else official
    return ComposedCatalog(official, data, official_by_key, frozenset(copies), frozenset(created) & copies.keys())
