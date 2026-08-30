"""漂移防御：lib.prompt_rules.episode_pacing 的常量与项目字段名必须出现在对应 subagent .md 中。

用首尾 60 字符锚点做 substring 断言，避免空白差异误报。
"""

from pathlib import Path

import pytest

from lib.episode_target_duration import EPISODE_TARGET_DURATION_FIELD
from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
)
from lib.reference_video.writing_syntax import SCENE_REFERENCE_RULES

REPO = Path(__file__).resolve().parents[4]

SCRIPT_PLAN_DRAFT_AGENTS = (
    "agent_runtime_profile/.claude/agents/create-episode-script.md",
    "agent_runtime_profile/.claude/agents/normalize-drama-script.md",
    "agent_runtime_profile/.claude/agents/split-reference-video-units.md",
)


def _normalize(text: str) -> str:
    return "".join(text.split())


def test_drama_pacing_in_normalize_drama_md() -> None:
    md = (REPO / "agent_runtime_profile/.claude/agents/normalize-drama-script.md").read_text(encoding="utf-8")
    md_norm = _normalize(md)
    rules_norm = _normalize(DRAMA_PACING_RULES)
    assert rules_norm[:60] in md_norm, "DRAMA_PACING_RULES 首段未在 normalize-drama-script.md 中找到（漂移）"
    assert rules_norm[-60:] in md_norm, "DRAMA_PACING_RULES 末段未在 normalize-drama-script.md 中找到（漂移）"


def test_drama_repair_scope_covers_entire_draft_content() -> None:
    md = (REPO / "agent_runtime_profile/.claude/agents/normalize-drama-script.md").read_text(encoding="utf-8")

    assert "修复草稿 `content` 中对应字段" in md
    assert "只修改草稿的 `content.scenes[i]`" not in md


@pytest.mark.parametrize("relative_path", SCRIPT_PLAN_DRAFT_AGENTS)
def test_script_plan_draft_repair_combines_user_intent_with_violation_repair(relative_path: str) -> None:
    md = (REPO / relative_path).read_text(encoding="utf-8")

    assert (
        "保留草稿中已有修改；如主 Agent 本轮传入用户修改意见，先应用该意见；`violations[]` 非空时，在上述修改基础上"
    ) in md
    assert "为空时保留已有修改" not in md
    assert "为空时按用户修改意见定位" not in md
    assert "不得因为没有违约就原样晋升" not in md


def test_scene_reference_rules_in_split_reference_video_units_md() -> None:
    md_norm = _normalize(
        (REPO / "agent_runtime_profile/.claude/agents/split-reference-video-units.md").read_text(encoding="utf-8")
    )
    rules_norm = _normalize(SCENE_REFERENCE_RULES)
    assert rules_norm in md_norm, "SCENE_REFERENCE_RULES 未完整同步到 split-reference-video-units.md（漂移）"


def test_narration_pacing_in_split_narration_md() -> None:
    md = (REPO / "agent_runtime_profile/.claude/agents/split-narration-segments.md").read_text(encoding="utf-8")
    md_norm = _normalize(md)
    rules_norm = _normalize(NARRATION_PACING_RULES)
    assert rules_norm in md_norm, "NARRATION_PACING_RULES 未完整同步到 split-narration-segments.md（漂移）"


#: 会在 Step 0 读 ``get_video_capabilities`` 里的单集目标时长的三条脚本规划子智能体，
#: 加上把该字段列进 ``patch_project`` 白名单的 skill。
EPISODE_TARGET_DURATION_READERS = (
    "agent_runtime_profile/.claude/agents/normalize-drama-script.md",
    "agent_runtime_profile/.claude/agents/split-narration-segments.md",
    "agent_runtime_profile/.claude/agents/split-reference-video-units.md",
    "agent_runtime_profile/.claude/skills/manage-project/SKILL.md",
)


@pytest.mark.parametrize("relative_path", EPISODE_TARGET_DURATION_READERS)
def test_episode_target_duration_field_name_is_mirrored(relative_path: str) -> None:
    """字段名是 .md 里写死的机器契约：改了 Python 常量而没同步 .md，子智能体会去读一个不存在的键。"""
    md = (REPO / relative_path).read_text(encoding="utf-8")

    assert EPISODE_TARGET_DURATION_FIELD in md, f"{EPISODE_TARGET_DURATION_FIELD} 未在 {relative_path} 中找到（漂移）"
