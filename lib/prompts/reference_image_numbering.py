"""Reference image numbering as “图N”: type declarations and body ``@[名称]`` replacement.

Storyboard/grid references reach image models as arrays, identified as “图N”. After orchestration finalizes
the list, this module mechanically renders a nameless type declaration (YAML key
``Reference_Images``) and replaces body ``@[名称]`` with the corresponding “图N”. Numbers are list positions
(starting at 1); compression and execution-time copying preserve count/order, so numbering occurs once here
with consistent semantics across image backends.

Input is a :class:`ReferenceImageSlot` sequence exactly matching sent references in length and order
(:class:`lib.artifacts.visual_artifact_provenance.VisualReference` satisfies the protocol). Entry reference
fields still determine the list; mentions identify images without deriving additional references.

Each image backend limits reference count and discards excess trailing images. Orchestration must first
apply :func:`clamp_reference_images` for that limit, then number here, so declarations never identify unsent images.
Clamping also returns a warning matching task ``result.warnings``, informing users and Agents of dropped references.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from lib.project.asset_types import asset_name_comparison_key
from lib.script.reference_video.text_parser import render_mentions

logger = logging.getLogger(__name__)

#: YAML key for the type declaration, inserted between ``Style`` and ``Scene``.
REFERENCE_IMAGES_KEY = "Reference_Images"

#: Previous storyboard role in reference evidence.
PREVIOUS_STORYBOARD_ROLE = "previous_storyboard"

#: Previous storyboard constraint sent to the model with its type declaration.
PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION = "use only composition and color"

#: Warning key for dropped trailing references, sharing i18n copy with reference-to-video.
REFERENCE_IMAGES_CLAMPED_WARNING = "ref_too_many_images"

#: Type descriptions by asset type; product entries include fidelity requirements.
_TYPE_DESCRIPTIONS: dict[str, str] = {
    "product": "product reference images; the product in the frame must match exactly",
    "character": "character reference images",
    "scene": "scene reference images",
    "prop": "prop reference images",
}
_PREVIOUS_STORYBOARD_DESCRIPTION = f"previous storyboard image, {PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION}"
_EXTRA_DESCRIPTION = "additional reference images"


class ReferenceImageSlot(Protocol):
    """One reference actually sent with a request: role and logical identity, without paths."""

    @property
    def role(self) -> str: ...

    @property
    def logical_type(self) -> str | None: ...

    @property
    def logical_id(self) -> str | None: ...


def reference_image_label(position: int) -> str:
    """The “图N” label for a reference's zero-based position in the request array."""
    return f"图{position + 1}"


def _describe(slot: ReferenceImageSlot) -> str:
    if slot.role == PREVIOUS_STORYBOARD_ROLE:
        return _PREVIOUS_STORYBOARD_DESCRIPTION
    if slot.logical_type in _TYPE_DESCRIPTIONS:
        return _TYPE_DESCRIPTIONS[slot.logical_type]
    return _EXTRA_DESCRIPTION


def reference_images_declaration(references: Sequence[ReferenceImageSlot]) -> str:
    """Value for ``Reference_Images``: numbered declarations grouped by type, or empty with no references."""
    groups: dict[str, list[str]] = {}
    for position, slot in enumerate(references):
        groups.setdefault(_describe(slot), []).append(reference_image_label(position))
    if not groups:
        return ""
    return "; ".join(f"{', '.join(labels)}: {description}" for description, labels in groups.items()) + "."


@dataclass(frozen=True)
class ReferenceImageClamp:
    """One clamping decision for the image backend's limit: retain first ``kept`` and drop the tail (same order as backend truncation).

    ``dropped`` lists dropped type descriptions (deduplicated in order) for logging only; outward warnings use
    :meth:`warning`, matching reference-to-video overflow warnings.
    """

    total: int
    kept: int
    max_reference_images: int | None
    model: str
    dropped: tuple[str, ...] = ()

    @property
    def clamped(self) -> bool:
        """Whether any references were actually dropped."""
        return self.kept < self.total

    def warning(self) -> dict[str, Any] | None:
        """Warning shaped as ``{"key", "params"}``, or ``None`` when not clamped."""
        if not self.clamped:
            return None
        return {
            "key": REFERENCE_IMAGES_CLAMPED_WARNING,
            "params": {"count": self.total, "model": self.model, "max_count": self.max_reference_images},
        }


def clamp_reference_images(
    references: Sequence[ReferenceImageSlot],
    max_reference_images: int | None,
    *,
    model: str,
) -> ReferenceImageClamp:
    """Determine the retained count from the image backend limit: keep first N, drop the tail.

    ``max_reference_images`` is the effective clamping limit (``InputLimit.clamp_bound``); ``None`` retains
    all references. The caller truncates both sent references and numbering slots using ``kept`` so they
    remain equal-length and ordered; otherwise declarations identify images the backend discarded and never sent.
    Clamping details enter task results and previews through :meth:`ReferenceImageClamp.warning`;
    logs serve only as troubleshooting evidence.
    """
    total = len(references)
    if max_reference_images is None or total <= max_reference_images:
        return ReferenceImageClamp(total=total, kept=total, max_reference_images=max_reference_images, model=model)
    dropped: list[str] = []
    for slot in references[max_reference_images:]:
        description = _describe(slot)
        if description not in dropped:
            dropped.append(description)
    logger.info(
        "Reference count %d exceeds model=%s limit %d; clamped before numbering; dropped: %s",
        total,
        model,
        max_reference_images,
        "、".join(dropped),
    )
    return ReferenceImageClamp(
        total=total,
        kept=max_reference_images,
        max_reference_images=max_reference_images,
        model=model,
        dropped=tuple(dropped),
    )


def mention_replacements(references: Sequence[ReferenceImageSlot]) -> dict[str, str]:
    """Reference name (comparison space) → first asset reference “图N”. Previous storyboard and extra references have no addressable names."""
    replacements: dict[str, str] = {}
    for position, slot in enumerate(references):
        if slot.role == PREVIOUS_STORYBOARD_ROLE or slot.logical_id is None:
            continue
        replacements.setdefault(asset_name_comparison_key(slot.logical_id), reference_image_label(position))
    return replacements


def render_reference_mentions(text: str, references: Sequence[ReferenceImageSlot]) -> str:
    """Replace body ``@[名称]`` with matching “图N”; unmatched mentions render as bare names."""
    replacements = mention_replacements(references)
    return render_mentions(text, lambda name: replacements.get(name, name))


__all__ = [
    "PREVIOUS_STORYBOARD_REFERENCE_DESCRIPTION",
    "PREVIOUS_STORYBOARD_ROLE",
    "REFERENCE_IMAGES_CLAMPED_WARNING",
    "REFERENCE_IMAGES_KEY",
    "ReferenceImageClamp",
    "ReferenceImageSlot",
    "clamp_reference_images",
    "mention_replacements",
    "reference_image_label",
    "reference_images_declaration",
    "render_reference_mentions",
]
