"""Read-side unit capabilities: bucket by available images and provide that bucket's video-request facts.

Unit lists, content review, and Agent capability payloads consume this module. Bucket decisions share
``ReferenceUnitRequestProjector`` logic (:func:`hydrate_unit_references` plus manifest-aware availability),
preventing UI r2v tiers from executing as i2v. Quotes use the same hydration through ``ReferenceUnitRequestProjector``.
Same-tier free-reuse visual digests, planning duration gates, and new-unit defaults needing only buckets use
:func:`hydrate_reference_units`, never duplicate decisions. Callers provide bucket-fact lookups, evaluating
each bucket at most once per request; unit counts do not multiply configuration resolution.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.config.resolver import VideoGenerationType, duration_endpoint_fixed_reason
from lib.generation.video_request_facts import VideoRequestFacts, VideoRequestFactsFailure
from lib.script.reference_video.artifact_selection import CurrentReferenceAssets
from lib.script.reference_video.request_projection import (
    MemoizedReferenceAssets,
    ReferenceAssetAvailability,
    ReferenceExpansionCache,
    ReferenceRequestFactsLookup,
    ReferenceUnitHydration,
    hydrate_unit_references,
    resolve_reference_assets,
)


@dataclass(frozen=True)
class ReferenceUnitCapability:
    """One unit's current bucket and that bucket's video-request facts."""

    unit_id: str
    hydration: ReferenceUnitHydration
    request_facts: VideoRequestFacts | VideoRequestFactsFailure

    @property
    def generation_type(self) -> VideoGenerationType:
        return self.hydration.hydrated_generation_type

    def to_payload(self) -> dict[str, object]:
        """Shared per-unit Web/Agent envelope.

        ``problem`` is bucket request-fact failure; ``problems`` holds structured blockers for declared/available
        reference divergence (unregistered, missing images, changed buckets). Never collapse them together.
        """

        facts = self.request_facts
        resolved, failure = (facts, None) if isinstance(facts, VideoRequestFacts) else (None, facts)
        fixed = resolved is not None and resolved.duration_endpoint_fixed
        hydration = self.hydration
        return {
            "unit_id": self.unit_id,
            "declared_capability": hydration.declared_generation_type,
            "hydrated_capability": hydration.hydrated_generation_type,
            "declared_references": [
                {"type": reference.type, "name": reference.name} for reference in hydration.declared_references
            ],
            "unavailable_references": [
                {"type": reference.type, "name": reference.name} for reference in hydration.unavailable_references
            ],
            "unregistered_references": list(hydration.unregistered_references),
            "allowed_durations": list(resolved.allowed_durations) if resolved is not None else None,
            "excluded_durations": dict(resolved.excluded_durations) if resolved is not None else None,
            "duration_endpoint_fixed": fixed,
            "duration_endpoint_fixed_reason": duration_endpoint_fixed_reason(fixed),
            "problem": failure.problem_payload() if failure is not None else None,
            "problems": [problem.to_payload(unit_id=self.unit_id) for problem in hydration.problems],
        }


def hydrate_reference_units(
    project: dict,
    project_path: Path,
    units: Iterable[dict],
    *,
    availability: ReferenceAssetAvailability | None = None,
) -> tuple[ReferenceUnitHydration, ...]:
    """Hydrate declared references per unit with execution-equivalent rules, returning current buckets and divergence problems.

    Default ``availability`` requires existing files claimed by manifests, constructed once per batch;
    no units means no project-asset access. Share one catalog, path resolver, and per-image availability cache.
    """

    pending = list(units)
    if not pending:
        return ()
    assets = MemoizedReferenceAssets(
        availability if availability is not None else CurrentReferenceAssets(project_path, project)
    )
    expansion = ReferenceExpansionCache(project, project_path)
    return tuple(
        hydrate_unit_references(
            project,
            unit,
            resolve_reference_assets(project, project_path, unit, expansion=expansion),
            assets,
            catalog=expansion.catalog,
        )
        for unit in pending
    )


async def evaluate_reference_unit_capabilities(
    project: dict,
    project_path: Path,
    units: Iterable[dict],
    *,
    request_facts: ReferenceRequestFactsLookup,
    availability: ReferenceAssetAvailability | None = None,
) -> tuple[ReferenceUnitCapability, ...]:
    """Choose each unit's bucket and obtain its video-request facts.

    ``availability`` defaults to the same manifest-aware assessment used by execution; callers supply per-bucket ``request_facts`` lookup (read paths use ``configured_reference_request_facts``)."""

    pending = list(units)
    # Hydration reads manifests and hashes sheets; move the complete batch to a worker thread.
    hydrations = await asyncio.to_thread(
        hydrate_reference_units, project, project_path, pending, availability=availability
    )
    results: list[ReferenceUnitCapability] = []
    for unit, hydration in zip(pending, hydrations, strict=True):
        results.append(
            ReferenceUnitCapability(
                unit_id=str(unit.get("unit_id") or ""),
                hydration=hydration,
                request_facts=await request_facts(hydration.hydrated_generation_type),
            )
        )
    return tuple(results)


def reference_unit_capability_payloads(capabilities: Sequence[ReferenceUnitCapability]) -> dict[str, dict[str, object]]:
    """Per-unit envelopes indexed by ``unit_id``, ready for response attachment."""

    return {capability.unit_id: capability.to_payload() for capability in capabilities}
