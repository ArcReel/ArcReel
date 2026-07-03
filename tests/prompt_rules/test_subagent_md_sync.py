"""漂移防御：lib.prompt_rules.episode_pacing 的常量必须出现在对应 subagent .md 中。

用首尾 60 字符锚点做 substring 断言，避免空白差异误报。
"""

from pathlib import Path

from lib.prompt_rules.creative_quality import CREATIVE_QUALITY_RULES
from lib.prompt_rules.episode_pacing import (
    DRAMA_PACING_RULES,
    NARRATION_PACING_RULES,
)
from lib.prompt_rules.seedance_policy import SEEDANCE_PROMPT_POLICY
from lib.prompt_rules.storyboard_continuity import STORYBOARD_CONTINUITY_RULES

REPO = Path(__file__).resolve().parents[2]


def _normalize(text: str) -> str:
    return "".join(text.split())


def test_drama_pacing_in_normalize_drama_md() -> None:
    md = (REPO / "agent_runtime_profile/.claude/agents/normalize-drama-script.md").read_text(encoding="utf-8")
    md_norm = _normalize(md)
    rules_norm = _normalize(DRAMA_PACING_RULES)
    assert rules_norm[:60] in md_norm, "DRAMA_PACING_RULES 首段未在 normalize-drama-script.md 中找到（漂移）"
    assert rules_norm[-60:] in md_norm, "DRAMA_PACING_RULES 末段未在 normalize-drama-script.md 中找到（漂移）"


def test_narration_pacing_in_split_narration_md() -> None:
    md = (REPO / "agent_runtime_profile/.claude/agents/split-narration-segments.md").read_text(encoding="utf-8")
    md_norm = _normalize(md)
    rules_norm = _normalize(NARRATION_PACING_RULES)
    assert rules_norm[:60] in md_norm, "NARRATION_PACING_RULES 首段未在 split-narration-segments.md 中找到（漂移）"
    assert rules_norm[-60:] in md_norm, "NARRATION_PACING_RULES 末段未在 split-narration-segments.md 中找到（漂移）"


def _assert_rules_in_md(md_path: str, rules: str, label: str) -> None:
    md = (REPO / md_path).read_text(encoding="utf-8")
    md_norm = _normalize(md)
    rules_norm = _normalize(rules)
    assert rules_norm[:60] in md_norm, f"{label} 首段未在 {md_path} 中找到（漂移）"
    assert rules_norm[-60:] in md_norm, f"{label} 末段未在 {md_path} 中找到（漂移）"


def test_short_drama_standards_in_subagent_md() -> None:
    md_paths = [
        "agent_runtime_profile/.claude/agents/normalize-drama-script.md",
        "agent_runtime_profile/.claude/agents/split-narration-segments.md",
        "agent_runtime_profile/.claude/agents/create-episode-script.md",
    ]
    for md_path in md_paths:
        _assert_rules_in_md(md_path, SEEDANCE_PROMPT_POLICY, "SEEDANCE_PROMPT_POLICY")
        _assert_rules_in_md(md_path, STORYBOARD_CONTINUITY_RULES, "STORYBOARD_CONTINUITY_RULES")
        _assert_rules_in_md(md_path, CREATIVE_QUALITY_RULES, "CREATIVE_QUALITY_RULES")
