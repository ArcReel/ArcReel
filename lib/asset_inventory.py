"""Atomic completion marker for project asset-inventory analysis."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from lib.asset_types import ASSET_SPECS, resolve_asset_key, validate_asset_name
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
    expected_source_revision: object,
    entries: object = None,
) -> AssetInventoryCompletion:
    """Validate and atomically persist extracted assets plus their inventory fact."""

    if (
        not isinstance(expected_source_revision, str)
        or re.fullmatch(r"sha256-v1:[0-9a-f]{64}", expected_source_revision) is None
    ):
        raise AssetInventoryInvalidRequest("expected_source_revision must be a sha256-v1 revision")

    prepared = _prepare_entries(entries)

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

        # Extracted definitions and the marker are one transaction.  The source revision is checked
        # before any in-memory mutation; update_project writes only after this callback returns, so a
        # conflict cannot leave stale definitions behind without a matching completion fact.
        from lib.data_validator import DataValidator

        before_errors = set(DataValidator(str(pm.projects_root)).validate_project_payload(project).errors)
        for bucket_name, bucket_entries in prepared.items():
            bucket = project.setdefault(bucket_name, {})
            if not isinstance(bucket, dict):
                raise AssetInventoryError(f"project[{bucket_name!r}] must be an object")
            for name, entry in bucket_entries.items():
                if resolve_asset_key(bucket, name) is not None:
                    continue
                bucket[name] = entry
        after_errors = set(DataValidator(str(pm.projects_root)).validate_project_payload(project).errors)
        new_errors = after_errors - before_errors
        if new_errors:
            raise AssetInventoryInvalidRequest("invalid asset entries: " + "; ".join(sorted(new_errors)))

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


def _prepare_entries(entries: object) -> dict[str, dict[str, dict[str, Any]]]:
    """Normalize the three extraction buckets before entering the project transaction."""

    if entries is None:
        return {}
    if not isinstance(entries, Mapping):
        raise AssetInventoryInvalidRequest("entries must be an object")
    allowed_buckets = {spec.bucket_key: spec for kind, spec in ASSET_SPECS.items() if kind != "product"}
    unknown = sorted(str(key) for key in entries if key not in allowed_buckets)
    if unknown:
        raise AssetInventoryInvalidRequest(f"entries contains unsupported buckets: {unknown}")

    prepared: dict[str, dict[str, dict[str, Any]]] = {}
    for bucket_name, raw_entries in entries.items():
        if not isinstance(bucket_name, str) or not isinstance(raw_entries, Mapping):
            raise AssetInventoryInvalidRequest(f"entries[{bucket_name!r}] must be an object")
        spec = allowed_buckets[bucket_name]
        allowed_fields = {"description", *spec.agent_editable_extra_fields}
        normalized: dict[str, dict[str, Any]] = {}
        for raw_name, raw_attrs in raw_entries.items():
            try:
                name = validate_asset_name(raw_name)
            except ValueError as exc:
                raise AssetInventoryInvalidRequest(f"{bucket_name}: {exc}") from exc
            if name in normalized:
                raise AssetInventoryInvalidRequest(f"{bucket_name} contains duplicate normalized name {name!r}")
            if not isinstance(raw_attrs, Mapping):
                raise AssetInventoryInvalidRequest(f"{bucket_name}[{name!r}] must be an object")
            extra = sorted(str(key) for key in raw_attrs if key not in allowed_fields)
            if extra:
                raise AssetInventoryInvalidRequest(f"{bucket_name}[{name!r}] contains unsupported fields: {extra}")
            attrs = dict(raw_attrs)
            description = attrs.get("description")
            if not isinstance(description, str):
                raise AssetInventoryInvalidRequest(f"{bucket_name}[{name!r}].description must be a string")
            for field in spec.agent_editable_extra_fields:
                if field in attrs and not isinstance(attrs[field], str):
                    raise AssetInventoryInvalidRequest(f"{bucket_name}[{name!r}].{field} must be a string")
            entry: dict[str, Any] = {"description": description, spec.sheet_field: ""}
            for field in spec.extra_string_fields:
                entry[field] = attrs.get(field, "")
            for field in spec.extra_list_fields:
                entry[field] = []
            normalized[name] = entry
        prepared[bucket_name] = normalized
    return prepared


__all__ = [
    "AssetInventoryCompletion",
    "AssetInventoryError",
    "AssetInventoryInvalidRequest",
    "AssetInventoryRevisionConflict",
    "AssetInventorySourceBlocked",
    "complete_asset_inventory",
]
