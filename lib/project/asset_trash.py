"""Asset/derivative project trash: deletion creates structural-snapshot records; restoration adds according to landing rules.

Asset records include named sheets/originals/audio, base/derivative version snapshots, and derivative
images. Entries/history/registrations remain in snapshots. Asset deletion, merge removal, and derivative
deletion all use this module.

Reuse free originals (no normalized-name asset collision, residual same-name history, or occupied file
locations), naturally reconnecting script references. Otherwise append “ (n)” and run full rename
cascades in migrated temporary projects (files/history/derivatives/manifest keys) before adding whole.
Restored merge-removed assets can be merged again."""

from __future__ import annotations

import contextlib
import copy
import json
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_registration import forget_current_resource_artifact, register_artifact_entries_atomically
from lib.artifacts.formal_write import formal_write_transaction
from lib.artifacts.version_manager import VersionManager
from lib.infra.api_errors import ConflictError
from lib.project import project_trash
from lib.project.asset_derivatives import (
    derivative_artifact_id,
    derivative_artifact_key,
    derivative_sheet_dir,
    derivative_sheet_relative_path,
    derivative_table,
    derivative_version_dir,
    ensure_derivative_table,
    find_derivative_name,
)
from lib.project.asset_rename import (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    asset_files_named,
)
from lib.project.asset_types import (
    ALIASES_FIELD,
    ASSET_SPECS,
    AssetSpec,
    ProjectAssetNameConflictError,
    asset_name_namespace_key,
    find_project_asset_name,
    resolve_asset_key,
)
from lib.project.project_manager import ProjectManager
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE
from lib.project.trash_restore import Landing, Restoration, RestoreBlocked

__all__ = [
    "ASSET_DERIVATIVE_KIND",
    "ASSET_KIND",
    "deleted_asset_record",
    "state_before",
    "state_paths",
    "take_asset",
    "take_asset_derivative",
    "trash_asset",
    "trash_asset_derivative",
]

ASSET_KIND = "asset"
ASSET_DERIVATIVE_KIND = "asset_derivative"

_VERSIONS_FILE = "versions/versions.json"
#: Structures changed in the same deletion critical section: asset entries, history, and registrations.
_STATE_FILES = ("project.json", _VERSIONS_FILE, MANIFEST_FILENAME)
#: Maximum suffix attempts during renaming.
_MAX_SUFFIX = 1000
_NAME_TAKEN = (
    AssetRenameConflictError,
    AssetRenameFileCollisionError,
    AssetRenameHistoryCollisionError,
    ProjectAssetNameConflictError,
)


# ---------------------------------------------------------------- Deletion


def state_paths(project_dir: Path) -> tuple[Path, ...]:
    """Structures changed by asset deletion; callers include them in one formal transaction, restoring all if records cannot be created."""
    return tuple(project_dir / rel for rel in _STATE_FILES)


def state_before(project_dir: Path) -> dict[str, bytes]:
    """Read :func:`state_paths` before changes under project locks, for deletion-record snapshots."""
    return {
        rel: path.read_bytes()
        for rel in _STATE_FILES
        if (path := project_dir / rel).is_file() and not path.is_symlink()
    }


def trash_asset(
    project_dir: Path,
    spec: AssetSpec,
    name: str,
    entry: Mapping[str, Any],
    before: Mapping[str, bytes],
    **details: Any,
) -> project_trash.TrashRecord:
    """Move removed project.json asset ``name`` files/history/derivatives into one deletion record.

    Callers hold project locks, supply ``before`` from :func:`state_before` before project.json edits,
    and run inside formal transactions covering :func:`state_paths`, rolling back manifests/history on
    record failure. ``details`` enters object descriptions."""
    derivatives = list(derivative_table(entry)) if spec.supports_derivatives else []
    return _record(
        project_dir,
        {"kind": ASSET_KIND, "asset_type": spec.asset_type, "name": name, **details},
        owner=name,
        resources=[(spec.bucket_key, name), *_derivative_resources(name, derivatives)],
        files=[
            *_relative(project_dir, asset_files_named(project_dir, spec, name)),
            *_derivative_sheets(project_dir, name, derivatives),
        ],
        before=before,
    )


def trash_asset_derivative(
    project_dir: Path, owner: str, name: str, before: Mapping[str, bytes]
) -> project_trash.TrashRecord:
    """Trash removed ``owner/name`` derivative images/history; prerequisites match :func:`trash_asset`."""
    return _record(
        project_dir,
        {"kind": ASSET_DERIVATIVE_KIND, "asset_type": "character", "owner": owner, "name": name},
        owner=owner,
        resources=_derivative_resources(owner, [name]),
        files=_derivative_sheets(project_dir, owner, [name]),
        before=before,
    )


def _record(
    project_dir: Path,
    obj: Mapping[str, Any],
    *,
    owner: str,
    resources: Sequence[tuple[str, str]],
    files: Sequence[str],
    before: Mapping[str, bytes],
) -> project_trash.TrashRecord:
    """Deregister resources/history, record files/snapshots as one deletion, then remove emptied derivative directories."""
    for resource_type, resource_id in resources:
        forget_current_resource_artifact(project_dir, resource_type=resource_type, resource_id=resource_id)
    histories = _histories(project_dir, resources)
    snapshots = _history_files(project_dir, histories)
    VersionManager(project_dir).detach_resources(_ids(histories))
    record = project_trash.record_deletion(project_dir, obj, [*files, *snapshots], snapshot=True, before=before)
    for directory in (derivative_sheet_dir(owner), derivative_version_dir(owner)):
        # Nonempty/absent/inaccessible directories are harmless containers; retaining them affects no registrations.
        with contextlib.suppress(OSError):
            (
                project_dir / directory
            ).rmdir()  # hard-delete: infra -- Empty directories after derivative images enter trash.
    return record


def deleted_asset_record(project_dir: Path, asset_type: str, name: str) -> str | None:
    """Latest same-type normalized-name asset deletion-record ID, or ``None``."""
    key = asset_name_namespace_key(name)
    return next(
        (
            record.id
            for record in project_trash.list_records(project_dir)
            if record.has_snapshot
            and record.object.get("kind") == ASSET_KIND
            and record.object.get("asset_type") == asset_type
            and asset_name_namespace_key(str(record.object.get("name", ""))) == key
        ),
        None,
    )


# ---------------------------------------------------------------- Restoration


def take_asset(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Extract assets by name from migrated temporary projects; rename there if landing names are occupied before adding."""
    spec = ASSET_SPECS[str(obj.get("asset_type"))]
    original = resolve_asset_key(_read_project(staged).get(spec.bucket_key), str(obj.get("name")))
    if original is None:
        raise ConflictError("trash_restore_failed", id=str(obj.get("name")))
    #: Derivatives moved to retained assets during merging belong there, never restored with removed assets.
    moved = {str(item) for item in obj.get("derivatives_moved") or ()}
    projects = ProjectManager.for_project_dir(project_dir)
    staged_projects = ProjectManager.for_project_dir(staged)

    def resources(name: str) -> list[tuple[str, str]]:
        entry = _read_project(staged)[spec.bucket_key][name]
        owned = [(spec.bucket_key, name)]
        if spec.supports_derivatives:
            owned += [
                (CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_artifact_id(name, derivative))
                for derivative in derivative_table(entry)
                if derivative not in moved
            ]
        return owned

    def files(name: str) -> list[str]:
        named = _relative(staged, asset_files_named(staged, spec, name))
        derivatives = [
            derivative_sheet_relative_path(*artifact_id.split("/", 1))
            for resource_type, artifact_id in resources(name)
            if resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE
        ]
        return [*named, *_existing(staged, derivatives), *_history_files(staged, _histories(staged, resources(name)))]

    def free(name: str) -> bool:
        current = projects.load_project(project_dir.name)
        key = asset_name_namespace_key(name)
        derivatives = (
            _history_keys(project_dir, CHARACTER_DERIVATIVE_RESOURCE_TYPE) if spec.supports_derivatives else ()
        )
        return (
            find_project_asset_name(current, name) is None
            and key not in _history_keys(project_dir, spec.bucket_key)
            and not any(item.startswith(f"{key}/") for item in derivatives)
        )

    def rename(old: str, new: str) -> None:
        staged_projects.rename_asset(staged.name, spec.bucket_key, old, new)

    def land(landing: Landing) -> Mapping[str, Any]:
        aliases = _read_project(staged)[spec.bucket_key][original].get(ALIASES_FIELD)
        landed = _land_name(original, landing, free=free, rename=rename, files=files)
        entry = copy.deepcopy(_read_project(staged)[spec.bucket_key][landed])
        if landed != original and ALIASES_FIELD in entry:
            # Rename cascades add original-name aliases, but that name now belongs to another current asset; restored assets must not claim it.
            entry[ALIASES_FIELD] = copy.deepcopy(aliases) if isinstance(aliases, list) else []
        if spec.supports_derivatives:
            table = ensure_derivative_table(entry)
            for derivative in moved:
                table.pop(derivative, None)
        claims = [ArtifactKey.asset_sheet(spec.asset_type, landed)] + [
            derivative_artifact_key(*artifact_id.split("/", 1))
            for resource_type, artifact_id in resources(landed)
            if resource_type == CHARACTER_DERIVATIVE_RESOURCE_TYPE
        ]

        def add(project: dict) -> None:
            project.setdefault(spec.bucket_key, {})[landed] = entry

        _lift(project_dir, staged, landing, files(landed), resources(landed), claims, projects, add)
        return {**obj, "name": landed}

    return Restoration(land=land, after=lambda: _changed(project_dir))


def take_asset_derivative(project_dir: Path, staged: Path, obj: Mapping[str, Any]) -> Restoration:
    """Extract derivatives by base/name from temporary projects, renaming there if occupied.

    If bases are also trashed, raise :class:`RestoreBlocked`: restore bases first, without record degradation.
    Permanently deleted bases prevent structural restoration; degrade as unavailable objects."""
    spec = ASSET_SPECS["character"]
    owner = resolve_asset_key(_read_project(staged).get(spec.bucket_key), str(obj.get("owner")))
    original = (
        resolve_asset_key(derivative_table(_read_project(staged)[spec.bucket_key][owner]), str(obj.get("name")))
        if owner is not None
        else None
    )
    if owner is None or original is None:
        raise ConflictError("trash_restore_failed", id=str(obj.get("name")))
    projects = ProjectManager.for_project_dir(project_dir)
    staged_projects = ProjectManager.for_project_dir(staged)

    def current_owner() -> tuple[str, dict[str, Any]]:
        bucket = projects.load_project(project_dir.name).get(spec.bucket_key)
        key = resolve_asset_key(bucket, owner)
        if key is None or not isinstance(bucket, dict) or not isinstance(bucket[key], dict):
            if deleted_asset_record(project_dir, spec.asset_type, owner) is not None:
                raise RestoreBlocked("trash_restore_needs_character", name=owner)
            # Base permanently deleted; restored derivatives have no owner.
            raise ConflictError("trash_restore_failed", id=str(obj.get("name")))
        return key, bucket[key]

    # Degrade permanently deleted bases or reject trashed bases; recheck under landing locks.
    current_owner()

    def free(name: str) -> bool:
        _key, entry = current_owner()
        artifact_id = asset_name_namespace_key(derivative_artifact_id(owner, name))
        return find_derivative_name(derivative_table(entry), name) is None and artifact_id not in _history_keys(
            project_dir, CHARACTER_DERIVATIVE_RESOURCE_TYPE
        )

    def files(name: str) -> list[str]:
        resource = [(CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_artifact_id(owner, name))]
        return [
            *_existing(staged, [derivative_sheet_relative_path(owner, name)]),
            *_history_files(staged, _histories(staged, resource)),
        ]

    def rename(old: str, new: str) -> None:
        staged_projects.rename_asset_derivative("character", staged.name, owner, old, new)

    def land(landing: Landing) -> Mapping[str, Any]:
        landed = _land_name(original, landing, free=free, rename=rename, files=files)
        row = copy.deepcopy(derivative_table(_read_project(staged)[spec.bucket_key][owner])[landed])

        def add(project: dict) -> None:
            key = resolve_asset_key(project.get(spec.bucket_key), owner)
            ensure_derivative_table(project[spec.bucket_key][key])[landed] = row

        _lift(
            project_dir,
            staged,
            landing,
            files(landed),
            [(CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_artifact_id(owner, landed))],
            [derivative_artifact_key(owner, landed)],
            projects,
            add,
        )
        return {**obj, "name": landed}

    return Restoration(land=land, after=lambda: _changed(project_dir))


def _land_name(
    original: str,
    landing: Landing,
    *,
    free: Callable[[str], bool],
    rename: Callable[[str, str], None],
    files: Callable[[str], list[str]],
) -> str:
    """Reuse free original names, otherwise try “ (n)” suffixes; cascade temporary-project renames and return landing names."""
    current = original
    for n in range(_MAX_SUFFIX):
        candidate = original if n == 0 else f"{original} ({n})"
        if not free(candidate):
            continue
        if candidate != current:
            try:
                rename(current, candidate)
            except _NAME_TAKEN:
                continue  # This name or residual history already existed in deletion-time temporary projects.
            current = candidate
        if all(landing.free(rel) for rel in files(current)):
            return current
    raise ConflictError("trash_restore_failed", id=original)


def _lift(
    project_dir: Path,
    staged: Path,
    landing: Landing,
    files: Sequence[str],
    resources: Sequence[tuple[str, str]],
    claims: Sequence[ArtifactKey],
    projects: ProjectManager,
    add: Callable[[dict], None],
) -> None:
    """Add files/history/registrations/entries from temporary to current projects; restore prior structures on any failure."""
    histories = _histories(staged, resources)
    staged_manifest = ProjectArtifactManifestAdapter(staged)
    with formal_write_transaction(*state_paths(project_dir)):
        for rel in files:
            landing.place(staged / rel, rel, original=rel)
        VersionManager(project_dir).attach_resources(histories)
        projects.update_project_locked(project_dir.name, add)
        entries = {key: entry for key in claims if (entry := staged_manifest.get_entry(key)) is not None}
        if entries:
            # Backfill predeletion/migrated registrations with unchanged basis digests; later-changed inputs read stale normally.
            register_artifact_entries_atomically(
                project_dir,
                {key: ArtifactManifestEntry(entry.artifact_path, entry.basis_digest) for key, entry in entries.items()},
            )


def _history_keys(project_dir: Path, resource_type: str) -> set[str]:
    """Existing history resource IDs for this type, normalized by asset-name rules, including remnants of earlier asset deletions."""
    bucket = _read_json(project_dir / _VERSIONS_FILE).get(resource_type)
    return (
        {asset_name_namespace_key(key) for key in bucket if isinstance(key, str)} if isinstance(bucket, dict) else set()
    )


def _derivative_resources(owner: str, derivatives: Sequence[str]) -> list[tuple[str, str]]:
    return [(CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivative_artifact_id(owner, name)) for name in derivatives]


def _derivative_sheets(root: Path, owner: str, derivatives: Sequence[str]) -> list[str]:
    return _existing(root, [derivative_sheet_relative_path(owner, name) for name in derivatives])


def _histories(root: Path, resources: Sequence[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    """History for these resources; NFC-resolve IDs to actual persisted keys, which may be legacy NFD."""
    data = _read_json(root / _VERSIONS_FILE)
    resolved = [
        (resource_type, key)
        for resource_type, resource_id in resources
        if (key := resolve_asset_key(data.get(resource_type), resource_id)) is not None
    ]
    return VersionManager(root).resource_records(resolved)


def _history_files(root: Path, histories: Mapping[str, Mapping[str, Any]]) -> list[str]:
    return _existing(root, VersionManager(root).snapshot_files(histories))


def _ids(histories: Mapping[str, Mapping[str, Any]]) -> list[tuple[str, str]]:
    return [(resource_type, resource_id) for resource_type, bucket in histories.items() for resource_id in bucket]


def _changed(project_dir: Path) -> None:
    from lib.project.project_change_hints import emit_project_change_hint

    emit_project_change_hint(project_dir.name, changed_paths=[ProjectManager.PROJECT_FILE])


def _read_project(project_dir: Path) -> dict[str, Any]:
    return _read_json(project_dir / ProjectManager.PROJECT_FILE)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _existing(root: Path, rels: Sequence[str]) -> list[str]:
    return [rel for rel in dict.fromkeys(rels) if (root / rel).is_file() and not (root / rel).is_symlink()]


def _relative(root: Path, paths: Sequence[Path]) -> list[str]:
    return _existing(root, [path.relative_to(root).as_posix() for path in paths])
