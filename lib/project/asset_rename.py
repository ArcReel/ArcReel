"""Pure cascading-rename layer: script/draft references, file-relocation plans, and result reports.

Assets use name identity (docs/adr/0057). Cascades atomically update bucket keys, all script/plan
references (arrays/speaker/``@[名称]``), and named files (stem = asset name). **Side-effect-free**
portions here mutate dict references/return counts and plan (src,dst) relocations, letting ProjectManager
scan under locks before persistence, sharing dry-run/execution scans. :class:`AssetDeletionPreview`
shares the same reference scans.

Compare names by strip + NFC (:func:`lib.project.asset_types.asset_name_comparison_key`), since
padded/NFD bodies and arrays otherwise miss references. Stems normalize NFC only, preserving path semantics."""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from lib.project.asset_types import (
    ASSET_SPECS,
    AssetSpec,
    asset_name_comparison_key,
    normalize_asset_name,
    rekey_equivalent_entries,
)
from lib.references.reference_catalog import renamed_reference, split_derivative_reference
from lib.script.grid.models import ReferenceImage
from lib.script.reference_video.text_parser import rewrite_mentions
from lib.script.script_references import iter_reference_sites


class AssetRenameNotFoundError(KeyError):
    """Old names are absent from asset buckets; messages include recovery guidance because previous renames may already have succeeded."""


class AssetRenameConflictError(ValueError):
    """Normalized target conflicts with existing same-type assets; reject wholly without persistence."""

    def __init__(self, conflict_name: str):
        super().__init__(f"目标名与既有资产 {conflict_name!r} 冲突（按 NFC 归一判定），请换一个名字或先处理既有资产")
        self.conflict_name = conflict_name


class AssetRenameFileCollisionError(ValueError):
    """Destination files occupied; reject wholly without persistence.

    Occupants are assetless orphan files (assets already trigger :class:`AssetRenameConflictError`);
    ``os.replace`` would silently destroy them."""

    def __init__(self, destination: Path):
        super().__init__(f"迁移目标文件已存在: {destination}，请先移除该文件或换一个名字")
        self.destination = destination


class AssetRenameHistoryCollisionError(ValueError):
    """Retained history under new names; reject wholly without persistence.

    Earlier asset deletion removed bucket keys only, leaving history/snapshots. Relocating would overwrite
    unrecoverably. Like orphan files, fail loudly for user handling instead of silent destruction."""

    def __init__(self, resource_id: str):
        super().__init__(f"新名 {resource_id!r} 下已有保留的版本历史，请先清理该历史或换一个名字")
        self.resource_id = resource_id


@dataclass(frozen=True)
class AssetRenameReport:
    """Cascade impact report; dry-run/execution share one scan and necessarily identical counts."""

    table: str
    old_name: str
    new_name: str
    episodes: int
    references: int
    files: int
    dry_run: bool


@dataclass(frozen=True)
class AssetEpisodeReferences:
    """Episode reference-site counts for one asset."""

    episode: int
    references: int


@dataclass(frozen=True)
class AssetDeletionPreview:
    """Predeletion references sharing rename scans; deletion would leave them dangling.

    ``references`` is total sites, matching rename previews; ``episodes`` lists attributable subsets by ascending ID."""

    table: str
    name: str
    references: int
    episodes: tuple[AssetEpisodeReferences, ...]


#: Name-list fields by asset type; only str elements count as references.
#: Drama top-level ``scenes`` contains storyboard dicts, while narration uses the same key for scene names;
#: distinguish by element type without skeleton special cases.
_LIST_FIELDS_BY_TYPE: dict[str, frozenset[str]] = {
    asset_type: frozenset(spec.reference_list_fields) for asset_type, spec in ASSET_SPECS.items()
}


def rewrite_payload_references(payload: dict, asset_type: str, old_name: str, new_name: str) -> int:
    """Rewrite *old_name* to *new_name* in script/draft payload references in place, returning count.

    :func:`lib.script.script_references.iter_reference_sites` defines arrays, ``speaker``, bodies/visual
    ``@[名称]``. ``character`` also rekeys legacy embedded top-level ``characters`` mirrors and updates paths.

    *old_name* may be ``角色/衍生``. :func:`lib.references.reference_catalog.renamed_reference` centralizes
    matching: base renames include all derivatives; derivative renames affect only that character's set.
    ``speaker`` carries bases only, naturally never matching derivative renames.

    Recognize skeletons without semantic validation; centralized do-not-worsen persistence guards handle structure."""
    list_fields = _LIST_FIELDS_BY_TYPE[asset_type]
    count = 0

    if asset_type == "character" and not split_derivative_reference(asset_name_comparison_key(old_name))[1]:
        # Legacy scripts embed top-level character mirrors through update_character_sheet;
        # rekey/update sheet paths, preventing old-name mirrors. Derivative renames leave base keys unchanged.
        embedded = payload.get("characters")
        if isinstance(embedded, dict):
            # Always adopt: even if winning keys already use new names for encoding-only renames, equivalent keys
            # may retain old paths. Never skip by apparent winning-name equality; count only actual key changes.
            keys_before = list(embedded)
            entry = rekey_equivalent_entries(embedded, old_name, new_name)
            if entry is not None:
                if list(embedded) != keys_before:
                    count += 1
                if isinstance(entry, dict):
                    count += rewrite_entry_paths(entry, ASSET_SPECS[asset_type], old_name, new_name)

    for kind, value, write in iter_reference_sites(payload, list_fields, with_speaker=asset_type == "character"):
        if kind in ("text", "scene"):
            rewritten, changes = rewrite_mentions(value, old_name, new_name)
            if changes:
                write(rewritten)
                count += changes
            continue
        renamed = renamed_reference(value, old_name, new_name)
        if renamed is not None and renamed != value:
            write(renamed)
            count += 1
    return count


def rewrite_grid_reference_images(
    references: list[ReferenceImage],
    asset_type: str,
    old_name: str,
    new_name: str,
    path_rewrites: Mapping[str, str],
) -> int:
    """Rewrite frozen grid references from *old_name* to *new_name* in place, returning count.

    Grids freeze actual outgoing paths/names/types for basis replay. Moving sheets without record updates
    would make replay lose images and falsely stale grids. :func:`renamed_reference` matches names,
    including base descendants. Rewrite paths only in *path_rewrites*, the files actually relocated by
    this rename, with project-relative keys/values. Unnamed files stay in place and retain valid paths."""
    count = 0
    for reference in references:
        if reference.ref_type != asset_type:
            continue
        renamed = renamed_reference(reference.name, old_name, new_name)
        if renamed is None:
            continue
        path = path_rewrites.get(unicodedata.normalize("NFC", reference.path), reference.path)
        if renamed != reference.name or path != reference.path:
            reference.name = renamed
            reference.path = path
            count += 1
    return count


def renamed_file_stem(stem: str, old_name: str, new_name: str, *, allow_sequence: bool = False) -> str | None:
    """Derive stems under filename = asset-name invariants; None when unrelated to old names.

    Default normalized stems must **exactly** match old names (sheets/image/audio references).
    ``allow_sequence`` means all files here mechanically use ``名_{序号}`` (product references, sequenced
    uploads in server/routers/files.py). Then accept **only** ``旧名_{序号}``, not exact old stems.
    ``validate_asset_name`` permits underscore-number suffixes: with products ``Cup_1`` and ``Cup``,
    ``Cup``'s first ``Cup_1.png`` would otherwise appear to belong to ``Cup_1``. ``Cup_1`` is therefore ambiguous. Never enable
    ``allow_sequence`` for ordinary directories, or sibling “旧名_2” files move and references dangle.

    Compare/construct in normalized coordinates, as macOS filenames may be NFD."""
    normalized = normalize_asset_name(stem)
    target = normalize_asset_name(old_name)
    if not allow_sequence:
        return new_name if normalized == target else None
    prefix = f"{target}_"
    if normalized.startswith(prefix) and normalized[len(prefix) :].isdigit():
        return f"{new_name}_{normalized[len(prefix) :]}"
    return None


def renamed_relpath(rel: str, old_name: str, new_name: str, *, allow_sequence: bool = False) -> str | None:
    """Rename path-field stems; None if unrelated to old names."""
    path = PurePosixPath(rel.replace("\\", "/"))
    new_stem = renamed_file_stem(path.stem, old_name, new_name, allow_sequence=allow_sequence)
    if new_stem is None:
        return None
    return str(path.with_name(new_stem + path.suffix))


def rewrite_entry_paths(entry: dict, spec: AssetSpec, old_name: str, new_name: str) -> int:
    """Update named entry paths (sheet/image/audio/multiple-reference sequence) in place, returning count.

    Share predicates with :func:`plan_asset_file_renames` per dimension: only asset-root/``refs`` /
    ``refs_audio`` paths with matching stems. Permit ``旧名_{序号}`` only in sequence-asset ``refs``;
    require exact names elsewhere. Preserve user-directed other paths such as ``thumbnails/旧名.png``:
    those files do not move, so rewriting would break valid references."""
    base = PurePosixPath(spec.subdir)
    migrated_dirs = {base, base / "refs", base / "refs_audio"}
    sequenced_refs = "reference_images" in spec.extra_list_fields

    def rewrite(value: str, *, sequenced_field: bool = False) -> str | None:
        parent = PurePosixPath(value.replace("\\", "/")).parent
        if parent not in migrated_dirs:
            return None
        allow_sequence = sequenced_field and sequenced_refs and parent == base / "refs"
        renamed = renamed_relpath(value, old_name, new_name, allow_sequence=allow_sequence)
        return renamed if renamed != value else None

    count = 0
    for field in (spec.sheet_field, "reference_image", "reference_audio"):
        value = entry.get(field)
        if isinstance(value, str) and value:
            renamed = rewrite(value)
            if renamed is not None:
                entry[field] = renamed
                count += 1
    images = entry.get("reference_images")
    if isinstance(images, list):
        for i, value in enumerate(images):
            if isinstance(value, str) and value:
                renamed = rewrite(value, sequenced_field=True)
                if renamed is not None:
                    images[i] = renamed
                    count += 1
    return count


def _named_file_layout(spec: AssetSpec) -> tuple[tuple[PurePosixPath, bool], ...]:
    """Project-relative named-file directories and whether each accepts ``名字_{序号}``."""
    base = PurePosixPath(spec.subdir)
    sequenced_refs = "reference_images" in spec.extra_list_fields
    return ((base, False), (base / "refs", sequenced_refs), (base / "refs_audio", False))


def _named_file_dirs(project_dir: Path, spec: AssetSpec) -> list[tuple[Path, bool]]:
    candidates = ((project_dir.joinpath(*directory.parts), allow) for directory, allow in _named_file_layout(spec))
    return [(directory, allow_sequence) for directory, allow_sequence in candidates if directory.is_dir()]


#: Uploaded-original reference fields (images/audio/sequences), owned by name.
REFERENCE_FILE_FIELDS = ("reference_image", "reference_audio", "reference_images")


def named_file_fields(spec: AssetSpec) -> tuple[str, ...]:
    """Name-owned entry fields: sheet plus type-registered reference fields."""
    return (
        spec.sheet_field,
        *(
            field
            for field in REFERENCE_FILE_FIELDS
            if field in spec.extra_string_fields or field in spec.extra_list_fields
        ),
    )


def owns_named_file(spec: AssetSpec, name: str, rel: str) -> bool:
    """Whether relative *rel* belongs to *name*, matching :func:`asset_files_named`.

    Sheets/references/audio belong by name, never cross-asset shared. Rename/delete/restore touch only
    owned files; foreign fields could dangle afterward, so writes accept owned paths only."""
    path = PurePosixPath(rel.replace("\\", "/"))
    for directory, allow_sequence in _named_file_layout(spec):
        if path.parent == directory:
            return renamed_file_stem(path.stem, name, name, allow_sequence=allow_sequence) is not None
    return False


def asset_files_named(project_dir: Path, spec: AssetSpec, name: str) -> list[Path]:
    """Files named by *name* in this asset type (sheets/originals/audio), matching rename scope."""
    return [
        file
        for directory, allow_sequence in _named_file_dirs(project_dir, spec)
        for file in sorted(directory.iterdir())
        if file.is_file() and renamed_file_stem(file.stem, name, name, allow_sequence=allow_sequence) is not None
    ]


def plan_asset_file_renames(
    project_dir: Path, spec: AssetSpec, old_name: str, new_name: str
) -> list[tuple[Path, Path]]:
    """Scan asset directories for matching stems and plan ``(src, dst)`` relocations.

    Cover sheet roots and uploaded ``refs`` / ``refs_audio``; VersionManager handles snapshots.
    Scan directories, not only entry fields, because intermediate outputs may exist before fields are
    written and must not remain under old names.

    Do not exclude hidden filenames: stem equality already excludes clutter (``.DS_Store`` stem stays
    ``.DS_Store``; atomic temps resemble ``tmpXXXX.tmp``). Leading dots are valid names; blanket
    skipping would leave invalid paths after renaming ``.甲``.

    Permit ``旧名_{序号}`` only in ``refs`` for sequences (``reference_images``), where upload naming
    is mechanical and cannot be sibling names. Else require exact names, avoiding sibling “旧名_2” sheets.

    Occupied destinations raise :class:`AssetRenameFileCollisionError` before writes, rejecting wholly.
    Guard both disk orphans and same-batch collisions (e.g. NFC/NFD equivalent sources), where a second
    ``os.replace`` would erase the first result.

    Case/normalization-insensitive APFS/NTFS can resolve case/encoding-only destinations to sources;
    these are not occupied, exempt via ``samefile``.

    Interrupted retries still converge: completed ``src`` files are absent and no longer scanned.

    Raises:
        AssetRenameFileCollisionError: destinations occupied on disk or by another planned relocation."""
    moves: list[tuple[Path, Path]] = []
    planned: set[Path] = set()
    for directory, allow_sequence in _named_file_dirs(project_dir, spec):
        for file in sorted(directory.iterdir()):
            if not file.is_file():
                continue
            new_stem = renamed_file_stem(file.stem, old_name, new_name, allow_sequence=allow_sequence)
            if new_stem is not None and file.stem != new_stem:
                destination = file.with_name(new_stem + file.suffix)
                if destination in planned:
                    raise AssetRenameFileCollisionError(destination)
                if destination.exists() and not destination.samefile(file):
                    raise AssetRenameFileCollisionError(destination)
                planned.add(destination)
                moves.append((file, destination))
    return moves


def foreign_named_file_field(
    spec: AssetSpec, name: str, entry: Mapping[str, object], values: Mapping[str, object]
) -> str | None:
    """First *values* newly written named-file field whose path is not owned by *name*, otherwise ``None``.

    Only new values count; unchanged resubmission of existing paths is not a new write."""
    for field in named_file_fields(spec):
        value, current = values.get(field), entry.get(field)
        written = value if isinstance(value, list) else [value]
        kept = current if isinstance(current, list) else [current]
        if any(
            isinstance(item, str) and item and item not in kept and not owns_named_file(spec, name, item)
            for item in written
        ):
            return field
    return None


__all__ = [
    "REFERENCE_FILE_FIELDS",
    "AssetRenameConflictError",
    "AssetRenameFileCollisionError",
    "AssetRenameHistoryCollisionError",
    "AssetRenameNotFoundError",
    "AssetRenameReport",
    "asset_files_named",
    "foreign_named_file_field",
    "named_file_fields",
    "owns_named_file",
    "plan_asset_file_renames",
    "renamed_file_stem",
    "renamed_relpath",
    "rewrite_entry_paths",
    "rewrite_grid_reference_images",
    "rewrite_payload_references",
]
