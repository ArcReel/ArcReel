"""Atomic completion marker for project asset-inventory analysis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lib.project_manager import ProjectManager
from lib.source_revision import SourceRevisionBlocker, SourceScope, compute_source_revision


class AssetInventoryError(ValueError):
    """Base class for inventory completion failures."""


class AssetInventoryInvalidRequest(AssetInventoryError):
    """The completion request itself is malformed."""


class AssetInventoryRevisionConflict(AssetInventoryError):
    """The analyzed source changed before its completion marker was committed."""

    def __init__(self, expected_revision: str, actual_revision: str) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        super().__init__("source revision changed before asset inventory completion")


class AssetInventorySourceBlocked(AssetInventoryError):
    """The requested source scope cannot be safely revised."""

    def __init__(self, blockers: list[SourceRevisionBlocker]) -> None:
        self.blockers = blockers
        super().__init__("source scope is blocked")


@dataclass(frozen=True)
class AssetInventoryCompletion:
    scope: SourceScope
    source_revision: str
    counts: dict[str, int]


def _bucket_count(value: object) -> int:
    return len(value) if isinstance(value, Mapping) else 0


def complete_asset_inventory(
    pm: ProjectManager,
    project_name: str,
    scope: SourceScope,
    expected_source_revision: str,
) -> AssetInventoryCompletion:
    """Validate and persist an inventory fact within one project lock."""

    if not expected_source_revision.startswith("sha256-v1:"):
        raise AssetInventoryInvalidRequest("expected_source_revision must be a sha256-v1 revision")

    result: AssetInventoryCompletion | None = None
    project_path = pm.get_project_path(project_name)

    def _mutate(project: dict[str, Any]) -> None:
        nonlocal result
        revision = compute_source_revision(project_path, project, scope)
        if revision.blockers:
            raise AssetInventorySourceBlocked(revision.blockers)
        if revision.revision is None:
            raise AssetInventoryError("source revision is unavailable")
        if revision.revision != expected_source_revision:
            raise AssetInventoryRevisionConflict(expected_source_revision, revision.revision)
        completed_scope = revision.scope
        if completed_scope is None:
            raise AssetInventoryError("source scope is unavailable")

        workflow = project.get("workflow")
        if workflow is None:
            workflow = {}
            project["workflow"] = workflow
        elif not isinstance(workflow, dict):
            raise AssetInventoryError("workflow must be an object")
        workflow["asset_inventory"] = {
            "scope": completed_scope.model_dump(mode="json"),
            "source_revision": revision.revision,
            "completed_at": datetime.now(UTC).isoformat(),
        }
        result = AssetInventoryCompletion(
            scope=completed_scope,
            source_revision=revision.revision,
            counts={
                "characters": _bucket_count(project.get("characters")),
                "scenes": _bucket_count(project.get("scenes")),
                "props": _bucket_count(project.get("props")),
            },
        )

    pm.update_project(project_name, _mutate)
    if result is None:  # pragma: no cover - update_project always invokes the callback or raises
        raise RuntimeError("asset inventory completion did not run")
    return result


__all__ = [
    "AssetInventoryCompletion",
    "AssetInventoryError",
    "AssetInventoryInvalidRequest",
    "AssetInventoryRevisionConflict",
    "AssetInventorySourceBlocked",
    "complete_asset_inventory",
]
