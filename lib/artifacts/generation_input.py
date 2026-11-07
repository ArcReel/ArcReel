"""Generation inputs: content assembled from current project state for one target in one generation stage.

Executors, target-state planners, and final-prompt previews share reference assembly/gap checks
(``GLOSSARY.md`` generation inputs; ``docs/adr/0073`` shared execution rules):

- Entry functions always return generation inputs: available values plus all gaps (``docs/adr/0098``).
  Gaps block only generation. Executors/previews inspect :attr:`ImageGenerationInput.refusal`, an
  :class:`InputRefused` when gaps exist; executors use the server's sole translation function to produce
  error codes. Planners still obtain expected bases, recording missing items as absent.
- Entry functions divide by stage: storyboards :func:`storyboard_image_input`, sheets :func:`asset_sheet_input`,
  derivatives :func:`derivative_sheet_input`. Valid inputs share shape with typed semantics.
- Obtain expected bases only through ``expected_basis()``, registered bases only through ``freeze()``.
  Both use private :func:`_visual_basis`, guaranteeing consistency by construction (``docs/adr/0062``).
  ``freeze()`` accepts only gap-free inputs; complete-input expected bases remain byte-for-byte unchanged.
- Record full reference sets in assembly order without cropping. Provider limits affect only sent images
  and 图N numbering, never bases.

Read project state only through :class:`InputObservation`: no server imports or currency-resolver calls,
so planners calling here do not recurse into themselves."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Protocol

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactInputClaim,
    ArtifactKey,
    ArtifactManifestAdapter,
    ArtifactManifestEntry,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.image_reference_snapshot import FrozenImageReferences, freeze_image_references
from lib.artifacts.input_gap import InputGap
from lib.artifacts.video_visual_provenance import resolve_video_aspect_ratio
from lib.artifacts.visual_artifact_provenance import (
    VisualReference,
    build_asset_sheet_visual_basis,
    build_storyboard_image_visual_basis,
    project_basis_records_asset_names,
)
from lib.project.asset_derivatives import (
    DERIVATIVE_ASSET_TYPE,
    DERIVATIVE_SOURCE_KIND,
    DERIVATIVE_SOURCE_ROLE,
    build_derivative_sheet_basis,
)
from lib.project.asset_types import ASSET_SPECS, DERIVATIVES_FIELD, AssetSpec, asset_name_comparison_key
from lib.prompts.prompt_builders import (
    build_character_derivative_prompt,
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
    render_storyboard_image_prompt,
)
from lib.prompts.reference_image_numbering import PREVIOUS_STORYBOARD_ROLE, clamp_reference_images
from lib.references.reference_admission import SHEET_MISSING_CODE, UNREGISTERED_REFERENCE_CODE
from lib.references.reference_catalog import ReferenceCatalog, build_reference_catalog
from lib.script.storyboard_sequence import find_storyboard_item, get_storyboard_items

#: Declared but unreadable originals. Separate from ``reference_asset_missing``: fix by reuploading originals or clearing original fields,
#: not generating sheets.
ORIGINAL_MISSING_CODE = "asset_original_missing"

#: Pending storyboard image prompts: semantics unready, using the generation entry point's code.
PROMPT_PENDING_CODE = "script_prompt_pending"

#: Asset without a description: sheet semantics unready.
ASSET_DESCRIPTION_REQUIRED_CODE = "asset_description_required"

#: Derivative without appearance-change descriptions: semantics unready, using the derivative-generation entry point's code.
DERIVATIVE_DESCRIPTION_REQUIRED_CODE = "derivative_description_required"

#: Derivative base sheet unavailable (undeclared, missing file, unregistered), using the derivative-generation entry point's code.
DERIVATIVE_OWNER_SHEET_MISSING_CODE = "derivative_owner_sheet_missing"

#: Sheet/derivative-sheet aspect ratio, shared by executors/planners.
ASSET_SHEET_CANVAS_RATIO = "16:9"


@dataclass(frozen=True, slots=True)
class ObservedFile:
    """One present project file.

    ``artifact_path`` is the canonical project-relative path for registration/claims; ``path`` is the
    absolute path reading current bytes. Pending renames may make them differ. Observers needing stability
    evidence supply ``content_digest``; others leave it empty for computation during basis construction."""

    artifact_path: str
    path: Path
    content_digest: str | None = None


class InputObservation(Protocol):
    """Sole seam for generation inputs to read project state."""

    def observe(self, artifact_path: str) -> ObservedFile | None:
        """Return observation when files are present and paths safe, otherwise ``None``."""
        ...

    def registered(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        """Manifest entry for the key, ``None`` if unregistered."""
        ...


class ManifestInputObservation:
    """Executor/preview observation: read registrations through manifest adapters and observe file presence without computing digests.

    One instance serves one assembly."""

    def __init__(self, project_dir: Path, manifest: ArtifactManifestAdapter) -> None:
        self._project_dir = project_dir
        self._manifest = manifest
        self._entries: Mapping[ArtifactKey, ArtifactManifestEntry] | None = None

    def observe(self, artifact_path: str) -> ObservedFile | None:
        observation = self._manifest.inspect_artifact(artifact_path)
        if observation.blocker is not None or not observation.present:
            return None
        return ObservedFile(
            artifact_path=observation.artifact_path,
            path=self._project_dir.joinpath(*Path(observation.artifact_path).parts),
        )

    def registered(self, key: ArtifactKey) -> ArtifactManifestEntry | None:
        # One assembly reads one manifest snapshot, giving all reference availability decisions the same observation time.
        if self._entries is None:
            self._entries = self._manifest.snapshot_entries()
        return self._entries.get(key)


def project_input_observation(project_dir: Path) -> ManifestInputObservation:
    """Observation based on the project directory's artifact manifest."""

    resolved = Path(project_dir).resolve(strict=True)
    return ManifestInputObservation(resolved, ProjectArtifactManifestAdapter(resolved))


@dataclass(frozen=True, slots=True)
class InputRefused:
    """All gaps for one target: semantic first, references in assembly order, deduplicated per item."""

    reasons: tuple[InputGap, ...]

    def __post_init__(self) -> None:
        if not self.reasons:
            raise ValueError("a refused generation input must carry at least one gap")

    @property
    def codes(self) -> tuple[str, ...]:
        """Gap codes deduplicated in first-occurrence order."""

        return tuple(dict.fromkeys(gap.code for gap in self.reasons))


@dataclass(frozen=True, slots=True)
class AssembledReference:
    """One reference image in assembly order.

    ``visual`` carries logical identity/current read path; ``artifact_path`` is canonical relative path.
    ``claim`` is a candidate input claim produced alongside usable status. Originals are unregistered
    artifacts and have no claims."""

    visual: VisualReference
    artifact_path: str
    claim: ArtifactInputClaim | None = None


@dataclass(frozen=True, slots=True)
class StoryboardImageSemantics:
    """Semantic inputs for a storyboard image."""

    resource_id: str
    image_prompt: object
    style: str
    style_description: str
    #: schema < 19 retains name-bearing basis rules (``project_basis_records_asset_names``).
    asset_names: bool = False


@dataclass(frozen=True, slots=True)
class AssetSheetSemantics:
    """Sheet semantic inputs: description only from stored project entries; ``name`` uses comparison coordinates."""

    asset_type: str
    name: str
    description: str
    style: str
    style_description: str


@dataclass(frozen=True, slots=True)
class DerivativeSheetSemantics:
    """Derivative-sheet semantics: appearance changes relative to base only; base sheets carry style."""

    owner: str
    derivative: str
    description: str


type GenerationSemantics = StoryboardImageSemantics | AssetSheetSemantics | DerivativeSheetSemantics


@dataclass(frozen=True, slots=True)
class RenderedInput:
    """Prompt rendered after provider-limit cropping; ``warnings`` matches task-result ``{key, params}`` shape."""

    prompt: str
    sent_count: int
    warnings: tuple[dict[str, Any], ...] = ()


#: Rebind candidate claims by content digests after freezing: inputs are candidate claims and canonical-relative-path → frozen-byte-digest mapping.
ClaimBinder = Callable[[Sequence[ArtifactInputClaim], Mapping[str, str]], Sequence[ArtifactInputClaim]]


class FrozenGenerationInput:
    """Frozen inputs: sent references, rendered prompt, registration basis, and bound claims.

    Use as a context manager, cleaning task-private reference snapshots on exit."""

    __slots__ = ("basis", "claims", "prompt", "references", "warnings")

    def __init__(
        self,
        *,
        prompt: str,
        references: FrozenImageReferences,
        basis: ArtifactBasis,
        claims: tuple[ArtifactInputClaim, ...],
        warnings: tuple[dict[str, Any], ...],
    ) -> None:
        self.prompt = prompt
        #: First N reference images sent with requests, cropped by provider limits.
        self.references = references
        #: Registration basis constructed from the full frozen set.
        self.basis = basis
        self.claims = claims
        self.warnings = warnings

    def __enter__(self) -> FrozenGenerationInput:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.references.cleanup()


def _refusal(gaps: tuple[InputGap, ...]) -> InputRefused | None:
    return InputRefused(reasons=gaps) if gaps else None


@dataclass(frozen=True)
class ImageGenerationInput[S: GenerationSemantics]:
    """Generation inputs: semantics, aspect ratio, full assembly-ordered references, and all gaps.

    With gaps, missing semantic values stay empty and missing images are absent from references; only
    expected-basis projection is possible, not rendering/freezing."""

    semantics: S
    canvas_ratio: str
    #: Complete assembly order, without cropping.
    references: tuple[AssembledReference, ...]
    #: Semantic gaps first, reference gaps in assembly order, deduplicated per item.
    gaps: tuple[InputGap, ...] = ()

    @property
    def refusal(self) -> InputRefused | None:
        """All gaps refusing generation, ``None`` when inputs are valid."""

        return _refusal(self.gaps)

    def expected_basis(self) -> ArtifactBasis:
        """Expected basis projected from current project state for planner currency checks, recording missing inputs absent."""

        return _visual_basis(
            self.semantics,
            self.canvas_ratio,
            [reference.visual for reference in self.references],
            absent=self.gaps,
        )

    def render(self, *, max_reference_images: int | None, model: str) -> RenderedInput:
        """Render after provider-limit cropping without freezing references; preview uses the same numbering as :meth:`freeze`."""

        self._require_complete()
        visuals = [reference.visual for reference in self.references]
        return _render_clamped(self.semantics, visuals, max_reference_images=max_reference_images, model=model)

    def freeze(
        self, *, max_reference_images: int | None, model: str, bind_claims: ClaimBinder
    ) -> FrozenGenerationInput:
        """Freeze the full reference set into a task-private snapshot, then bind claims, crop, render, and construct registration bases.

        Bases/claims use the full frozen set; providers receive only the first N images. Cropping adds one warning."""

        self._require_complete()
        return _freeze(
            self.semantics,
            self.canvas_ratio,
            self.references,
            max_reference_images=max_reference_images,
            model=model,
            bind_claims=bind_claims,
        )

    def _require_complete(self) -> None:
        if self.gaps:
            raise ValueError("generation input has gaps; it can only project an expected basis")


class StoryboardImageInput(ImageGenerationInput[StoryboardImageSemantics]):
    """Valid generation inputs for one ordinary storyboard image."""


class AssetSheetInput(ImageGenerationInput[AssetSheetSemantics]):
    """Valid generation inputs for one character/scene/prop/product sheet."""


class DerivativeSheetInput(ImageGenerationInput[DerivativeSheetSemantics]):
    """Valid derivative-sheet generation inputs: the base sheet is the sole reference."""


def storyboard_image_input(
    project: Mapping[str, Any],
    script: dict[str, Any],
    *,
    episode: int,
    resource_id: str,
    observation: InputObservation,
) -> StoryboardImageInput:
    """Assemble generation inputs for one storyboard from current project state.

    Order: products (sheet then originals per product; originals only without sheets) → character/scene/prop
    sheets in field order → previous storyboard (index > 0 and not ``segment_break``). Deduplicate the
    whole sequence by path, retaining first occurrence.

    Gaps: unregistered references; missing/unavailable character/scene/prop sheets; declared but unavailable
    product sheets; declared but unreadable originals; pending prompts. Omit unavailable previous storyboards.
    Products with neither sheets nor originals use text only, not a gap.

    Absent entries/corrupt field structure raise ``ValueError``."""

    items, id_field, char_field, scene_field, prop_field = get_storyboard_items(script)
    resolved = find_storyboard_item(items, id_field, resource_id)
    if resolved is None:
        raise ValueError(f"scene/segment not found: {resource_id}")
    item, index = resolved

    style = project.get("style", "")
    style_description = project.get("style_description", "")
    if not isinstance(style, str) or not isinstance(style_description, str):
        raise ValueError("storyboard style and style description must be strings")
    canvas_ratio = resolve_video_aspect_ratio(project, "storyboards")

    catalog = build_reference_catalog(project)
    assembly = _Assembly(observation)
    image_prompt = item.get("image_prompt")
    if image_prompt is None:
        assembly.gap(InputGap(PROMPT_PENDING_CODE, name=resource_id))

    for name in _reference_names(item, "products_in_shot"):
        assembly.add_product(catalog, name)
    for asset_type, field in (("character", char_field), ("scene", scene_field), ("prop", prop_field)):
        for name in _reference_names(item, field):
            assembly.add_sheet(catalog, asset_type, name)

    if index > 0 and not item.get("segment_break"):
        assembly.add_previous_storyboard(items[index - 1], id_field=id_field, episode=episode)

    return StoryboardImageInput(
        semantics=StoryboardImageSemantics(
            resource_id=resource_id,
            image_prompt=image_prompt,
            style=style,
            style_description=style_description,
            asset_names=project_basis_records_asset_names(project),
        ),
        canvas_ratio=canvas_ratio,
        references=tuple(assembly.references),
        gaps=tuple(assembly.gaps),
    )


@dataclass(frozen=True, slots=True)
class GridReferences:
    """Reference set/gaps for a grid; grid executors construct composite bases from frozen references."""

    #: Complete assembly order, without cropping.
    references: tuple[AssembledReference, ...]
    #: Reference gaps in assembly order, deduplicated per item.
    gaps: tuple[InputGap, ...] = ()

    @property
    def refusal(self) -> InputRefused | None:
        """All gaps refusing generation, ``None`` when the reference set is valid."""

        return _refusal(self.gaps)


def grid_references(
    project: Mapping[str, Any],
    script: dict[str, Any],
    *,
    member_ids: Sequence[str],
    observation: InputObservation,
) -> GridReferences:
    """Assemble grid references from current project state.

    In ``member_ids`` order, obtain each storyboard member's character/scene/prop sheets in field order,
    union/deduplicate by path, keeping first occurrence. Exclude products and previous storyboards.

    Gaps: unregistered references; missing/unavailable character/scene/prop sheets.

    Absent members/corrupt field structure raise ``ValueError``."""

    items, id_field, char_field, scene_field, prop_field = get_storyboard_items(script)
    catalog = build_reference_catalog(project)
    assembly = _Assembly(observation)
    for member_id in member_ids:
        resolved = find_storyboard_item(items, id_field, member_id)
        if resolved is None:
            raise ValueError(f"scene/segment not found: {member_id}")
        item, _index = resolved
        for asset_type, field in (("character", char_field), ("scene", scene_field), ("prop", prop_field)):
            for name in _reference_names(item, field):
                assembly.add_sheet(catalog, asset_type, name)

    return GridReferences(references=tuple(assembly.references), gaps=tuple(assembly.gaps))


def asset_sheet_input(
    project: Mapping[str, Any],
    *,
    asset_type: str,
    name: str,
    observation: InputObservation,
) -> AssetSheetInput:
    """Assemble sheet generation inputs from current project state.

    Descriptions come only from stored project entries. References are author-uploaded originals: 0–1 for
    characters, all for products, none for scenes/props. Originals are unregistered and have no claims.

    Gaps: empty description, declared but unreadable originals.

    Absent assets/corrupt field structure raise ``ValueError``."""

    spec = ASSET_SPECS[asset_type]
    asset_name, asset = _asset_entry(project.get(spec.bucket_key), name, label=asset_type)
    style = project.get("style", "")
    style_description = project.get("style_description", "")
    if not isinstance(style, str) or not isinstance(style_description, str):
        raise ValueError("asset sheet style and style description must be strings")

    assembly = _Assembly(observation)
    description = _stored_description(asset)
    if not description:
        assembly.gap(InputGap(ASSET_DESCRIPTION_REQUIRED_CODE, asset_type, asset_name))
    for original in _asset_originals(asset, spec):
        assembly.add_original(asset_type, asset_name, original)

    return AssetSheetInput(
        semantics=AssetSheetSemantics(
            asset_type=asset_type,
            name=asset_name,
            description=description,
            style=style,
            style_description=style_description,
        ),
        canvas_ratio=ASSET_SHEET_CANVAS_RATIO,
        references=tuple(assembly.references),
        gaps=tuple(assembly.gaps),
    )


def derivative_sheet_input(
    project: Mapping[str, Any],
    *,
    owner: str,
    derivative: str,
    observation: InputObservation,
) -> DerivativeSheetInput:
    """Assemble derivative-sheet inputs from current project state.

    The base sheet is the sole reference (``docs/adr/0072``), with availability checked like sheets referenced
    by storyboards.

    Gaps: missing derivative appearance-change description; undeclared/unavailable base sheet.

    Absent base/derivative or corrupt field structure raises ``ValueError``."""

    spec = ASSET_SPECS[DERIVATIVE_ASSET_TYPE]
    owner_name, owner_asset = _asset_entry(project.get(spec.bucket_key), owner, label=DERIVATIVE_ASSET_TYPE)
    derivative_name, derivative_asset = _asset_entry(
        owner_asset.get(DERIVATIVES_FIELD), derivative, label=f"derivative of {owner_name}"
    )

    assembly = _Assembly(observation)
    description = _stored_description(derivative_asset)
    if not description:
        assembly.gap(InputGap(DERIVATIVE_DESCRIPTION_REQUIRED_CODE, DERIVATIVE_ASSET_TYPE, derivative_name))
    assembly.add_owner_sheet(spec, owner_name, owner_asset)

    return DerivativeSheetInput(
        semantics=DerivativeSheetSemantics(owner=owner_name, derivative=derivative_name, description=description),
        canvas_ratio=ASSET_SHEET_CANVAS_RATIO,
        references=tuple(assembly.references),
        gaps=tuple(assembly.gaps),
    )


def _asset_entry(bucket: object, name: str, *, label: str) -> tuple[str, Mapping[str, Any]]:
    """Get an asset-table entry by trimmed/NFC identity, returning identity name and entry.

    Use target-state-planner sheet-registration coordinates: existing whitespace-padded keys address by
    trimmed name. Last-written keys win among identical identities. Missing/non-object entries raise ``ValueError``."""

    identity = asset_name_comparison_key(name)
    found: object = None
    if isinstance(bucket, Mapping):
        for key, entry in bucket.items():
            if isinstance(key, str) and asset_name_comparison_key(key) == identity:
                found = entry
    if not identity or not isinstance(found, Mapping):
        raise ValueError(f"{label} not found: {name}")
    return identity, found


def _stored_description(asset: Mapping[str, Any]) -> str:
    """Stored entry description, trimmed; empty string if absent, ``ValueError`` on wrong types."""

    description = asset.get("description")
    if description is None:
        return ""
    if not isinstance(description, str):
        raise ValueError("asset description must be a string")
    return description.strip()


def _asset_originals(asset: Mapping[str, Any], spec: AssetSpec) -> list[str]:
    """Registered original paths in field declaration order; lists for schema-list fields, single values otherwise."""

    paths: list[str] = []
    for field in spec.original_image_fields:
        if field in spec.extra_list_fields:
            paths.extend(_declared_originals(asset, field))
        elif (path := _declared_path(asset, field)) is not None:
            paths.append(path)
    return paths


def _reference_names(item: Mapping[str, Any], field: str | None) -> list[str]:
    if field is None:
        return []
    values = item.get(field)
    if values is None:
        return []
    if not isinstance(values, list | tuple) or any(not isinstance(value, str) for value in values):
        raise ValueError(f"storyboard reference field {field} must be a list of names")
    return [value for value in values if value]


class _Assembly:
    """Assembly accumulator: deduplicate references by path and gaps by content, preserving both orders."""

    def __init__(self, observation: InputObservation) -> None:
        self._observation = observation
        self.references: list[AssembledReference] = []
        self.gaps: list[InputGap] = []
        self._seen_paths: set[str] = set()

    def gap(self, gap: InputGap) -> None:
        if gap not in self.gaps:
            self.gaps.append(gap)

    def _append(self, reference: AssembledReference) -> None:
        if reference.artifact_path in self._seen_paths:
            return
        self._seen_paths.add(reference.artifact_path)
        self.references.append(reference)

    def _available(self, key: ArtifactKey, declared_path: str) -> tuple[ObservedFile, ArtifactInputClaim] | None:
        """Sole usable criterion: manifest registration at this path plus file presence; success also produces a candidate claim."""

        entry = self._observation.registered(key)
        if entry is None:
            return None
        observed = self._observation.observe(declared_path)
        if observed is None or observed.artifact_path != entry.artifact_path:
            return None
        return observed, ArtifactInputClaim(key=key, artifact_path=observed.artifact_path)

    def add_sheet(self, catalog: ReferenceCatalog, asset_type: str, name: str) -> None:
        entry = catalog.lookup(asset_type, name)
        if entry is None:
            self.gap(InputGap(UNREGISTERED_REFERENCE_CODE, asset_type, name))
            return
        name = entry.name
        sheet = _declared_path(entry.asset, entry.spec.sheet_field)
        available = (
            self._available(ArtifactKey.asset_sheet(asset_type, entry.name), sheet) if sheet is not None else None
        )
        if available is None:
            self.gap(InputGap(SHEET_MISSING_CODE, asset_type, name))
            return
        observed, claim = available
        self._append(
            AssembledReference(
                visual=_visual(observed, role="asset_sheet", logical_type=asset_type, logical_id=name, kind="sheet"),
                artifact_path=observed.artifact_path,
                claim=claim,
            )
        )

    def add_product(self, catalog: ReferenceCatalog, name: str) -> None:
        entry = catalog.lookup("product", name)
        if entry is None:
            self.gap(InputGap(UNREGISTERED_REFERENCE_CODE, "product", name))
            return
        name = entry.name
        sheet = _declared_path(entry.asset, entry.spec.sheet_field)
        if sheet is not None:
            available = self._available(ArtifactKey.asset_sheet("product", entry.name), sheet)
            if available is None:
                self.gap(InputGap(SHEET_MISSING_CODE, "product", name))
            else:
                observed, claim = available
                self._append(
                    AssembledReference(
                        visual=_visual(
                            observed, role="asset_sheet", logical_type="product", logical_id=name, kind="sheet"
                        ),
                        artifact_path=observed.artifact_path,
                        claim=claim,
                    )
                )
        for original in _declared_originals(entry.asset, "reference_images"):
            self.add_original("product", name, original)

    def add_original(self, asset_type: str, name: str, original: str) -> None:
        """One sheet original: declared but unreadable means a gap."""

        observed = self._observation.observe(original)
        if observed is None:
            self.gap(InputGap(ORIGINAL_MISSING_CODE, asset_type, name))
            return
        self._append(
            AssembledReference(
                visual=_visual(observed, role="source", logical_type=asset_type, logical_id=name, kind="original"),
                artifact_path=observed.artifact_path,
            )
        )

    def add_owner_sheet(self, spec: AssetSpec, owner: str, asset: Mapping[str, Any]) -> None:
        """Derivative-sheet source: undeclared/unavailable base sheets mean a gap."""

        sheet = _declared_path(asset, spec.sheet_field)
        available = self._available(ArtifactKey.asset_sheet(spec.asset_type, owner), sheet) if sheet else None
        if available is None:
            self.gap(InputGap(DERIVATIVE_OWNER_SHEET_MISSING_CODE, spec.asset_type, owner))
            return
        observed, claim = available
        self._append(
            AssembledReference(
                visual=_visual(
                    observed,
                    role=DERIVATIVE_SOURCE_ROLE,
                    logical_type=spec.asset_type,
                    logical_id=owner,
                    kind=DERIVATIVE_SOURCE_KIND,
                ),
                artifact_path=observed.artifact_path,
                claim=claim,
            )
        )

    def add_previous_storyboard(self, previous_item: object, *, id_field: str, episode: int) -> None:
        """Previous storyboard is an optional system-derived input: omit unavailable ones without gaps."""

        if not isinstance(previous_item, Mapping):
            return
        previous_id = str(previous_item.get(id_field) or "").strip()
        assets = previous_item.get("generated_assets")
        if not previous_id or not isinstance(assets, Mapping):
            return
        declared = _declared_path(assets, "storyboard_image")
        if declared is None:
            return
        available = self._available(ArtifactKey.episode_storyboard(episode, previous_id), declared)
        if available is None:
            return
        observed, claim = available
        self._append(
            AssembledReference(
                visual=_visual(
                    observed, role=PREVIOUS_STORYBOARD_ROLE, logical_type="storyboard", logical_id=previous_id
                ),
                artifact_path=observed.artifact_path,
                claim=claim,
            )
        )


def _declared_path(container: object, field: str) -> str | None:
    """Declared entry path; ``None`` when undeclared (missing field, ``None``, empty string), ``ValueError`` on wrong types."""

    if not isinstance(container, Mapping):
        return None
    value = container.get(field)
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a project-relative path")
    return value


def _declared_originals(container: object, field: str) -> list[str]:
    if not isinstance(container, Mapping):
        return []
    values = container.get(field)
    if values is None:
        return []
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
        raise ValueError(f"{field} must be a list of project-relative paths")
    return [value for value in values if value]


def _visual(
    observed: ObservedFile,
    *,
    role: str,
    logical_type: str,
    logical_id: str,
    kind: str | None = None,
) -> VisualReference:
    return VisualReference(
        path=observed.path,
        role=role,
        logical_type=logical_type,
        logical_id=logical_id,
        kind=kind,
        content_digest=observed.content_digest,
    )


def _visual_basis(
    semantics: GenerationSemantics,
    canvas_ratio: str,
    references: Sequence[VisualReference],
    *,
    absent: Sequence[InputGap] = (),
) -> ArtifactBasis:
    """Shared constructor for expected/registered bases, dispatched by semantic type to visual-basis constructors.

    ``absent`` comes only from expected bases: registered bases come from freezing, which accepts only gap-free inputs."""

    match semantics:
        case StoryboardImageSemantics():
            return build_storyboard_image_visual_basis(
                resource_id=semantics.resource_id,
                image_prompt=semantics.image_prompt,
                style=semantics.style,
                style_description=semantics.style_description,
                aspect_ratio=canvas_ratio,
                references=references,
                absent=absent,
                asset_names=semantics.asset_names,
            )
        case AssetSheetSemantics():
            return build_asset_sheet_visual_basis(
                asset_type=semantics.asset_type,
                asset_id=semantics.name,
                description=semantics.description,
                style=semantics.style,
                style_description=semantics.style_description,
                aspect_ratio=canvas_ratio,
                references=references,
                absent=absent,
            )
        case DerivativeSheetSemantics():
            # Unavailable base sheets mean no source; record the gap in absent.
            [*sources] = references
            if len(sources) > 1:
                raise ValueError("a derivative sheet has at most one source image")
            return build_derivative_sheet_basis(
                owner_name=semantics.owner,
                derivative_name=semantics.derivative,
                description=semantics.description,
                aspect_ratio=canvas_ratio,
                source=sources[0] if sources else None,
                absent=absent,
            )


#: Sheet-prompt renderers by asset type.
_ASSET_SHEET_PROMPT_BUILDERS: dict[str, Callable[[str, str, str, str], str]] = {
    "character": build_character_prompt,
    "scene": build_scene_prompt,
    "prop": build_prop_prompt,
    "product": build_product_prompt,
}


def _render_prompt(semantics: GenerationSemantics, sent: Sequence[VisualReference]) -> str:
    match semantics:
        case StoryboardImageSemantics():
            return render_storyboard_image_prompt(
                semantics.image_prompt,
                style=semantics.style,
                style_description=semantics.style_description,
                references=sent,
            )
        case AssetSheetSemantics():
            builder = _ASSET_SHEET_PROMPT_BUILDERS[semantics.asset_type]
            return builder(semantics.name, semantics.description, semantics.style, semantics.style_description)
        case DerivativeSheetSemantics():
            return build_character_derivative_prompt(semantics.description)


def _render_clamped(
    semantics: GenerationSemantics,
    visuals: Sequence[VisualReference],
    *,
    max_reference_images: int | None,
    model: str,
) -> RenderedInput:
    clamp = clamp_reference_images(visuals, max_reference_images, model=model)
    warning = clamp.warning()
    return RenderedInput(
        prompt=_render_prompt(semantics, visuals[: clamp.kept]),
        sent_count=clamp.kept,
        warnings=(warning,) if warning is not None else (),
    )


def _freeze(
    semantics: GenerationSemantics,
    canvas_ratio: str,
    references: Sequence[AssembledReference],
    *,
    max_reference_images: int | None,
    model: str,
    bind_claims: ClaimBinder,
) -> FrozenGenerationInput:
    frozen = freeze_image_references(
        [{"image": reference.visual.path} for reference in references] or None,
        [reference.visual for reference in references],
    )
    try:
        content_digests: dict[str, str] = {}
        for reference, frozen_visual in zip(references, frozen.visual_references, strict=True):
            if frozen_visual.content_digest is None:
                raise ValueError("frozen visual reference has no content digest")
            content_digests[reference.artifact_path] = frozen_visual.content_digest
        claims = tuple(
            bind_claims([reference.claim for reference in references if reference.claim is not None], content_digests)
        )
        basis = _visual_basis(semantics, canvas_ratio, frozen.visual_references)
        rendered = _render_clamped(
            semantics,
            frozen.visual_references,
            max_reference_images=max_reference_images,
            model=model,
        )
    except BaseException:
        frozen.cleanup()
        raise
    return FrozenGenerationInput(
        prompt=rendered.prompt,
        references=frozen.sent(rendered.sent_count),
        basis=basis,
        claims=claims,
        warnings=rendered.warnings,
    )


__all__ = [
    "ASSET_DESCRIPTION_REQUIRED_CODE",
    "ASSET_SHEET_CANVAS_RATIO",
    "DERIVATIVE_DESCRIPTION_REQUIRED_CODE",
    "DERIVATIVE_OWNER_SHEET_MISSING_CODE",
    "ORIGINAL_MISSING_CODE",
    "PROMPT_PENDING_CODE",
    "AssembledReference",
    "AssetSheetInput",
    "AssetSheetSemantics",
    "ClaimBinder",
    "DerivativeSheetInput",
    "DerivativeSheetSemantics",
    "FrozenGenerationInput",
    "GenerationSemantics",
    "GridReferences",
    "ImageGenerationInput",
    "InputGap",
    "InputObservation",
    "InputRefused",
    "ManifestInputObservation",
    "ObservedFile",
    "RenderedInput",
    "StoryboardImageInput",
    "StoryboardImageSemantics",
    "asset_sheet_input",
    "derivative_sheet_input",
    "grid_references",
    "project_input_observation",
    "storyboard_image_input",
]
