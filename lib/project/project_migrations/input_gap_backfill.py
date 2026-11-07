"""v17→v18 backfill rerun: register artifacts previously skipped for generation gaps (``docs/adr/0098``).

From v18, gaps block generation only; expected basis records missing inputs as absent
(``lib.artifacts.input_gap``). Earlier activation/provenance used gap-means-unprojectable rules,
leaving skipped artifacts unregistered. This step:

1. Reruns legacy provenance (``docs/adr/0075``) under absent-input rules, updating only selected video
   records still lacking typed provenance, leaving uploads unchanged.
2. Plans targets before (schema 17, no registration with gaps) and after (schema 18).
3. Adds only targets absent before, present after, and still absent from manifests. Existing registrations stay byte-identical.

Backfilled artifacts are current; later filling gaps changes basis and makes them stale normally."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from lib.artifacts.artifact_activation import assert_artifact_target_state_plan_unchanged
from lib.artifacts.artifact_manifest import (
    MANIFEST_FILENAME,
    ArtifactKey,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.artifact_planner import (
    ArtifactTargetStatePlan,
    gap_backfill_entries,
    plan_artifact_target_state_by_gap_rule,
)
from lib.artifacts.input_gap import GAP_TOLERANT_BASIS_SCHEMA_VERSION
from lib.infra.json_io import atomic_write_bytes
from lib.project.legacy_media_provenance import backfill_legacy_media_provenance
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.backups import ensure_versioned_backup

_FROM_VERSION = GAP_TOLERANT_BASIS_SCHEMA_VERSION - 1


def backfill_input_gap_artifacts(project_dir: Path, project: Mapping[str, Any]) -> ArtifactBackfillOutcome:
    """Backfill gap-skipped artifacts. Callers hold project metadata locks; ``project`` is on-disk v17 content.

    Back up manifests/version records before changing each; postcommit dependency drift restores both from original bytes."""

    project_dir = Path(project_dir)
    project_bytes = (project_dir / "project.json").read_bytes()
    versions_path = project_dir / "versions" / "versions.json"
    versions_bytes = versions_path.read_bytes() if versions_path.is_file() else None
    ensure_versioned_backup(versions_path, _FROM_VERSION)
    legacy = backfill_legacy_media_provenance(project_dir, record_gaps=True)
    try:
        before = plan_artifact_target_state_by_gap_rule(project_dir, project, record_gaps=False)
        after = plan_artifact_target_state_by_gap_rule(project_dir, project, record_gaps=True)
        plans = (before, after)
        for plan in plans:
            assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
        adapter = ProjectArtifactManifestAdapter(project_dir)
        stored = adapter.snapshot_entries()
        additions = gap_backfill_entries(stored, before.entries, after.entries)
        if additions:
            _commit_additions(project_dir, adapter, additions, plans, project_bytes)
    except BaseException:
        if legacy.amended and versions_bytes is not None:
            atomic_write_bytes(versions_path, versions_bytes)
        raise
    return ArtifactBackfillOutcome.from_entries(
        adapter.snapshot_entries(),
        legacy.skipped,
        after.skipped,
        preserve_previous_skips=True,
        resolved=additions,
    )


def _commit_additions(
    project_dir: Path,
    adapter: ProjectArtifactManifestAdapter,
    additions: Mapping[ArtifactKey, ArtifactManifestEntry],
    plans: tuple[ArtifactTargetStatePlan, ...],
    project_bytes: bytes,
) -> None:
    ensure_versioned_backup(project_dir / MANIFEST_FILENAME, _FROM_VERSION)
    expected: dict[ArtifactKey, ArtifactManifestEntry | None] = dict.fromkeys(additions)
    if not adapter.replace_entries_if_matches_atomically(expected=expected, replacements=additions):
        raise RuntimeError("artifact manifest changed while backfilling input-gap artifacts")
    try:
        for plan in plans:
            assert_artifact_target_state_plan_unchanged(project_dir, plan, expected_project_bytes=project_bytes)
    except BaseException as original_error:
        restored = adapter.replace_entries_if_matches_atomically(expected=additions, replacements=expected)
        if not restored and any(adapter.get_entry(key) is not None for key in additions):
            raise RuntimeError("artifact manifest dependency drifted and rollback was incomplete") from original_error
        raise


__all__ = ["backfill_input_gap_artifacts"]
