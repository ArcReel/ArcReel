"""Episode IDs/broadcast order (ADR 0096).

Integer episode numbers are internal identities across paths/item IDs (``E3S01``)/artifacts/tasks, allocated never reused.
Ledger ``episodes[]`` array order is broadcast order; creators identify through titles/positions.

Persist historical maxima at ``project.json`` top-level :data:`EPISODE_ID_HIGH_WATER_KEY`, separate from ledgers:
reset/deletion never lowers it, allocating next IDs above it. ``ProjectManager`` raises maxima on each
``project.json`` write (:func:`raise_episode_id_high_water`), covering
IDs registered by every write path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from lib.episode.episode_ledger import parse_positive_episode_num
from lib.project.asset_types import ASSET_SPECS
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE

#: ``project.json`` top-level maximum historical episode ID.
EPISODE_ID_HIGH_WATER_KEY = "episode_id_high_water"

#: Item prefixes ``E3S01`` / ``E3U02`` / ``E3G01``.
_ITEM_ID_PREFIX_RE = re.compile(r"^E(\d+)(?=[A-Z]\d)")
#: Names with ``episode_3``: scripts/drafts/derived-source backups/render/subtitle directories;
#: media ``E3S01`` / ``E3U02`` prefixes such as ``scene_E3S01.png``.
_NAME_EPISODE_RE = re.compile(r"(?:^|[^A-Za-z0-9])episode[_-](\d+)(?![0-9])")
_NAME_ITEM_ID_RE = re.compile(r"(?:^|[^A-Za-z0-9])E(\d+)[A-Z]\d")
#: Asset directories relative to project roots, same-named snapshots under ``versions/``. Creator asset names
#: resembling IDs, such as ``E12A1``, are not episode IDs.
_ASSET_DIRS = frozenset(spec.subdir for spec in ASSET_SPECS.values())


def _stored_high_water(project: Mapping[str, Any]) -> int:
    value = project.get(EPISODE_ID_HIGH_WATER_KEY)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _ledger_entries(project: Mapping[str, Any]) -> Iterator[Mapping[str, Any]]:
    raw = project.get("episodes")
    for entry in raw if isinstance(raw, list) else []:
        if isinstance(entry, Mapping):
            yield entry


def ledger_episode_ids(project: Mapping[str, Any]) -> list[int]:
    """Parseable ledger IDs in broadcast order."""

    ids: list[int] = []
    for entry in _ledger_entries(project):
        episode_id = parse_positive_episode_num(entry.get("episode"))
        if episode_id is not None:
            ids.append(episode_id)
    return ids


def episode_id_high_water(project: Mapping[str, Any]) -> int:
    """Historical maxima, max of recorded and ledger IDs."""

    return max(_stored_high_water(project), *ledger_episode_ids(project), 0)


def raise_episode_id_high_water(project: dict[str, Any], *recorded: int) -> None:
    """Raise maxima over ledger/``recorded`` IDs, never lower; omit fields without IDs."""

    high_water = max(episode_id_high_water(project), *recorded, 0)
    if high_water > _stored_high_water(project):
        project[EPISODE_ID_HIGH_WATER_KEY] = high_water


def allocate_episode_ids(project: dict[str, Any], count: int) -> list[int]:
    """Allocate ``count`` episode IDs, advancing historical maxima to last."""

    first = episode_id_high_water(project) + 1
    ids = list(range(first, first + count))
    if ids:
        raise_episode_id_high_water(project, ids[-1])
    return ids


def episode_position(project: Mapping[str, Any], episode_id: int) -> int | None:
    """1-based broadcast positions; None outside ledgers."""

    for index, candidate in enumerate(ledger_episode_ids(project), start=1):
        if candidate == episode_id:
            return index
    return None


def find_ledger_entry(project: Mapping[str, Any], episode_id: int) -> Mapping[str, Any] | None:
    for entry in _ledger_entries(project):
        if parse_positive_episode_num(entry.get("episode")) == episode_id:
            return entry
    return None


def following_ledger_entry(project: Mapping[str, Any], episode_id: int) -> Mapping[str, Any] | None:
    """Episodes immediately after ``episode_id``; None for final/absent episodes."""

    entries = [entry for entry in _ledger_entries(project) if parse_positive_episode_num(entry.get("episode"))]
    for index, entry in enumerate(entries[:-1]):
        if parse_positive_episode_num(entry.get("episode")) == episode_id:
            return entries[index + 1]
    return None


def episode_title(project: Mapping[str, Any], episode_id: int) -> str:
    entry = find_ledger_entry(project, episode_id)
    title = entry.get("title") if entry is not None else None
    return title.strip() if isinstance(title, str) else ""


def episode_display_name(project: Mapping[str, Any], episode_id: int, translate: Callable[..., str]) -> str:
    """Creator names: ledger titles, otherwise Episode N from current broadcast positions; unnamed outside ledgers.

    Derived display values never persist and follow insertion/reordering positions.
    """

    title = episode_title(project, episode_id)
    if title:
        return title
    position = episode_position(project, episode_id)
    if position is None:
        return translate("episode_unlisted_name")
    return translate("episode_position_name", position=position)


def episode_file_label(project: Mapping[str, Any], episode_id: int, translate: Callable[..., str]) -> str | None:
    """Filesystem episode labels: ``{两位播出位置}_{集名}``; None outside ledgers."""

    position = episode_position(project, episode_id)
    if position is None:
        return None
    return f"{position:02d}_{episode_display_name(project, episode_id, translate)}"


def describe_episode_for_agent(project: Mapping[str, Any], episode_id: int) -> str:
    """Agent output labels: ``《标题》（第 N 个，id=X）``.

    Omit title brackets without titles, IDs only outside ledgers.
    """

    position = episode_position(project, episode_id)
    if position is None:
        return f"集（id={episode_id}）"
    title = episode_title(project, episode_id)
    locator = f"（第 {position} 个，id={episode_id}）"
    return f"《{title}》{locator}" if title else f"未命名集{locator}"


def item_id_within_episode(item_id: str) -> str:
    """Strip episode prefixes: ``E3S01`` → ``S01``; unchanged without prefixes."""

    return _ITEM_ID_PREFIX_RE.sub("", item_id, count=1)


def item_id_episode(item_id: str) -> int | None:
    match = _ITEM_ID_PREFIX_RE.match(item_id)
    return int(match.group(1)) if match else None


def episode_item_ref(project: Mapping[str, Any], item_id: str) -> dict[str, Any] | None:
    """References outside episode pages: parent titles/positions plus local IDs (``S01``).

    UI localizes title · S01, positions when untitled. Missing prefixes/absent parent episodes return
    None, with Web fallback to unnamed episodes/local IDs.
    """

    episode_id = item_id_episode(item_id)
    position = episode_position(project, episode_id) if episode_id is not None else None
    if episode_id is None or position is None:
        return None
    return {
        "episode_title": episode_title(project, episode_id),
        "episode_position": position,
        "item_id": item_id_within_episode(item_id),
    }


def _is_asset_path(name: str) -> bool:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if parts[:1] == ("versions",):
        parts = parts[1:]
    return len(parts) > 1 and parts[0] in _ASSET_DIRS


def episode_ids_in_names(names: Iterable[str]) -> set[int]:
    """Episode IDs in filenames/relative paths/resource IDs, excluding asset images/version snapshot paths."""

    found: set[int] = set()
    for name in names:
        if _is_asset_path(name):
            continue
        found.update(int(value) for value in _NAME_EPISODE_RE.findall(name))
        found.update(int(value) for value in _NAME_ITEM_ID_RE.findall(name))
    return {value for value in found if value > 0}


def episode_ids_in_record(value: object) -> set[int]:
    """IDs from persisted paths/resource keys/explicit fields, never prompts/creative prose."""
    if isinstance(value, str):
        return episode_ids_in_names([value])
    found: set[int] = set()
    if isinstance(value, dict):
        found.update(episode_ids_in_names(str(key) for key in value))
        for key, item in value.items():
            if key in {"episode", "episode_id", "artifact_episode"}:
                episode_id = parse_positive_episode_num(item)
                if episode_id is not None:
                    found.add(episode_id)
            elif key not in {"prompt", "text", "source_text", "novel_text", "instructions", "description", "title"}:
                found.update(episode_ids_in_record(item))
    elif isinstance(value, list):
        for item in value:
            found.update(episode_ids_in_record(item))
    return found


def episode_ids_on_disk(project_dir: Path) -> set[int]:
    """Disk filenames/directories with IDs: scripts/drafts/source backups/media prefixes/etc.

    Exclude asset images/snapshots/asset version-history records.
    """

    project_dir = Path(project_dir)
    found = episode_ids_in_names(
        path.name for path in project_dir.rglob("*") if not _is_asset_path(path.relative_to(project_dir).as_posix())
    )
    # Version/grid records may outlive media on disk without IDs in filenames.
    for directory in (project_dir / "versions", project_dir / "grids"):
        for path in directory.rglob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(record, dict) and directory.name == "versions":
                record = {key: value for key, value in record.items() if not _is_asset_resource_type(key)}
            found.update(episode_ids_in_record(record))
    return found


def _is_asset_resource_type(resource_type: object) -> bool:
    return resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE or resource_type in _ASSET_DIRS


__all__ = [
    "EPISODE_ID_HIGH_WATER_KEY",
    "allocate_episode_ids",
    "describe_episode_for_agent",
    "episode_display_name",
    "episode_file_label",
    "episode_id_high_water",
    "episode_ids_in_names",
    "episode_ids_in_record",
    "episode_ids_on_disk",
    "episode_item_ref",
    "episode_position",
    "episode_title",
    "find_ledger_entry",
    "following_ledger_entry",
    "item_id_episode",
    "item_id_within_episode",
    "ledger_episode_ids",
    "raise_episode_id_high_water",
]
