"""Task-owned snapshots for provider-facing image references.

Formal visual provenance and the provider must consume the same immutable
bytes.  This module owns the transport projection so every image-generation
entry point gets that invariant without duplicating staging logic.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from lib.artifacts.visual_artifact_provenance import VisualReference, visual_file_digest
from lib.backends.image_errors import ImageCapabilityError


@dataclass(frozen=True, slots=True)
class FrozenImageReferences:
    """Provider inputs and provenance projected onto task-owned files."""

    reference_images: list[object] | None
    visual_references: tuple[VisualReference, ...]
    _directory: Path | None
    #: Snapshot path to original source, for durable usage-record locators.
    source_paths: Mapping[str, Path] = field(default_factory=dict)

    def cleanup(self) -> None:
        """Remove snapshots after the awaited provider submission completes."""

        if self._directory is not None:
            shutil.rmtree(self._directory, ignore_errors=True)  # hard-delete: temp -- 冻结参考图的临时副本目录


def freeze_image_references(
    reference_images: Sequence[object] | None,
    visual_references: Sequence[VisualReference],
    *,
    model: str | None = None,
) -> FrozenImageReferences:
    """Copy aligned provider/provenance inputs into one private task directory."""

    references = list(reference_images or ())
    visuals = tuple(visual_references)
    if len(references) != len(visuals):
        raise ValueError("provider image references and visual evidence must be aligned")
    if not references:
        return FrozenImageReferences(None, (), None)

    sources: list[tuple[object, VisualReference, Path, Path, str]] = []
    missing: list[str] = []
    for index, (reference, visual) in enumerate(zip(references, visuals, strict=True), 1):
        source = _reference_path(reference)
        try:
            resolved = source.resolve(strict=True)
            if resolved != visual.path.resolve(strict=True):
                raise ValueError("provider image reference and visual evidence identify different files")
            sources.append((reference, visual, source, resolved, visual_file_digest(source)))
        except OSError:
            if model is None:
                raise
            missing.append(source.name or f"#{index}")
    if missing:
        raise ImageCapabilityError("image_reference_images_unreadable", model=model, names=", ".join(missing))

    directory = Path(tempfile.mkdtemp(prefix="arcreel-image-references-"))
    frozen_provider: list[object] = []
    source_paths: dict[str, Path] = {}
    frozen_visuals: list[VisualReference] = []
    try:
        for index, (reference, visual, source, _resolved, source_digest) in enumerate(sources):
            target = directory / f"{index:04d}-{source.name}"
            shutil.copyfile(source, target)
            frozen_provider.append(_replace_reference_path(reference, target))
            source_paths[str(target)] = source
            content_digest = visual_file_digest(target)
            if content_digest != source_digest:
                raise OSError("reference images changed while they were frozen")
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
        for _reference, _visual, source, resolved, source_digest in sources:
            if source.resolve(strict=True) != resolved or visual_file_digest(source) != source_digest:
                raise OSError("reference images changed while they were frozen")
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)  # hard-delete: temp -- 冻结参考图失败时的临时副本目录
        raise
    return FrozenImageReferences(frozen_provider, tuple(frozen_visuals), directory, source_paths)


def _reference_path(reference: object) -> Path:
    raw_path = reference.get("image") if isinstance(reference, Mapping) else reference
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
