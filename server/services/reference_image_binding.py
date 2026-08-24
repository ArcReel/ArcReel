"""Explicit prompt-to-image bindings for reference-driven image tasks."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.reference_video.request_projection import ResolvedReferenceAsset
from lib.visual_artifact_provenance import VisualReference


@dataclass(frozen=True, slots=True)
class BoundImageReference:
    path: Path
    label: str
    logical_type: str
    logical_id: str
    kind: str


def bind_resolved_assets(
    assets: Sequence[ResolvedReferenceAsset], *, start_index: int = 1
) -> tuple[BoundImageReference, ...]:
    """Bind actual provider inputs to stable ``Picture N`` prompt labels."""

    return tuple(
        BoundImageReference(
            path=asset.path,
            label=f"Picture {index} = @[{asset.reference.name}]（{asset.reference.type} reference）",
            logical_type=asset.reference.type,
            logical_id=asset.reference.name,
            kind=asset.kind,
        )
        for index, asset in enumerate(assets, start=start_index)
    )


def prepend_storyboard_sheet(
    unit_id: str,
    path: Path,
    assets: Sequence[ResolvedReferenceAsset],
) -> tuple[BoundImageReference, ...]:
    """Put the Video Unit Storyboard Sheet first, then bind mentioned assets."""

    shifted = bind_resolved_assets(assets, start_index=2)
    return (
        BoundImageReference(
            path=path,
            label=(
                f"Picture 1 = {unit_id} 的 Video Unit Storyboard Sheet"
                "（整段 Video Unit 的镜头顺序与场景变化参考，不是单一目标帧）"
            ),
            logical_type="storyboard_sheet",
            logical_id=unit_id,
            kind="storyboard_sheet",
        ),
        *shifted,
    )


def provider_inputs(bindings: Sequence[BoundImageReference]) -> list[dict[str, object]]:
    return [{"image": item.path, "label": item.label} for item in bindings]


def visual_references(bindings: Sequence[BoundImageReference], *, role: str) -> tuple[VisualReference, ...]:
    return tuple(
        VisualReference(
            path=item.path,
            role=role,
            logical_type=item.logical_type,
            logical_id=item.logical_id,
            kind=item.kind,
        )
        for item in bindings
    )


def prompt_roster(bindings: Sequence[BoundImageReference]) -> str:
    if not bindings:
        return "（本次没有可用参考图）"
    return "\n".join(f"- {item.label}" for item in bindings)


__all__ = [
    "BoundImageReference",
    "bind_resolved_assets",
    "prepend_storyboard_sheet",
    "prompt_roster",
    "provider_inputs",
    "visual_references",
]
