"""Contract coverage for the video-workflow Agent Profile.

Profiles are prompts, but assert only coverage grounded in code: controlled-action/problem-code
enums, artifact-state enums, admission outcomes, narration-delivery constants, registered
`mcp__arcreel__*` tools, and content-mode materialization mappings. Wording is outside assertions:
new server enum values missing in profiles fail; rephrasing does not. Server contracts and
``AgentAccessPolicy`` reject out-of-bounds behavior at tool boundaries, never copied prompt assertions.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from lib.agent.profile_manifest import VALID_CONTENT_MODES, resolve_profile_files_for_mode
from lib.generation.batch_admission import DURATION_CONFIRMATION_CODE, BatchAdmissionDecision
from lib.generation.generation_result import (
    _TASK_FAILURE_ACTIONS,
    GenerationItemState,
    GenerationProblemCode,
)
from lib.workflow.workflow_rules import WORKFLOW_RULES
from lib.workflow.workflow_state import WorkflowActionType, WorkflowTarget
from server.agent_toolset.toolset import ARCREEL_MCP_TOOL_IDS

REPO = Path(__file__).resolve().parents[4]
PROFILE = REPO / "agent_runtime_profile"
SKILL_DIR = PROFILE / ".claude" / "skills" / "video-workflow"
REFERENCES = PROFILE / ".claude" / "references"
WORKFLOW_PLAN_REFERENCE = REFERENCES / "workflow-plan.md"
GENERATION_RESULTS_REFERENCE = REFERENCES / "generation-results.md"
VIDEO_SKILL = PROFILE / ".claude" / "skills" / "generate-video" / "SKILL.md"
DISTRIBUTED_VIDEO_WORKFLOW = REPO / "skills" / "video-workflow" / "SKILL.md"

WORKFLOW_VARIANTS = ("SKILL.narration.md", "SKILL.drama.md", "SKILL.ad.md")
EPISODIC_VARIANTS = ("SKILL.narration.md", "SKILL.drama.md")

# ``next_action.type`` closes over ``WorkflowActionType``: orchestration/planned actions and
# ``GenerationAction`` (returned unchanged on rejected batch admission) all belong. Derive enums rather than copy them,
# making additions fail this contract test directly.
CONTROLLED_ACTIONS = tuple(action.value for action in WorkflowActionType)

#: Problem codes removed after video generation stopped reading narration delivery; stale profile codes mislead Agents into nonexistent outcomes.
RETIRED_NARRATED_VIDEO_CODES = (
    "tts_duration_endpoint_fixed",
    "video_shorter_than_tts",
    "video_duration_unavailable",
    "video_request_cost_unavailable",
    "tts_conflicts_with_active_narrated_video",
    "tts_missing",
    "tts_generating",
    "tts_stale",
    "tts_state_unavailable",
    "tts_duration_unavailable",
    "tts_not_configured",
)


def _skill(filename: str) -> str:
    return (SKILL_DIR / filename).read_text(encoding="utf-8")


def _reference(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ------------------------------------------------- Plans solely determine step applicability


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_route_through_the_registered_plan_tool(filename: str) -> None:
    content = _skill(filename)

    assert "get_workflow_plan" in ARCREEL_MCP_TOOL_IDS
    assert "mcp__arcreel__get_workflow_plan" in content
    assert "mcp__arcreel__get_workflow_status" not in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_do_not_name_the_preprocessor_subagents_themselves(filename: str) -> None:
    """Plan ``next_action.args.preprocessor`` names script-planning subagents; profiles never infer them again."""

    content = _skill(filename)

    for rule in WORKFLOW_RULES.values():
        if rule.preprocessor is not None:
            assert rule.preprocessor not in content, (
                f"{filename} 硬编码了脚本规划子智能体 {rule.preprocessor}；应改读 next_action.args.preprocessor"
            )


def test_plan_reference_covers_every_controlled_action() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    for action in CONTROLLED_ACTIONS:
        assert f"`{action}`" in content, f"受控动作表缺 {action}"


def test_plan_reference_names_only_registered_mcp_tools() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    assert "mcp__arcreel__get_workflow_plan" in content
    for tool_id in ("plan_episodes", "reset_episode_planning", "patch_episode_script"):
        assert tool_id in ARCREEL_MCP_TOOL_IDS
        assert f"mcp__arcreel__{tool_id}" in content


def test_plan_reference_documents_every_target_field() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    for field in WorkflowTarget.model_fields:
        assert f"`{field}`" in content


# ------------------------------------------------------------------- Narration delivery


def test_profile_no_longer_routes_a_per_request_delivery_choice() -> None:
    content = "\n".join(path.read_text(encoding="utf-8") for path in PROFILE.rglob("*.md"))

    assert "choose_narration_delivery" not in content
    assert "WorkflowPlanRequest" not in content
    assert '"narration_delivery":' not in _reference(WORKFLOW_PLAN_REFERENCE)


def test_edit_step_names_the_registered_timeline_tool() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    assert "create_timeline" in ARCREEL_MCP_TOOL_IDS
    assert "mcp__arcreel__create_timeline" in content


def _forbids_force(line: str) -> bool:
    """Every force-bearing clause must prohibit force, with at least one such clause."""
    force_clauses = [
        clause for clause in re.split(r"[，,；;。！？!?.]", line) if re.search(r"\bforce\b", clause, re.IGNORECASE)
    ]
    return bool(force_clauses) and all(
        re.search(r"\b(?:do not|must not|never|don't)\b", clause, re.IGNORECASE) for clause in force_clauses
    )


@pytest.mark.parametrize(
    ("line", "forbidden"),
    [
        ("Do not automatically force regeneration while a task is in flight; wait for its batch.", True),
        ("Never force while in flight; do not force completed targets either.", True),
        ("Automatically force while in flight; do not wait.", False),
        ("Force while in flight; never force other targets.", False),
        ("Do not wait; force while in flight.", False),
        ("Wait for the in-flight task.", False),
    ],
)
def test_force_prohibition_requires_negation_in_every_force_clause(line: str, forbidden: bool) -> None:
    assert _forbids_force(line) is forbidden


def test_generate_video_waits_on_the_durable_batch_without_forcing_completed_targets() -> None:
    content = _reference(VIDEO_SKILL)

    assert "get_generation_batch" in content
    assert "poll_after_seconds" in content
    # Forbidding automatic force for in-flight work prevents duplicate charges after the task finishes.
    # Each force-bearing clause on that line must prohibit it, rejecting mixed instructions such as
    # “force while in flight; never force otherwise” or “automatically force; do not wait”.
    assert any("in flight" in line and _forbids_force(line) for line in content.splitlines()), (
        "generate-video skill 未写明已有在途任务时不自动 force 重做"
    )


def test_preexisting_tasks_use_bounded_plan_polling() -> None:
    content = _reference(DISTRIBUTED_VIDEO_WORKFLOW)

    assert "wait_for_task" in content
    assert "max_poll_attempts" in content
    assert "get_workflow_plan" in content


def test_profile_names_no_retired_narrated_video_code() -> None:
    for code in RETIRED_NARRATED_VIDEO_CODES:
        assert code not in _TASK_FAILURE_ACTIONS
    content = "\n".join(
        path.read_text(encoding="utf-8") for path in PROFILE.rglob("*") if path.suffix in {".json", ".md", ".py"}
    )

    for code in RETIRED_NARRATED_VIDEO_CODES:
        assert code not in content, f"档案仍提到已删除的问题码 {code}"


# ------------------------------------------------------------------- Batch admission


def test_plan_reference_covers_every_admission_decision() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    for decision in BatchAdmissionDecision:
        assert decision.value in content
    assert GenerationProblemCode.BATCH_ADMISSION_WITHHELD.value in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_name_the_admitted_decision(filename: str) -> None:
    assert BatchAdmissionDecision.ADMITTED.value in _skill(filename)


def test_video_skill_names_the_duration_confirmation_code() -> None:
    assert DURATION_CONFIRMATION_CODE in VIDEO_SKILL.read_text(encoding="utf-8")


# ------------------------------------------------------------------- Artifact-state axis


def test_generation_results_reference_covers_every_item_state() -> None:
    content = _reference(GENERATION_RESULTS_REFERENCE)

    for state in GenerationItemState:
        assert state.value in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_name_the_per_id_outcome_states(filename: str) -> None:
    content = _skill(filename)

    for state in (GenerationItemState.SUCCEEDED, GenerationItemState.FAILED, GenerationItemState.BLOCKED):
        assert state.value in content


# ---------------------------------------------------- Tool coverage by variant destination


@pytest.mark.parametrize("filename", EPISODIC_VARIANTS)
def test_episodic_variants_name_the_registered_recovery_tools(filename: str) -> None:
    content = _skill(filename)

    for tool_id in (
        "generate_videos",
        "reset_episode_planning",
        "complete_script_plan_rebuild",
        "get_episode_script",
        "patch_episode_script",
    ):
        assert tool_id in ARCREEL_MCP_TOOL_IDS
        assert f"mcp__arcreel__{tool_id}" in content


def test_ad_variant_names_the_registered_video_tools() -> None:
    content = _skill("SKILL.ad.md")

    for tool_id in ("generate_videos",):
        assert tool_id in ARCREEL_MCP_TOOL_IDS
        assert f"mcp__arcreel__{tool_id}" in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_workflow_variants_force_explicit_video_targets(filename: str) -> None:
    content = _skill(filename)
    selected = content.index('"scope": "selected", "ids": requested_ids')

    assert '"force": true' in content[selected : selected + 180]


def test_profile_has_no_retired_video_tools_or_batch_resume_guidance() -> None:
    content = "\n".join(
        path.read_text(encoding="utf-8") for path in PROFILE.rglob("*") if path.suffix in {".json", ".md", ".py"}
    )

    for retired in (
        "generate_video_episode",
        "generate_video_scene",
        "generate_video_all",
        "generate_video_selected",
    ):
        assert retired not in content
    assert "--resume" not in content
    assert ".checkpoint_" not in content


# ------------------------------------ Profile materialization gives every mode workflow skills


@pytest.mark.parametrize("mode", sorted(VALID_CONTENT_MODES))
def test_every_content_mode_materializes_the_video_workflow_skill(mode: str) -> None:
    mapping = resolve_profile_files_for_mode(PROFILE, mode)

    assert mapping[".claude/skills/video-workflow/SKILL.md"] == f".claude/skills/video-workflow/SKILL.{mode}.md"
    assert mapping[".claude/references/workflow-plan.md"] == ".claude/references/workflow-plan.md"
    assert mapping["CLAUDE.md"] == f"CLAUDE.{mode}.md"
    assert not any(logical.startswith(".claude/skills/manga-workflow/") for logical in mapping)


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_asset_sheet_step_generates_by_episode_id(filename: str) -> None:
    """Servers derive episode-referenced assets; workflows call once by episode_id, never build names in prompts."""

    content = _skill(filename)
    calls = re.findall(r"mcp__arcreel__generate_assets\((\{[^}]*\})\)", content)

    assert calls, f"{filename} 没有 generate_assets 调用模板，断言失去意义"
    for call in calls:
        assert '"episode_id"' in call
        assert '"names"' not in call
    assert "missing_ids ∩" not in content
