"""Drift defense: project field names must appear verbatim in corresponding subagent .md files."""

from pathlib import Path

import pytest

from lib.episode.episode_target_duration import EPISODE_TARGET_DURATION_FIELD
from lib.episode.episode_target_volume import EPISODE_TARGET_UNITS_FIELD

REPO = Path(__file__).resolve().parents[5]


#: Three script-planning subagents reading episode target duration from ``get_video_capabilities`` at Step 0,
#: plus the skill allowing that field in ``patch_project`` and two video-workflow skills deriving
#: episode volume from it during episode planning.
EPISODE_TARGET_DURATION_READERS = (
    "agent_runtime_profile/.claude/agents/normalize-drama-script.md",
    "agent_runtime_profile/.claude/agents/split-narration-segments.md",
    "agent_runtime_profile/.claude/agents/split-reference-video-units.md",
    "agent_runtime_profile/.claude/skills/manage-project/SKILL.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.narration.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.drama.md",
)

#: Skill checking episode target volume before planning and writing the field through ``patch_project``.
EPISODE_TARGET_UNITS_READERS = (
    "agent_runtime_profile/.claude/skills/manage-project/SKILL.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.narration.md",
    "agent_runtime_profile/.claude/skills/video-workflow/SKILL.drama.md",
)

#: Episode fallback explanations must mention both fields on one line, preventing false passes from unrelated mentions elsewhere.
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
    """Field names are hardcoded machine contracts in .md; changing Python constants alone makes subagents read nonexistent keys."""
    md = (REPO / relative_path).read_text(encoding="utf-8")

    assert EPISODE_TARGET_DURATION_FIELD in md, f"{EPISODE_TARGET_DURATION_FIELD} 未在 {relative_path} 中找到（漂移）"


@pytest.mark.parametrize("relative_path", EPISODE_TARGET_UNITS_READERS)
def test_episode_target_units_field_name_is_mirrored(relative_path: str) -> None:
    """Likewise, skills hardcode fields in patch_project examples/checks; synchronize .md when constants change."""
    md = (REPO / relative_path).read_text(encoding="utf-8")

    assert EPISODE_TARGET_UNITS_FIELD in md, f"{EPISODE_TARGET_UNITS_FIELD} 未在 {relative_path} 中找到（漂移）"


@pytest.mark.parametrize("relative_path", EPISODE_TARGET_FALLBACK_READERS)
def test_episode_target_duration_fallback_is_mirrored(relative_path: str) -> None:
    """Every episode-planning instruction states both fields’ relationship on one line: explicit volume first, duration fallback only.

    Assert same-line field names only, not exact fallback wording; deleting the explanation or
    leaving only one field fails.
    """
    lines = (REPO / relative_path).read_text(encoding="utf-8").splitlines()

    assert any(EPISODE_TARGET_UNITS_FIELD in line and EPISODE_TARGET_DURATION_FIELD in line for line in lines), (
        f"{relative_path} 未在同一段说明 {EPISODE_TARGET_UNITS_FIELD} 与 {EPISODE_TARGET_DURATION_FIELD} 的回退关系"
    )
