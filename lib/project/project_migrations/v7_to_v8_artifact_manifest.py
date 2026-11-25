"""v7 → v8: eagerly activate the complete Artifact Manifest target state."""

from __future__ import annotations

from pathlib import Path

from lib.artifacts.artifact_activation import activate_artifact_target_state, plan_artifact_target_state
from lib.artifacts.media_artifact_currency import V1_AUDIO_RULES
from lib.project.project_migration_report import ArtifactBackfillOutcome
from lib.project.project_migrations.v9_to_v10_script_plan_naming import (
    apply_script_plan_draft_renames,
    pending_draft_rename_map,
    plan_script_plan_draft_renames,
)


def migrate_v7_to_v8(project_dir: Path) -> ArtifactBackfillOutcome:
    project_dir = Path(project_dir)

    renames = plan_script_plan_draft_renames(project_dir)
    plan = plan_artifact_target_state(
        project_dir,
        audio_rules=V1_AUDIO_RULES,
        pending_renames=pending_draft_rename_map(renames, project_dir),
    )

    apply_script_plan_draft_renames(renames, from_version=7)
    activate_artifact_target_state(project_dir, bump_schema=True, plan=plan)
    return ArtifactBackfillOutcome.from_entries(plan.entries, plan.skipped)


__all__ = ["migrate_v7_to_v8"]
