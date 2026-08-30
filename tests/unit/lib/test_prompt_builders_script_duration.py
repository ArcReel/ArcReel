"""script_plan prompt 的时长约束：档位文案、空集守卫与单集目标时长软约束的注入。"""

from __future__ import annotations

import pytest

from lib.prompt_builders_script import (
    build_narration_split_prompt,
    build_normalize_prompt,
    format_duration_constraint,
)
from lib.prompt_rules.episode_target_duration import (
    EPISODE_TARGET_DURATION_RULE_TEMPLATE,
    render_episode_target_duration_rule,
)

#: 规则句的固定开头：未设目标时整段不注入，该开头不应出现在提示词中。
_RULE_HEAD = EPISODE_TARGET_DURATION_RULE_TEMPLATE.split("{seconds}")[0]


class TestFormatDurationConstraint:
    def test_discrete_set(self):
        text = format_duration_constraint([4, 6, 8], default_duration=None)
        for duration in (4, 6, 8):
            assert str(duration) in text

    def test_discrete_set_with_default(self):
        text = format_duration_constraint([4, 6, 8], default_duration=6)
        assert "6" in text
        assert text != format_duration_constraint([4, 6, 8], default_duration=None)

    def test_default_duration_must_be_in_supported(self):
        """default_duration 不在 supported 集合时应抛错，避免 prompt 自相矛盾。"""
        with pytest.raises(ValueError, match="default_duration=6 不在"):
            format_duration_constraint([4, 8], default_duration=6)

    def test_continuous_range_uses_min_max_phrasing(self):
        """长度 ≥5 且连续整数时压缩为只包含边界的区间。"""
        text = format_duration_constraint([3, 4, 5, 6, 7, 8, 9, 10], default_duration=None)
        assert "3" in text
        assert "10" in text
        assert "[3, 4, 5, 6, 7, 8, 9, 10]" not in text

    def test_short_continuous_still_uses_list(self):
        """长度 <5 即使连续，仍保留中间值。"""
        text = format_duration_constraint([4, 5, 6], default_duration=None)
        assert "5" in text


class TestBuildersRequireDurations:
    """删除 fallback 后，传 None / 空 list 不应再被静默回填。"""

    def test_format_constraint_rejects_empty(self):
        with pytest.raises(ValueError, match="supported_durations 不能为空"):
            format_duration_constraint([], default_duration=None)


def _drama_prompt(**overrides) -> str:
    kwargs = {
        "novel_text": "text",
        "project_overview": {},
        "style": "s",
        "characters": {},
        "scenes": {},
        "props": {},
        "default_duration": None,
        "supported_durations": [4, 8],
        "episode": 1,
    }
    kwargs.update(overrides)
    return build_normalize_prompt(**kwargs)


def _narration_prompt(**overrides) -> str:
    kwargs = {
        "novel_text": "text",
        "project_overview": {},
        "characters": {},
        "scenes": {},
        "props": {},
        "default_duration": None,
        "supported_durations": [4, 8],
        "episode": 1,
    }
    kwargs.update(overrides)
    return build_narration_split_prompt(**kwargs)


class TestEpisodeTargetDurationInjection:
    """单集目标时长非 None 时注入共享软约束句；未设时提示词与不带该参数时逐字相同。"""

    def test_drama_prompt_carries_the_shared_rule(self):
        prompt = _drama_prompt(episode_target_duration=120)
        assert render_episode_target_duration_rule(120) in prompt

    def test_narration_prompt_carries_the_shared_rule(self):
        prompt = _narration_prompt(episode_target_duration=120)
        assert render_episode_target_duration_rule(120) in prompt

    def test_drama_prompt_omits_the_rule_without_a_target(self):
        assert _RULE_HEAD not in _drama_prompt()

    def test_narration_prompt_omits_the_rule_without_a_target(self):
        assert _RULE_HEAD not in _narration_prompt()

    def test_the_rule_coexists_with_the_default_duration_preference(self):
        """两条约束尺度不同（整集体量 vs 单场秒数），须同时呈现而非互相取代。"""
        prompt = _drama_prompt(default_duration=8, episode_target_duration=120)
        assert render_episode_target_duration_rule(120) in prompt
        assert "默认 8 秒" in prompt
