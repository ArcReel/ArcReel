"""Before its first artifact target-state plan, the migration chain normalizes shapes written by past normal workflows into shapes current code recognizes (ADR 0098).

v6→v7 script rewriting, artifact target-state planning (steps from v7→v8 onward and archive import),
and v8→v9, v9→v10, v14→v15 used to reject whole projects treating these shapes as data errors.
They were actually written by older code. Normalize deterministically here, retaining the alternative
as a permanent kept copy when choosing between them
(:func:`lib.project.project_migrations.backups.set_aside_as_kept_copy`). Each change enters the
migration report as a :class:`MigrationAdjustment`:

- **Multiple ledger entries for one episode ID**: retain one (a bound script exists on disk, preferring
  the canonical path; otherwise the first entry), removing the others from the ledger. Leave their bound
  script files unchanged on disk and retain the previous ``project.json`` as a kept copy.
- **Different episode IDs bound to the same script** (old ledger synchronization used the script's
  self-declared episode): leave the script with its declared episode, or the earliest ledger episode
  if none matches. Rebind the other episodes to their own canonical paths.
- **Episode binding is not ``scripts/episode_N.json``** (schema < 15; arbitrary scripts registered by old
  SSE index synchronization): move the script to its canonical path and rebind it. If another file already
  occupies that path with different contents, set it aside as a kept copy first. Rewrite that episode's
  script manifest entry, ``execution_script_file`` in version records, and ``script_file`` in grid records
  accordingly, preserving registered artifacts' ownership through rebinding.
- **Missing or mismatched top-level script episode ID** (old synchronization and saving both accepted
  scripts without episode IDs): write the ledger binding's episode ID.
- **Names normalize identically within an asset table** (trim, Unicode NFC, case-insensitive), or normalize
  to empty: keep the first, removing the rest from the table. Leave asset-image files untouched and retain
  the previous ``project.json`` as a kept copy. Merge removed entries' derivatives into the retained entry
  under asset-merge rules (move relocated derivative images under the retained name), and rewrite script
  and draft references to case-only variants to the retained name.
- **Asset images, reference images, or reference audio point to files under another asset's name** (old
  asset PATCH accepted arbitrary paths): copy under this asset's name and repoint, leaving the original
  with its owner. If a different file occupies the destination, set it aside as a kept copy first. Rewrite
  asset-image manifest entries: move entries at the old path to the copy (same bytes, unchanged basis
  digest); remove entries at the destination because they describe the displaced file. These files are
  owned by name; later rename, delete, and restore handle only files under this asset's name.
- **Missing or duplicate script-entry IDs**: assign fresh IDs to missing entries and duplicates after the
  first, using the same numbering rules as new entries. Clear artifact references to the old ID's canonical
  media paths on renumbered entries: those files belong to the entry retaining the original ID. Clear
  generation state describing only those media too, so renumbered entries read as not yet generated.
- **Identical entry IDs in two episodes**: media and entries belong to the episode indicated by the ID
  prefix, or the earliest ledger episode if none matches. Assign a fresh ID within the other episode
  without taking the media along.
- **Old and new draft names coexist** (schema < 10): prefer the new name, moving the old file to a kept copy.

Unreadable ``project.json`` or scripts are not handled here: later migration steps still reject the
whole project."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePath, PurePosixPath
from typing import Any, cast

from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
    normalize_artifact_path,
)
from lib.artifacts.formal_write import formal_write_transaction
from lib.episode.episode_paths import episode_drafts_dir, episode_script_relpath
from lib.infra.json_io import atomic_write_bytes, atomic_write_json
from lib.infra.path_safety import try_safe_join
from lib.project.asset_derivatives import (
    DERIVATIVE_ASSET_TYPE,
    derivative_artifact_id,
    derivative_artifact_key,
    derivative_sheet_dir,
    derivative_sheet_relative_path,
    derivative_table,
    ensure_derivative_table,
)
from lib.project.asset_merge import DerivativeMergePlan, merge_payload_references, plan_derivative_merge
from lib.project.asset_rename import named_file_fields, owns_named_file
from lib.project.asset_types import (
    ASSET_SPECS,
    DERIVATIVES_FIELD,
    AssetSpec,
    asset_name_comparison_key,
    asset_name_namespace_key,
    normalize_asset_name,
    resolve_asset_key,
)
from lib.project.project_migration_report import MigrationAdjustment
from lib.project.project_migrations.backups import ensure_versioned_backup, set_aside_as_kept_copy
from lib.project.project_migrations.v9_to_v10_script_plan_naming import DRAFT_FILE_RENAMES
from lib.project.resource_paths import CHARACTER_DERIVATIVE_RESOURCE_TYPE, resource_relative_path
from lib.script.script_editor import ScriptEditError, new_item_id, resolve_items

logger = logging.getLogger(__name__)

#: Ledger and asset namespaces exist from this version onward; normalize before v6→v7 (which rewrites scripts by ledger episode)
#: and every subsequent artifact target-state plan.
NORMALIZE_FROM_SCHEMA_VERSION = 6
_CANONICAL_BINDING_SCHEMA_VERSION = 15
_DRAFT_RENAME_SCHEMA_VERSION = 10
#: Pre-v9→v10 persisted names: script-plan draft new → old names, plus episode confirmation-record fields.
_LEGACY_DRAFT_NAMES = {new: old for old, new in DRAFT_FILE_RENAMES.items()}
_LEGACY_REVIEW_FIELD = "step1_review"
_DERIVATIVE_SHEET_FIELD = ASSET_SPECS[DERIVATIVE_ASSET_TYPE].sheet_field

#: Entry artifact field → resource type; reference-to-video unit videos live under ``reference_videos``.
_MEDIA_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("storyboard_image", "storyboards", "storyboards"),
    ("video_clip", "videos", "reference_videos"),
    ("narration_audio", "audio", "audio"),
)
#: Generation state describing only a main medium has no subject when that medium is absent.
_MEDIA_COMPANION_FIELDS: dict[str, tuple[str, ...]] = {
    "storyboard_image": ("storyboard_last_image", "grid_id", "grid_cell_index"),
    "video_clip": ("video_thumbnail", "video_uri", "video_generated_at"),
}


@dataclass
class _Work:
    project_dir: Path
    schema_version: int
    project: dict[str, Any]
    #: In-memory ``project`` has unpersisted changes.
    project_changed: bool = False
    #: Persisted normalization items: reruns cannot discover them; record even on mid-process failure. ``project.json`` items come first.
    adjusted: list[MigrationAdjustment] = field(default_factory=list)
    #: ``project.json`` normalization items move to ``adjusted`` only after writing; kept-copy paths become known at the first write.
    project_adjustments: list[MigrationAdjustment] = field(default_factory=list)
    project_recorded: int = 0
    project_committed: bool = False
    project_backup: str | None = None

    def rel(self, path: Path) -> str:
        return path.relative_to(self.project_dir).as_posix()


def _episode_entries(project: dict[str, Any]) -> Iterator[tuple[int, dict[str, Any]]]:
    episodes = project.get("episodes")
    if not isinstance(episodes, list):
        return
    for entry in episodes:
        if not isinstance(entry, dict):
            continue
        episode = entry.get("episode")
        if type(episode) is int and episode >= 1:
            yield episode, entry


def _bound_path(project_dir: Path, binding: object) -> Path | None:
    if not isinstance(binding, str) or not binding:
        return None
    return try_safe_join(project_dir, binding.replace("\\", "/"))


def _load_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _drop_duplicate_episodes(work: _Work) -> None:
    episodes = work.project.get("episodes")
    if not isinstance(episodes, list):
        return
    groups: dict[int, list[dict[str, Any]]] = {}
    for episode, entry in _episode_entries(work.project):
        groups.setdefault(episode, []).append(entry)
    dropped: list[tuple[int, dict[str, Any]]] = []
    for episode, entries in groups.items():
        if len(entries) < 2:
            continue
        canonical = work.project_dir / episode_script_relpath(episode)

        def rank(entry: dict[str, Any], canonical: Path = canonical) -> int:
            path = _bound_path(work.project_dir, entry.get("script_file"))
            if path is None or not path.is_file():
                return 2
            return 0 if path == canonical else 1

        keeper = min(entries, key=rank)
        dropped.extend((episode, entry) for entry in entries if entry is not keeper)
    if not dropped:
        return
    dropped_ids = {id(entry) for _episode, entry in dropped}
    work.project["episodes"] = [entry for entry in episodes if id(entry) not in dropped_ids]
    work.project_changed = True
    for episode, entry in dropped:
        binding = entry.get("script_file")
        work.project_adjustments.append(
            MigrationAdjustment(
                code="duplicate_episode_entry_removed",
                episode=episode,
                file=binding if isinstance(binding, str) and binding else None,
            )
        )


def _split_shared_bindings(work: _Work) -> None:
    """When different episode IDs bind to the same script, leave it with its self-declared episode (or the earliest ledger episode if none matches).
    Rebind others to their own canonical paths; an episode without a file there has no script yet."""

    groups: dict[Path, list[tuple[int, dict[str, Any]]]] = {}
    for episode, entry in _episode_entries(work.project):
        path = _bound_path(work.project_dir, entry.get("script_file"))
        if path is not None:
            groups.setdefault(path, []).append((episode, entry))
    for path, sharers in groups.items():
        if len({episode for episode, _entry in sharers}) < 2:
            continue
        script = _load_object(path) if path.is_file() and not path.is_symlink() else None
        if script is None and path.is_file():
            # Unreadable scripts are outside normalization: preserve bindings; later migration steps still reject the whole project.
            continue
        claimed = script.get("episode") if script is not None else None
        owner = next((sharer for sharer in sharers if sharer[0] == claimed), sharers[0])
        for episode, entry in sharers:
            if episode == owner[0]:
                continue
            raw = entry["script_file"]
            canonical_rel = episode_script_relpath(episode)
            entry["script_file"] = canonical_rel
            work.project_changed = True
            work.project_adjustments.append(
                MigrationAdjustment(
                    code="shared_script_binding_split",
                    episode=episode,
                    file=canonical_rel,
                    params={"from": raw, "to": canonical_rel, "owner": str(owner[0])},
                )
            )


def _correct_script_episodes(work: _Work) -> None:
    """Make top-level script episode IDs match ledger bindings: fill missing IDs and correct mismatches."""

    for episode, entry in _episode_entries(work.project):
        path = _bound_path(work.project_dir, entry.get("script_file"))
        if path is None or not path.is_file() or path.is_symlink():
            continue
        script = _load_object(path)
        if script is None:
            continue
        current = script.get("episode")
        if type(current) is int and current == episode:
            continue
        script["episode"] = episode
        ensure_versioned_backup(path, work.schema_version)
        atomic_write_json(path, script)
        work.adjusted.append(
            MigrationAdjustment(
                code="script_episode_corrected",
                episode=episode,
                file=work.rel(path),
                params={"from": "" if current is None else str(current), "to": str(episode)},
            )
        )


def _commit_project(work: _Work, *, keep_copy: bool = False) -> None:
    """Persist in-memory ``project`` and move its written ``project.json`` normalization items into ``adjusted``.

    Back up before the first write: retain a kept copy (referenced by the report) if normalizations modify
    ``project.json``, otherwise a versioned backup. ``keep_copy`` handles commits before recording the
    normalization item so later items can still refer to the pre-change copy."""

    project_file = work.project_dir / "project.json"
    if not work.project_committed:
        if work.project_adjustments or keep_copy:
            work.project_backup = work.rel(set_aside_as_kept_copy(project_file, work.schema_version, copy=True))
        else:
            ensure_versioned_backup(project_file, work.schema_version)
        work.project_committed = True
    atomic_write_json(project_file, work.project)
    work.project_changed = False
    committed = [
        item.model_copy(update={"backup": work.project_backup})
        for item in work.project_adjustments[work.project_recorded :]
    ]
    work.adjusted[work.project_recorded : work.project_recorded] = committed
    work.project_recorded = len(work.project_adjustments)


@dataclass
class _Rebind:
    """Rebind one episode from a noncanonical binding to its canonical path.

    ``source`` is the script file to move, or ``None`` if absent; ``old_paths`` lists old spellings that
    may reference it in version and grid records."""

    episode: int
    entry: dict[str, Any]
    raw: str
    old_paths: set[str]
    source: Path | None

    @property
    def canonical_rel(self) -> str:
        return episode_script_relpath(self.episode)


def _staged_path(bound: Path, episode: int) -> Path:
    """Temporary script name for cyclic cross-bindings, determined by original binding and episode ID for recognition on interrupted reruns."""

    return bound.with_name(f".{bound.name}.rebind-{episode}")


def _unstaged_path(path: Path, episode: int) -> Path | None:
    """If ``path`` is this episode's cyclic temporary name, return its original binding before the temporary move."""

    prefix, suffix = ".", f".rebind-{episode}"
    if not (path.name.startswith(prefix) and path.name.endswith(suffix)):
        return None
    name = path.name[len(prefix) : -len(suffix)]
    return path.with_name(name) if name else None


def _rewrite_script_references(work: _Work, episode: int, old_paths: set[str], target_rel: str) -> None:
    """Repoint references to this episode's old script in version and grid records to ``target_rel``.

    Write a filename without directories, matching generation tasks and grid requests (grids compare
    script names literally)."""

    target = target_rel.removeprefix("scripts/")

    def points_at_old(value: object) -> bool:
        if not isinstance(value, str) or not value:
            return False
        normalized = value.replace("\\", "/").removeprefix("./")
        return normalized in old_paths or f"scripts/{normalized}" in old_paths

    versions_path = work.project_dir / "versions" / "versions.json"
    versions = _load_object(versions_path) if versions_path.is_file() else None
    if versions is not None:
        changed = False
        for bucket in versions.values():
            if not isinstance(bucket, dict):
                continue
            for resource in bucket.values():
                records = resource.get("versions") if isinstance(resource, dict) else None
                for record in records if isinstance(records, list) else []:
                    if isinstance(record, dict) and points_at_old(record.get("execution_script_file")):
                        record["execution_script_file"] = target
                        changed = True
        if changed:
            ensure_versioned_backup(versions_path, work.schema_version)
            atomic_write_json(versions_path, versions)

    grids_dir = work.project_dir / "grids"
    if grids_dir.is_dir() and not grids_dir.is_symlink():
        for path in sorted(grids_dir.glob("grid_*.json")):
            record = _load_object(path)
            if record is None or record.get("episode") != episode or not points_at_old(record.get("script_file")):
                continue
            record["script_file"] = target
            ensure_versioned_backup(path, work.schema_version)
            atomic_write_json(path, record)


def _rebind_manifest_entry(work: _Work, episode: int, canonical: str) -> None:
    if not (work.project_dir / MANIFEST_FILENAME).is_file():
        return
    adapter = ProjectArtifactManifestAdapter(work.project_dir)
    key = ArtifactKey.episode_script(episode)
    stored = adapter.get_entry(key)
    if stored is None or stored.artifact_path == canonical:
        return
    ensure_versioned_backup(work.project_dir / MANIFEST_FILENAME, work.schema_version)
    replacement = replace(stored, artifact_path=canonical)
    if not adapter.replace_entries_if_matches_atomically(expected={key: stored}, replacements={key: replacement}):
        raise RuntimeError("artifact manifest changed while rebinding an episode script")


def _relocate(work: _Work, rebind: _Rebind, target_rel: str) -> None:
    """Repoint one episode's script references and binding to ``target_rel`` and immediately commit ``project.json``.

    The script file is already in place before this call. Commit after each move: on interrupted reruns,
    rebound episodes use new bindings; unrebound episodes no longer have files at old bindings (or have
    temporary names), so they will not claim scripts moved in from other episodes."""

    _rewrite_script_references(work, rebind.episode, rebind.old_paths, target_rel)
    rebind.entry["script_file"] = target_rel
    work.project_changed = True
    _commit_project(work)
    rebind.old_paths = {target_rel}


def _canonicalize_bindings(work: _Work) -> None:
    """Move scripts to canonical paths and rebind when episode bindings are not ``scripts/episode_N.json``.

    One episode's canonical path may hold another episode's not-yet-moved script (old code bound using
    self-declared IDs, allowing crossed filenames and episode IDs). Move episodes with unoccupied canonical
    paths first; break cycles by moving one script to a temporary name. Files left at canonical paths belong
    to no episode; set them aside as kept copies when contents differ."""

    if work.schema_version >= _CANONICAL_BINDING_SCHEMA_VERSION:
        return
    rebinds: list[_Rebind] = []
    for episode, entry in _episode_entries(work.project):
        raw = entry.get("script_file")
        canonical_rel = episode_script_relpath(episode)
        if not isinstance(raw, str) or not raw or raw == canonical_rel:
            continue
        canonical = work.project_dir / canonical_rel
        bound = _bound_path(work.project_dir, raw)
        old_paths = {raw.replace("\\", "/").removeprefix("./")}
        source: Path | None = None
        if bound is not None:
            old_paths.add(work.rel(bound))
            original = _unstaged_path(bound, episode)
            if original is not None:
                # Previous cyclic run stopped after rebinding to a temporary name: report the original binding.
                raw = work.rel(original)
            staged = _staged_path(bound, episode)
            if staged.is_file():
                # Interrupted at a cycle: this script has a temporary name; any file at its original binding was moved from another episode.
                source = staged
                old_paths.add(work.rel(staged))
            elif bound != canonical and bound.is_file():
                source = bound
        if source is not None and _load_object(source) is None:
            # Unreadable scripts are outside normalization; later steps still reject the whole project. Move no episode scripts now,
            # avoiding displacement to another episode's kept copy or rejection naming a different file from the original.
            return
        rebinds.append(_Rebind(episode=episode, entry=entry, raw=raw, old_paths=old_paths, source=source))

    # Include episodes with no movable file: if another script occupies their canonical path, repoint references after it moves.
    pending = rebinds
    while pending:
        sources = {rebind.source for rebind in pending if rebind.source is not None}
        ready = next((r for r in pending if work.project_dir / r.canonical_rel not in sources), None)
        if ready is None:
            canonicals = {work.project_dir / r.canonical_rel for r in pending}
            blocking = next(r for r in pending if r.source is not None and r.source in canonicals)
            source = cast(Path, blocking.source)
            staged = _staged_path(source, blocking.episode)
            source.replace(staged)
            blocking.source = staged
            _relocate(work, blocking, work.rel(staged))
            continue
        pending.remove(ready)
        backup: str | None = None
        try:
            if ready.source is not None:
                canonical = work.project_dir / ready.canonical_rel
                if canonical.is_file() and canonical.read_bytes() != ready.source.read_bytes():
                    backup = work.rel(set_aside_as_kept_copy(canonical, work.schema_version))
                canonical.parent.mkdir(parents=True, exist_ok=True)
                ready.source.replace(canonical)
            _rebind_manifest_entry(work, ready.episode, ready.canonical_rel)
            _relocate(work, ready, ready.canonical_rel)
        except BaseException:
            if backup is not None:
                # The kept copy is already moved and cannot be rediscovered. Report it even on failure. A rerun finishing this same rebinding
                # records no kept copy; retain only one record for the same change.
                work.adjusted.append(_binding_canonicalized(ready, backup))
            raise
        work.adjusted.append(_binding_canonicalized(ready, backup))


def _binding_canonicalized(rebind: _Rebind, backup: str | None) -> MigrationAdjustment:
    return MigrationAdjustment(
        code="script_binding_canonicalized",
        episode=rebind.episode,
        file=rebind.canonical_rel,
        backup=backup,
        params={"from": rebind.raw, "to": rebind.canonical_rel},
    )


@dataclass(frozen=True)
class _AssetDrop:
    """An asset-table duplicate to remove and its retained entry (``kept`` is empty when the name normalizes to empty)."""

    asset_type: str
    name: str
    kept: str
    derivatives: DerivativeMergePlan

    @property
    def renames_references(self) -> bool:
        """Whether references need rewriting: yes for case-only differences; whitespace/Unicode-only variants already resolve to retained entries via comparison coordinates."""
        return bool(self.kept) and asset_name_comparison_key(self.name) != asset_name_comparison_key(self.kept)

    def params(self) -> dict[str, str]:
        return {"asset_type": self.asset_type, "name": self.name, "kept": self.kept}


def _drop_duplicate_assets(work: _Work) -> None:
    """Keep the first asset with each normalized name; merge the rest into it, moving derivatives and repointing references to the retained name.

    Persistence order converges on reruns after failure: change references, the artifact manifest, and
    version history before committing ``project.json`` with asset removals. A pre-commit failure leaves
    duplicates discoverable; rewritten references and entries do not change again."""

    drops: list[_AssetDrop] = []
    for asset_type, spec in ASSET_SPECS.items():
        bucket = work.project.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        seen: dict[str, str] = {}
        # Retained derivatives include those moved from earlier duplicates; multiple duplicates may merge into one retained entry.
        kept_tables: dict[str, dict[str, Any]] = {}
        for name in list(bucket):
            if not isinstance(name, str):
                continue
            key = asset_name_namespace_key(name)
            if key and key not in seen:
                seen[key] = name
                continue
            kept = seen.get(key, "")
            source_entry, kept_entry = bucket[name], bucket.get(kept)
            derivatives = DerivativeMergePlan()
            if kept and spec.supports_derivatives and isinstance(source_entry, dict) and isinstance(kept_entry, dict):
                kept_table = kept_tables.setdefault(kept, derivative_table(kept_entry))
                derivatives = plan_derivative_merge(
                    source_entry, {DERIVATIVES_FIELD: kept_table}, name, as_derivative=False
                )
                kept_table.update(dict.fromkeys(derivatives.moved))
            drops.append(_AssetDrop(asset_type=asset_type, name=name, kept=kept, derivatives=derivatives))
    if not drops:
        return
    _rewrite_dropped_asset_references(work, drops)
    _carry_merged_derivative_records(work, drops)
    for drop in drops:
        bucket = work.project[ASSET_SPECS[drop.asset_type].bucket_key]
        source_entry = bucket.pop(drop.name)
        work.project_changed = True
        work.project_adjustments.append(MigrationAdjustment(code="duplicate_asset_removed", params=drop.params()))
        source_table = derivative_table(source_entry)
        if drop.derivatives.moved:
            kept_table = ensure_derivative_table(bucket[drop.kept])
            for derivative in drop.derivatives.moved:
                kept_table[derivative] = source_table[derivative]
        for derivative in source_table:
            if derivative not in drop.derivatives.moved and derivative not in drop.derivatives.folded:
                continue
            into = drop.derivatives.kept_derivative(asset_name_comparison_key(derivative))
            params = {"name": drop.name, "kept": drop.kept, "derivative": derivative, "into": into}
            if derivative in drop.derivatives.folded:
                params["folded"] = "true"
            work.project_adjustments.append(
                MigrationAdjustment(code="duplicate_asset_derivative_merged", params=params)
            )


def _rewrite_dropped_asset_references(work: _Work, drops: list[_AssetDrop]) -> None:
    """Repoint script and draft references to removed assets to the retained name, using asset-merge rules (merge into the base).

    When rewriting a confirmed formal script plan, shift its confirmation fingerprint too, committing it
    before the draft. Failure after draft rewriting but before shifting confirmation would prevent reruns
    from recognizing that confirmation. Pre-v10 projects recognize plans via historical draft names and
    confirmation fields; without shifting confirmation, v14→v15 will not produce formal scripts for episodes
    with only confirmed plans either."""

    renaming = [drop for drop in drops if drop.renames_references]
    if not renaming:
        return
    # script_review indirectly depends on this migration chain through project_manager; lazy import breaks the cycle.
    from lib.script.script_review import (
        carry_confirmation_through_migration,
        content_fingerprint_of_data,
        script_plan_path,
    )

    changed: list[tuple[int, Path, dict[str, Any], str | None, list[int]]] = []
    scanned: set[Path] = set()
    for episode, entry in _episode_entries(work.project):
        paths = [_bound_path(work.project_dir, entry.get("script_file"))]
        drafts_dir = episode_drafts_dir(work.project_dir, episode)
        if drafts_dir.is_dir() and not drafts_dir.is_symlink():
            paths.extend(sorted(drafts_dir.glob("*.json")))
        for path in paths:
            if path is None or path in scanned or path.is_symlink() or not path.is_file():
                continue
            scanned.add(path)
            payload = _load_object(path)
            if payload is None:
                continue
            before = content_fingerprint_of_data(payload)
            hits = [
                index
                for index, drop in enumerate(renaming)
                if merge_payload_references(
                    payload,
                    drop.asset_type,
                    drop.name,
                    asset_name_comparison_key(drop.kept),
                    derivatives=drop.derivatives,
                ).total
            ]
            if not hits:
                continue
            is_plan = path.parent == drafts_dir
            changed.append((episode, path, payload, before if is_plan else None, hits))

    legacy_names = work.schema_version < _DRAFT_RENAME_SCHEMA_VERSION
    carried = False
    for episode, path, payload, before, _hits in changed:
        plan_path = script_plan_path(work.project_dir, work.project, episode) if before is not None else None
        if plan_path is not None and legacy_names:
            plan_path = plan_path.with_name(_LEGACY_DRAFT_NAMES.get(plan_path.name, plan_path.name))
        if before is None or plan_path != path:
            continue
        after = content_fingerprint_of_data(payload)
        if not legacy_names:
            carried |= carry_confirmation_through_migration(work.project, episode, before, after)
            continue
        entry = next(entry for number, entry in _episode_entries(work.project) if number == episode)
        review = entry.get(_LEGACY_REVIEW_FIELD)
        if isinstance(review, dict) and review.get("fingerprint") == before:
            review["fingerprint"] = after
            carried = True
    if carried:
        work.project_changed = True
        _commit_project(work, keep_copy=True)
    # Record each file rewrite immediately: failures leave rewritten episodes undiscoverable on rerun, requiring these records for retry.
    recorded: set[tuple[int, int]] = set()
    for episode, path, payload, _before, hits in changed:
        ensure_versioned_backup(path, work.schema_version)
        atomic_write_json(path, payload)
        for index in hits:
            if (index, episode) in recorded:
                continue
            recorded.add((index, episode))
            work.adjusted.append(
                MigrationAdjustment(
                    code="duplicate_asset_references_rewritten", episode=episode, params=renaming[index].params()
                )
            )


def _carry_merged_derivative_records(work: _Work, drops: list[_AssetDrop]) -> None:
    """Derivatives merged into retained entries carry images, manifest entries, and version history under asset-merge derivative rules.

    - Relocated derivatives: move images from removed entries' canonical paths under the retained names,
      updating paths in entries. Change manifest keys and paths without changing basis digests; change
      version-history keys. Without image moves, later derivative/base renames move images by canonical
      paths but paths in entries do not match, leaving dangling references.
    - Derivatives merged into existing derivatives: remove manifest entries and version history. Remaining
      entries occupy the retained derivative's registration slot using case-insensitive paths. Their images
      and version snapshot files remain on disk.

    Preserve entries/history already at new keys. Reruns similarly converge because old keys are absent
    and images are already at new paths."""

    # (removed entry name, retained name, derivative name, manifest paths to rewrite) and (removed entry name, derivative name).
    moved: list[tuple[str, str, str, dict[str, str]]] = []
    retired: list[tuple[str, str]] = []
    for drop in drops:
        source_table = derivative_table(work.project[ASSET_SPECS[drop.asset_type].bucket_key][drop.name])
        for name in drop.derivatives.moved:
            path_rewrites = _relocate_derivative_sheet(work, source_table[name], drop.name, drop.kept, name)
            moved.append((drop.name, drop.kept, name, path_rewrites))
        retired.extend((drop.name, name) for name in drop.derivatives.folded)
    if not moved and not retired:
        return
    if (work.project_dir / MANIFEST_FILENAME).is_file():
        adapter = ProjectArtifactManifestAdapter(work.project_dir)
        for owner, kept, name, path_rewrites in moved:
            old_key, new_key = derivative_artifact_key(owner, name), derivative_artifact_key(kept, name)
            stored = adapter.get_entry(old_key)
            if stored is None or adapter.get_entry(new_key) is not None:
                continue
            ensure_versioned_backup(work.project_dir / MANIFEST_FILENAME, work.schema_version)
            claim = replace(stored, artifact_path=path_rewrites.get(stored.artifact_path, stored.artifact_path))
            if not adapter.replace_entries_if_matches_atomically(
                expected={old_key: stored, new_key: None}, replacements={old_key: None, new_key: claim}
            ):
                raise RuntimeError("artifact manifest changed while moving a derivative claim")
        for owner, name in retired:
            old_key = derivative_artifact_key(owner, name)
            stored = adapter.get_entry(old_key)
            if stored is None:
                continue
            ensure_versioned_backup(work.project_dir / MANIFEST_FILENAME, work.schema_version)
            if not adapter.replace_entries_if_matches_atomically(
                expected={old_key: stored}, replacements={old_key: None}
            ):
                raise RuntimeError("artifact manifest changed while retiring a derivative claim")
    versions_path = work.project_dir / "versions" / "versions.json"
    versions = _load_object(versions_path) if versions_path.is_file() else None
    history = versions.get(CHARACTER_DERIVATIVE_RESOURCE_TYPE) if versions is not None else None
    if not isinstance(history, dict):
        return
    changed = False
    for owner, kept, name, _path_rewrites in moved:
        old_key = resolve_asset_key(history, derivative_artifact_id(owner, name))
        new_id = derivative_artifact_id(kept, name)
        if old_key is None or resolve_asset_key(history, new_id) is not None:
            continue
        history[new_id] = history.pop(old_key)
        changed = True
    for owner, name in retired:
        old_key = resolve_asset_key(history, derivative_artifact_id(owner, name))
        if old_key is not None:
            del history[old_key]
            changed = True
    if changed:
        ensure_versioned_backup(versions_path, work.schema_version)
        atomic_write_json(versions_path, versions)


def _relocate_derivative_sheet(work: _Work, derivative: object, owner: str, kept: str, name: str) -> dict[str, str]:
    """Move a relocated derivative image from its canonical path under ``owner`` to ``kept``, updating its entry path; return paths to rewrite in the manifest.

    Move only images pointing to old canonical paths. On case-insensitive filesystems the paths may name
    the same file, so only rewrite the path; likewise when the image is absent, matching derivative rename.
    If another file occupies the new path, do not move, retaining the old path."""

    if not isinstance(derivative, dict):
        return {}
    old_rel = derivative_sheet_relative_path(owner, name)
    new_rel = derivative_sheet_relative_path(kept, name)
    value = derivative.get(_DERIVATIVE_SHEET_FIELD)
    if (
        old_rel == new_rel
        or not isinstance(value, str)
        or normalize_asset_name(value.replace("\\", "/")) != normalize_asset_name(old_rel)
    ):
        return {}
    source, target = work.project_dir / old_rel, work.project_dir / new_rel
    if source.is_file() and not (target.exists() and target.samefile(source)):
        if target.exists():
            return {}
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source, target)
        # Container directory only: nonempty or undeletable directories affect no entries.
        with contextlib.suppress(OSError):
            source.parent.rmdir()  # hard-delete: infra -- empty directories after migration relocation
    derivative[_DERIVATIVE_SHEET_FIELD] = new_rel
    return {normalize_artifact_path(value): normalize_artifact_path(new_rel)}


#: Reference field → destination under its own asset (subdirectory of the asset-type directory); sequences use ``名字_{序号}``. Sheets live directly in type directories.
_REFERENCE_FILE_SUBDIRS = {"reference_image": "refs", "reference_audio": "refs_audio", "reference_images": "refs"}


def normalize_shared_reference_files(project_dir: Path, *, schema_version: int) -> list[MigrationAdjustment]:
    """Normalize only shared asset images and reference files, allowing the asset-namespace migration to split references early in its staging tree."""
    project = _load_object(project_dir / "project.json")
    if project is None:
        return []
    work = _Work(project_dir=project_dir, schema_version=schema_version, project=project)
    _copy_shared_reference_files(work)
    return work.adjusted


def _named_file_fields(work: _Work) -> Iterator[tuple[str, str, dict[str, Any], str]]:
    for asset_type, spec in ASSET_SPECS.items():
        bucket = work.project.get(spec.bucket_key)
        if not isinstance(bucket, dict):
            continue
        for name, entry in bucket.items():
            # Copy destinations build filenames from asset names. Imports migrate before asset-name validation; separators could write copies outside the directory.
            if not isinstance(name, str) or not isinstance(entry, dict) or "/" in name or "\\" in name:
                continue
            for field_name in named_file_fields(spec):
                yield asset_type, name, entry, field_name


def _copy_shared_reference_files(work: _Work) -> None:
    """Copy asset images and reference files under their own assets; freeze source bytes for mutual references and restore sources plus manifest on reference-commit failure for retry."""
    fields = list(_named_file_fields(work))
    sources: dict[str, Path] = {}
    for asset_type, name, entry, field_name in fields:
        value = entry.get(field_name)
        for item in value if isinstance(value, list) else [value]:
            if not isinstance(item, str) or not item:
                continue
            rel = _canonical_reference(work.project_dir, item)
            if owns_named_file(ASSET_SPECS[asset_type], name, rel) or _named_file_owner(work.project, rel) is None:
                continue
            if field_name == "reference_audio" and PurePosixPath(rel).parent != PurePosixPath("characters/refs_audio"):
                continue
            path = PurePosixPath(rel)
            if any(
                (work.project_dir / part).is_symlink() for part in (path, *path.parents) if part != PurePosixPath(".")
            ):
                continue
            source = try_safe_join(work.project_dir, rel)
            if source is not None and source.is_file():
                sources[rel] = source
    content = {rel: source.read_bytes() for rel, source in sources.items()}
    before = len(work.adjusted)
    #: Repointed asset-image copies: (asset type, name, old value, copy path).
    sheets: list[tuple[str, str, str, str]] = []
    manifest = work.project_dir / MANIFEST_FILENAME
    try:
        with (
            formal_write_transaction(work.project_dir / "project.json", manifest, *sources.values()),
            contextlib.ExitStack() as copies,
        ):
            for asset_type, name, entry, field_name in fields:
                value = entry.get(field_name)
                if isinstance(value, list):
                    for index, item in enumerate(value):
                        copied = _own_reference_copy(
                            work, asset_type, name, field_name, item, content, copies, siblings=value
                        )
                        if copied is not None:
                            value[index] = copied
                elif (
                    copied := _own_reference_copy(work, asset_type, name, field_name, value, content, copies)
                ) is not None:
                    entry[field_name] = copied
                    if field_name == ASSET_SPECS[asset_type].sheet_field:
                        sheets.append((asset_type, name, cast(str, value), copied))
            if len(work.adjusted) > before:
                _carry_sheet_claims(work, sheets)
                _commit_project(work)
    except BaseException:
        del work.adjusted[before:]
        raise


def _carry_sheet_claims(work: _Work, sheets: list[tuple[str, str, str, str]]) -> None:
    """Asset images repointed to copies carry their manifest entries, preserving currency.

    Pre-repoint currency depends on whether the entry is at the old value's path: if so, move it to the
    copy with unchanged basis digest (identical bytes preserve current status). Otherwise it already reads
    as missing: destination entries describe the image displaced to a kept copy, so remove them rather than
    falsely marking the copy current. Mutual sharing may cross entries at each other's paths; rewriting
    individually would let two keys temporarily register the same path, so commit the whole set atomically."""

    if not sheets or not (work.project_dir / MANIFEST_FILENAME).is_file():
        return
    adapter = ProjectArtifactManifestAdapter(work.project_dir)
    expected: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
    replacements: dict[ArtifactKey, ArtifactManifestEntry | None] = {}
    for asset_type, name, old_value, copied in sheets:
        identity = asset_name_comparison_key(name)
        if not identity:
            continue
        key = ArtifactKey.asset_sheet(asset_type, identity)
        stored = adapter.get_entry(key)
        if stored is None:
            continue
        try:
            declared = normalize_artifact_path(old_value)
        except ValueError:
            declared = None
        if stored.artifact_path == declared:
            replacement: ArtifactManifestEntry | None = replace(stored, artifact_path=normalize_artifact_path(copied))
        elif stored.artifact_path == normalize_artifact_path(copied):
            replacement = None
        else:
            continue
        expected[key] = stored
        replacements[key] = replacement
    if not replacements:
        return
    ensure_versioned_backup(work.project_dir / MANIFEST_FILENAME, work.schema_version)
    if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=replacements):
        raise RuntimeError("artifact manifest changed while carrying an asset sheet claim")


def _own_reference_copy(
    work: _Work,
    asset_type: str,
    name: str,
    field_name: str,
    value: object,
    sources: dict[str, bytes],
    copies: contextlib.ExitStack,
    *,
    siblings: list[Any] | None = None,
) -> str | None:
    """Copy *value* under *name* if it belongs to another asset; return the new path, or ``None`` when no copy is needed."""

    spec = ASSET_SPECS[asset_type]
    if not isinstance(value, str) or not value:
        return None
    rel = _canonical_reference(work.project_dir, value)
    if owns_named_file(spec, name, rel):
        return None
    owner = _named_file_owner(work.project, rel)
    target_dir = PurePosixPath(spec.subdir)
    if field_name != spec.sheet_field:
        target_dir /= _REFERENCE_FILE_SUBDIRS[field_name]
    # Reference audio recognizes only character reference-audio directories; other values are already ineffective and copying would activate them.
    if owner is None or (field_name == "reference_audio" and PurePosixPath(rel).parent != target_dir):
        return None
    content = sources.get(rel)
    if content is None:
        return None
    if any(
        (work.project_dir / directory).is_symlink()
        for directory in (target_dir, *target_dir.parents)
        if directory != PurePosixPath(".")
    ):
        raise ValueError(f"asset copy directory is a symlink: {target_dir}")
    stem, suffix = normalize_asset_name(name), PurePosixPath(rel).suffix
    backup: str | None = None
    if siblings is None:
        target_rel = (target_dir / f"{stem}{suffix}").as_posix()
        target = work.project_dir / target_rel
        copies.enter_context(formal_write_transaction(target))
        if target.is_symlink() or (target.exists() and not (target.is_file() and target.read_bytes() == content)):
            backup = work.rel(copies.enter_context(_displaced_copy(target, work.schema_version)))
    else:
        target_rel = _sequenced_copy_target(work, spec, target_dir, stem, suffix, content, siblings)
        target = work.project_dir / target_rel
        copies.enter_context(formal_write_transaction(target))
    work.adjusted.append(
        MigrationAdjustment(
            code="shared_reference_file_copied",
            file=target_rel,
            backup=backup,
            params={"asset_type": asset_type, "name": name, "owner": owner[1], "from": rel},
        )
    )
    if not target.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, content)
    work.project_changed = True
    return target_rel


@contextlib.contextmanager
def _displaced_copy(target: Path, schema_version: int) -> Generator[Path]:
    """Retain a displaced file; restore it on outer commit failure to avoid accumulating duplicate kept copies on retries."""
    backup = set_aside_as_kept_copy(target, schema_version)
    try:
        yield backup
    except BaseException:
        backup.replace(target)
        raise


def _sequenced_copy_target(
    work: _Work,
    spec: AssetSpec,
    target_dir: PurePosixPath,
    stem: str,
    suffix: str,
    content: bytes,
    siblings: list[Any],
) -> str:
    """Destination for a new multi-image copy: reuse an unregistered own-asset file with identical bytes (rerun convergence), otherwise the lowest free sequence number."""

    listed = {item.replace("\\", "/") for item in siblings if isinstance(item, str)}
    directory = work.project_dir.joinpath(*target_dir.parts)
    taken: set[str] = set()
    for existing in sorted(directory.iterdir()) if directory.is_dir() else ():
        existing_rel = (target_dir / existing.name).as_posix()
        if not owns_named_file(spec, stem, existing_rel):
            continue
        taken.add(normalize_asset_name(existing.stem))
        if (
            existing_rel not in listed
            and existing.suffix == suffix
            and not existing.is_symlink()
            and existing.is_file()
            and existing.read_bytes() == content
        ):
            return existing_rel
    sequence = 1
    while f"{stem}_{sequence}" in taken:
        sequence += 1
    return (target_dir / f"{stem}_{sequence}{suffix}").as_posix()


def _canonical_reference(project_dir: Path, value: str) -> str:
    """Project-relative spelling of a reference-file field value.

    Old PATCH accepted arbitrary strings unchanged; runtime resolves their locations inside the project:
    ``refs/../refs/名.png``, ``./``, backslashes, and absolute paths inside the project normalize to one
    spelling so ownership checks recognize them. Values outside the project remain unchanged and belong
    to no asset."""

    # Join using runtime-platform path semantics: ``C:/…`` is recognized as absolute only on Windows.
    joined = os.path.normpath(os.path.join(project_dir, value.replace("\\", "/")))
    for base in dict.fromkeys((os.path.normpath(project_dir), os.path.realpath(project_dir))):
        if joined.startswith(base + os.sep):
            return PurePath(joined[len(base) + 1 :]).as_posix()
    return value


def _named_file_owner(project: dict[str, Any], rel: str) -> tuple[str, str] | None:
    """Asset (type, name) owning *rel* by name, or ``None`` if it is under no asset name."""

    for asset_type, spec in ASSET_SPECS.items():
        bucket = project.get(spec.bucket_key)
        for name in bucket if isinstance(bucket, dict) else ():
            if not isinstance(name, str):
                continue
            if owns_named_file(spec, name, rel) or (
                spec.supports_derivatives and PurePosixPath(rel).parent == PurePosixPath(derivative_sheet_dir(name))
            ):
                return asset_type, name
    return None


#: Episode prefix in entry IDs (same shape as ``script_editor``, including ``_N`` split suffixes).
_ITEM_ID_EPISODE = re.compile(r"\AE(\d+)[SU]\d+(?:_\d+)?\Z")
#: IDs allocated by ``new_item_id``: episode ID, letter, sequence number.
_ITEM_ID_PARTS = re.compile(r"\AE(\d+)([SU])(\d+)\Z")


def _id_owner_episode(item_id: str) -> int | None:
    match = _ITEM_ID_EPISODE.match(item_id)
    return int(match.group(1)) if match else None


def _fresh_item_id(script: dict[str, Any], taken: set[str]) -> str:
    """Allocate an ID using new-entry numbering rules, avoiding IDs already used in other episodes."""

    candidate = new_item_id(script)
    match = _ITEM_ID_PARTS.match(candidate)
    if match is None:
        return candidate
    prefix, number = f"E{match.group(1)}{match.group(2)}", int(match.group(3))
    width = len(match.group(3)) if match.group(2) == "S" else 0
    while candidate in taken:
        number += 1
        candidate = f"{prefix}{number:0{width}d}"
    return candidate


def _repair_item_ids(work: _Work) -> None:
    """Renumber entries with missing/duplicate IDs within scripts or IDs shared with another episode.

    Artifact paths depend only on entry IDs, so equal IDs in two episodes claim the same media file.
    Media and registration belong to the episode indicated by ID prefix ``E<n>``, or the earliest ledger
    episode if neither matches. The other episode gets a fresh local ID without taking media along."""

    scripts: list[tuple[int, Path, dict[str, Any], list[Any], str, str]] = []
    for episode, entry in _episode_entries(work.project):
        path = _bound_path(work.project_dir, entry.get("script_file"))
        if path is None or not path.is_file() or path.is_symlink():
            continue
        script = _load_object(path)
        if script is None:
            continue
        try:
            items, id_field, kind = resolve_items(script)
        except ScriptEditError:
            continue
        scripts.append((episode, path, script, items, id_field, kind))

    holders: dict[str, list[int]] = {}
    for episode, _path, _script, items, id_field, _kind in scripts:
        for item in items:
            current = item.get(id_field) if isinstance(item, dict) else None
            if isinstance(current, str) and current and episode not in holders.setdefault(current, []):
                holders[current].append(episode)
    owners: dict[str, int] = {}
    for item_id, episodes in holders.items():
        if len(episodes) > 1:
            prefixed = _id_owner_episode(item_id)
            owners[item_id] = prefixed if prefixed in episodes else episodes[0]
    taken = set(holders)

    for episode, path, script, items, id_field, kind in scripts:
        seen: set[str] = set()
        changes: list[tuple[str, str]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            current = item.get(id_field)
            foreign = isinstance(current, str) and owners.get(current, episode) != episode
            if isinstance(current, str) and current and current not in seen and not foreign:
                seen.add(current)
                continue
            old_id = current if isinstance(current, str) else ""
            # Clear invalid IDs before allocation, which reads existing entries; invalid IDs must not advance the sequence.
            item.pop(id_field, None)
            new_id = _fresh_item_id(script, taken)
            item[id_field] = new_id
            seen.add(new_id)
            taken.add(new_id)
            if old_id:
                _release_media_of(item, old_id, kind)
            changes.append((old_id, new_id))
        if not changes:
            continue
        ensure_versioned_backup(path, work.schema_version)
        atomic_write_json(path, script)
        for old_id, new_id in changes:
            work.adjusted.append(
                MigrationAdjustment(
                    code="item_id_reassigned",
                    episode=episode,
                    file=work.rel(path),
                    params={"from": old_id, "to": new_id},
                )
            )


def _release_media_of(item: dict[str, Any], old_id: str, kind: str) -> None:
    """Renumbered entries release canonical media for the old ID and generation state describing only those media, reading as not yet generated.

    Thumbnails, video URIs, and generation times follow videos; end-frame snapshots and grid membership
    follow storyboard images. Without main media these describe the original ID's artifacts and are cleared
    together. Media references elsewhere are not the old ID's canonical paths and remain with their related
    state. Recompute persisted ``status`` from remaining media, matching ``ProjectManager.update_scene_status``."""

    if item.get("end_frame_image") == resource_relative_path("end_frames", old_id):
        item.pop("end_frame_image")
    assets = item.get("generated_assets")
    if not isinstance(assets, dict):
        return
    for field_name, storyboard_type, unit_type in _MEDIA_FIELDS:
        resource_type = unit_type if kind == "video_units" else storyboard_type
        if assets.get(field_name) == resource_relative_path(resource_type, old_id):
            assets[field_name] = None
    for field_name, companions in _MEDIA_COMPANION_FIELDS.items():
        if not assets.get(field_name):
            for companion in companions:
                if assets.get(companion) is not None:
                    assets[companion] = None
    if "status" in assets:
        if assets.get("video_clip"):
            assets["status"] = "completed"
        elif assets.get("storyboard_image"):
            assets["status"] = "storyboard_ready"
        else:
            assets["status"] = "pending"


def _resolve_draft_name_conflicts(work: _Work) -> None:
    if work.schema_version >= _DRAFT_RENAME_SCHEMA_VERSION:
        return
    for episode, _entry in _episode_entries(work.project):
        drafts_dir = episode_drafts_dir(work.project_dir, episode)
        if not drafts_dir.is_dir() or drafts_dir.is_symlink():
            continue
        for old_name, new_name in DRAFT_FILE_RENAMES.items():
            old, new = drafts_dir / old_name, drafts_dir / new_name
            if old.is_symlink() or new.is_symlink() or not (old.is_file() and new.is_file()):
                continue
            kept = set_aside_as_kept_copy(old, work.schema_version)
            work.adjusted.append(
                MigrationAdjustment(
                    code="draft_name_conflict_resolved",
                    episode=episode,
                    file=work.rel(new),
                    backup=work.rel(kept),
                )
            )


def _adjustment_identity(item: MigrationAdjustment) -> str:
    return item.model_dump_json(exclude={"backup"})


def normalize_legacy_shapes(project_dir: Path, *, schema_version: int, record: list[MigrationAdjustment]) -> None:
    """Normalize legacy shapes in ``project_dir``, appending changes to ``record``; do not write when nothing needs changing.

    On mid-process failure, still append already-persisted changes to ``record``: reruns cannot rediscover
    them, so unfinished reports must carry them to retries. Changes to ``project.json`` count as persisted
    only after writing it."""

    project_file = project_dir / "project.json"
    project = _load_object(project_file)
    if project is None:
        return
    work = _Work(project_dir=project_dir, schema_version=schema_version, project=project)
    try:
        _drop_duplicate_episodes(work)
        _split_shared_bindings(work)
        # Deduplicate assets before rebinding: normalization items are pending at the first project.json commit, creating a kept copy
        # covering all project.json normalizations. Confirmation-fingerprint shifts commit first themselves, also retaining a kept copy.
        _drop_duplicate_assets(work)
        _copy_shared_reference_files(work)
        _canonicalize_bindings(work)
        _correct_script_episodes(work)
        _repair_item_ids(work)
        _resolve_draft_name_conflicts(work)
        if work.project_changed:
            _commit_project(work)
    finally:
        # Reruns may rediscover changes recorded before an earlier failure (e.g. uncommitted rebinding). Record each change once,
        # retaining the first record with the kept copy displaced at that time.
        recorded = {_adjustment_identity(item) for item in record}
        record.extend(item for item in work.adjusted if _adjustment_identity(item) not in recorded)
    if work.adjusted:
        logger.info("规整旧形态 %s：%d 处", project_dir.name, len(work.adjusted))


__all__ = ["NORMALIZE_FROM_SCHEMA_VERSION", "normalize_legacy_shapes", "normalize_shared_reference_files"]
