"""Current request projection for video units.

An advisory/current-state read model: callers supply current project/script/unit, resolved asset
candidates, and request options to obtain immutable facts shared by quotes, submission preflight,
and throttling routes. Results contain no tokens, fingerprints, or executable request snapshots;
workers must reproject current state when processing starts.

Read capabilities only through video-request facts (``docs/adr/0086``). Projection selects facts for
its unit's bucket: reads evaluate under current configuration identity, while execution supplies the
lane's facts evaluated under actual backend identity. Provider material links follow the same rule:
reads check channels assembled from current configuration; execution checks actual channels
(see :data:`ReferenceAssetLinkCheck`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.backends.input_limits import InputLimit
from lib.config.resolver import (
    VideoGenerationType,
    video_capability_satisfied,
)
from lib.generation.video_request_facts import (
    CONFIGURED_VIDEO_IDENTITY,
    DEFAULT_PLANNED_DURATION_SECONDS,
    VideoRequestCostFacts,
    VideoRequestFacts,
    VideoRequestFactsFailure,
    audio_switch_conflict,
    evaluate_video_request_facts,
)
from lib.infra.path_safety import PathTraversalError, safe_join
from lib.infra.schema_guards import is_int
from lib.material.work_files import material_file_present
from lib.project.asset_types import AssetSpec, asset_name_comparison_key
from lib.references.reference_admission import (
    SHEET_REQUIRED_ASSET_TYPES,
    UNREGISTERED_REFERENCE_CODE,
    ReferenceAdmission,
    admit_references,
)
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script.reference_video.duration_slots import (
    DurationSlot,
    project_request_duration,
)
from lib.script.reference_video.text_parser import derive_references_from_text
from lib.script.script_models import ReferenceResource

if TYPE_CHECKING:
    from lib.config.resolver import ConfigResolver


@dataclass(frozen=True)
class ReferenceRequestOptions:
    """Call options affecting current unit request projections, outside script content.

    Queues retain only duration tiers explicitly accepted by users; workers reproject against latest
    scripts/model capabilities, so old confirmations no longer authorize changed tiers.
    Older queued options may contain ``narration_delivery``; ignore it when reading.
    """

    confirmed_request_duration_seconds: int | None = None
    _legacy_duration_confirmed: bool = field(default=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        confirmed = self.confirmed_request_duration_seconds
        if confirmed is not None and not is_int(confirmed, minimum=1):
            raise ValueError("confirmed_request_duration_seconds must be a positive integer or null")

    @property
    def legacy_duration_confirmed(self) -> bool:
        """Whether an option-less task predates explicit tier coordinates."""

        return self._legacy_duration_confirmed

    def to_payload(self) -> dict[str, object]:
        if self.confirmed_request_duration_seconds is None:
            return {}
        return {"confirmed_request_duration_seconds": self.confirmed_request_duration_seconds}

    @classmethod
    def from_payload(
        cls,
        payload: object,
        *,
        key: str = "reference_request_options",
        legacy_duration_confirmed: bool = False,
    ) -> ReferenceRequestOptions:
        """Read queue payloads tolerantly; missing option fields may complete duration confirmation under caller compatibility semantics."""

        root = payload if isinstance(payload, dict) else {}
        if key not in root:
            return cls(_legacy_duration_confirmed=legacy_duration_confirmed)
        raw = root.get(key)
        if not isinstance(raw, dict):
            return cls()
        confirmed = raw.get("confirmed_request_duration_seconds")
        return cls(confirmed_request_duration_seconds=confirmed if is_int(confirmed, minimum=1) else None)


@dataclass(frozen=True)
class ResolvedReferenceAsset:
    """An image candidate expanded from one logical reference; injected asset adapters determine availability."""

    path: Path
    reference: ReferenceResource
    kind: str = "asset"


@dataclass(frozen=True)
class ProjectionProblem:
    """A structured problem comparable across Web, Agent, and queue interfaces."""

    code: str
    blocking: bool
    params: tuple[tuple[str, object], ...] = ()
    reason: str | None = None
    action: str | None = None
    locations: tuple[tuple[str | int, ...], ...] | None = None

    def parameters(self) -> dict[str, object]:
        return dict(self.params)

    @classmethod
    def from_request_facts_failure(
        cls,
        failure: VideoRequestFactsFailure,
        *,
        capability: VideoGenerationType | None = None,
        locations: tuple[tuple[str | int, ...], ...] | None = None,
    ) -> ProjectionProblem:
        """Convert video-fact failures into blocking problems, preserving codes/parameters/repair instructions and adding only the bucket."""

        params = {**({"capability": capability} if capability is not None else {}), **failure.parameters()}
        return cls(
            code=failure.code,
            blocking=True,
            params=tuple(params.items()),
            action=failure.action,
            locations=locations,
        )

    def to_payload(self, *, unit_id: str) -> dict[str, object]:
        """Return the problem envelope shared by Web, Agent, and quotes."""

        default_action, default_paths = _PROBLEM_PRESENTATION.get(
            self.code,
            ("review_request_configuration", (("video_units", unit_id),)),
        )
        payload: dict[str, object] = {
            "code": self.code,
            "blocking": self.blocking,
            "unit_id": unit_id,
            "locations": [{"path": list(path), "line": None} for path in (self.locations or default_paths)],
            "params": self.parameters(),
            "action": self.action or default_action,
        }
        if self.reason is not None:
            payload["reason"] = self.reason
        return payload


class ReferenceProjectionBlockedError(ValueError):
    """Execution-time rejection backed by the projector's canonical problem."""

    def __init__(self, problem: ProjectionProblem) -> None:
        if not problem.blocking:
            raise ValueError("projection failure must wrap a blocking problem")
        self.problem = problem
        super().__init__(problem.code)

    @property
    def code(self) -> str:
        return self.problem.code

    @property
    def params(self) -> dict[str, object]:
        return self.problem.parameters()


@dataclass(frozen=True)
class ReferenceUnitRequestProjection:
    """Canonical request projection for one unit at invocation time."""

    unit_id: str
    declared_references: tuple[ReferenceResource, ...]
    available_assets: tuple[ResolvedReferenceAsset, ...]
    request_assets: tuple[ResolvedReferenceAsset, ...]
    #: External payload keys are fixed as ``declared_capability`` / ``hydrated_capability`` (API contract).
    declared_generation_type: VideoGenerationType
    hydrated_generation_type: VideoGenerationType
    #: Video-request facts for the unit bucket; None on evaluation failure, already converted to blocking ``problems``.
    request_facts: VideoRequestFacts | None
    planned_duration: int
    duration_input: int
    request_duration: DurationSlot | None
    cost: VideoRequestCostFacts | None
    problems: tuple[ProjectionProblem, ...]

    @property
    def provider_id(self) -> str | None:
        return self.request_facts.provider_id if self.request_facts is not None else None

    @property
    def model_id(self) -> str | None:
        return self.request_facts.model_id if self.request_facts is not None else None

    @property
    def blocking_problems(self) -> tuple[ProjectionProblem, ...]:
        return tuple(problem for problem in self.problems if problem.blocking)

    def problem_payloads(self) -> list[dict[str, object]]:
        return [problem.to_payload(unit_id=self.unit_id) for problem in self.problems]

    def to_advisory_payload(self) -> dict[str, object]:
        """Serialize current-state projection facts comparable across entry points."""

        payload: dict[str, object] = {
            "allowed": not self.blocking_problems,
            "kind": "reference_request_projection",
            "advisory": True,
            "unit_id": self.unit_id,
            "declared_capability": self.declared_generation_type,
            "hydrated_capability": self.hydrated_generation_type,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "planned_duration": self.planned_duration,
            "duration_input": self.duration_input,
            "request_duration": self.request_duration.seconds if self.request_duration is not None else None,
            "problems": self.problem_payloads(),
        }
        return payload


class ReferenceAssetAvailability(Protocol):
    """Asset-availability adapter: production checks project files and manifest ownership; tests may use in-memory doubles."""

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        raise NotImplementedError


VideoRequestFactsResult = VideoRequestFacts | VideoRequestFactsFailure

#: Get video-request facts by task-type bucket. Reads use :func:`configured_reference_request_facts`;
#: execution supplies existing lane facts. Tests directly return constructed result/failure objects.
ReferenceRequestFactsLookup = Callable[[VideoGenerationType], Awaitable[VideoRequestFactsResult]]


#: Check provider material links for clipped images about to be sent within this call's material-library scope;
#: order matches prompt image indices. Unavailable applicable links or failed registrations return blocking
#: problems with execution's codes/parameters; no problem means None. Read implementation:
#: :mod:`lib.script.reference_video.provider_asset_links`; execution supplies its actual call channel's check.
ReferenceAssetLinkCheck = Callable[
    [VideoRequestFacts, Sequence[ResolvedReferenceAsset]], Awaitable["ProjectionProblem | None"]
]


def configured_reference_request_facts(project: dict, resolver: ConfigResolver) -> ReferenceRequestFactsLookup:
    """Read side: evaluate reference-route video facts per bucket under current configuration identity, once per bucket within a lookup."""

    evaluated: dict[VideoGenerationType, VideoRequestFactsResult] = {}

    async def lookup(generation_type: VideoGenerationType) -> VideoRequestFactsResult:
        result = evaluated.get(generation_type)
        if result is None:
            result = await evaluate_video_request_facts(
                project,
                route="reference_video",
                generation_type=generation_type,
                identity=CONFIGURED_VIDEO_IDENTITY,
                resolver=resolver,
            )
            evaluated[generation_type] = result
        return result

    return lookup


@dataclass(frozen=True)
class ReferenceAssetHydration:
    available: tuple[ResolvedReferenceAsset, ...]
    missing: tuple[ReferenceResource, ...]


def hydrate_reference_assets(
    declared: Sequence[ReferenceResource],
    resolved_assets: Sequence[ResolvedReferenceAsset],
    availability: ReferenceAssetAvailability,
) -> ReferenceAssetHydration:
    """Filter candidates by declared scope, returning actually available images and missing-image logical references."""

    declared_keys = {(ref.type, asset_name_comparison_key(ref.name)) for ref in declared}
    candidates = tuple(asset for asset in resolved_assets if _asset_key(asset) in declared_keys)
    available = tuple(asset for asset in candidates if availability.is_available(asset))
    available_keys = {_asset_key(asset) for asset in available}
    missing = tuple(ref for ref in declared if (ref.type, asset_name_comparison_key(ref.name)) not in available_keys)
    return ReferenceAssetHydration(available=available, missing=missing)


@dataclass(frozen=True)
class ReferenceUnitHydration:
    """Bucket determination from hydrated declared unit references, shared by execution and reads.

    ``declared_generation_type`` sees only registered declarations in the body;
    ``hydrated_generation_type`` sees available images actually sendable now and determines the actual
    bucket. When declarations diverge from available images (unregistered references, registered assets
    missing images, or resulting bucket changes), ``problems`` blocks and names unavailable references,
    never silently switching buckets.
    """

    declared_references: tuple[ReferenceResource, ...]
    available_assets: tuple[ResolvedReferenceAsset, ...]
    #: Declared references currently lacking any available image candidate.
    unavailable_references: tuple[ReferenceResource, ...]
    #: Names mentioned in the body but unregistered in the project.
    unregistered_references: tuple[str, ...]
    declared_generation_type: VideoGenerationType
    hydrated_generation_type: VideoGenerationType
    problems: tuple[ProjectionProblem, ...]


def hydrate_unit_references(
    project: dict,
    unit: dict,
    resolved_assets: Sequence[ResolvedReferenceAsset],
    availability: ReferenceAssetAvailability,
    *,
    catalog: ReferenceCatalog | None = None,
) -> ReferenceUnitHydration:
    """Hydrate declared unit-body references into available images and determine the bucket.

    ``catalog`` is already constructed by the caller from the same ``project``; supply it for batch
    hydration to avoid rebuilding per unit.
    """

    if catalog is None:
        catalog = build_reference_catalog(project)
    references, unregistered = derive_references_from_text(_unit_text(unit), project, catalog=catalog)
    canonical = tuple(references)
    declared_generation_type: VideoGenerationType = "r2v" if canonical else "i2v"
    hydration = hydrate_reference_assets(canonical, resolved_assets, availability)
    available = hydration.available

    problems: list[ProjectionProblem] = []
    # Unregistered mentions derive no references and never appear in ``hydration.missing``; block and
    # name them separately here. Characters/scenes/props without asset images need no separate report:
    # they never fall back to originals, yield no candidates, and all enter ``hydration.missing``.
    admission = _admit_unit_references(catalog, references, unregistered)
    if admission.unregistered:
        problems.append(
            _problem(
                UNREGISTERED_REFERENCE_CODE,
                blocking=True,
                unregistered=admission.unregistered,
                missing_text=admission.unregistered_text(),
            )
        )
    if hydration.missing:
        missing = tuple((ref.type, ref.name) for ref in hydration.missing)
        problems.append(
            _problem(
                "reference_asset_missing",
                blocking=True,
                missing=missing,
                missing_text=", ".join(f"{asset_type}: {name}" for asset_type, name in missing),
            )
        )

    hydrated_generation_type: VideoGenerationType = "r2v" if available else "i2v"
    if hydrated_generation_type != declared_generation_type:
        problems.append(
            _problem(
                "reference_capability_changed",
                blocking=True,
                declared=declared_generation_type,
                hydrated=hydrated_generation_type,
            )
        )
    return ReferenceUnitHydration(
        declared_references=canonical,
        available_assets=available,
        unavailable_references=hydration.missing,
        unregistered_references=admission.unregistered,
        declared_generation_type=declared_generation_type,
        hydrated_generation_type=hydrated_generation_type,
        problems=tuple(problems),
    )


class FilesystemReferenceAssets:
    """Check that image candidates actually exist as regular files within project-directory boundaries."""

    def __init__(self, project_path: Path) -> None:
        self._project_path = project_path

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        try:
            path = safe_join(self._project_path, asset.path)
        except (OSError, PathTraversalError, TypeError):
            return False
        # Remote content versions remain retrievable after cache clearing, so do not count as missing images.
        return material_file_present(path)


def _candidate_path(project_path: Path, value: object) -> Path | None:
    if not isinstance(value, (str, Path)) or not str(value):
        return None
    try:
        return safe_join(project_path, value)
    except (OSError, PathTraversalError, TypeError):
        return None


class MemoizedReferenceAssets:
    """Memoize per-image availability within a read batch: cost scales with design-image count, not reference frequency."""

    def __init__(self, inner: ReferenceAssetAvailability) -> None:
        self._inner = inner
        self._known: dict[tuple[Path, str, str, str], bool] = {}

    def is_available(self, asset: ResolvedReferenceAsset) -> bool:
        # Availability depends on path, candidate kind, and ownership key (reference type + name): four image-defining inputs.
        key = (asset.path, asset.kind, asset.reference.type, asset.reference.name)
        known = self._known.get(key)
        if known is None:
            known = self._known[key] = self._inner.is_available(asset)
        return known


class ReferenceExpansionCache:
    """Shared reference catalog and candidate-path resolution within one read-side batch.

    Expanding all units against one ``project`` builds the catalog once and resolves path boundaries
    once per registered path. Cost scales with asset count rather than reference frequency. Do not
    mutate ``project`` during this batch. Cache path resolution only, not file existence, which the
    availability adapter determines.
    """

    def __init__(self, project: dict, project_path: Path) -> None:
        self.catalog = build_reference_catalog(project)
        self._project_path = project_path
        self._paths: dict[str, Path | None] = {}

    def candidate_path(self, value: object) -> Path | None:
        if not isinstance(value, str):
            return _candidate_path(self._project_path, value)
        if value not in self._paths:
            self._paths[value] = _candidate_path(self._project_path, value)
        return self._paths[value]


def _unit_text(unit: dict) -> str:
    raw_text = unit.get("text")
    return raw_text if isinstance(raw_text, str) else ""


def unit_reference_declarations(
    project: dict, unit: dict, *, catalog: ReferenceCatalog | None = None
) -> tuple[ReferenceResource, ...]:
    """Derive generation's logical image references from the video-unit body in first-mention order.

    The body is the sole source of truth: references are not persisted, and all reads derive them here.
    Products share other assets' rules without type priorities (ADR 0064). Unregistered names derive
    no references; rendering/previews emit a nonblocking warning without preventing this generation.
    """

    references, _missing = derive_references_from_text(_unit_text(unit), project, catalog=catalog)
    return tuple(references)


def unit_reference_admission(project: dict, unit: dict) -> ReferenceAdmission:
    """Reference admission for this unit body, sharing :mod:`lib.references.reference_admission` with storyboard routes.

    Both individual generation and batch admission use this projector, preventing contradictory answers
    for the same body.
    """

    catalog = build_reference_catalog(project)
    references, unregistered = derive_references_from_text(_unit_text(unit), project, catalog=catalog)
    return _admit_unit_references(catalog, references, unregistered)


def _admit_unit_references(
    catalog: ReferenceCatalog, references: Sequence[ReferenceResource], unregistered: Sequence[str]
) -> ReferenceAdmission:
    return admit_references(
        catalog,
        references=[(reference.type, reference.name) for reference in references],
        unregistered=unregistered,
    )


def resolve_reference_assets(
    project: dict,
    project_path: Path,
    unit: dict,
    *,
    expansion: ReferenceExpansionCache | None = None,
) -> tuple[ResolvedReferenceAsset, ...]:
    """Expand body-derived logical references into image candidates, never confusing registered paths with existing files.

    Use asset images when present. Only products without asset images fall back to all original images,
    their fidelity acceptance anchors (ADR 0034). Character/scene/prop originals only feed asset-image
    generation and never video requests (ADR 0073); missing asset images yield no candidates and block
    through the projector. With asset images, no type additionally injects originals or gets sorted by
    type (ADR 0064). Missing fields, unregistered names, and out-of-bounds paths create no candidates;
    the projector compares derived references and reports ``reference_asset_missing`` consistently.

    Supply ``expansion`` for batches to share reference catalogs and path resolution across the batch.
    """

    if expansion is None:
        expansion = ReferenceExpansionCache(project, project_path)
    catalog = expansion.catalog
    result: list[ResolvedReferenceAsset] = []
    for reference in unit_reference_declarations(project, unit, catalog=catalog):
        catalog_entry = catalog.lookup(reference.type, reference.name)
        if catalog_entry is None:
            continue
        entry = catalog_entry.asset
        if not isinstance(entry, dict):
            continue
        spec = catalog_entry.spec
        sheet = expansion.candidate_path(entry.get(spec.sheet_field))
        if sheet is not None:
            result.append(ResolvedReferenceAsset(path=sheet, reference=reference, kind="sheet"))
            continue
        if catalog_entry.asset_type in SHEET_REQUIRED_ASSET_TYPES:
            continue
        for raw_path in _original_image_paths(entry, spec):
            original = expansion.candidate_path(raw_path)
            if original is not None:
                result.append(ResolvedReferenceAsset(path=original, reference=reference, kind="original"))
    return tuple(result)


def _original_image_paths(entry: dict, spec: AssetSpec) -> list[object]:
    """All original-image paths registered on this asset entry, in declaration order."""

    paths: list[object] = []
    for field_name in spec.original_image_fields:
        value = entry.get(field_name)
        if isinstance(value, list):
            paths.extend(value)
        elif value:
            paths.append(value)
    return paths


def clamp_reference_assets(
    assets: Sequence[ResolvedReferenceAsset], max_references: int | None
) -> tuple[ResolvedReferenceAsset, ...]:
    """Keep the first images in body-mention order when over limits, without type priorities.

    ``max_references`` is the effective reference-image limit (``lib.backends.video_capabilities``):
    ``None`` means unlisted and does not clip; ``0`` means reference images are unsupported and likewise
    does not clip, leaving rejection to execution validation rather than silently switching to a
    no-reference request after clipping to zero.
    """

    bound = InputLimit.from_json(max_references).clamp_bound()
    if bound is None or len(assets) <= bound:
        return tuple(assets)
    return tuple(assets[:bound])


_PROBLEM_PRESENTATION: dict[str, tuple[str, tuple[tuple[str | int, ...], ...]]] = {
    "reference_asset_missing": ("repair_reference_assets", (("text",),)),
    UNREGISTERED_REFERENCE_CODE: ("repair_reference_assets", (("text",),)),
    "reference_capability_changed": ("repair_reference_assets", (("text",),)),
    "reference_images_clamped": ("review_reference_selection", (("text",),)),
    "provider_asset_link_processing": ("review_reference_selection", (("text",),)),
    "provider_asset_link_failed": ("review_reference_selection", (("text",),)),
    "video_audio_switch_not_supported": (
        "enable_model_audio",
        (("generation_settings", "generate_audio"),),
    ),
    "reference_duration_confirmation_required": ("confirm_duration", (("duration_seconds",),)),
    "needs_replan": ("replan_unit", (("duration_seconds",),)),
    "reference_supported_durations_missing": ("configure_video_model", (("duration_seconds",),)),
    "reference_supported_durations_invalid": ("configure_video_model", (("duration_seconds",),)),
    "reference_supported_durations_incompatible": ("configure_video_model", (("duration_seconds",),)),
    "reference_capability_unavailable": ("configure_video_model", (("text",),)),
    "video_capability_missing_i2v": ("configure_video_model", (("text",),)),
    "video_capability_missing_r2v": ("configure_video_model", (("text",),)),
    "video_capability_missing_t2v": ("configure_video_model", (("text",),)),
    "video_capability_reference_unavailable": ("configure_video_model", (("text",),)),
    "model_not_in_provider_catalog": ("configure_video_model", (("text",),)),
    "model_discontinued": ("configure_video_model", (("text",),)),
}


def _problem(code: str, *, blocking: bool, **params: object) -> ProjectionProblem:
    return ProjectionProblem(code=code, blocking=blocking, params=tuple(params.items()))


def _asset_key(asset: ResolvedReferenceAsset) -> tuple[str, str]:
    return asset.reference.type, asset_name_comparison_key(asset.reference.name)


def _planned_duration(unit: dict) -> int:
    raw = unit.get("duration_seconds", DEFAULT_PLANNED_DURATION_SECONDS)
    if isinstance(raw, bool):
        raise ValueError("duration_seconds must be a positive integer")
    value = int(raw or DEFAULT_PLANNED_DURATION_SECONDS)
    if value <= 0:
        raise ValueError("duration_seconds must be a positive integer")
    return value


class ReferenceUnitRequestProjector:
    """Project current unit intent into canonical request facts shared by all read interfaces."""

    def __init__(
        self,
        request_facts: ReferenceRequestFactsLookup,
        assets: ReferenceAssetAvailability,
        asset_links: ReferenceAssetLinkCheck | None = None,
    ) -> None:
        """None ``asset_links`` skips provider material-link checks, only for callers needing bucket determinations (such as throttling routes)."""
        self._request_facts = request_facts
        self._assets = assets
        self._asset_links = asset_links

    async def project_current(
        self,
        *,
        project: dict,
        script: dict,
        unit: dict,
        resolved_assets: Sequence[ResolvedReferenceAsset],
        options: ReferenceRequestOptions | None = None,
    ) -> ReferenceUnitRequestProjection:
        """Project state at invocation time; explicit ``script`` inputs fix the public seam's complete context contract."""

        del script
        # Asset availability reads manifests and hashes design images; move the entire hydration into a worker thread.
        hydration = await asyncio.to_thread(hydrate_unit_references, project, unit, resolved_assets, self._assets)
        return await self.project_hydrated(project=project, unit=unit, hydration=hydration, options=options)

    async def project_hydrated(
        self,
        *,
        project: dict,
        unit: dict,
        hydration: ReferenceUnitHydration,
        options: ReferenceRequestOptions | None = None,
    ) -> ReferenceUnitRequestProjection:
        """Project caller-completed hydration; batch callers may hydrate all units with one worker-thread entry."""

        options = options or ReferenceRequestOptions()
        canonical = hydration.declared_references
        available = hydration.available_assets
        declared_generation_type = hydration.declared_generation_type
        hydrated_generation_type = hydration.hydrated_generation_type

        problems: list[ProjectionProblem] = list(hydration.problems)

        facts: VideoRequestFacts | None = None
        try:
            evaluated = await self._request_facts(hydrated_generation_type)
        except Exception:
            evaluated = VideoRequestFactsFailure(
                "reference_capability_unavailable", (("capability", hydrated_generation_type),)
            )
        if isinstance(evaluated, VideoRequestFactsFailure):
            problems.append(
                ProjectionProblem.from_request_facts_failure(evaluated, capability=hydrated_generation_type)
            )
        else:
            facts = evaluated

        request_assets = available
        if facts is not None:
            if (
                hydrated_generation_type == "i2v"
                and not available
                and not video_capability_satisfied(
                    generation_type=hydrated_generation_type,
                    first_frame=facts.first_frame,
                    max_reference_images=facts.max_reference_images,
                    text_to_video=facts.text_to_video,
                    has_image=False,
                )
            ):
                problems.append(
                    _problem(
                        "video_capability_missing_t2v",
                        blocking=True,
                        provider=facts.provider_id,
                        model=facts.model_id,
                    )
                )
            request_assets = clamp_reference_assets(available, facts.max_reference_images)
            if len(request_assets) < len(available):
                problems.append(
                    _problem(
                        "reference_images_clamped",
                        blocking=False,
                        count=len(available),
                        max_count=facts.max_reference_images,
                        provider=facts.provider_id,
                        model=facts.model_id,
                    )
                )
            if audio_switch_conflict(facts) is not None:
                problems.append(
                    _problem(
                        "video_audio_switch_not_supported",
                        blocking=True,
                        provider=facts.provider_id,
                        model=facts.model_id,
                    )
                )
            if self._asset_links is not None and request_assets:
                link_problem = await self._asset_links(facts, request_assets)
                if link_problem is not None:
                    problems.append(link_problem)

        planned_duration = _planned_duration(unit)
        duration_input = planned_duration
        request_duration: DurationSlot | None = None
        cost: VideoRequestCostFacts | None = None

        if facts is not None:
            # Share duration-tier determination with storyboard routes; only map results to reference-to-video problem codes here.
            projected = project_request_duration(
                planned_duration_seconds=planned_duration,
                supported_durations=facts.allowed_durations,
                duration_endpoint_fixed=facts.duration_endpoint_fixed,
                confirmed_request_duration_seconds=options.confirmed_request_duration_seconds,
                confirmation_waived=options.legacy_duration_confirmed,
            )
            duration_input = projected.duration_input
            request_duration = projected.slot
            if projected.problem == "supported_durations_missing":
                problems.append(
                    _problem(
                        "reference_supported_durations_missing",
                        blocking=True,
                        provider=facts.provider_id,
                        model=facts.model_id,
                    )
                )
            elif projected.problem == "needs_replan" and projected.slot is not None:
                problems.append(
                    _problem(
                        "needs_replan",
                        blocking=True,
                        duration_input=projected.duration_input,
                        maximum_duration=projected.slot.seconds,
                    )
                )
            elif projected.problem == "confirmation_required" and projected.slot is not None:
                problems.append(
                    _problem(
                        "reference_duration_confirmation_required",
                        blocking=True,
                        script_duration=planned_duration,
                        duration_input=projected.duration_input,
                        request_duration=projected.slot.seconds,
                        adjustment=projected.slot.adjustment,
                    )
                )
            if projected.slot is not None:
                cost = VideoRequestCostFacts(
                    request_facts=facts,
                    duration_seconds=projected.slot.seconds,
                    aspect_ratio=resolve_video_aspect_ratio(project),
                    reference_image_count=len(request_assets),
                )

        return ReferenceUnitRequestProjection(
            unit_id=str(unit.get("unit_id") or ""),
            declared_references=canonical,
            available_assets=available,
            request_assets=request_assets,
            declared_generation_type=declared_generation_type,
            hydrated_generation_type=hydrated_generation_type,
            request_facts=facts,
            planned_duration=planned_duration,
            duration_input=duration_input,
            request_duration=request_duration,
            cost=cost,
            problems=tuple(problems),
        )


async def project_reference_unit_request(
    *,
    project: dict,
    script: dict,
    unit: dict,
    project_path: Path,
    options: ReferenceRequestOptions | None = None,
    resolver: ConfigResolver | None = None,
    request_facts_lookup: ReferenceRequestFactsLookup | None = None,
    shared_assets: Callable[[], ReferenceAssetAvailability] | None = None,
    asset_links: ReferenceAssetLinkCheck | None = None,
    check_asset_links: bool = True,
) -> ReferenceUnitRequestProjection:
    """Production entry: construct one advisory projection from current project files and configuration.

    Asset availability matches execution (existing files and manifest ownership), so admission, individual
    enqueueing, prompt previews, and throttling routes do not label execution-i2v units as r2v. Batch
    callers supply ``shared_assets`` for worker-thread batch-wide determinations against one manifest
    snapshot; otherwise build availability for this unit at invocation time.

    Provider material links default to channels assembled from current configuration; batch callers
    supply shared ``asset_links``. Callers needing only bucket determination (throttling routes) pass
    ``check_asset_links=False`` to skip link checks.
    """

    # artifact_selection and provider_asset_links depend on this module; defer imports to avoid cycles.
    from lib.script.reference_video.artifact_selection import CurrentReferenceAssets

    if resolver is None:
        from lib.config.resolver import ConfigResolver
        from lib.db import async_session_factory

        resolver = ConfigResolver(async_session_factory)
    if request_facts_lookup is None:
        request_facts_lookup = configured_reference_request_facts(project, resolver)
    if not check_asset_links:
        asset_links = None
    elif asset_links is None:
        from lib.script.reference_video.provider_asset_links import configured_asset_link_check

        asset_links = configured_asset_link_check(project_path=project_path, resolver=resolver)

    def current_assets() -> tuple[ReferenceAssetAvailability, tuple[ResolvedReferenceAsset, ...]]:
        # Building manifest-aware availability and expanding candidate paths both access filesystems; use a worker thread.
        availability = CurrentReferenceAssets(project_path, project) if shared_assets is None else shared_assets()
        return availability, resolve_reference_assets(project, project_path, unit)

    availability, resolved_assets = await asyncio.to_thread(current_assets)
    projector = ReferenceUnitRequestProjector(request_facts_lookup, availability, asset_links)
    return await projector.project_current(
        project=project,
        script=script,
        unit=unit,
        resolved_assets=resolved_assets,
        options=options or ReferenceRequestOptions(),
    )
