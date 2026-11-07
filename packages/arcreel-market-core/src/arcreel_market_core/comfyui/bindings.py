"""Semantic key catalog for node bindings, shared by inference, validation, and runtime value population.

Semantic keys share declarative-endpoint names (``prompt`` / ``start_image`` / ``reference_images`` …), so the same
dimension has the same name across both ``kind`` values. Key membership by media type is a catalog fact rather than duplicated
``if media_type == "image"`` branches: whitelist drift lets save-time-accepted bindings fail during population.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from .workflow import node_inputs

logger = logging.getLogger(__name__)

#: All semantic keys for video endpoints, also the closed key set for schema ``bindings``.
VIDEO_BINDING_KEYS = (
    "prompt",
    "negative_prompt",
    "start_image",
    "end_image",
    "reference_images",
    "width",
    "height",
    "frames",
    "fps",
    "seed",
    "output",
)

#: Semantic keys for image endpoints: no first/last frames and no frame-count/frame-rate timeline dimensions.
IMAGE_BINDING_KEYS = (
    "prompt",
    "negative_prompt",
    "reference_images",
    "width",
    "height",
    "seed",
    "output",
)

#: ``media_type`` to allowed semantic keys for that media type.
BINDING_KEYS_BY_MEDIA_TYPE: dict[str, frozenset[str]] = {
    "image": frozenset(IMAGE_BINDING_KEYS),
    "video": frozenset(VIDEO_BINDING_KEYS),
}

#: Semantic keys requiring bindings for both media types: no prompt means no starting input; no artifact means no retrievable output.
REQUIRED_BINDING_KEYS = ("prompt", "output")


def targets_of(raw: object) -> tuple[Mapping[str, Any], ...]:
    """Target list for one semantic key.

    Among the three states, empty lists and absent keys are equivalent here: both mean no targets. They differ only in whether import reruns inference,
    not population, validation, or capability derivation. Discard nonlist values and nonobject list entries alike: schema validation already gates shape before
    saving; this merely ensures every item supplied to readers is usable as an entry.
    """
    if not isinstance(raw, list):
        return ()
    return tuple(target for target in raw if isinstance(target, Mapping))


def literal_of(workflow: Mapping[str, Any], target: Mapping[str, Any]) -> object:
    """A target's current literal value, or ``None`` if its node or field no longer exists in the graph.

    Population, validation, and capability derivation read the same literal, so keep the criterion here: three separate implementations would eventually judge boundaries such as a node missing
    from the graph differently, though they should reach the same conclusion for the same workflow.
    """
    node = workflow.get(str(target["node"]))
    name = target.get("input")
    if not isinstance(node, Mapping) or not isinstance(name, str):
        return None
    return node_inputs(node).get(name)


def int_literal_of(workflow: Mapping[str, Any], target: Mapping[str, Any]) -> int | None:
    """Read integer literals, excluding booleans: ``True`` is an ``int`` subclass and silently becomes 1 when used as size or frame count."""
    raw = literal_of(workflow, target)
    return raw if isinstance(raw, int) and not isinstance(raw, bool) else None


def positive_number(raw: object) -> float | None:
    """A positive numeric value, otherwise ``None``. Frame rates and dimensions are meaningful only when positive."""
    return float(raw) if isinstance(raw, int | float) and not isinstance(raw, bool) and raw > 0 else None


def bound_fps(workflow: Mapping[str, Any], bindings: Mapping[str, Any]) -> float | None:
    """The actual frame rate used by this workflow, read from read-only ``fps`` bindings.

    Use the first literal when multiple read-only bindings differ: this is a contradictory definition already blocked at save time by
    ``comfyui_fps_conflict`` (``arcreel_market_core/comfyui/validator.py``). Only existing
    data reaches here; any selection is a guess, and the first at least keeps population and derivation consistent.
    """
    for target in targets_of(bindings.get("fps")):
        fps = positive_number(literal_of(workflow, target))
        if fps is not None:
            return fps
    return None


def fps_literals(workflow: Mapping[str, Any], bindings: Mapping[str, Any]) -> list[float]:
    """Positive literal values read from all read-only ``fps`` bindings, in binding order."""
    return [
        value for target in targets_of(bindings.get("fps")) if (value := positive_number(literal_of(workflow, target)))
    ]


def step_of(target: Mapping[str, Any]) -> int:
    """Step declared by the entry, defaulting to 1 when omitted; do not assume a step for the workflow author without information."""
    raw = target.get("step")
    return raw if isinstance(raw, int) and raw >= 1 else 1


def align_frames(frames: int, step: int) -> int:
    """Align downward to ``frames ≡ 1 (mod step)``, with lower bound ``1 + step``.

    Log the lower bound without raising: if duration is too short for one step, submitting the minimum valid frame count can still produce output; rejecting generation
    is less useful than showing the user output shorter than expected.

    Population and capability derivation share this: the former writes frame counts; the latter decides whether selecting this tier still preserves the original graph.
    """
    aligned = frames - (frames - 1) % step
    if aligned < 1 + step:
        logger.info("帧数 %d 低于步长 %d 的最小合法值，按 %d 提交", frames, step, 1 + step)
        return 1 + step
    return aligned
