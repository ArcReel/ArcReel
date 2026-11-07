"""Catalog composition: official data plus user modifications form runtime catalogs (docs/adr/0099, 0103).

- Official: newer dataset version of factory snapshots/synchronized data (:func:`newest_official`); retain entries unique to the other
  candidate (:func:`merge_official`), never deleting entries on sync (docs/adr/0103).
- User modifications: whole-entry copies. Copies replace official following until discarded. User-created entries manually
  entered before official listing never follow official data; later official entries with matching identities do not change their ownership,
  but expose official values so users can switch to official data.

Pure computation only; copy persistence/loading in ``lib.catalog.user_entries``, process caches in ``lib.catalog.query``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Literal

from arcreel_catalog_core import CatalogData, CatalogEntry, EntryKey, parse_dataset_version

#: Entry ownership: ``official`` follows official data, ``modified`` changes official entries, ``user_created`` is user-authored.
EntrySource = Literal["official", "modified", "user_created"]


def newest_official(candidates: Sequence[CatalogData]) -> CatalogData:
    """Newest dataset version among official candidates, preferring earlier candidates on ties. ``candidates`` must not be empty."""
    newest = candidates[0]
    for candidate in candidates[1:]:
        if parse_dataset_version(candidate.dataset_version) > parse_dataset_version(newest.dataset_version):
            newest = candidate
    return newest


def retain_entries(newest: CatalogData, older: CatalogData) -> CatalogData:
    """Append entries present in ``older`` but absent from ``newest`` after ``newest`` entries; retain ``newest`` provider manifests/dataset versions."""
    keys = {entry.key for entry in newest.entries}
    retained = tuple(entry for entry in older.entries if entry.key not in keys)
    return replace(newest, entries=newest.entries + retained) if retained else newest


def merge_official(snapshot: CatalogData, synced: CatalogData) -> CatalogData:
    """Compose official snapshot/sync data: newer dataset version, snapshot on ties, retaining entries unique to the other."""
    newest = newest_official((snapshot, synced))
    return retain_entries(newest, synced if newest is snapshot else snapshot)


@dataclass(frozen=True)
class ComposedCatalog:
    """Composed catalogs. Runtime ``data`` replaces entries with user copies, retaining official manifests/dataset versions."""

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
        """Official entry with matching identity; user-created entries lack one until official data lists it."""
        return self._official_by_key.get(key)


def compose_catalog(
    official: CatalogData, user_entries: Iterable[CatalogEntry], created: Iterable[EntryKey] = ()
) -> ComposedCatalog:
    """Replace official entries by matching user-copy identity, preserving official order; append nonofficial copies. ``created`` holds user-created identities;
    copies absent from official data count as user-created whether present there or not."""
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
