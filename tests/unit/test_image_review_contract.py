"""资产图与分镜图的审核检查点：skill 与审图子智能体里写死的机器契约须与代码真相源一致。

子智能体名由访问策略按名约束，skill 按这个名字派发；任一处改了而 .md 没跟上，主 Agent 派不出审图子智能体，
或子智能体失去只读约束。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from server.agent_runtime.agent_access_policy import AgentAccessPolicy
from server.media_tools.image_review import MAX_IMAGES_PER_CALL

REPO = Path(__file__).resolve().parents[2]
PROFILE = REPO / "agent_runtime_profile"
REVIEWER = PROFILE / ".claude/agents/review-images.md"
CHECKPOINT = "审核检查点：审图"


def _section(text: str, heading: str) -> str:
    """取一个 ``##`` 节的正文，到下一个 ``##`` 为止。"""

    start = text.index(f"## {heading}\n")
    end = text.find("\n## ", start + 1)
    return text[start : end if end != -1 else len(text)]


def _frontmatter(path: Path) -> dict[str, object]:
    _, frontmatter, _ = path.read_text(encoding="utf-8").split("---", 2)
    return yaml.safe_load(frontmatter)


def _reviewer_name() -> str:
    return str(_frontmatter(REVIEWER)["name"])


def test_review_images_definition_grants_exactly_the_tools_the_access_policy_allows() -> None:
    """frontmatter 的 tools 由 CLI 收窄，访问策略在 hook 上再拒一次；两份名单不一致时总有一层失效。"""

    meta = _frontmatter(REVIEWER)
    tools = {tool.strip() for tool in str(meta["tools"]).split(",")}

    assert meta["name"] in AgentAccessPolicy.READ_ONLY_SUBAGENT_TOOLS
    assert tools == AgentAccessPolicy.READ_ONLY_SUBAGENT_TOOLS[str(meta["name"])]


def test_reviewer_batches_by_the_per_call_image_limit() -> None:
    assert f"一次最多 {MAX_IMAGES_PER_CALL} 张" in REVIEWER.read_text(encoding="utf-8")


@pytest.mark.parametrize("skill", ["generate-assets", "generate-storyboard"])
def test_generation_checkpoint_dispatches_the_reviewer_and_leaves_regeneration_to_the_user(skill: str) -> None:
    text = (PROFILE / ".claude/skills" / skill / "SKILL.md").read_text(encoding="utf-8")
    checkpoint = _section(text, CHECKPOINT)
    workflow = _section(text, "工作流程")

    assert f"`{_reviewer_name()}`" in checkpoint
    assert f"「{CHECKPOINT}」" in workflow
    for part in ("AskUserQuestion", "用户确认前"):
        assert part in checkpoint, part


@pytest.mark.parametrize(
    ("variant", "stages"),
    [
        (
            "SKILL.drama.md",
            ["`generate_asset_sheets`：本集资产图", "`generate_storyboards` / `generate_grid`：分镜图生成"],
        ),
        (
            "SKILL.narration.md",
            ["`generate_asset_sheets`：本集资产图", "`generate_storyboards` / `generate_grid`：分镜图生成"],
        ),
        ("SKILL.ad.md", ["工作流步骤"]),
    ],
)
def test_workflow_image_stages_route_to_the_generation_checkpoint(variant: str, stages: list[str]) -> None:
    """编排流程里资产图与分镜图生成完，也走同一个审核检查点，而不是只把生成摘要交给用户。"""

    text = (PROFILE / ".claude/skills/video-workflow" / variant).read_text(encoding="utf-8")
    for stage in stages:
        section = _section(text, stage)
        assert f"`{_reviewer_name()}`" in section, stage
        assert f"「{CHECKPOINT}」" in section, stage
