"""v18→v19: record episode item-ID high-water marks and rebase storyboard/grid basis without asset names.

IDs (``docs/adr/0090``, allocation only): earlier new items avoided live script IDs only, reusing deleted
tail IDs. Find episode maximum primary numbers across five sources (scripts/history/manifests/trash
records/timeline clips), storing ``item_id_high_water`` in ``project.json`` ledger entries. Future IDs
advance beyond it; existing IDs/files remain unchanged.

Basis: storyboard/grid/split-image model inputs exclude asset names, so v19 basis does too
(``project_basis_records_asset_names``), avoiding staleness on asset-only renames. Plan v18/v19
states and rewrite only previously current entries with changed target registrations, preserving
currency through upgrades and never making already-stale entries current. Frozen version bases
contain full inputs; rewrite individually with the same ``without_asset_names`` constructor function,
keeping playback/restoration consistent with manifests.

Commit history, manifests, then ``project.json``. Crash retries converge: history rewrites are idempotent;
before-plans still derive from untouched ``project.json``, while rewritten entries no longer match old
targets and are skipped.

Trash registrations contain digests only and are not rewritten. Storyboards/grids deleted before upgrade
and restored afterward read stale if they referenced assets."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactBasis,
    ArtifactKey,
    ArtifactKind,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_planner import ArtifactTargetStatePlan, TargetStatePlanner
from lib.artifacts.artifact_version_provenance import IMAGE_ARTIFACT_BASIS_FIELD
from lib.artifacts.formal_write import project_metadata_lock
from lib.artifacts.visual_artifact_provenance import ASSET_NAME_FREE_IMAGE_BASIS_KINDS, without_asset_names
from lib.infra.json_io import atomic_write_bytes, atomic_write_json
from lib.project.item_id_history import max_used_item_numbers
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.backups import ensure_versioned_backup
from lib.project.project_schema import parse_project_schema_version
from lib.script.item_ids import raise_item_id_high_water

TARGET_SCHEMA_VERSION = 19

#: Artifact kinds whose basis omits asset names (split images/single storyboards share image keys).
_NAME_FREE_KINDS = frozenset({ArtifactKind.EPISODE_STORYBOARD, ArtifactKind.EPISODE_GRID})
#: History buckets whose frozen bases may contain asset names.
_NAME_FREE_VERSION_BUCKETS = ("storyboards", "grids")


def migrate_project_dict(project: Mapping[str, Any], used: Mapping[int, int]) -> dict[str, Any]:
    """Pure, idempotent high-water updates from ``used`` (``{集 ID: 用过的最大主序号}``), increasing episode ledger maxima only."""

    migrated = dict(project)
    episodes = migrated.get("episodes")
    if not isinstance(episodes, list):
        return migrated
    entries: list[Any] = []
    for entry in episodes:
        if isinstance(entry, dict) and isinstance(episode := entry.get("episode"), int) and episode in used:
            entry = dict(entry)
            raise_item_id_high_water(entry, used[episode])
        entries.append(entry)
    migrated["episodes"] = entries
    return migrated


def without_asset_names_in_versions(versions: dict[str, Any]) -> bool:
    """Rewrite frozen storyboard/grid version bases without asset names in place, returning whether changed.

    This represents the same inputs differently without judging current-project freshness. Already-stale
    versions remain evaluated against frozen inputs. Preserve unreadable/digest-mismatched/excluded-kind bases."""

    changed = False
    for bucket_name in _NAME_FREE_VERSION_BUCKETS:
        bucket = versions.get(bucket_name)
        if not isinstance(bucket, Mapping):
            continue
        for resource in bucket.values():
            records = resource.get("versions") if isinstance(resource, Mapping) else None
            if not isinstance(records, list):
                continue
            for record in records:
                if not isinstance(record, dict):
                    continue
                try:
                    frozen = ArtifactBasis.from_evidence_dict(record.get(IMAGE_ARTIFACT_BASIS_FIELD))
                except (TypeError, ValueError):
                    continue
                if frozen.kind not in ASSET_NAME_FREE_IMAGE_BASIS_KINDS:
                    continue
                inputs = frozen.to_evidence_dict()["inputs"]
                if not isinstance(inputs, Mapping):
                    continue
                try:
                    rewritten = ArtifactBasis.build(
                        frozen.kind,
                        kind_version=frozen.kind_version,
                        inputs=without_asset_names(frozen.kind, inputs),
                    )
                except (TypeError, ValueError):
                    continue
                if rewritten.digest != frozen.digest:
                    record[IMAGE_ARTIFACT_BASIS_FIELD] = rewritten.to_evidence_dict()
                    changed = True
    return changed


def _plan(project_dir: Path, project: Mapping[str, Any]) -> ArtifactTargetStatePlan:
    project_bytes = json.dumps(project, ensure_ascii=False).encode("utf-8")
    return TargetStatePlanner(project_dir, project_bytes=project_bytes).plan()


def _rebase_entries(
    stored: Mapping[ArtifactKey, ArtifactManifestEntry],
    before: Mapping[ArtifactKey, ArtifactManifestEntry],
    after: Mapping[ArtifactKey, ArtifactManifestEntry],
) -> dict[ArtifactKey, ArtifactManifestEntry]:
    """Select previously current storyboard/grid entries whose targets changed; different paths mean different artifacts."""

    rebased: dict[ArtifactKey, ArtifactManifestEntry] = {}
    for key, current in stored.items():
        if key.kind not in _NAME_FREE_KINDS:
            continue
        target_after = after.get(key)
        if target_after is None or current == target_after or target_after.artifact_path != current.artifact_path:
            continue
        if current == before.get(key):
            rebased[key] = target_after
    return rebased


def _with_versions_bytes(plan: ArtifactTargetStatePlan, versions_path: Path, raw: bytes) -> ArtifactTargetStatePlan:
    """History is a planning dependency; after this step rewrites it, stability gates must compare rewritten bytes."""

    resolved = versions_path.resolve()
    return replace(
        plan,
        dependency_bytes={
            path: (raw if path.resolve() == resolved else content) for path, content in plan.dependency_bytes.items()
        },
    )


def _rebase_name_free_bases(
    project_dir: Path, project: Mapping[str, Any], migrated: Mapping[str, Any], project_bytes: bytes
) -> ArtifactBackfillOutcome | None:
    """Rewrite frozen bases and previously current registrations; callers hold project metadata locks."""

    versions_path = project_dir / "versions" / "versions.json"
    try:
        versions_bytes: bytes | None = versions_path.read_bytes()
    except FileNotFoundError:
        versions_bytes = None
    versions = json.loads(versions_bytes) if versions_bytes is not None else None
    versions_changed = isinstance(versions, dict) and without_asset_names_in_versions(versions)

    adapter = ProjectArtifactManifestAdapter(project_dir)
    stored = adapter.snapshot_entries()
    rebased: dict[ArtifactKey, ArtifactManifestEntry] = {}
    plans: list[ArtifactTargetStatePlan] = []
    if any(key.kind in _NAME_FREE_KINDS for key in stored):
        plans = [_plan(project_dir, project), _plan(project_dir, migrated)]
        for plan in plans:
            assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
        rebased = _rebase_entries(stored, plans[0].entries, plans[1].entries)

    if versions_changed:
        ensure_versioned_backup(versions_path, TARGET_SCHEMA_VERSION - 1)
        atomic_write_json(versions_path, versions)
        rewritten_bytes = versions_path.read_bytes()
        plans = [_with_versions_bytes(plan, versions_path, rewritten_bytes) for plan in plans]
    if not rebased:
        return None
    expected = {key: stored[key] for key in rebased}

    def restore_versions() -> None:
        if versions_changed and versions_bytes is not None:
            atomic_write_bytes(versions_path, versions_bytes)

    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, TARGET_SCHEMA_VERSION - 1)
    try:
        if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=rebased):
            raise RuntimeError("artifact manifest changed while rebasing storyboard and grid bases")
    except BaseException:
        restore_versions()
        raise
    try:
        for plan in plans:
            assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
    except BaseException as original_error:
        restored = adapter.replace_entries_if_matches_atomically(expected=rebased, replacements=expected)
        if not restored and any(adapter.get_entry(key) != entry for key, entry in expected.items()):
            # Incomplete manifest rollback retains rewritten history too, keeping both on new semantics for convergent retries.
            raise RuntimeError("artifact manifest dependency drifted and rollback was incomplete") from original_error
        restore_versions()
        raise
    return ArtifactBackfillOutcome.from_entries(adapter.snapshot_entries(), preserve_previous_skips=True)


def migrate_v18_to_v19(project_dir: Path) -> ArtifactBackfillOutcome | None:
    """File-level v18→v19 migration, retryable after crashes."""

    project_dir = Path(project_dir)
    project_file = project_dir / "project.json"
    if not project_file.is_file():
        return None
    with project_metadata_lock(project_dir):
        project_bytes = project_file.read_bytes()
        project = json.loads(project_bytes)
        if not isinstance(project, dict):
            raise ValueError("project.json 必须是对象")
        if parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
            return None
        used = max_used_item_numbers(project_dir)
        migrated = {**migrate_project_dict(project, used), "schema_version": TARGET_SCHEMA_VERSION}
        outcome = _rebase_name_free_bases(project_dir, project, migrated, project_bytes)
        atomic_write_json(project_file, migrated)
    return outcome


__all__ = ["TARGET_SCHEMA_VERSION", "migrate_project_dict", "migrate_v18_to_v19", "without_asset_names_in_versions"]
