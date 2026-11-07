"""v2→v3: stamp episode-ledger version.

Change schema_version only, preserving episodes verbatim. v2 entries lack source_range. Ledger
coordinates bind specific source content; mechanically inferred coordinates cannot support split
adjustment reconstruction. Upgraded episodes remain usable (scripts/media/status/export ignore ranges).
Replanning uses full ``reset_episode_planning`` to enter the new mechanism."""

from __future__ import annotations

from pathlib import Path

from lib.infra.json_io import atomic_write_json, load_json


def migrate_v2_to_v3(project_dir: Path) -> None:
    """File-level v2→v3 migration: one atomic write, inherently retryable after crashes with old/new values and no partial state."""
    pj = project_dir / "project.json"
    if not pj.exists():
        return
    data = load_json(pj)
    # Match runner int normalization: legacy project.json may store version strings.
    if int(data.get("schema_version") or 0) >= 3:
        return
    data["schema_version"] = 3
    atomic_write_json(pj, data)
