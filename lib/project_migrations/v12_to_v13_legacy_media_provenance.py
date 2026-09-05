"""v12 → v13: backfill typed provenance onto legacy media records, then re-activate the manifest.

Video version records written before typed provenance existed cannot be
registered by the manifest planner nor read by the presentation read model.
This step projects the missing facts from the project as it stands now (the
same projection the currency comparison uses), stamps each amended record with
``provenance_backfilled_at``, and replaces the whole manifest from a fresh
plan so the videos those records back are registered current. Scripts whose
formal script_plan slot is empty are registered on a plan-less basis by the
same plan.

Order matters: the record amendment is a plain versions.json rewrite that
precedes the activation preflight, so the plan reads the amended bytes and the
activation's stability gate holds them fixed through the commit.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from lib.artifact_activation import activate_artifact_target_state, plan_artifact_target_state
from lib.json_io import load_json
from lib.legacy_media_provenance import backfill_legacy_media_provenance
from lib.project_migration_report import ArtifactBackfillOutcome, merge_skipped
from lib.project_migrations.backups import ensure_versioned_backup
from lib.project_schema import parse_project_schema_version

TARGET_SCHEMA_VERSION = 13


def migrate_v12_to_v13(project_dir: Path) -> ArtifactBackfillOutcome | None:
    project_dir = Path(project_dir)
    project = load_json(project_dir / "project.json")
    if not isinstance(project, dict) or parse_project_schema_version(project) >= TARGET_SCHEMA_VERSION:
        return None

    ensure_versioned_backup(project_dir / "versions" / "versions.json", TARGET_SCHEMA_VERSION - 1)
    backfill = backfill_legacy_media_provenance(project_dir)

    plan = plan_artifact_target_state(project_dir)
    activate_artifact_target_state(
        project_dir,
        bump_schema=True,
        plan=plan,
        target_schema_version=TARGET_SCHEMA_VERSION,
    )
    registered = Counter(key.kind.value for key in plan.entries)
    return ArtifactBackfillOutcome(
        registered=dict(registered),
        skipped=merge_skipped(backfill.skipped, plan.skipped),
    )


__all__ = ["TARGET_SCHEMA_VERSION", "migrate_v12_to_v13"]
