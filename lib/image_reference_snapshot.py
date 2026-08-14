"""Task-owned snapshots for provider-facing image references.

Formal visual provenance and the provider must consume the same immutable
bytes.  This module owns the transport projection so every image-generation
entry point gets that invariant without duplicating staging logic.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from lib.visual_artifact_provenance import VisualReference, visual_file_digest


@dataclass(frozen=True, slots=True)
class FrozenImageReferences:
    """Provider inputs and provenance projected onto task-owned files."""

    reference_images: list[object] | None
    visual_references: tuple[VisualReference, ...]
    _directory: Path | None

    def cleanup(self) -> None:
        """Remove snapshots after the awaited provider submission completes."""

        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)


def freeze_image_references(
    reference_images: Sequence[object] | None,
    visual_references: Sequence[VisualReference],
) -> FrozenImageReferences:
    """Copy aligned provider/provenance inputs into one private task directory."""

    references = list(reference_images or ())
    visuals = tuple(visual_references)
    if len(references) != len(visuals):
        raise ValueError("provider image references and visual evidence must be aligned")
    if not references:
        return FrozenImageReferences(None, (), None)

    directory = Path(tempfile.mkdtemp(prefix="arcreel-image-references-"))
    frozen_provider: list[object] = []
    frozen_visuals: list[VisualReference] = []
    try:
        for index, (reference, visual) in enumerate(zip(references, visuals, strict=True)):
            source = _reference_path(reference)
            if source.resolve(strict=True) != visual.path.resolve(strict=True):
                raise ValueError("provider image reference and visual evidence identify different files")
            target = directory / f"{index:04d}-{source.name}"
            shutil.copyfile(source, target)
            frozen_provider.append(_replace_reference_path(reference, target))
            content_digest = visual_file_digest(target)
            frozen_visuals.append(
                VisualReference(
                    path=target,
                    role=visual.role,
                    logical_type=visual.logical_type,
                    logical_id=visual.logical_id,
                    kind=visual.kind,
                    content_digest=content_digest,
                )
            )
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise
    return FrozenImageReferences(frozen_provider, tuple(frozen_visuals), directory)


def _reference_path(reference: object) -> Path:
    if isinstance(reference, Mapping):
        raw_path = reference.get("image")
    else:
        raw_path = reference
    if not isinstance(raw_path, (str, Path)):
        raise TypeError("provider image reference must carry a filesystem path")
    return Path(raw_path)


def _replace_reference_path(reference: object, target: Path) -> object:
    if isinstance(reference, Mapping):
        projected = dict(reference)
        projected["image"] = target
        return projected
    return target


__all__ = ["FrozenImageReferences", "freeze_image_references"]
