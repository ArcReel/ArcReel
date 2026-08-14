"""Canonical visual-content provenance for Artifact Manifest currency.

The builders in this module describe formal visual content. They deliberately do
not describe provider request equivalence: provider/model selection, credentials,
pixel resolution, seeds, audio controls, and prompt-renderer revisions are not
Artifact Manifest currency inputs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.artifact_manifest import ArtifactBasis
from lib.asset_types import ASSET_TYPES, normalize_asset_name
from lib.prompt_utils import normalize_style
from lib.reference_video.request_projection import ResolvedReferenceAsset
from lib.reference_video.shot_parser import match_dialogue_line, match_voiceover_line, strip_shot_header


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

    def __post_init__(self) -> None:
        if not isinstance(self.path, Path):
            raise TypeError("visual reference path must be a Path")
        _require_non_empty("visual reference role", self.role)
        if (self.logical_type is None) != (self.logical_id is None):
            raise ValueError("visual reference logical_type and logical_id must be provided together")
        if self.logical_type is not None:
            _require_non_empty("visual reference logical_type", self.logical_type)
            logical_id = _require_non_empty("visual reference logical_id", self.logical_id)
            object.__setattr__(self, "logical_id", normalize_asset_name(logical_id))
        if self.kind is not None:
            _require_non_empty("visual reference kind", self.kind)

    def evidence(self) -> dict[str, object]:
        """Return path-independent, content-addressed manifest evidence."""

        evidence: dict[str, object] = {
            "role": self.role,
            "sha256": _file_digest(self.path),
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


def build_asset_sheet_visual_basis(
    *,
    asset_type: str,
    asset_id: str,
    description: str,
    style: str,
    style_description: str,
    aspect_ratio: str,
    references: Sequence[VisualReference] = (),
) -> ArtifactBasis:
    """Describe one character, scene, prop, or product design sheet."""

    if asset_type not in ASSET_TYPES:
        raise ValueError(f"unsupported asset type: {asset_type!r}")
    canonical_id = normalize_asset_name(_require_non_empty("asset_id", asset_id))
    normalized_description = _require_string("description", description).strip()
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
    return ArtifactBasis.build(
        "artifact-visual/asset-sheet",
        kind_version=1,
        inputs=inputs,
    )


def build_storyboard_image_visual_basis(
    *,
    resource_id: str,
    image_prompt: object,
    style: str,
    aspect_ratio: str,
    references: Sequence[VisualReference] = (),
) -> ArtifactBasis:
    """Describe one ordinary storyboard image and its actual ordered image inputs."""

    identity = _require_non_empty("resource_id", resource_id)
    _require_string("style", style)
    prompt, style_input = _project_storyboard_image_prompt(image_prompt, style)
    inputs: dict[str, object] = {
        "resource_id": identity,
        "image_prompt": prompt,
        "canvas": {"aspect_ratio": _require_non_empty("aspect_ratio", aspect_ratio)},
        "references": _reference_evidence(references),
    }
    if style_input is not None:
        inputs["style"] = style_input
    return ArtifactBasis.build(
        "artifact-visual/storyboard-image",
        kind_version=1,
        inputs=inputs,
    )


def build_grid_composite_visual_basis(
    *,
    group_id: str,
    members: Sequence[GridStoryboardVisual],
    rows: int,
    columns: int,
    style: str,
    grid_aspect_ratio: str,
    references: Sequence[VisualReference] = (),
) -> ArtifactBasis:
    """Describe one grid composite without hashing its rendered provider prompt."""

    member_tuple = _validate_grid_members(members, rows=rows, columns=columns)
    _require_string("style", style)
    return ArtifactBasis.build(
        "artifact-visual/grid-composite",
        kind_version=1,
        inputs={
            "group_id": _require_non_empty("group_id", group_id),
            "cells": _project_grid_cells(member_tuple),
            "layout": {
                "rows": rows,
                "columns": columns,
                "grid_aspect_ratio": _require_non_empty("grid_aspect_ratio", grid_aspect_ratio),
            },
            "style": style,
            "references": _reference_evidence(references),
        },
    )


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
    references: Sequence[VisualReference] = (),
) -> ArtifactBasis:
    """Describe one split cell while preserving grid dependency locality.

    The member does not embed the composite's target basis. It records only the
    selected cell's semantic inputs plus the actual composite bytes it was split
    from. Editing a different cell therefore leaves this member current until a
    replacement composite is really produced.
    """

    member_tuple = _validate_grid_members(members, rows=rows, columns=columns)
    if type(cell_index) is not int or not 0 <= cell_index < len(member_tuple):
        raise ValueError("cell_index must identify a content cell")
    _require_string("style", style)
    return ArtifactBasis.build(
        "artifact-visual/grid-member",
        kind_version=1,
        inputs={
            "group_id": _require_non_empty("group_id", group_id),
            "cell": _project_grid_cells(member_tuple)[cell_index],
            "layout": {
                "rows": rows,
                "columns": columns,
                "member_aspect_ratio": _require_non_empty("member_aspect_ratio", member_aspect_ratio),
            },
            "style": style,
            "references": _reference_evidence(references),
            "source_composite": {"sha256": _file_digest(composite_image)},
        },
    )


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
            "source_composite": {"sha256": _file_digest(composite_image)},
        },
    )


def build_storyboard_video_artifact_visual_basis(
    *,
    resource_id: str,
    visual_prompt: object,
    storyboard_image: Path,
    end_frame_image: Path | None,
    aspect_ratio: str,
) -> ArtifactBasis:
    """Describe the visual component of one storyboard-driven video.

    Sound design, dialogue, voice profiles, duration, and execution options are
    intentionally absent. They either belong to later video components or are not
    Artifact Manifest currency at all.
    """

    if isinstance(visual_prompt, str):
        visual_text = visual_prompt.strip()
        if not visual_text:
            raise ValueError("visual_prompt must not be empty")
        projected_prompt: object = visual_text
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
    frame_evidence: list[dict[str, object]] = [{"role": "storyboard", "sha256": _file_digest(storyboard_image)}]
    if end_frame_image is not None:
        frame_evidence.append({"role": "end_frame", "sha256": _file_digest(end_frame_image)})
    return ArtifactBasis.build(
        "artifact-visual/video-storyboard",
        kind_version=1,
        inputs={
            "resource_id": _require_non_empty("resource_id", resource_id),
            "visual_prompt": projected_prompt,
            "canvas": {"aspect_ratio": _require_non_empty("aspect_ratio", aspect_ratio)},
            "frames": frame_evidence,
        },
    )


def build_reference_video_artifact_visual_basis(
    *,
    unit: Mapping[str, object],
    request_assets: Sequence[ResolvedReferenceAsset],
    style: str | None,
    aspect_ratio: str,
) -> ArtifactBasis:
    """Describe one canonical ``video_unit`` and the images actually sent for it.

    Only ``unit_id`` and visual lines from ``shots`` are projected. Legacy grouping
    fields and speech-only lines never enter the basis. ``request_assets`` must be
    the already-clamped request projection, so unavailable or provider-truncated
    declarations cannot make the formal video stale.
    """

    unit_id = _require_non_empty("unit.unit_id", unit.get("unit_id"))
    raw_shots = unit.get("shots")
    if not isinstance(raw_shots, (list, tuple)):
        raise ValueError("unit.shots must be an array")
    visual_shots: list[dict[str, object]] = []
    for index, raw_shot in enumerate(raw_shots):
        if not isinstance(raw_shot, Mapping):
            raise ValueError(f"unit.shots[{index}] must be an object")
        raw_text = raw_shot.get("text")
        if not isinstance(raw_text, str):
            raise ValueError(f"unit.shots[{index}].text must be a string")
        visual_lines = _reference_visual_lines(raw_text)
        if not visual_lines:
            continue
        visual_shots.append(
            {
                "shot_index": len(visual_shots),
                "lines": visual_lines,
            }
        )
    references: list[VisualReference] = []
    for asset in request_assets:
        if not isinstance(asset, ResolvedReferenceAsset):
            raise TypeError("request_assets must contain ResolvedReferenceAsset values")
        references.append(
            VisualReference(
                path=asset.path,
                role="reference_image",
                logical_type=asset.reference.type,
                logical_id=asset.reference.name,
                kind=asset.kind,
            )
        )
    return ArtifactBasis.build(
        "artifact-visual/video-reference",
        kind_version=1,
        inputs={
            "unit_id": unit_id,
            "visual_shots": visual_shots,
            "style": normalize_style(style),
            "canvas": {"aspect_ratio": _require_non_empty("aspect_ratio", aspect_ratio)},
            "request_references": _reference_evidence(references),
        },
    )


def _reference_visual_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = strip_shot_header(raw_line).strip()
        if not line:
            continue
        if match_dialogue_line(line) is not None or match_voiceover_line(line) is not None:
            continue
        lines.append(line)
    return lines


def _project_storyboard_image_prompt(image_prompt: object, style: str) -> tuple[object, str | None]:
    if isinstance(image_prompt, str):
        prompt = image_prompt.strip()
        if not prompt:
            raise ValueError("image_prompt must not be empty")
        return prompt, None
    if not isinstance(image_prompt, Mapping):
        raise ValueError("image_prompt must be a string or object")
    scene = image_prompt.get("scene")
    if not isinstance(scene, str) or not scene.strip():
        raise ValueError("image_prompt.scene must be a non-empty string")
    raw_composition = image_prompt.get("composition")
    composition = raw_composition if isinstance(raw_composition, Mapping) else {}
    return (
        {
            "scene": scene.strip(),
            "composition": {
                "shot_type": str(composition.get("shot_type") or "Medium Shot"),
                "lighting": str(composition.get("lighting") or ""),
                "ambiance": str(composition.get("ambiance") or ""),
            },
        },
        normalize_style(style),
    )


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
    if any(not isinstance(member, GridStoryboardVisual) for member in member_tuple):
        raise TypeError("grid members must be GridStoryboardVisual values")
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
                "image_prompt": _project_grid_image_prompt(member.image_prompt),
                "transition": transition,
            }
        )
    return cells


def _project_grid_image_prompt(image_prompt: object) -> object:
    if not isinstance(image_prompt, Mapping):
        return str(image_prompt)
    scene = image_prompt.get("scene")
    if scene is None:
        scene = ""
    if not isinstance(scene, str):
        raise ValueError("grid image_prompt.scene must be a string")
    raw_composition = image_prompt.get("composition")
    if raw_composition is None:
        raw_composition = {}
    if not isinstance(raw_composition, Mapping):
        raise ValueError("grid image_prompt.composition must be an object")
    projected_composition: dict[str, str] = {}
    for key, value in raw_composition.items():
        if not isinstance(key, str):
            raise ValueError("grid image_prompt.composition keys must be strings")
        if value:
            projected_composition[key] = str(value)
    return {
        "scene": scene,
        "composition": projected_composition,
    }


def _project_grid_action(video_prompt: object) -> str:
    if isinstance(video_prompt, Mapping):
        return str(video_prompt.get("action") or "")
    return str(video_prompt)


def _reference_evidence(references: Sequence[VisualReference]) -> list[dict[str, object]]:
    if any(not isinstance(reference, VisualReference) for reference in references):
        raise TypeError("visual references must be VisualReference values")
    return [reference.evidence() for reference in references]


def _file_digest(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(path)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_non_empty(field: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _require_string(field: str, value: object) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


__all__ = [
    "GridStoryboardVisual",
    "VisualReference",
    "build_asset_sheet_visual_basis",
    "build_grid_composite_visual_basis",
    "build_grid_member_storyboard_visual_basis",
    "build_stale_grid_member_storyboard_visual_basis",
    "build_reference_video_artifact_visual_basis",
    "build_storyboard_image_visual_basis",
    "build_storyboard_video_artifact_visual_basis",
]
