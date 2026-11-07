"""Derivative-sheet rename relocation: images, history, and manifest keys (``docs/adr/0072``).

Coordinates depend entirely on base/derivative names (``characters/derivatives/{本体}/{衍生}.png``,
resource ID ``本体/衍生``, manifest key ``asset_sheet("character", "本体/衍生")``). Changing either
requires moving all three, or images remain outside new coordinates and registrations no longer reference them.

Base/derivative renames differ only by changed segment, so share planning. Callers provide old→new
base names and derivative-name pairs; return complete plans so zero-write preflight and ordered
persistence share caller locks/formal transactions.

Paired with :mod:`lib.project.asset_trash`: deletion trashes all three together; renaming relocates them together."""

from __future__ import annotations

import contextlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from lib.artifacts.artifact_manifest import ArtifactEntryRekeyPlan, ArtifactManifest, ArtifactManifestAdapter
from lib.artifacts.version_manager import VersionManager
from lib.project.asset_derivatives import (
    DERIVATIVE_ASSET_TYPE,
    derivative_artifact_id,
    derivative_artifact_key,
    derivative_sheet_dir,
    derivative_sheet_relative_path,
    derivative_table,
    derivative_version_dir,
)
from lib.project.asset_rename import AssetRenameFileCollisionError
from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, normalize_asset_name
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE

_SHEET_FIELD = ASSET_SPECS[DERIVATIVE_ASSET_TYPE].sheet_field


@dataclass(frozen=True, slots=True)
class DerivativeSheetRelocation:
    """Complete derivative-sheet persistence actions for one rename, computed in planning and followed at write time.

    ``relocate`` moves images/history; ``commit_manifest`` rekeys within caller formal transactions.
    Both are idempotent, skipping absent sources and converging on retries of interrupted renames."""

    moves: tuple[tuple[Path, Path], ...]
    version_renames: tuple[tuple[str, str], ...]
    manifest_plans: tuple[ArtifactEntryRekeyPlan, ...]
    retired_dirs: tuple[Path, ...]
    files: int
    _version_manager: VersionManager

    def relocate(self) -> None:
        """Move images/snapshots, then remove emptied old directories."""
        for source, destination in self.moves:
            if source.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
        for old_id, new_id in self.version_renames:
            self._version_manager.rename_resource(CHARACTER_DERIVATIVE_RESOURCE_TYPE, old_id, new_id)
        for directory in self.retired_dirs:
            # Nonempty/absent/inaccessible directories are harmless containers; retaining them affects no registrations.
            with contextlib.suppress(OSError):
                directory.rmdir()  # hard-delete: infra -- Empty directories after derivative-sheet relocation.

    def commit_manifest(self) -> None:
        """Rekey each derivative registration; disjoint planned keys permit safe individual CAS commits."""
        for plan in self.manifest_plans:
            plan.commit()


def plan_derivative_sheet_relocation(
    project_dir: Path,
    *,
    manifest_adapter: ArtifactManifestAdapter | None,
    old_owner: str,
    new_owner: str,
    renames: Sequence[tuple[str, str]],
    carry_unlisted: bool = True,
) -> DerivativeSheetRelocation:
    """Plan derivative-sheet relocation read-only, without writing bytes.

    ``renames`` maps old→new derivative names: base renames preserve both sides per derivative; derivative
    renames have one pair. Base renames also move **all** files under old base directories and remove
    those directories, like :func:`lib.project.asset_rename.plan_asset_file_renames`: incomplete-field
    intermediate outputs must not remain under old names. Without base renaming, touch only the renamed
    derivative, never siblings. ``carry_unlisted=False`` moves only derivatives listed in ``renames`` and leaves old
    directories for callers to clean, supporting partial migration during merges.

    Raises:
        AssetRenameFileCollisionError: a destination path is occupied by others.
        AssetRenameHistoryCollisionError: new-ID history belongs to another derivative."""
    owner_moved = normalize_asset_name(old_owner) != normalize_asset_name(new_owner)
    stem_map = {normalize_asset_name(old): new for old, new in renames}

    moves: list[tuple[Path, Path]] = []
    planned: set[Path] = set()
    source_dir = project_dir / derivative_sheet_dir(old_owner)
    destination_dir = project_dir / derivative_sheet_dir(new_owner)
    if source_dir.is_dir():
        for file in sorted(source_dir.iterdir()):
            if not file.is_file():
                continue
            new_stem = stem_map.get(normalize_asset_name(file.stem)) or (
                file.stem if owner_moved and carry_unlisted else None
            )
            if new_stem is None:
                continue
            destination = destination_dir / (new_stem + file.suffix)
            if destination == file:
                continue
            if destination in planned or (destination.exists() and not destination.samefile(file)):
                raise AssetRenameFileCollisionError(destination)
            planned.add(destination)
            moves.append((file, destination))

    version_manager = VersionManager(project_dir)
    manifest = ArtifactManifest(manifest_adapter) if manifest_adapter is not None else None
    version_renames: list[tuple[str, str]] = []
    manifest_plans: list[ArtifactEntryRekeyPlan] = []
    snapshots = 0
    for old_name, new_name in renames:
        old_id = derivative_artifact_id(old_owner, old_name)
        new_id = derivative_artifact_id(new_owner, new_name)
        if old_id == new_id:
            continue
        # Dry-run rejects other-owned new-ID history without writes, like base renames.
        snapshots += version_manager.rename_resource(CHARACTER_DERIVATIVE_RESOURCE_TYPE, old_id, new_id, dry_run=True)
        version_renames.append((old_id, new_id))
        if manifest is not None:
            manifest_plans.append(
                manifest.plan_entry_rekey(
                    derivative_artifact_key(old_owner, old_name),
                    derivative_artifact_key(new_owner, new_name),
                    artifact_path_rewrites={
                        derivative_sheet_relative_path(old_owner, old_name): derivative_sheet_relative_path(
                            new_owner, new_name
                        )
                    },
                )
            )

    return DerivativeSheetRelocation(
        moves=tuple(moves),
        version_renames=tuple(version_renames),
        manifest_plans=tuple(manifest_plans),
        # Base renames invalidate both old storage directories; within-base derivative renames leave directories.
        retired_dirs=(
            (source_dir, project_dir / derivative_version_dir(old_owner)) if owner_moved and carry_unlisted else ()
        ),
        files=len(moves) + snapshots,
        _version_manager=version_manager,
    )


def rewrite_derivative_sheet_paths(
    entry: Mapping[str, Any],
    *,
    old_owner: str,
    new_owner: str,
    renames: Sequence[tuple[str, str]],
) -> int:
    """Repoint derivative-sheet fields in place, returning rewrite counts.

    Change only exact old canonical paths, as :func:`lib.project.asset_rename.rewrite_entry_paths` does.
    Other paths are outside relocation; changing them would turn valid references into missing targets."""
    table = entry.get(DERIVATIVES_FIELD)
    if not isinstance(table, dict):
        return 0
    count = 0
    for old_name, new_name in renames:
        # Caller keys may already be new names or remain old (base renames preserve derivative names).
        derivative = table.get(new_name) if new_name in table else table.get(old_name)
        if not isinstance(derivative, dict):
            continue
        value = derivative.get(_SHEET_FIELD)
        renamed = derivative_sheet_relative_path(new_owner, new_name)
        if not isinstance(value, str) or not value or value == renamed:
            continue
        current = PurePosixPath(value.replace("\\", "/")).as_posix()
        if normalize_asset_name(current) != normalize_asset_name(derivative_sheet_relative_path(old_owner, old_name)):
            continue
        derivative[_SHEET_FIELD] = renamed
        count += 1
    return count


def owner_derivative_renames(entry: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    """Base-rename pairs: derivative names stay unchanged, moving directories with the base."""
    return tuple((name, name) for name in derivative_table(entry))


__all__ = [
    "DerivativeSheetRelocation",
    "owner_derivative_renames",
    "plan_derivative_sheet_relocation",
    "rewrite_derivative_sheet_paths",
]
