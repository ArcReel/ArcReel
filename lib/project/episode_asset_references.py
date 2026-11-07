"""Episode asset references: assets/derivatives in a formal script, using actual persisted asset-table names.

Compute required sheets once here, shared by episode batch generation, Agent episode listings/pending
asset generation, and production-state missing-referenced-sheet checks, without three independent sets.

Share :func:`lib.script.script_references.payload_reference_names` with cascading renames/reference
status (arrays, ``speaker``, body ``@[名称]``). :class:`ReferenceCatalog` resolves ownership; unregistered
names do not count. Derivative ``本体/衍生`` also references its base, since derivative sheets edit base
sheets and cannot generate without an available base sheet."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from lib.project.asset_derivatives import derivative_artifact_id, derivative_table
from lib.project.asset_types import resolve_asset_key
from lib.references.reference_catalog import build_reference_catalog, split_derivative_reference
from lib.script.script_references import payload_reference_names


@dataclass(frozen=True, slots=True)
class ReferencedAsset:
    """One asset/derivative referenced by this episode."""

    asset_type: str
    #: Actual asset-table name; derivatives use ``本体/衍生`` with actual names in both segments.
    name: str
    #: Derivative base actual name, ``None`` for base entries.
    owner: str | None = None


def episode_referenced_assets(
    project: Mapping[str, Any], script: Mapping[str, Any] | None
) -> frozenset[ReferencedAsset]:
    """All registered assets/derivatives referenced by an episode formal script; empty without one."""

    if script is None:
        return frozenset()
    catalog = build_reference_catalog(project)
    referenced: set[ReferencedAsset] = set()
    for name in payload_reference_names(script):
        entry = catalog.resolve(name)
        if entry is None:
            continue
        bucket = project.get(entry.spec.bucket_key)
        owner_key = resolve_asset_key(bucket, entry.asset_name)
        if owner_key is None:
            continue
        referenced.add(ReferencedAsset(asset_type=entry.asset_type, name=owner_key))
        _base, derivative_name = split_derivative_reference(entry.name)
        if not derivative_name:
            continue
        table = derivative_table(bucket.get(owner_key) if isinstance(bucket, Mapping) else None)
        derivative_key = resolve_asset_key(table, derivative_name)
        if derivative_key is None:
            continue
        referenced.add(
            ReferencedAsset(
                asset_type=entry.asset_type,
                name=derivative_artifact_id(owner_key, derivative_key),
                owner=owner_key,
            )
        )
    return frozenset(referenced)


__all__ = ["ReferencedAsset", "episode_referenced_assets"]
