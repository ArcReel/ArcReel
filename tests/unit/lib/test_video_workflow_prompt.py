"""Contract coverage for the video-workflow Agent Profile.

档案是 prompt，但这里只断言**能对照代码真相源的覆盖**：受控动作与问题码枚举、产物状态枚举、
准入结论、旁白交付常量、已注册的 `mcp__arcreel__*` 工具名，以及按创作类型物化出的文件映射。
措辞不在断言范围内——服务端扩一个枚举而档案没跟上会红，改一句措辞不会。越界行为由服务端契约
与 ``AgentAccessPolicy`` 在工具边界上拒绝，不靠在测试里抄一遍 prompt 原文。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.batch_admission import DURATION_CONFIRMATION_CODE, BatchAdmissionDecision
from lib.generation_result import (
    _TASK_FAILURE_ACTIONS,
    GenerationAction,
    GenerationItemState,
    GenerationProblemCode,
)
from lib.narration_delivery import POST_PRODUCTION, USE_TTS
from lib.profile_manifest import VALID_CONTENT_MODES, resolve_profile_files_for_mode
from lib.workflow_rules import WORKFLOW_RULES
from lib.workflow_state import WorkflowActionType, WorkflowTarget
from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

REPO = Path(__file__).resolve().parents[3]
PROFILE = REPO / "agent_runtime_profile"
SKILL_DIR = PROFILE / ".claude" / "skills" / "video-workflow"
REFERENCES = PROFILE / ".claude" / "references"
WORKFLOW_PLAN_REFERENCE = REFERENCES / "workflow-plan.md"
GENERATION_RESULTS_REFERENCE = REFERENCES / "generation-results.md"
VIDEO_SKILL = PROFILE / ".claude" / "skills" / "generate-video" / "SKILL.md"
DISTRIBUTED_VIDEO_WORKFLOW = REPO / "skills" / "video-workflow" / "SKILL.md"
NARRATION_AUDIO_SKILL = PROFILE / ".claude" / "skills" / "generate-narration-audio" / "SKILL.md"

WORKFLOW_VARIANTS = ("SKILL.narration.md", "SKILL.drama.md", "SKILL.ad.md")
EPISODIC_VARIANTS = ("SKILL.narration.md", "SKILL.drama.md")

# ``next_action.type`` 的闭集就是 ``WorkflowActionType``：编排动作、计划注入的动作与
# ``GenerationAction``（整批准入判定被拒时原样交回）都在其中。从枚举导出而不是手抄，新增成员
# 时这份契约测试会直接红。
CONTROLLED_ACTIONS = tuple(action.value for action in WorkflowActionType)

TTS_PROBLEM_CODES = tuple(code for code in _TASK_FAILURE_ACTIONS if code.startswith("tts_"))
assert TTS_PROBLEM_CODES, "_TASK_FAILURE_ACTIONS 里已没有 tts_ 前缀问题码，请更新本测试的派生条件"


def _skill(filename: str) -> str:
    return (SKILL_DIR / filename).read_text(encoding="utf-8")


def _reference(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ------------------------------------------------- 计划是步骤适用性的唯一真相源


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_route_through_the_registered_plan_tool(filename: str) -> None:
    content = _skill(filename)

    assert "get_workflow_plan" in ARCREEL_MCP_TOOL_IDS
    assert "mcp__arcreel__get_workflow_plan" in content
    assert "mcp__arcreel__get_workflow_status" not in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_do_not_name_the_preprocessor_subagents_themselves(filename: str) -> None:
    """脚本规划子智能体由计划的 ``next_action.args.preprocessor`` 指名，档案侧不得再推一遍。"""

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


# ------------------------------------------------------------------- 旁白交付


@pytest.mark.parametrize("path", (WORKFLOW_PLAN_REFERENCE, VIDEO_SKILL))
def test_delivery_options_are_both_named_where_the_choice_is_made(path: Path) -> None:
    content = _reference(path)

    assert POST_PRODUCTION in content
    assert USE_TTS in content


def test_generate_video_waits_on_the_durable_batch_without_forcing_completed_targets() -> None:
    content = _reference(VIDEO_SKILL)

    assert "get_generation_batch" in content
    assert "poll_after_seconds" in content
    assert "已有在途任务时不自动 force 重做" in content


def test_preexisting_tasks_use_bounded_plan_polling() -> None:
    content = _reference(DISTRIBUTED_VIDEO_WORKFLOW)

    assert "wait_for_task" in content
    assert "max_poll_attempts" in content
    assert "get_workflow_plan" in content


def test_plan_reference_covers_every_tts_problem_code_and_its_action() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    for code in TTS_PROBLEM_CODES:
        assert f"`{code}`" in content, f"旁白问题码表缺 {code}"
        assert f"`{_TASK_FAILURE_ACTIONS[code].value}`" in content


def test_narration_audio_skill_covers_the_tts_actions() -> None:
    content = NARRATION_AUDIO_SKILL.read_text(encoding="utf-8")

    for action in (GenerationAction.GENERATE_TTS, GenerationAction.REGENERATE_TTS, GenerationAction.WAIT_FOR_TASK):
        assert action.value in content
    for code in ("tts_stale", "tts_duration_unavailable"):
        assert _TASK_FAILURE_ACTIONS[code] is GenerationAction.REGENERATE_TTS
        assert code in content


# ------------------------------------------------------------------- 整批准入判定


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


# ----------------------------------------------------------------- 产物状态轴


def test_generation_results_reference_covers_every_item_state() -> None:
    content = _reference(GENERATION_RESULTS_REFERENCE)

    for state in GenerationItemState:
        assert state.value in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_name_the_per_id_outcome_states(filename: str) -> None:
    content = _skill(filename)

    for state in (GenerationItemState.SUCCEEDED, GenerationItemState.FAILED, GenerationItemState.BLOCKED):
        assert state.value in content


# ---------------------------------------------------- 各变体落点的工具名覆盖


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


def test_asset_analysis_subagent_names_its_registered_tool() -> None:
    content = (PROFILE / ".claude" / "agents" / "analyze-assets.md").read_text(encoding="utf-8")

    assert "complete_asset_inventory" in ARCREEL_MCP_TOOL_IDS
    assert "mcp__arcreel__complete_asset_inventory" in content


# ------------------------------------ Profile 物化：每个模式都拿到工作流 skill


@pytest.mark.parametrize("mode", sorted(VALID_CONTENT_MODES))
def test_every_content_mode_materializes_the_video_workflow_skill(mode: str) -> None:
    mapping = resolve_profile_files_for_mode(PROFILE, mode)

    assert mapping[".claude/skills/video-workflow/SKILL.md"] == f".claude/skills/video-workflow/SKILL.{mode}.md"
    assert mapping[".claude/references/workflow-plan.md"] == ".claude/references/workflow-plan.md"
    assert mapping["CLAUDE.md"] == f"CLAUDE.{mode}.md"
    assert not any(logical.startswith(".claude/skills/manga-workflow/") for logical in mapping)
