"""v7 → v8: eagerly activate the complete Artifact Manifest target state."""

from __future__ import annotations

from pathlib import Path

from lib.artifact_activation import activate_artifact_target_state


def migrate_v7_to_v8(project_dir: Path) -> None:
    activate_artifact_target_state(project_dir, bump_schema=True)


__all__ = ["migrate_v7_to_v8"]
