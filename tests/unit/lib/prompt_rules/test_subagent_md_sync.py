"""漂移防御：项目字段名必须逐字出现在对应 subagent .md 中。"""

from pathlib import Path

import pytest

from lib.episode_target_duration import EPISODE_TARGET_DURATION_FIELD
from lib.episode_target_volume import EPISODE_TARGET_UNITS_FIELD

REPO = Path(__file__).resolve().parents[4]


#: 会在 Step 0 读 ``get_video_capabilities`` 里的单集目标时长的三条脚本规划子智能体，
#: 加上把该字段列进 ``patch_project`` 白名单的 skill，以及分集规划步骤里据它折算每集体量的
#: 两条 video-workflow skill。
EPISODE_TARGET_DURATION_READERS = (
    "agent_runtime_profile/.claude/agents/normalize-drama-script.md",
    "agent_runtime_profile/.claude/agents/split-narration-segments.md",
    "agent_runtime_profile/.claude/agents/split-reference-video-units.md",
    "agent_runtime_profile/.claude/skills/manage-project/SKILL.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.narration.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.drama.md",
)

#: 会在分集规划前核对每集目标体量、并经 ``patch_project`` 写入该字段的 skill。
EPISODE_TARGET_UNITS_READERS = (
    "agent_runtime_profile/.claude/skills/manage-project/SKILL.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.narration.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.drama.md",
)

#: 分集规划回退说明必须在同一行同时说明两个字段，避免只因文件别处出现字段名而误通过。
EPISODE_TARGET_FALLBACK_READERS = (
    "agent_runtime_profile/.claude/references/generation-modes.md",
    "agent_runtime_profile/.claude/skills/manage-project/SKILL.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.narration.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.drama.md",
    "agent_runtime_profile/CLAUDE.narration.md",
    "agent_runtime_profile/CLAUDE.drama.md",
)


@pytest.mark.parametrize("relative_path", EPISODE_TARGET_DURATION_READERS)
def test_episode_target_duration_field_name_is_mirrored(relative_path: str) -> None:
    """字段名是 .md 里写死的机器契约：改了 Python 常量而没同步 .md，子智能体会去读一个不存在的键。"""
    md = (REPO / relative_path).read_text(encoding="utf-8")

    assert EPISODE_TARGET_DURATION_FIELD in md, f"{EPISODE_TARGET_DURATION_FIELD} 未在 {relative_path} 中找到（漂移）"


@pytest.mark.parametrize("relative_path", EPISODE_TARGET_UNITS_READERS)
def test_episode_target_units_field_name_is_mirrored(relative_path: str) -> None:
    """同上：字段名写死在 skill 的 patch_project 调用示例与核对步骤里，改常量须同步 .md。"""
    md = (REPO / relative_path).read_text(encoding="utf-8")

    assert EPISODE_TARGET_UNITS_FIELD in md, f"{EPISODE_TARGET_UNITS_FIELD} 未在 {relative_path} 中找到（漂移）"


@pytest.mark.parametrize("relative_path", EPISODE_TARGET_FALLBACK_READERS)
def test_episode_target_duration_fallback_is_mirrored(relative_path: str) -> None:
    """每份分集规划说明都明确记录显式体量优先、时长仅作回退。"""
    lines = (REPO / relative_path).read_text(encoding="utf-8").splitlines()

    assert any(
        EPISODE_TARGET_UNITS_FIELD in line
        and EPISODE_TARGET_DURATION_FIELD in line
        and any(marker in line for marker in ("未设", "未显式设", "缺失"))
        for line in lines
    ), f"{relative_path} 未在同一段说明 {EPISODE_TARGET_UNITS_FIELD} 与 {EPISODE_TARGET_DURATION_FIELD} 的回退关系"
