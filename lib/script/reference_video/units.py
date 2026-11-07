"""Pure reference-unit lookup and bucket mapping.

Executable requests derive references from bodies and hydrate project files through ``ReferenceUnitRequestProjector``.
This module only maps supplied boolean facts and locates units.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only import: lib.project.project_manager indirectly loads this module through package initialization,
    # while lib.config.resolver imports lib.project.project_manager back; runtime imports would cycle.
    from lib.config.resolver import VideoGenerationType


def reference_video_bucket(*, with_references: bool) -> VideoGenerationType:
    """Task bucket: references → r2v; no references → i2v.

    Purely maps an already-established boolean fact. Preflight, quoting, Agents, queueing, and execution must first
    use ``ReferenceUnitRequestProjector`` to derive body references and hydrate available assets, never guess independently.
    """
    return "r2v" if with_references else "i2v"


def find_reference_unit(script: dict, unit_id: str) -> dict | None:
    """Locate a unit in the script's self-contained ``video_units``."""
    units = script.get("video_units") or []
    return next((u for u in units if isinstance(u, dict) and u.get("unit_id") == unit_id), None)
