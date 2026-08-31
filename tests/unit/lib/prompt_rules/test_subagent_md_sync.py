"""漂移防御：项目字段名必须逐字出现在对应 subagent .md 中。"""

from pathlib import Path

import pytest

from lib.episode_target_duration import EPISODE_TARGET_DURATION_FIELD

REPO = Path(__file__).resolve().parents[4]


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
