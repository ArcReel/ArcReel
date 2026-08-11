from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
WORKFLOW_VARIANTS = (
    "SKILL.narration.md",
    "SKILL.drama.md",
    "SKILL.ad.md",
)

EXPECTED_ROUTES = {
    "SKILL.narration.md": (
        'next_action.type == "plan_episodes"',
        'next_action.type == "prepare_step1"',
        'next_action.type == "generate_asset_sheets"',
        'next_action.type == "generate_storyboards"',
        'next_action.type == "generate_grid"',
        'next_action.type == "generate_videos"',
        'next_action.type == "generate_narration_audio"',
    ),
    "SKILL.drama.md": (
        'next_action.type == "plan_episodes"',
        'next_action.type == "prepare_step1"',
        'next_action.type == "generate_asset_sheets"',
        'next_action.type == "generate_storyboards"',
        'next_action.type == "generate_grid"',
        'next_action.type == "generate_videos"',
    ),
    "SKILL.ad.md": (
        'next_action.type == "draft_selling_points"',
        'next_action.type == "generate_script"',
        'next_action.type == "generate_asset_sheets"',
        'next_action.type == "generate_storyboards"',
        'next_action.type == "generate_grid"',
        'next_action.type == "generate_videos"',
        'next_action.type == "export"',
    ),
}


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_workflow_variants_use_authoritative_status_tool(filename: str) -> None:
    path = REPO / "agent_runtime_profile" / ".claude" / "skills" / "manga-workflow" / filename
    content = path.read_text(encoding="utf-8")

    assert "mcp__arcreel__get_workflow_status" in content
    assert "阶段判断的唯一真相源" in content
    route_positions = []
    for route in EXPECTED_ROUTES[filename]:
        assert route in content
        route_positions.append(content.index(route))
    assert route_positions == sorted(route_positions)


@pytest.mark.parametrize("filename", ("SKILL.narration.md", "SKILL.drama.md"))
def test_workflow_asset_and_storyboard_routes_forward_authoritative_arguments(filename: str) -> None:
    path = REPO / "agent_runtime_profile" / ".claude" / "skills" / "manga-workflow" / filename
    content = path.read_text(encoding="utf-8")

    assert '"names": [该类型 requested_ids]' in content
    assert '"segment_ids": requested_ids' in content
    assert '"scene_ids": requested_ids' in content
    assert "不二次检查 `generation_mode` 或 `grid_storyboard`" in content


def test_asset_analysis_records_completion_fact() -> None:
    path = REPO / "agent_runtime_profile" / ".claude" / "agents" / "analyze-assets.md"
    content = path.read_text(encoding="utf-8")

    assert "mcp__arcreel__complete_asset_inventory" in content
    assert "expected_source_revision" in content
