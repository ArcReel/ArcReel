"""Data-driven workflow step rules for every content and generation mode pair."""

from __future__ import annotations

from dataclasses import dataclass

from lib.script_skeleton import resolve_declared_kind


@dataclass(frozen=True, slots=True)
class WorkflowStepRule:
    """One ordered workflow step and the status checkpoint that owns it."""

    id: str
    checkpoint: str | None
    applicable: bool


@dataclass(frozen=True, slots=True)
class WorkflowRule:
    """The complete workflow shape for one immutable project mode pair."""

    content_mode: str
    generation_mode: str
    skeleton_kind: str
    preprocessor: str | None
    steps: tuple[WorkflowStepRule, ...]


_STEP_CHECKPOINTS: tuple[tuple[str, str | None], ...] = (
    ("project_input", "PROJECT_INPUT"),
    ("selling_points", "SELLING_POINTS"),
    ("asset_inventory", "ASSET_INVENTORY"),
    ("episode_plan", "EPISODE_PLAN"),
    ("step1_content", "STEP1_CONTENT"),
    ("step1_review", "STEP1_REVIEW"),
    ("final_script", "FINAL_SCRIPT"),
    ("asset_sheets", "ASSET_SHEETS"),
    ("script_structure", None),
    ("storyboard", "STORYBOARD"),
    ("narration_delivery", None),
    ("video", "VIDEO"),
    ("export", "EXPORT_READY"),
)

_EPISODIC_STEPS = frozenset(
    {
        "project_input",
        "asset_inventory",
        "episode_plan",
        "step1_content",
        "step1_review",
        "final_script",
        "asset_sheets",
        "script_structure",
        "narration_delivery",
        "video",
        "export",
    }
)

_CONTENT_STEPS: dict[str, frozenset[str]] = {
    "narration": _EPISODIC_STEPS,
    "drama": _EPISODIC_STEPS,
    "ad": frozenset(
        {
            "project_input",
            "selling_points",
            "final_script",
            "asset_sheets",
            "script_structure",
            "narration_delivery",
            "video",
            "export",
        }
    ),
}

_PREPROCESSORS: dict[tuple[str, str], str | None] = {
    ("narration", "storyboard"): "split-narration-segments",
    ("narration", "reference_video"): "split-reference-video-units",
    ("drama", "storyboard"): "normalize-drama-script",
    ("drama", "reference_video"): "split-reference-video-units",
    ("ad", "storyboard"): None,
    ("ad", "reference_video"): None,
}


def _build_rule(content_mode: str, generation_mode: str) -> WorkflowRule:
    applicable = set(_CONTENT_STEPS[content_mode])
    if generation_mode == "storyboard":
        applicable.add("storyboard")
    return WorkflowRule(
        content_mode=content_mode,
        generation_mode=generation_mode,
        skeleton_kind=resolve_declared_kind(content_mode, generation_mode),
        preprocessor=_PREPROCESSORS[(content_mode, generation_mode)],
        steps=tuple(
            WorkflowStepRule(id=step_id, checkpoint=checkpoint, applicable=step_id in applicable)
            for step_id, checkpoint in _STEP_CHECKPOINTS
        ),
    )


WORKFLOW_RULES: dict[tuple[str, str], WorkflowRule] = {
    (content_mode, generation_mode): _build_rule(content_mode, generation_mode)
    for content_mode in ("narration", "drama", "ad")
    for generation_mode in ("storyboard", "reference_video")
}


def workflow_rule(content_mode: str, generation_mode: str) -> WorkflowRule:
    """Return the exhaustive rule for a validated project mode pair."""

    try:
        return WORKFLOW_RULES[(content_mode, generation_mode)]
    except KeyError as exc:
        raise ValueError(f"unsupported workflow mode pair: {content_mode!r}, {generation_mode!r}") from exc


__all__ = [
    "WORKFLOW_RULES",
    "WorkflowRule",
    "WorkflowStepRule",
    "workflow_rule",
]
