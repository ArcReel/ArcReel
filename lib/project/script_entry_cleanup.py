"""Move media/history of items leaving formal scripts into project trash (``docs/adr/0107``).

Two departures: structural removal (batch/Agent remove, or items absent after formal overwrite), and
identity replacement (reinsertion, new items reusing old IDs on overwrite). Retaining old history/current
files on replacement would attach old content to new-item history on generation, enabling rollback
to unrelated visuals. Move all old files in both cases.

Each item gets a deletion record with pre-edit script structural snapshots; restore via
:mod:`lib.project.script_entry_trash`. Structural removals get records even without files, restoring
items themselves; all old-script items count as structurally removed on overwrite. Batch replacement
IDs absent from pre-edit scripts have no item to restore and get no record without files.

Separate module because it needs both :mod:`lib.artifacts.version_manager` (history) and
:mod:`lib.project.resource_paths` (current-file paths)."""

from __future__ import annotations

import json
from collections.abc import Callable, Collection, Iterable, Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import VersionManager
from lib.infra.path_safety import try_safe_join
from lib.project import project_trash
from lib.project.resource_paths import END_FRAME_RESOURCE_TYPE, resource_relative_path
from lib.script.item_excerpt import position_snapshot
from lib.speech.narration_config import POST_PRODUCTION, USE_TTS
from lib.speech.speech_presentation import presentation_artifact_paths

#: Deletion object type; description ``{"kind", "id": 条目编号, "episode", "script_file", "item_kind": 条目数组键}``,
#: plus ``item_position`` / ``item_excerpt`` naming snapshots for items in pre-edit scripts, used by trash display.
SCRIPT_ENTRY_KIND = "script_entry"

#: ID-named media: storyboard images, end frames, storyboard/reference video, narration.
ENTRY_MEDIA_RESOURCE_TYPES: tuple[str, ...] = (
    "storyboards",
    END_FRAME_RESOURCE_TYPE,
    "videos",
    "reference_videos",
    "audio",
)


def entry_media_paths(entry_id: str) -> list[str]:
    """Item-ID-named media/video-thumbnail project-relative POSIX paths, regardless of existence."""
    return [
        *(resource_relative_path(resource_type, entry_id) for resource_type in ENTRY_MEDIA_RESOURCE_TYPES),
        f"thumbnails/scene_{entry_id}.jpg",
        f"reference_videos/thumbnails/{entry_id}.jpg",
    ]


def entry_derived_paths(episode: int, entry_id: str) -> list[str]:
    """Subtitle drafts/presentations (both delivery variants), regardless of existence; none for invalid episodes/IDs."""
    try:
        return [
            rel
            for variant in (POST_PRODUCTION, USE_TTS)
            for rel in presentation_artifact_paths(episode, entry_id, variant)
        ]
    except ValueError:
        return []


def entry_files(project_dir: Path, episode: int, entry_id: str) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Regular files/history belonging to this item in ``project_dir``; use current projects for deletion, migrated temporary projects for restoration.

    On-disk IDs are untrusted; those unable to resolve project-contained paths have no files."""
    versions = VersionManager(project_dir)
    history = versions.resource_records((resource_type, entry_id) for resource_type in ENTRY_MEDIA_RESOURCE_TYPES)
    candidates = [
        *entry_media_paths(entry_id),
        *entry_derived_paths(episode, entry_id),
        *versions.snapshot_files(history),
    ]
    files = [
        rel
        for rel in dict.fromkeys(candidates)
        if (path := try_safe_join(project_dir, rel)) is not None and path.is_file() and not path.is_symlink()
    ]
    return files, history


def trash_entries(
    project_dir: Path,
    *,
    episode: int,
    script_file: str,
    entries: Mapping[str, str],
    before: Mapping[str, bytes],
    removed: Collection[str] = (),
    then: Callable[[], None] | None = None,
) -> list[project_trash.TrashRecord]:
    """Trash these items' files (ID → item-array key), one record per item, then run ``then``.

    Callers hold script/project locks in the script-write critical section, before manifest deregistration
    (passed as ``then``), preserving registrations/history in snapshots. ``before`` holds pre-edit content
    of structures already changed there (script, ``project.json``). Structurally ``removed`` items get
    records even without files. Any failure withdraws created records, restores history, and propagates;
    caller write transactions roll back scripts."""
    versions = VersionManager(project_dir)
    records: list[project_trash.TrashRecord] = []
    script_before = _script_before(before, script_file)
    with formal_write_transaction(versions.versions_file):
        try:
            for entry_id, item_kind in entries.items():
                files, history = entry_files(project_dir, episode, entry_id)
                if not files and entry_id not in removed:
                    continue
                obj = {
                    "kind": SCRIPT_ENTRY_KIND,
                    "id": entry_id,
                    "episode": episode,
                    "script_file": script_file,
                    "item_kind": item_kind,
                    **position_snapshot(script_before, entry_id),
                }
                records.append(project_trash.record_deletion(project_dir, obj, files, snapshot=True, before=before))
                # Snapshot files moved with records; remove history records only.
                versions.detach_resources(_resources(history))
            if then is not None:
                then()
        except BaseException:
            for record in reversed(records):
                project_trash.withdraw(project_dir, record.id)
            raise
    return records


def _script_before(before: Mapping[str, bytes], script_file: str) -> Mapping[str, Any]:
    """Pre-edit script; empty if unreadable, omitting naming snapshots from records."""
    try:
        script = json.loads(before.get(f"scripts/{script_file}", b"{}"))
    except ValueError:
        return {}
    return script if isinstance(script, Mapping) else {}


def _resources(history: Mapping[str, Mapping[str, Any]]) -> Iterable[tuple[str, str]]:
    return ((resource_type, resource_id) for resource_type, bucket in history.items() for resource_id in bucket)
