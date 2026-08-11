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
        'next_action.type == "analyze_assets"',
        'next_action.type` 为 `"reset_episode_planning"`',
        'next_action.type == "plan_episodes"',
        'next_action.type == "prepare_step1"',
        'next_action.type == "confirm_step1"',
        'next_action.type == "generate_script"',
        'next_action.type == "generate_asset_sheets"',
        'next_action.type == "generate_storyboards"',
        'next_action.type == "generate_grid"',
        'next_action.type == "generate_videos"',
        'next_action.type == "generate_narration_audio"',
    ),
    "SKILL.drama.md": (
        'next_action.type == "analyze_assets"',
        'next_action.type` 为 `"reset_episode_planning"`',
        'next_action.type == "plan_episodes"',
        'next_action.type == "prepare_step1"',
        'next_action.type == "confirm_step1"',
        'next_action.type == "generate_script"',
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

EXPECTED_BLOCKER_ROUTE = {
    "SKILL.narration.md": "| `none` | 展示 `blockers` 并停止变更 |",
    "SKILL.drama.md": "| `none` | 展示 `blockers` 并停止变更 |",
    "SKILL.ad.md": '`next_action.type == "none"` 时展示 blockers 并停止变更',
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
    assert EXPECTED_BLOCKER_ROUTE[filename] in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_workflow_asset_and_storyboard_routes_forward_authoritative_arguments(filename: str) -> None:
    path = REPO / "agent_runtime_profile" / ".claude" / "skills" / "manga-workflow" / filename
    content = path.read_text(encoding="utf-8")

    assert '"names": [该类型 requested_ids]' in content
    assert '"segment_ids": requested_ids' in content
    assert '"scene_ids": requested_ids' in content
    assert "不二次检查 `generation_mode` 或 `grid_storyboard`" in content
    assert "target.episode" in content
    assert '"script": target.script_filename' in content
    assert "next_action.args" in content
    if filename != "SKILL.ad.md":
        assert "names = artifacts.asset_sheets[type].missing_ids ∩ requested_ids" in content
        assert "若 names 为空 → 跳过，不 dispatch；不得回退到整类 missing_ids" in content


def test_asset_analysis_records_completion_fact() -> None:
    path = REPO / "agent_runtime_profile" / ".claude" / "agents" / "analyze-assets.md"
    content = path.read_text(encoding="utf-8")

    assert "mcp__arcreel__complete_asset_inventory" in content
    assert "expected_source_revision" in content
    assert "严格按主 agent 传入的 `scope`" in content
    assert "排除文件名以 `.` / `_` 开头" in content
    assert "`episode_[0-9]+.txt`" in content
    assert "`.text`" not in content
    assert "不要调用 `patch_project`" in content


@pytest.mark.parametrize("filename", ("SKILL.narration.md", "SKILL.drama.md"))
def test_workflow_reset_route_executes_recovery_and_refreshes_status(filename: str) -> None:
    path = REPO / "agent_runtime_profile" / ".claude" / "skills" / "manga-workflow" / filename
    content = path.read_text(encoding="utf-8")

    assert "mcp__arcreel__reset_episode_planning" in content
    assert "next_action.args" in content
    assert "confirm_consumed: true" in content
    assert "重置成功后刷新 workflow-status" in content


@pytest.mark.parametrize("filename", ("SKILL.narration.md", "SKILL.drama.md"))
def test_workflow_stale_step1_records_explicit_rebuild_completion(filename: str) -> None:
    path = REPO / "agent_runtime_profile" / ".claude" / "skills" / "manga-workflow" / filename
    content = path.read_text(encoding="utf-8")

    assert "mcp__arcreel__complete_step1_rebuild" in content
    assert "expected_stale_step1_revision" in content
    assert "确定性重建可能产出完全相同的 JSON" in content
