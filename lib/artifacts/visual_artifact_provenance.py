"""Canonical visual-content provenance for Artifact Manifest currency.

The builders in this module describe formal visual content. They deliberately do
not describe provider request equivalence: provider/model selection, credentials,
pixel resolution, seeds, audio controls, and prompt-renderer revisions are not
Artifact Manifest currency inputs.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from lib.artifacts.artifact_manifest import ArtifactBasis
from lib.artifacts.input_gap import InputGap, add_absent_inputs
from lib.material.work_files import material_file_digest
from lib.project.asset_types import ASSET_TYPES, asset_name_comparison_key, normalize_asset_name
from lib.project.project_schema import parse_project_schema_version
from lib.prompts.prompt_style import normalize_style_value
from lib.prompts.prompt_utils import project_storyboard_image_prompt
from lib.prompts.reference_image_numbering import reference_image_label
from lib.script.grid.prompt_builder import project_grid_image_prompt
from lib.script.reference_video.request_projection import ResolvedReferenceAsset
from lib.script.reference_video.text_parser import render_matched_mentions, strip_speech_marks


@dataclass(frozen=True, slots=True)
class VisualReference:
    """One ordered image actually supplied while producing formal visual content.

    Filesystem locations are transport details. The canonical evidence therefore
    records logical identity, role, variant, and content bytes, but not the path.
    """

    path: Path
    role: str
    logical_type: str | None = None
    logical_id: str | None = None
    kind: str | None = None
    content_digest: str | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        _require_non_empty("visual reference role", self.role)
        if (self.logical_type is None) != (self.logical_id is None):
            raise ValueError("visual reference logical_type and logical_id must be provided together")
        if self.logical_type is not None:
            _require_non_empty("visual reference logical_type", self.logical_type)
            logical_id = _require_non_empty("visual reference logical_id", self.logical_id)
            object.__setattr__(self, "logical_id", normalize_asset_name(logical_id))
        if self.kind is not None:
            _require_non_empty("visual reference kind", self.kind)
        if self.content_digest is not None:
            _require_sha256("visual reference content_digest", self.content_digest)

    def evidence(self) -> dict[str, object]:
        """Return path-independent, content-addressed manifest evidence."""

        evidence: dict[str, object] = {
            "role": self.role,
            "sha256": self.content_digest or visual_file_digest(self.path),
        }
        if self.logical_type is not None:
            evidence["logical_identity"] = {
                "type": self.logical_type,
                "id": self.logical_id,
            }
        if self.kind is not None:
            evidence["kind"] = self.kind
        return evidence


@dataclass(frozen=True, slots=True)
class GridStoryboardVisual:
    """Stable visual facts for one storyboard item participating in a grid."""

    resource_id: str
    image_prompt: object
    video_prompt: object

    def __post_init__(self) -> None:
        _require_non_empty("grid member resource_id", self.resource_id)


def snapshot_visual_references(references: Sequence[VisualReference]) -> tuple[VisualReference, ...]:
    """Freeze the observed bytes behind logical visual references.

    Callers that separate input selection from formal registration can retain
    one content-addressed observation even if a canonical path is atomically
    replaced while the operation is in flight.
    """

    return tuple(
        replace(
            reference,
            content_digest=reference.content_digest or visual_file_digest(reference.path),
        )
        for reference in references
    )


def visual_references_match_snapshot(references: Sequence[VisualReference]) -> bool:
    """Verify that every selected reference still has its frozen content digest."""

    for reference in references:
        if reference.content_digest is None:
            raise ValueError("visual reference comparison requires frozen snapshots")
        try:
            current_digest = visual_file_digest(reference.path)
        except OSError:
            return False
        if current_digest != reference.content_digest:
            return False
    return True


def build_asset_sheet_visual_basis(
    *,
    asset_type: str,
    asset_id: str,
    description: str,
    style: str,
    style_description: str,
    aspect_ratio: str,
    references: Sequence[VisualReference] = (),
    absent: Sequence[InputGap] = (),
) -> ArtifactBasis:
    """Describe one character, scene, prop, or product design sheet.

    ``absent`` records the generation-input gaps (``docs/adr/0098``). An empty
    description is accepted only together with recorded gaps.
    """

    if asset_type not in ASSET_TYPES:
        raise ValueError(f"unsupported asset type: {asset_type!r}")
    canonical_id = normalize_asset_name(_require_non_empty("asset_id", asset_id))
    normalized_description = _require_string("description", description).strip()
    if not absent:
        _require_non_empty("description", normalized_description)
    _require_string("style", style)
    _require_string("style_description", style_description)
    canvas_ratio = _require_non_empty("aspect_ratio", aspect_ratio)
    inputs: dict[str, object] = {
        "asset": {
            "type": asset_type,
            "id": canonical_id,
            "description": normalized_description,
        },
        "canvas": {"aspect_ratio": canvas_ratio},
        "references": _reference_evidence(references),
    }
    if asset_type != "product":
        inputs["style"] = {
            "name": style,
            "description": style_description,
        }
    add_absent_inputs(inputs, absent)
    return ArtifactBasis.build(
        "artifact-visual/asset-sheet",
        kind_version=1,
        inputs=inputs,
    )


UPLOADED_ASSET_SHEET_BASIS_KIND = "artifact-visual/asset-sheet-upload"
"""Basis kind for asset/derivative sheets carried in as finished media: image bytes only; descriptions, style, and base sheets are not inputs."""


def build_uploaded_asset_sheet_basis(*, asset_type: str, content_digest: str) -> ArtifactBasis:
    """Describe an asset sheet or derivative sheet brought in as finished content.

    Author uploads and sheets applied from the global asset library are the
    finished content itself: their basis is the image bytes alone, so
    description, project style, the owner sheet, and the asset's name are not
    inputs and editing them never makes the sheet stale.
    """

    if asset_type not in ASSET_TYPES:
        raise ValueError(f"unsupported asset type: {asset_type!r}")
    return ArtifactBasis.build(
        UPLOADED_ASSET_SHEET_BASIS_KIND,
        kind_version=1,
        inputs={
            "asset": {"type": asset_type},
            "upload": {"sha256": _require_sha256("uploaded sheet content_digest", content_digest)},
        },
    )


UPLOADED_STORYBOARD_BASIS_KIND = "artifact-visual/storyboard-upload"
"""Basis kind for manually uploaded storyboards: upload bytes only; prompts/reference images are not inputs."""

UPLOADED_VIDEO_BASIS_KIND = "artifact-video/upload"


def build_uploaded_storyboard_basis(*, content_digest: str) -> ArtifactBasis:
    """Describe a storyboard image the author uploaded as finished content.

    The basis is the uploaded bytes alone: the prompt, referenced asset sheets,
    project style, and the previous storyboard are not inputs, so editing them
    never makes the upload stale.
    """

    return ArtifactBasis.build(
        UPLOADED_STORYBOARD_BASIS_KIND,
        kind_version=1,
        inputs={"upload": {"sha256": _require_sha256("uploaded storyboard content_digest", content_digest)}},
    )


def build_uploaded_video_basis(*, content_digest: str) -> ArtifactBasis:
    """Describe a video the author uploaded as finished content.

    The basis is the uploaded bytes alone: prompts, speech, duration, the
    storyboard image, and reference assets are not inputs.
    """

    return ArtifactBasis.build(
        UPLOADED_VIDEO_BASIS_KIND,
        kind_version=1,
        inputs={"upload": {"sha256": _require_sha256("uploaded video content_digest", content_digest)}},
    )


def build_storyboard_image_visual_basis(
    *,
    resource_id: str,
    image_prompt: object,
    style: str,
    aspect_ratio: str,
    style_description: str = "",
    references: Sequence[VisualReference] = (),
    absent: Sequence[InputGap] = (),
    asset_names: bool = False,
) -> ArtifactBasis:
    """Describe one ordinary storyboard image and its actual ordered image inputs.

    ``absent`` records the generation-input gaps (``docs/adr/0098``). A pending
    ``image_prompt`` (``None``) is accepted only together with recorded gaps.
    ``asset_names`` keeps the pre-v19 shape that records asset names (see
    :func:`project_basis_records_asset_names`).
    """

    identity = _require_non_empty("resource_id", resource_id)
    _require_string("style", style)
    _require_string("style_description", style_description)
    prompt: object
    if image_prompt is None:
        if not absent:
            raise ValueError("image_prompt is pending; a storyboard image has no visual basis yet")
        prompt, style_input = None, style
    else:
        prompt, style_input = project_storyboard_image_prompt(image_prompt, style)
    inputs: dict[str, object] = {
        "resource_id": identity,
        "image_prompt": prompt,
        "canvas": {"aspect_ratio": _require_non_empty("aspect_ratio", aspect_ratio)},
        "references": _reference_evidence(references),
    }
    if style_input or not isinstance(prompt, str):
        inputs["style"] = style_input
    _add_style_description(inputs, style_description)
    add_absent_inputs(inputs, absent)
    return _build_image_basis(_STORYBOARD_IMAGE_KIND, inputs, asset_names=asset_names)


def build_grid_composite_visual_basis(
    *,
    group_id: str,
    members: Sequence[GridStoryboardVisual],
    rows: int,
    columns: int,
    style: str,
    grid_aspect_ratio: str,
    style_description: str = "",
    references: Sequence[VisualReference] = (),
    absent: Sequence[InputGap] = (),
    asset_names: bool = False,
) -> ArtifactBasis:
    """Describe one grid composite without hashing its rendered provider prompt.

    ``absent`` records reference images that are no longer available (``docs/adr/0098``).
    ``asset_names`` keeps the pre-v19 shape that records asset names.
    """

    member_tuple = _validate_grid_members(members, rows=rows, columns=columns)
    _require_string("style", style)
    inputs: dict[str, object] = {
        "group_id": _require_non_empty("group_id", group_id),
        "cells": _project_grid_cells(member_tuple),
        "layout": {
            "rows": rows,
            "columns": columns,
            "grid_aspect_ratio": _require_non_empty("grid_aspect_ratio", grid_aspect_ratio),
        },
        "style": style,
        "references": _reference_evidence(references),
    }
    _add_style_description(inputs, style_description)
    add_absent_inputs(inputs, absent)
    return _build_image_basis(_GRID_COMPOSITE_KIND, inputs, asset_names=asset_names)


def build_grid_member_storyboard_visual_basis(
    *,
    group_id: str,
    members: Sequence[GridStoryboardVisual],
    cell_index: int,
    composite_image: Path,
    rows: int,
    columns: int,
    style: str,
    member_aspect_ratio: str,
    style_description: str = "",
    references: Sequence[VisualReference] = (),
    source_composite_digest: str | None = None,
    absent: Sequence[InputGap] = (),
    asset_names: bool = False,
) -> ArtifactBasis:
    """Describe one split cell while preserving grid dependency locality.

    The member does not embed the composite's target basis. It records only the
    selected cell's semantic inputs plus the actual composite bytes it was split
    from. Editing a different cell therefore leaves this member current until a
    replacement composite is really produced. ``asset_names`` keeps the pre-v19
    shape that records asset names.
    """

    member_tuple = _validate_grid_members(members, rows=rows, columns=columns)
    if type(cell_index) is not int or not 0 <= cell_index < len(member_tuple):
        raise ValueError("cell_index must identify a content cell")
    _require_string("style", style)
    inputs: dict[str, object] = {
        "group_id": _require_non_empty("group_id", group_id),
        "cell": _project_grid_cells(member_tuple)[cell_index],
        "layout": {
            "rows": rows,
            "columns": columns,
            "member_aspect_ratio": _require_non_empty("member_aspect_ratio", member_aspect_ratio),
        },
        "style": style,
        "references": _reference_evidence(references),
        "source_composite": _composite_evidence(composite_image, source_composite_digest),
    }
    _add_style_description(inputs, style_description)
    add_absent_inputs(inputs, absent)
    return _build_image_basis(_GRID_MEMBER_KIND, inputs, asset_names=asset_names)


def build_stale_grid_member_storyboard_visual_basis(
    *,
    group_id: str,
    resource_id: str,
    cell_index: int,
    composite_image: Path,
    rows: int,
    columns: int,
    member_aspect_ratio: str,
    source_grid_basis_digest: str,
    source_composite_digest: str | None = None,
) -> ArtifactBasis:
    """Describe a cell derived from a claimed but stale grid composite.

    The old grid inputs cannot be reconstructed from current project state, but
    its frozen claim remains strict source evidence. Committing that claim, the
    selected cell identity, and the actual composite bytes preserves usable
    stale lineage without manufacturing a current storyboard claim.
    """

    if type(rows) is not int or rows < 1 or type(columns) is not int or columns < 1:
        raise ValueError("grid dimensions must be positive integers")
    if type(cell_index) is not int or not 0 <= cell_index < rows * columns:
        raise ValueError("cell_index must identify a grid cell")
    return ArtifactBasis.build(
        "artifact-visual/stale-grid-member",
        kind_version=1,
        inputs={
            "group_id": _require_non_empty("group_id", group_id),
            "resource_id": _require_non_empty("resource_id", resource_id),
            "cell_index": cell_index,
            "layout": {
                "rows": rows,
                "columns": columns,
                "member_aspect_ratio": _require_non_empty("member_aspect_ratio", member_aspect_ratio),
            },
            "source_grid_claim": {
                "basis_digest": _require_non_empty("source_grid_basis_digest", source_grid_basis_digest),
            },
            "source_composite": _composite_evidence(composite_image, source_composite_digest),
        },
    )


def build_storyboard_video_artifact_visual_basis(
    *,
    resource_id: str,
    visual_prompt: object,
    storyboard_image: Path | None,
    end_frame_image: Path | None,
    aspect_ratio: str,
    absent: Sequence[InputGap] = (),
) -> ArtifactBasis:
    """Describe the visual component of one storyboard-driven video.

    Sound design, dialogue, voice profiles, duration, and execution options are
    intentionally absent. They either belong to later video components or are not
    Artifact Manifest currency at all.

    ``absent`` records the generation-input gaps (``docs/adr/0098``). A pending
    ``visual_prompt`` and a missing storyboard image are accepted only together
    with recorded gaps.
    """

    projected_prompt: object
    if visual_prompt is None:
        if not absent:
            raise ValueError("visual_prompt is pending; a storyboard video has no visual basis yet")
        projected_prompt = None
    elif isinstance(visual_prompt, str):
        visual_text = visual_prompt.strip()
        if not visual_text:
            raise ValueError("visual_prompt must not be empty")
        projected_prompt = visual_text
    elif isinstance(visual_prompt, Mapping):
        action = str(visual_prompt.get("action") or "").strip()
        if not action:
            raise ValueError("visual_prompt.action must be a non-empty string")
        projected_prompt = {
            "action": action,
            "camera_motion": str(visual_prompt.get("camera_motion") or "Static"),
        }
    else:
        raise ValueError("visual_prompt must be a string or structured object")
    if storyboard_image is None and not absent:
        raise ValueError("a storyboard video basis without its storyboard image must record the gap")
    frame_evidence: list[dict[str, object]] = []
    if storyboard_image is not None:
        frame_evidence.append({"role": "storyboard", "sha256": visual_file_digest(storyboard_image)})
    if end_frame_image is not None:
        frame_evidence.append({"role": "end_frame", "sha256": visual_file_digest(end_frame_image)})
    inputs: dict[str, object] = {
        "resource_id": _require_non_empty("resource_id", resource_id),
        "visual_prompt": projected_prompt,
        "canvas": {"aspect_ratio": _require_non_empty("aspect_ratio", aspect_ratio)},
        "frames": frame_evidence,
    }
    add_absent_inputs(inputs, absent)
    return ArtifactBasis.build(
        "artifact-visual/video-storyboard",
        kind_version=1,
        inputs=inputs,
    )


def build_reference_video_artifact_visual_basis(
    *,
    unit: Mapping[str, object],
    request_assets: Sequence[ResolvedReferenceAsset],
    style: str | None,
    aspect_ratio: str,
    style_description: str = "",
    absent: Sequence[InputGap] = (),
) -> ArtifactBasis:
    """Describe one canonical ``video_unit`` and the images actually sent for it.

    Only ``unit_id`` and the visual lines of ``text`` are projected; speech-only lines
    never enter the basis, so rewording a line of dialogue does not make a rendered video
    stale. ``request_assets`` must be the already-clamped request projection, so
    provider-truncated declarations cannot make the formal video stale. Declared
    references without an available image are generation-input gaps and are
    recorded in ``absent`` (``docs/adr/0098``).
    """

    unit_id = _require_non_empty("unit.unit_id", unit.get("unit_id"))
    raw_text = unit.get("text")
    if not isinstance(raw_text, str):
        raise ValueError("unit.text must be a string")
    visual_lines = _reference_visual_lines(raw_text)
    references = [
        VisualReference(
            path=asset.path,
            role="reference_image",
            logical_type=asset.reference.type,
            logical_id=asset.reference.name,
            kind=asset.kind,
        )
        for asset in request_assets
    ]
    inputs: dict[str, object] = {
        "unit_id": unit_id,
        "visual_lines": visual_lines,
        "style": style or "",
        "canvas": {"aspect_ratio": _require_non_empty("aspect_ratio", aspect_ratio)},
        "request_references": _reference_evidence(references),
    }
    _add_style_description(inputs, style_description)
    add_absent_inputs(inputs, absent)
    return ArtifactBasis.build(
        "artifact-visual/video-reference",
        kind_version=1,
        inputs=inputs,
    )


#: Grid-composite, split-storyboard, and reference-video bases record style descriptions from this schema onward.
STYLE_DESCRIPTION_BASIS_SCHEMA_VERSION = 14


def project_basis_style_description(project: Mapping[str, object]) -> str:
    """Style description recorded in grid-composite, split-storyboard, and reference-video bases.

    Projects below schema 14 occur only within migration chains. Full activation/provenance backfills
    before v13→v14 retain the historical description-free rules; v13→v14 backfills descriptions together
    with selected-version records."""

    if parse_project_schema_version(project) < STYLE_DESCRIPTION_BASIS_SCHEMA_VERSION:
        return ""
    return normalize_style_value(project.get("style_description"))


#: Storyboard/grid (composite and split) bases omit asset names from this schema onward.
ASSET_NAME_FREE_IMAGE_BASIS_SCHEMA_VERSION = 19

_STORYBOARD_IMAGE_KIND = "artifact-visual/storyboard-image"
_GRID_COMPOSITE_KIND = "artifact-visual/grid-composite"
_GRID_MEMBER_KIND = "artifact-visual/grid-member"

#: Nameless basis kinds. Reference-to-video and asset-image model inputs contain names, retained in bases.
ASSET_NAME_FREE_IMAGE_BASIS_KINDS = frozenset({_STORYBOARD_IMAGE_KIND, _GRID_COMPOSITE_KIND, _GRID_MEMBER_KIND})


def project_basis_records_asset_names(project: Mapping[str, object]) -> bool:
    """Whether storyboard/grid bases still project under rules recording asset names.

    Projects below schema 19 occur only within migration chains. Full activation and before/after planning
    before v18→v19 retain historical asset-name rules matching existing manifest entries. v18→v19 shifts
    entries current before the rewrite together with version records."""

    return parse_project_schema_version(project) < ASSET_NAME_FREE_IMAGE_BASIS_SCHEMA_VERSION


def _build_image_basis(kind: str, inputs: dict[str, object], *, asset_names: bool) -> ArtifactBasis:
    return ArtifactBasis.build(
        kind,
        kind_version=1,
        inputs=inputs if asset_names else without_asset_names(kind, inputs),
    )


def without_asset_names(kind: str, inputs: Mapping[str, object]) -> dict[str, object]:
    """Rewrite storyboard/grid basis inputs to exclude asset names.

    Model inputs for these artifacts omit asset names: type declarations list 图N by type, and body
    ``@[名称]`` renders as 图N for the asset's first reference image (``lib/prompts/reference_image_numbering.py``).
    Align bases accordingly: asset-reference evidence drops names from logical identity, retaining type,
    role, kind, and content fingerprint. Image-prompt mentions identifying asset references become 图N;
    unmatched mentions remain unchanged because models see those names. Non-asset reference identities,
    such as previous storyboards, are not asset names and remain unchanged.

    Constructors and v18→v19 share this function: rewriting name-bearing bases frozen in version records
    produces exactly the same shape as direct constructor projection. Return already-nameless inputs unchanged."""

    if kind not in ASSET_NAME_FREE_IMAGE_BASIS_KINDS:
        raise ValueError(f"basis kind does not drop asset names: {kind!r}")
    references = inputs.get("references")
    if not isinstance(references, list):
        raise ValueError("image basis references must be a list")
    labels: dict[str, str] = {}
    stripped: list[object] = []
    for position, reference in enumerate(references):
        identity = reference.get("logical_identity") if isinstance(reference, Mapping) else None
        if isinstance(identity, Mapping) and identity.get("type") in ASSET_TYPES and "id" in identity:
            name = identity["id"]
            if not isinstance(name, str) or not name:
                raise ValueError("visual reference logical identity id must be a non-empty string")
            labels.setdefault(asset_name_comparison_key(name), reference_image_label(position))
            reference = {**reference, "logical_identity": {"type": identity["type"]}}
        stripped.append(reference)
    rewritten: dict[str, object] = {**inputs, "references": stripped}
    if not labels:
        return rewritten

    def render(prompt: object) -> object:
        if isinstance(prompt, str):
            return render_matched_mentions(prompt, labels.get)
        if isinstance(prompt, Mapping) and isinstance(scene := prompt.get("scene"), str):
            return {**prompt, "scene": render_matched_mentions(scene, labels.get)}
        return prompt

    def render_cell(cell: object) -> object:
        if not isinstance(cell, Mapping):
            raise ValueError("grid cell must be an object")
        return {**cell, "image_prompt": render(cell.get("image_prompt"))}

    if kind == _STORYBOARD_IMAGE_KIND:
        rewritten["image_prompt"] = render(inputs.get("image_prompt"))
    elif kind == _GRID_COMPOSITE_KIND:
        cells = inputs.get("cells")
        if not isinstance(cells, list):
            raise ValueError("grid composite cells must be a list")
        rewritten["cells"] = [render_cell(cell) for cell in cells]
    else:
        rewritten["cell"] = render_cell(inputs.get("cell"))
    return rewritten


def _add_style_description(inputs: dict[str, object], style_description: str) -> None:
    """Include only nonempty style descriptions: empty-description project digests equal description-free digests byte for byte, preserving existing currency."""

    if normalized := _require_string("style_description", style_description).strip():
        inputs["style_description"] = normalized


def _reference_visual_lines(text: str) -> list[str]:
    """Artifact bases use visual descriptions only: text remaining after all speech markers are removed.

    Changing one dialogue character must not outdate generated video, so visual bases cannot include
    dialogue. Markers may occur anywhere inline: remove marker-delimited spans, not whole lines,
    preserving visual descriptions sharing those lines."""
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = strip_speech_marks(raw_line).strip()
        if line:
            lines.append(line)
    return lines


def _validate_grid_members(
    members: Sequence[GridStoryboardVisual],
    *,
    rows: int,
    columns: int,
) -> tuple[GridStoryboardVisual, ...]:
    if type(rows) is not int or rows < 1 or type(columns) is not int or columns < 1:
        raise ValueError("grid rows and columns must be positive integers")
    member_tuple = tuple(members)
    if not member_tuple:
        raise ValueError("grid must contain at least one member")
    if len(member_tuple) > rows * columns:
        raise ValueError("grid members exceed the declared layout capacity")
    identities = [member.resource_id for member in member_tuple]
    if len(set(identities)) != len(identities):
        raise ValueError("grid member resource_id values must be unique")
    return member_tuple


def _project_grid_cells(members: Sequence[GridStoryboardVisual]) -> list[dict[str, object]]:
    cells: list[dict[str, object]] = []
    for index, member in enumerate(members):
        transition: dict[str, object] | None = None
        if index:
            previous = members[index - 1]
            transition = {
                "from_resource_id": previous.resource_id,
                "action": _project_grid_action(previous.video_prompt),
            }
        cells.append(
            {
                "cell_index": index,
                "resource_id": member.resource_id,
                "image_prompt": project_grid_image_prompt(member.image_prompt),
                "transition": transition,
            }
        )
    return cells


def _project_grid_action(video_prompt: object) -> str:
    if video_prompt is None:
        return ""
    if isinstance(video_prompt, Mapping):
        return str(video_prompt.get("action") or "")
    return str(video_prompt)


def _reference_evidence(references: Sequence[VisualReference]) -> list[dict[str, object]]:
    return [reference.evidence() for reference in references]


def _composite_evidence(composite_image: Path, source_composite_digest: str | None) -> dict[str, object]:
    """Project one composite source through the same supplied-or-observed digest rule."""

    return {
        "sha256": (
            _require_sha256("source_composite_digest", source_composite_digest)
            if source_composite_digest is not None
            else visual_file_digest(composite_image)
        )
    }


def visual_file_digest(path: Path) -> str:
    """Hash one visual input without loading the whole file into memory.

    A remote content version whose local work file was evicted keeps its recorded digest: a cache miss is
    not a missing formal image (``lib.material.work_files.material_file_digest``).  Readers of the bytes
    fetch the work file first.
    """

    return material_file_digest(path)


def _require_sha256(field: str, value: object) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _require_non_empty(field: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_string(field: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


__all__ = [
    "ASSET_NAME_FREE_IMAGE_BASIS_KINDS",
    "ASSET_NAME_FREE_IMAGE_BASIS_SCHEMA_VERSION",
    "UPLOADED_ASSET_SHEET_BASIS_KIND",
    "UPLOADED_STORYBOARD_BASIS_KIND",
    "UPLOADED_VIDEO_BASIS_KIND",
    "GridStoryboardVisual",
    "VisualReference",
    "build_asset_sheet_visual_basis",
    "build_grid_composite_visual_basis",
    "build_grid_member_storyboard_visual_basis",
    "build_reference_video_artifact_visual_basis",
    "build_stale_grid_member_storyboard_visual_basis",
    "build_storyboard_image_visual_basis",
    "build_storyboard_video_artifact_visual_basis",
    "build_uploaded_asset_sheet_basis",
    "build_uploaded_storyboard_basis",
    "build_uploaded_video_basis",
    "project_basis_records_asset_names",
    "snapshot_visual_references",
    "visual_file_digest",
    "visual_references_match_snapshot",
    "without_asset_names",
]
