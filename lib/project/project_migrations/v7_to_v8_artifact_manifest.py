"""v7 → v8: eagerly activate the complete Artifact Manifest target state."""

from __future__ import annotations

from pathlib import Path

from lib.artifacts.artifact_activation import activate_artifact_target_state, plan_artifact_target_state
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.v9_to_v10_script_plan_naming import (
    apply_script_plan_draft_renames,
    pending_draft_rename_map,
    plan_script_plan_draft_renames,
)


def migrate_v7_to_v8(project_dir: Path) -> ArtifactBackfillOutcome:
    project_dir = Path(project_dir)

    # Read-only preflight: activation resolves current/new plan names, so v9→v10 rename planning happens here first.
    # Both rename/activation refusals occur before changing any project bytes. Renames are not persisted yet,
    # so read drafts by old names but record dependencies/artifacts using postcommit new paths.
    renames = plan_script_plan_draft_renames(project_dir)
    plan = plan_artifact_target_state(
        project_dir,
        pending_renames=pending_draft_rename_map(renames, project_dir),
    )

    # Persistence: rename first so planned paths become disk facts, then activation stability gates recheck each.
    # Renaming is idempotent; rerunning after v8 does nothing.
    apply_script_plan_draft_renames(renames, from_version=7)
    activate_artifact_target_state(project_dir, bump_schema=True, plan=plan)
    return ArtifactBackfillOutcome.from_entries(plan.entries, plan.skipped)


__all__ = ["migrate_v7_to_v8"]
