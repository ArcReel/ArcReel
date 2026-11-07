"""v4→v5: reduce generation modes to two values; grids become the grid_storyboard switch.

Four duties:
- Reencode ``generation_mode == "grid"`` as ``storyboard + grid_storyboard=true`` (grid changes
  storyboard-image production, not generation mode).
- Replace absent/invalid nonbinary ``generation_mode`` with explicit ``storyboard``, matching prior
  read-side unknown-mode fallback without behavior changes.
- Remove all episode-level ``episodes[].generation_mode`` overrides; project mode determines axes.
- Leave scripts untouched."""

from __future__ import annotations

from pathlib import Path

from lib.infra.json_io import atomic_write_json, load_json

_ROUTE_MODES = {"storyboard", "reference_video"}


def migrate_project_dict(project: dict) -> dict:
    """Pure, idempotent v4→v5 project-dict conversion.

    Leave schema_version to file-level commit."""
    data = dict(project)

    mode = data.get("generation_mode")
    if mode == "grid":
        data["generation_mode"] = "storyboard"
        data["grid_storyboard"] = True
    # Check types before set membership: plaintext project.json may contain unhashable
    # list/dict generation_mode; direct `in` would raise TypeError and abort startup migration.
    elif not isinstance(mode, str) or mode not in _ROUTE_MODES:
        data["generation_mode"] = "storyboard"
    data.setdefault("grid_storyboard", False)

    episodes = data.get("episodes")
    if isinstance(episodes, list):
        data["episodes"] = [
            {k: v for k, v in ep.items() if k != "generation_mode"} if isinstance(ep, dict) else ep for ep in episodes
        ]

    return data


def migrate_v4_to_v5(project_dir: Path) -> None:
    """File-level v4→v5 migration: one atomic write, retryable after crashes with old/new values and no partial state."""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    # Match runner int normalization: legacy project.json may store version strings.
    if int(data.get("schema_version") or 0) >= 5:
        return
    migrated = migrate_project_dict(data)
    migrated["schema_version"] = 5
    atomic_write_json(pj, migrated)
