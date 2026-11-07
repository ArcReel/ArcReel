"""Asset/storyboard review checkpoints: hardcoded skill/subagent contracts match code sources.

Access policies constrain subagent names and skills dispatch by those names. Stale Markdown prevents
review-agent dispatch or removes read-only constraints.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.media_tools.image_review import MAX_IMAGES_PER_CALL

REPO = Path(__file__).resolve().parents[2]
PROFILE = REPO / "agent_runtime_profile"
REVIEWER = PROFILE / ".claude/agents/review-images.md"
CHECKPOINT = "Review checkpoint: image review"


def _section(text: str, heading: str) -> str:
    """Extract a ``##`` section body through the next ``##`` heading."""

    start = text.index(f"## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _frontmatter(path: Path) -> dict[str, object]:
    _, frontmatter, _ = path.read_text(encoding="utf-8").split("---", 2)
    return yaml.safe_load(frontmatter)


def _reviewer_name() -> str:
    return str(_frontmatter(REVIEWER)["name"])


def test_review_images_definition_grants_exactly_the_tools_the_access_policy_allows() -> None:
    """CLI narrows frontmatter tools and hooks enforce access again; differing lists invalidate one layer."""

    meta = _frontmatter(REVIEWER)
    tools = {tool.strip() for tool in str(meta["tools"]).split(",")}

    assert meta["name"] in AgentAccessPolicy.READ_ONLY_SUBAGENT_TOOLS
    assert tools == AgentAccessPolicy.READ_ONLY_SUBAGENT_TOOLS[str(meta["name"])]


def test_reviewer_batches_by_the_per_call_image_limit() -> None:
    assert f"At most {MAX_IMAGES_PER_CALL} images per call" in REVIEWER.read_text(encoding="utf-8")


@pytest.mark.parametrize("skill", ["generate-assets", "generate-storyboard"])
def test_generation_checkpoint_dispatches_the_reviewer_and_leaves_regeneration_to_the_user(skill: str) -> None:
    text = (PROFILE / ".claude/skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    checkpoint = _section(text, CHECKPOINT)
    workflow = _section(text, "Workflow")

    assert f"`{_reviewer_name()}`" in checkpoint
    assert f"“{CHECKPOINT}”" in workflow
    assert "AskUserQuestion" in checkpoint
    assert re.search(r"(?:Never|Do not) regenerate(?:/edit| or edit) before (?:user )?confirmation", checkpoint)


@pytest.mark.parametrize(
    ("variant", "stages"),
    [
        (
            "SKILL.drama.md",
            [
                "`generate_asset_sheets`: episode asset images",
                "`generate_storyboards` / `generate_grid`: storyboard image generation",
            ],
        ),
        (
            "SKILL.narration.md",
            [
                "`generate_asset_sheets`: episode asset images",
                "`generate_storyboards` / `generate_grid`: storyboard image generation",
            ],
        ),
        ("SKILL.ad.md", ["Workflow steps"]),
    ],
)
def test_workflow_image_stages_route_to_the_generation_checkpoint(variant: str, stages: list[str]) -> None:
    """Asset/storyboard generation in orchestrated flows uses the same review checkpoint, not just generation summaries."""

    text = (PROFILE / ".claude/skills/video-workflow" / variant).read_text(encoding="utf-8")
    for stage in stages:
        section = _section(text, stage)
        assert f"`{_reviewer_name()}`" in section, stage
        assert f"“{CHECKPOINT}”" in section, stage
