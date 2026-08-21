"""漂移防御：lib.prompt_rules.episode_pacing 的常量必须出现在对应 subagent .md 中。

用首尾 60 字符锚点做 substring 断言，避免空白差异误报。
"""

from pathlib import Path

import pytest

from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
)

pytestmark = pytest.mark.unit

REPO = Path(__file__).resolve().parents[2]


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


def test_narration_pacing_in_split_narration_md() -> None:
    md = (REPO / "agent_runtime_profile/.claude/agents/split-narration-segments.md").read_text(encoding="utf-8")
    md_norm = _normalize(md)
    rules_norm = _normalize(NARRATION_PACING_RULES)
    assert rules_norm[:60] in md_norm, "NARRATION_PACING_RULES 首段未在 split-narration-segments.md 中找到（漂移）"
    assert rules_norm[-60:] in md_norm, "NARRATION_PACING_RULES 末段未在 split-narration-segments.md 中找到（漂移）"
