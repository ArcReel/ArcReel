"""Item IDs ever used in a project (``docs/adr/0090``: allocation only, never reuse).

Five sources: scripts, history (``versions/versions.json``), manifests, trash deletion records
(including script/timeline snapshots), and timeline clip references. Migrations/whole-script persistence
use them for allocation lower bounds. Ledger high-water marks reserve planning-issued numbers;
content confirmation issues new IDs, never relying on cleanable history to decide reuse."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import decode_artifact_key_parts
from lib.project.project_trash import trash_dir
from lib.script.item_ids import ITEM_ID_RE, ItemIdIssuer, episode_item_id_high_water, reissue_item_ids
from lib.script.script_skeleton import resolve_kind_items

#: Item-ID fields: script IDs and timeline clip ``unit_id``.
_ID_FIELDS = frozenset({"segment_id", "scene_id", "shot_id", "unit_id"})
#: History resource types keyed by item IDs, matching ``script_entry_cleanup.ENTRY_MEDIA_RESOURCE_TYPES``.
_ENTRY_RESOURCE_TYPES = ("storyboards", "end_frames", "videos", "reference_videos", "audio")
_MANIFEST_FILE = ".arcreel_artifacts.json"
_SCRIPT_ENTRY_KIND = "script_entry"


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _record(found: dict[int, set[int]], value: object) -> None:
    if isinstance(value, str) and (match := ITEM_ID_RE.match(value)) is not None:
        found.setdefault(int(match.group(1)), set()).add(int(match.group(3)))


def _id_fields(value: object) -> Iterator[object]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _ID_FIELDS:
                yield item
            else:
                yield from _id_fields(item)
    elif isinstance(value, list):
        for item in value:
            yield from _id_fields(item)


def _json_files(directory: Path) -> Iterator[Path]:
    if directory.is_dir():
        yield from (path for path in directory.rglob("*.json") if path.is_file())


def used_item_numbers(project_dir: Path) -> dict[int, set[int]]:
    """``{集 ID: 用过的主序号}``; skip unreadable files."""

    project_dir = Path(project_dir)
    found: dict[int, set[int]] = {}

    for path in (*_json_files(project_dir / "scripts"), *_json_files(project_dir / "edit_timelines")):
        for item_id in _id_fields(_load(path)):
            _record(found, item_id)

    versions = _load(project_dir / "versions" / "versions.json")
    if isinstance(versions, dict):
        for resource_type in _ENTRY_RESOURCE_TYPES:
            resources = versions.get(resource_type)
            for resource_id in resources if isinstance(resources, dict) else ():
                _record(found, resource_id)

    manifest = _load(project_dir / _MANIFEST_FILE)
    entries = manifest.get("entries") if isinstance(manifest, dict) else None
    for key in entries if isinstance(entries, dict) else ():
        parts = decode_artifact_key_parts(key) if isinstance(key, str) else None
        for component in parts.components if parts is not None else ():
            _record(found, component)

    root = trash_dir(project_dir)
    for record_dir in root.iterdir() if root.is_dir() else ():
        record = _load(record_dir / "record.json")
        obj = record.get("object") if isinstance(record, dict) else None
        if isinstance(obj, dict) and obj.get("kind") == _SCRIPT_ENTRY_KIND:
            _record(found, obj.get("id"))
        # Pre-edit scripts, deleted episodes, and timelines inside snapshots.
        for path in _json_files(record_dir):
            for item_id in _id_fields(_load(path)):
                _record(found, item_id)

    return found


def max_used_item_numbers(project_dir: Path) -> dict[int, int]:
    """``{集 ID: 用过的最大主序号}``."""

    return {episode: max(numbers) for episode, numbers in used_item_numbers(project_dir).items() if numbers}


def issue_landing_item_ids(project_dir: Path, project: Mapping[str, Any], script: dict[str, Any]) -> dict[str, str]:
    """Issue IDs for all script items before generated output persists, updating in place and returning ``{原 ID: 新 ID}``.

    Every whole-script conversion/generation issues new numbers beyond the maximum used in the episode ledger and five sources."""

    episode = script.get("episode")
    if not isinstance(episode, int) or isinstance(episode, bool) or episode < 1:
        raise ValueError("script episode must be a positive integer before issuing item ids")
    items, id_field, kind = resolve_kind_items(script)
    entries = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    used = used_item_numbers(project_dir).get(episode, set())
    floor = max(episode_item_id_high_water(project, episode), *used, 0)
    issuer = ItemIdIssuer(episode, kind, floor)
    return reissue_item_ids(entries, id_field, issuer)


__all__ = ["issue_landing_item_ids", "max_used_item_numbers", "used_item_numbers"]
