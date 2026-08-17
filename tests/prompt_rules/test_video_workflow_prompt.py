"""Behaviour acceptance for the video-workflow Agent Profile.

The profile is a prompt, so what it promises the agent is checked as statements that must
be present in (or absent from) the materialized Markdown. Contract surfaces — action
types, problem codes, admission decisions, delivery options, tool ids — are asserted
through symbols imported from the authoritative module, so widening one of those enums
without teaching the profile about it breaks the test. The remaining assertions quote the
profile's own prose and only guard that the instruction has not been dropped.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from lib.artifact_manifest import ArtifactStatus
from lib.batch_admission import DURATION_CONFIRMATION_CODE, BatchAdmissionDecision
from lib.generation_result import (
    _TASK_FAILURE_ACTIONS,
    GenerationAction,
    GenerationCandidate,
    GenerationItemState,
    GenerationProblem,
    GenerationProblemCode,
    GenerationTargetState,
    artifact_is_reusable,
)
from lib.narration_delivery import POST_PRODUCTION, USE_TTS, NarrationTtsStatus
from lib.profile_manifest import VALID_CONTENT_MODES, resolve_profile_files_for_mode
from lib.workflow_plan import _structure_action
from lib.workflow_rules import WORKFLOW_RULES
from lib.workflow_state import WorkflowActionType, WorkflowTarget
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

# ``next_action.type`` 的闭集就是 ``WorkflowActionType``：编排动作、计划注入的动作与
# ``GenerationAction``（批量准入被拒时原样交回）都在其中。从枚举导出而不是手抄，新增成员
# 时这份契约测试会直接红。
CONTROLLED_ACTIONS = tuple(action.value for action in WorkflowActionType)


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
    # 禁的是「按模式挑步骤」，不是提到模式：AC2 要求变体写明参考路线只跳过分镜图，那句同样带
    # `generation_mode == "reference_video"`。因此只在同一行还出现路由标记时判违约，并放宽引号与空格。
    comparison = re.compile(
        r"(?:generation_mode|content_mode)\s*==\s*[\"']?(?:reference_video|storyboard|drama|narration|ad)\b"
    )
    routing = ("→", "dispatch", "步骤", "mcp__arcreel__generate")
    for line in content.splitlines():
        if comparison.search(line):
            assert not any(marker in line for marker in routing), (
                f"{filename} 按内容模式/生成路线自己挑步骤：{line.strip()}"
            )


def test_plan_reference_shows_duration_confirmation_keyed_by_a_real_unit_id() -> None:
    """键是 unit ID；写成字面量 `unit_id` 会被原样发出去，确认对不上任何 unit。"""

    content = _reference(WORKFLOW_PLAN_REFERENCE)

    assert '"confirmed_request_durations": {"unit_id"' not in content
    assert "confirmed_request_durations" in content
    assert "键是 unit ID" in content


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


def test_prompts_state_that_the_video_tools_require_a_declared_delivery() -> None:
    """工具侧已把交付方式收紧为必填，prompt 不能还在描述「省略即后期配音」。"""

    plan_reference = _reference(WORKFLOW_PLAN_REFERENCE)
    video_skill = (PROFILE / ".claude" / "skills" / "generate-video" / "SKILL.md").read_text(encoding="utf-8")

    for content in (plan_reference, video_skill):
        assert "必填" in content
        assert "不入队任何任务" in content
        assert "省略即按 `post_production` 处理" not in content


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


def test_plan_reference_documents_every_target_field_and_the_two_script_forms() -> None:
    """两个剧本字段一个是可读路径、一个是工具入参裸名，漏掉任一都会让 agent 选错。"""

    content = WORKFLOW_PLAN_REFERENCE.read_text(encoding="utf-8")

    for field in WorkflowTarget.model_fields:
        assert f"`{field}`" in content
    assert "`script` 是相对项目根的剧本路径" in content
    assert "所有 `mcp__arcreel__*` 工具的 `script` 参数用它" in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_read_the_script_by_path_and_call_tools_by_bare_filename(filename: str) -> None:
    content = _skill(filename)

    assert "target.script_filename" in content
    assert not re.search(r'"script":\s*target\.script(?!_filename)', content)
    assert re.search(r"(Read|读取)\s*`?target\.script`?(?!_filename)", content)


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
        'mcp__arcreel__generate_video_selected({"script": target.script_filename, "scene_ids": requested_ids, '
        '"narration_delivery": chosen_narration_delivery})' in content
    )
    assert "`requested_ids` 为空时才调 `mcp__arcreel__generate_video_episode" in content
    assert "`narration_delivery` 必填" in content


def test_asset_analysis_records_completion_fact() -> None:
    content = (PROFILE / ".claude" / "agents" / "analyze-assets.md").read_text(encoding="utf-8")

    assert "mcp__arcreel__complete_asset_inventory" in content
    assert "expected_source_revision" in content
    assert "严格按主 agent 传入的 `scope`" in content
    assert "排除文件名以 `.` / `_` 开头" in content
    assert "`episode_[0-9]+.txt`" in content
    assert "`.text`" not in content
    assert "不要调用 `patch_project`" in content


# ------------------------------------------- TTS 与档位确认按 action 路由、选择不丢


def test_narration_audio_skill_routes_by_problem_action_not_by_code() -> None:
    """`tts_duration_unavailable` 服务端登记为重新合成；按 code 自己推会把它漏成不处理。"""

    content = (PROFILE / ".claude" / "skills" / "generate-narration-audio" / "SKILL.md").read_text(encoding="utf-8")

    assert "problems[].action" in content
    assert "action 是权威" in content
    for code, action in (
        ("tts_stale", GenerationAction.REGENERATE_TTS),
        ("tts_duration_unavailable", GenerationAction.REGENERATE_TTS),
    ):
        assert _TASK_FAILURE_ACTIONS[code] is action
        assert code in content
    assert GenerationAction.GENERATE_TTS.value in content
    assert GenerationAction.WAIT_FOR_TASK.value in content


@pytest.mark.parametrize("filename", WORKFLOW_VARIANTS)
def test_variants_carry_the_delivery_choice_into_the_confirmed_resend(filename: str) -> None:
    """`narration_delivery` 不持久化，重发漏带即失败，不退回 post_production。"""

    content = _skill(filename)

    assert "confirmed_request_durations" in content
    assert "`narration_delivery`" in content
    assert "problems[].action" in content
    assert "必填" in content
    assert "不要自己填" in content or "不要自己填一个值" in content


def test_video_skill_resend_example_keeps_the_delivery_choice() -> None:
    content = (PROFILE / ".claude" / "skills" / "generate-video" / "SKILL.md").read_text(encoding="utf-8")

    blocks = [block for block in content.split("```")[1::2] if "confirmed_request_durations" in block]
    assert blocks, "确认重发的示例代码块不见了"
    for block in blocks:
        assert "narration_delivery" in block
        assert "mcp__arcreel__generate_video" in block
    assert POST_PRODUCTION in content


# ------------------------------------------------- 用户意图不越过计划、隔离草稿优先


@pytest.mark.parametrize("filename", EPISODIC_VARIANTS)
def test_user_named_action_is_checked_against_the_plan(filename: str) -> None:
    content = _skill(filename)

    assert "直接跳到该动作" not in content
    assert "与 `next_action.type` 一致才执行" in content


def test_narration_variant_routes_plan_driven_tts_as_a_controlled_action() -> None:
    """批量准入被拒时计划会把 TTS 动作交回成 next_action，不能一律当成用户显式触发。"""

    content = _skill("SKILL.narration.md")

    assert GenerationAction.GENERATE_TTS.value in content
    assert GenerationAction.REGENERATE_TTS.value in content
    assert "计划驱动" in content
    assert "用户显式触发" in content


def test_split_reference_subagent_prefers_the_quarantined_draft() -> None:
    """违约时正式 JSON 不写、只落 invalid.json——首次生成分支不收紧就会重跑工具重复计费。"""

    content = (PROFILE / ".claude" / "agents" / "split-reference-video-units.md").read_text(encoding="utf-8")

    trigger = content.split("### 情况 A", 1)[1].split("### 情况", 1)[0]
    assert "step1_reference_units.invalid.json" in trigger
    assert "都不存在" in trigger
    assert "先走情况 C" in trigger


# ----------------------------------------- openspec 与 eval 用例跟随计划权威一起迁移


def test_orchestration_spec_and_evals_describe_plan_driven_routing() -> None:
    spec = (REPO / "openspec" / "specs" / "workflow-orchestration" / "spec.md").read_text(encoding="utf-8")

    assert "get_workflow_plan" in spec
    assert "next_action" in spec
    assert "基于 project.json 和文件系统判断当前所处阶段" not in spec
    assert "状态检测" not in spec

    evals = json.loads((PROFILE / "skill-optimization-workspace" / "evals" / "evals.json").read_text(encoding="utf-8"))
    names = {
        assertion["name"]
        for case in evals["evals"]
        if "video-workflow" in case["target_skills"]
        for assertion in case["assertions"]
    }
    assert {"reads_project_json", "uses_glob_check", "identifies_correct_stage"}.isdisjoint(names)
    assert {"queries_workflow_plan", "does_not_infer_stage", "executes_next_action"}.issubset(names)


def test_repair_section_takes_the_revision_from_whichever_action_supplies_it() -> None:
    """`expected_revision` 只随 `patch_episode_script` 注入；两个动作共用一节，来源必须分开写。"""

    problem = GenerationProblem(
        code=GenerationProblemCode.UNIT_REQUEST_INVALID,
        detail="unit 规划不合法",
        action=GenerationAction.FIX_INPUT,
    )
    injected = _structure_action([problem], script_revision="r1")
    assert injected.type == "patch_episode_script"
    assert "expected_revision" in injected.args

    for filename in EPISODIC_VARIANTS:
        section = _skill(filename).split("## `repair_video_units`", 1)[1].split("\n---", 1)[0]
        assert "revision 按动作取" in section
        assert "不必再查" in section
        assert "mcp__arcreel__get_episode_script_revision" in section


def test_export_guidance_forbids_muting_rather_than_merely_recommending_against_it() -> None:
    texts = [_skill(name) for name in WORKFLOW_VARIANTS]
    texts += [(PROFILE / f"CLAUDE.{mode}.md").read_text(encoding="utf-8") for mode in VALID_CONTENT_MODES]

    for text in texts:
        if "静音" in text:
            assert "不要建议静音" not in text
            assert "不静音 provider 原音" in text or "不要静音 provider 原音" in text


def test_regenerating_narration_audio_names_the_segments(tmp_path: Path) -> None:
    """缺省是「只补缺失」，而 stale 算可复用被跳过——不带 ID 的重合成什么都不做。"""

    assert artifact_is_reusable(
        GenerationTargetState(
            candidate=GenerationCandidate(unit_id="E1S01", artifact_key=None, artifact_path="a.mp3"),
            status=ArtifactStatus.STALE,
            blocker=None,
        ),
        manifest_active=True,
        project_dir=tmp_path,
    )

    for content in (
        _skill("SKILL.narration.md"),
        (PROFILE / ".claude" / "skills" / "generate-narration-audio" / "SKILL.md").read_text(encoding="utf-8"),
    ):
        assert f"`{GenerationAction.REGENERATE_TTS.value}`" in content
        assert "segment_ids" in content
        assert "只补缺失" in content
