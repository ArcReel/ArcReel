"""Storyboard/unit IDs: allocate without reuse (like episode IDs in ``docs/adr/0096``).

IDs such as ``E3S05`` / ``E3U5`` combine episode prefix, letter (storyboards ``S``, reference units ``U``), and primary number;
legacy split entries may also have ``_k`` suffixes. Each episode's highest used number lives in its ``project.json`` ledger
:data:`ITEM_ID_HIGH_WATER_FIELD`, increasing only. All new items allocate above it; both letters share one number sequence.

Whenever scripts save, ``ProjectManager`` raises it to the script's highest number. Preallocating writers outside scripts
(planning drafts) use ``ProjectManager.reserve_item_ids``. Migration backfills pre-upgrade history from five sources:
scripts, version history, artifact manifests, project trash, and editing timelines.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

#: Episode-ledger field: highest primary item number used in this episode.
ITEM_ID_HIGH_WATER_FIELD = "item_id_high_water"

#: Entry-array key → ID letter.
ITEM_ID_LETTERS: dict[str, str] = {"segments": "S", "scenes": "S", "shots": "S", "video_units": "U"}

ITEM_ID_RE = re.compile(r"^E(\d+)([SU])(\d+)(?:_\d+)?$")


def item_id_number(item_id: object, episode: int | None = None) -> int | None:
    """Primary item-ID number; None for invalid IDs or episode prefixes different from ``episode``."""

    if not isinstance(item_id, str) or (match := ITEM_ID_RE.match(item_id)) is None:
        return None
    if episode is not None and int(match.group(1)) != episode:
        return None
    return int(match.group(3))


def max_item_number(item_ids: Iterable[object], episode: int) -> int:
    """Highest primary number belonging to ``episode`` in ``item_ids``, or zero."""

    return max((number for item_id in item_ids if (number := item_id_number(item_id, episode)) is not None), default=0)


def format_item_id(episode: int, kind: str, number: int) -> str:
    """Storyboard IDs use ``E{集}S{序号:02d}``; reference units use ``E{集}U{序号}``."""

    letter = ITEM_ID_LETTERS[kind]
    return f"E{episode}S{number:02d}" if letter == "S" else f"E{episode}U{number}"


def stored_item_id_high_water(entry: Mapping[str, Any] | None) -> int:
    value = entry.get(ITEM_ID_HIGH_WATER_FIELD) if entry is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def raise_item_id_high_water(entry: dict[str, Any], *numbers: int) -> None:
    """Raise the episode's maximum to the greatest ``numbers``; never decrease or write without a larger number."""

    high_water = max(stored_item_id_high_water(entry), *numbers, 0)
    if high_water > stored_item_id_high_water(entry):
        entry[ITEM_ID_HIGH_WATER_FIELD] = high_water


def episode_item_id_high_water(project: Mapping[str, Any], episode: int) -> int:
    """Recorded episode maximum in the ledger, or zero when absent."""

    episodes = project.get("episodes")
    for entry in episodes if isinstance(episodes, list) else []:
        if isinstance(entry, Mapping) and entry.get("episode") == episode:
            return stored_item_id_high_water(entry)
    return 0


class ItemIdIssuer:
    """Issue sequential numbers above ``floor`` (the highest previously used primary number)."""

    def __init__(self, episode: int, kind: str, floor: int) -> None:
        self.episode = episode
        self.kind = kind
        self.last = max(floor, 0)

    def issue(self) -> str:
        self.last += 1
        return format_item_id(self.episode, self.kind, self.last)


def reissue_item_ids(items: list[Any], id_field: str, issuer: ItemIdIssuer) -> dict[str, str]:
    """Assign new IDs in array order in place; return ``{原 ID: 新 ID}``, omitting missing/duplicate originals."""

    renamed: dict[str, str] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        previous = item.get(id_field)
        item[id_field] = issuer.issue()
        if isinstance(previous, str) and previous and previous not in renamed:
            renamed[previous] = item[id_field]
    return renamed


__all__ = [
    "ITEM_ID_HIGH_WATER_FIELD",
    "ITEM_ID_LETTERS",
    "ITEM_ID_RE",
    "ItemIdIssuer",
    "episode_item_id_high_water",
    "format_item_id",
    "item_id_number",
    "max_item_number",
    "raise_item_id_high_water",
    "reissue_item_ids",
    "stored_item_id_high_water",
]
