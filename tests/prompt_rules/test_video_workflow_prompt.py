"""Behaviour acceptance for the video-workflow Agent Profile.

The profile is a prompt, so what it promises the agent is checked as statements that must
be present in (or absent from) the materialized Markdown. Contract surfaces — action
types, problem codes, admission decisions, delivery options, tool ids — are asserted
through symbols imported from the authoritative module, so widening one of those enums
without teaching the profile about it breaks the test. The remaining assertions quote the
profile's own prose and only guard that the instruction has not been dropped.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.batch_admission import DURATION_CONFIRMATION_CODE, BatchAdmissionDecision
from lib.generation_result import GenerationAction, GenerationItemState, GenerationProblemCode
from lib.narration_delivery import POST_PRODUCTION, USE_TTS, NarrationTtsStatus
from lib.profile_manifest import VALID_CONTENT_MODES, resolve_profile_files_for_mode
from lib.workflow_rules import WORKFLOW_RULES
from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]
PROFILE = REPO / "agent_runtime_profile"
SKILL_DIR = PROFILE / ".claude" / "skills" / "video-workflow"
REFERENCES = PROFILE / ".claude" / "references"
WORKFLOW_PLAN_REFERENCE = REFERENCES / "workflow-plan.md"
GENERATION_RESULTS_REFERENCE = REFERENCES / "generation-results.md"

WORKFLOW_VARIANTS = ("SKILL.narration.md", "SKILL.drama.md", "SKILL.ad.md")
EPISODIC_VARIANTS = ("SKILL.narration.md", "SKILL.drama.md")

# ``workflow_state`` 自己产出的动作类型是散落的字符串字面量，没有枚举可导入，只能手写。
_WORKFLOW_STATE_ACTIONS = (
    "collect_project_input",
    "draft_selling_points",
    "analyze_assets",
    "plan_episodes",
    "reset_episode_planning",
    "prepare_step1",
    "confirm_step1",
    "generate_script",
    "generate_asset_sheets",
    "generate_storyboards",
    "generate_grid",
    "generate_videos",
    "repair_video_units",
    "export",
    "none",
)

# ``build_workflow_plan`` 额外注入的动作类型。
_PLAN_INJECTED_ACTIONS = ("patch_episode_script", "choose_narration_delivery")

# 批量准入被拒时 ``_admission_action`` 把 ``problems[0].action`` 直接当成 ``next_action.type``
# 交回，所以整个 ``GenerationAction`` 闭集都可能出现在计划里。从枚举导出而不是手抄，新增
# 动作时这份契约测试会直接红。
CONTROLLED_ACTIONS = tuple(
    dict.fromkeys((*_WORKFLOW_STATE_ACTIONS, *_PLAN_INJECTED_ACTIONS, *(action.value for action in GenerationAction)))
)


def _skill(filename: str) -> str:
    return (SKILL_DIR / filename).read_text(encoding="utf-8")


def _reference(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ------------------------------------------------- 计划是步骤适用性的唯一真相源


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_route_through_the_authoritative_plan(filename: str) -> None:
    content = _skill(filename)

    assert "mcp__arcreel__get_workflow_plan" in content
    assert "mcp__arcreel__get_workflow_status" not in content
    assert "workflow-plan.md" in content
    assert "next_action" in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_do_not_hardcode_a_route_or_content_mode_step_table(filename: str) -> None:
    """六组合的步骤适用性只能由计划表达，profile 侧不得再推一遍。"""

    content = _skill(filename)

    for rule in WORKFLOW_RULES.values():
        if rule.preprocessor is not None:
            assert rule.preprocessor not in content, (
                f"{filename} 硬编码了预处理 subagent {rule.preprocessor}；应改读 next_action.args.preprocessor"
            )
    assert "generation_mode == reference_video" not in content
    assert "content_mode == drama" not in content
    assert "content_mode == narration" not in content


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


# ------------------------------------------------------------------- 旁白交付


def test_plan_reference_states_both_narration_delivery_options() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    assert f"`{POST_PRODUCTION}`" in content
    assert f"`{USE_TTS}`" in content
    assert "choose_narration_delivery" in content
    assert "从不持久化" in content


def test_reference_route_skips_only_storyboard_images() -> None:
    for path in (WORKFLOW_PLAN_REFERENCE, REFERENCES / "generation-modes.md"):
        content = _reference(path)
        assert "只跳过" in content and "不跳过 audio" in content


def test_missing_tts_defaults_to_post_production_without_pushing_provider_setup() -> None:
    plan_reference = _reference(WORKFLOW_PLAN_REFERENCE)

    assert NarrationTtsStatus.NOT_CONFIGURED.value in plan_reference
    assert "tts_not_configured" in plan_reference
    assert "不要建议用户为了继续做视频去配置 TTS 供应商" in plan_reference

    audio_skill = (PROFILE / ".claude" / "skills" / "generate-narration-audio" / "SKILL.md").read_text(encoding="utf-8")
    assert "不要建议用户为了" in audio_skill

    video_skill = (PROFILE / ".claude" / "skills" / "generate-video" / "SKILL.md").read_text(encoding="utf-8")
    assert "试听" in video_skill
    assert "tts_missing" in video_skill
    assert "tts_stale" in video_skill


# ------------------------------------------------------------------- 批量准入


def test_plan_reference_states_all_or_nothing_admission() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    for decision in BatchAdmissionDecision:
        assert decision.value in content
    assert "全有或全无" in content
    assert GenerationProblemCode.BATCH_ADMISSION_WITHHELD.value in content
    assert "blocked_unit_ids" in content
    assert "不拆批" in content or "不要把整批拆成小批" in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_refuse_to_resubmit_the_passing_half_of_a_refused_batch(filename: str) -> None:
    content = _skill(filename)

    assert "admission" in content
    assert BatchAdmissionDecision.ADMITTED.value in content
    assert "一个任务都不入队" in content
    assert "不拆批先跑通过的那一半" in content


@pytest.mark.parametrize("filename", EPISODIC_VARIANTS)
def test_episodic_variants_route_repair_wait_and_named_redo(filename: str) -> None:
    """计划可以交回这三类动作，变体里必须各有落点，否则 agent 收到就没路可走。"""

    content = _skill(filename)

    assert "repair_video_units" in content
    assert "patch_episode_script" in content
    assert GenerationAction.WAIT_FOR_TASK.value in content
    # 点名即强制重做的请求选择语义：非空 requested_ids 必须走 selected 工具。
    assert "mcp__arcreel__generate_video_selected" in content
    assert "mcp__arcreel__generate_video_episode" in content


def test_video_skill_confirms_duration_tiers_without_splitting_the_batch() -> None:
    content = (PROFILE / ".claude" / "skills" / "generate-video" / "SKILL.md").read_text(encoding="utf-8")

    assert DURATION_CONFIRMATION_CODE in content
    assert "confirmed_request_durations" in content
    assert "仍作为一批重发" in content

    # 入队工具没有 confirm_duration 参数，写进 skill 会让 agent 发出必被拒的请求。
    # DURATION_CONFIRMATION_CODE 本身含该子串，先摘掉再查裸参数名。
    assert "confirm_duration" not in content.replace(DURATION_CONFIRMATION_CODE, "")


# ------------------------------------------------------- 四条状态轴 / 逐 ID 分账


def test_generation_results_reference_keeps_four_axes_apart() -> None:
    content = _reference(GENERATION_RESULTS_REFERENCE)

    assert "四条状态轴分开读" in content
    assert "provider_checkpoint" in content
    assert "任务成功 ≠ 产物匹配当前依据" in content
    for state in GenerationItemState:
        assert state.value in content


def test_plan_reference_keeps_four_axes_apart() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    assert "provider_checkpoint" in content
    assert "「任务成功」不等于「当前产物有效」" in content
    assert "interrupted" in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_report_per_id_outcomes_on_four_axes(filename: str) -> None:
    content = _skill(filename)

    assert "provider checkpoint" in content
    assert "四轴" in content
    for state in (GenerationItemState.SUCCEEDED, GenerationItemState.FAILED, GenerationItemState.BLOCKED):
        assert state.value in content


# ---------------------------------------------------------------- stale 与付费历史


def test_plan_reference_protects_stale_artifacts_and_paid_history() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    assert "stale 产物照常可预览、可导出、可参与成片" in content
    assert "不得自动删除、覆盖或重生任何已付费产物" in content
    assert "重复计费" in content


def test_generation_results_reference_protects_paid_history() -> None:
    content = _reference(GENERATION_RESULTS_REFERENCE)

    assert "历史版本" in content
    assert "不得自动删除、覆盖或重生" in content


# ------------------------------------------------------- 恢复任务与重试不可互换


def test_plan_reference_separates_resume_from_retry() -> None:
    content = _reference(WORKFLOW_PLAN_REFERENCE)

    assert "wait_for_task" in content
    assert "`resume` 与 `retry` 不可互换" in content


def test_video_skill_separates_resume_from_retry() -> None:
    content = (PROFILE / ".claude" / "skills" / "generate-video" / "SKILL.md").read_text(encoding="utf-8")

    assert "`resume` 与 `retry` 不可互换" in content
    assert "interrupted" in content


# ------------------------------------------ compose / export 的声音与字幕归服务端


def test_compose_skill_defers_sound_and_subtitles_to_presentation() -> None:
    content = (PROFILE / ".claude" / "skills" / "compose-video" / "SKILL.md").read_text(encoding="utf-8")

    assert "presentation" in content
    assert "不静音" in content
    assert "不自行估算字幕时间轴" in content
    assert "不替用户判断 TTS 是否必需" in content


@pytest.mark.parametrize("mode", sorted(VALID_CONTENT_MODES))
def test_top_level_profile_defers_export_semantics_to_presentation(mode: str) -> None:
    content = (PROFILE / f"CLAUDE.{mode}.md").read_text(encoding="utf-8")

    assert "presentation" in content
    assert "不静音 provider 原音" in content
    assert "不替用户判断 TTS 是否必需" in content


# ------------------------------------ Profile 物化：每个模式都拿到工作流 skill


@pytest.mark.parametrize("mode", sorted(VALID_CONTENT_MODES))
def test_every_content_mode_materializes_the_video_workflow_skill(mode: str) -> None:
    mapping = resolve_profile_files_for_mode(PROFILE, mode)

    assert mapping[".claude/skills/video-workflow/SKILL.md"] == f".claude/skills/video-workflow/SKILL.{mode}.md"
    assert mapping[".claude/references/workflow-plan.md"] == ".claude/references/workflow-plan.md"
    assert mapping["CLAUDE.md"] == f"CLAUDE.{mode}.md"
    assert not any(logical.startswith(".claude/skills/manga-workflow/") for logical in mapping)


# --------------------------------------------- 既有约束：权威参数原样透传、恢复动作


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_asset_and_storyboard_routes_forward_authoritative_arguments(filename: str) -> None:
    content = _skill(filename)

    assert '"names": [该类型 requested_ids]' in content
    assert '"segment_ids": requested_ids' in content
    assert '"scene_ids": requested_ids' in content
    assert "target.episode" in content
    assert '"script": target.script_filename' in content
    assert "next_action.args" in content
    if filename != "SKILL.ad.md":
        assert "names = artifacts.asset_sheets[type].missing_ids ∩ requested_ids" in content
        assert "若 names 为空 → 跳过，不 dispatch；不得回退到整类 missing_ids" in content


@pytest.mark.parametrize("filename", EPISODIC_VARIANTS)
def test_episodic_variants_take_the_preprocessor_from_the_plan(filename: str) -> None:
    """ad 无 step1，不参与本断言。"""

    assert "next_action.args.preprocessor" in _skill(filename)


@pytest.mark.parametrize("filename", EPISODIC_VARIANTS)
def test_reset_route_executes_recovery_and_refreshes_the_plan(filename: str) -> None:
    content = _skill(filename)

    assert "mcp__arcreel__reset_episode_planning" in content
    assert "confirm_consumed: true" in content
    assert "重置成功后刷新计划" in content


@pytest.mark.parametrize("filename", EPISODIC_VARIANTS)
def test_stale_step1_records_explicit_rebuild_completion(filename: str) -> None:
    content = _skill(filename)

    assert "mcp__arcreel__complete_step1_rebuild" in content
    assert "expected_stale_step1_revision" in content
    assert "确定性重建可能产出完全相同的 JSON" in content


def test_ad_workflow_regenerates_named_reference_units_with_selected_tool() -> None:
    content = _skill("SKILL.ad.md")

    assert (
        'mcp__arcreel__generate_video_selected({"script": target.script_filename, "scene_ids": requested_ids})'
        in content
    )
    assert "`requested_ids` 为空时才调 `mcp__arcreel__generate_video_episode" in content


def test_asset_analysis_records_completion_fact() -> None:
    content = (PROFILE / ".claude" / "agents" / "analyze-assets.md").read_text(encoding="utf-8")

    assert "mcp__arcreel__complete_asset_inventory" in content
    assert "expected_source_revision" in content
    assert "严格按主 agent 传入的 `scope`" in content
    assert "排除文件名以 `.` / `_` 开头" in content
    assert "`episode_[0-9]+.txt`" in content
    assert "`.text`" not in content
    assert "不要调用 `patch_project`" in content
