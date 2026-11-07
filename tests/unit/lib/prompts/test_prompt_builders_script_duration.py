"""script_plan duration constraints: tier copy, empty-set guards, and episode target-duration soft constraints."""

from __future__ import annotations

import pytest

from lib.prompts.prompt_builders_script import (
    build_narration_split_prompt,
    build_normalize_prompt,
    format_duration_constraint,
)

#: Fixed rule-sentence opening: with no target, omit the whole block and its opening from prompts.
_RULE_HEAD = "The target finished duration of this episode is approximately "

#: Shared-fragment target sentence combines both directions after the soft target into one sentence.
_SHARED_TARGET_RULE = (
    "The target finished duration of this episode is approximately {seconds} seconds: use it to decide the number of units and splitting granularity so their durations sum toward this target. "
    "This is a soft target, not a hard upper bound: do not pad or fragment content to fill it, and do not remove necessary plot to fit it."
)


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
        """default_duration outside supported sets raises, preventing self-contradictory prompts."""
        with pytest.raises(ValueError, match="default_duration=6 不在"):
            format_duration_constraint([4, 8], default_duration=6)

    def test_continuous_range_uses_min_max_phrasing(self):
        """Consecutive integer sets of length ≥5 compress into boundary-only ranges."""
        text = format_duration_constraint([3, 4, 5, 6, 7, 8, 9, 10], default_duration=None)
        assert "3" in text
        assert "10" in text
        assert "[3, 4, 5, 6, 7, 8, 9, 10]" not in text

    def test_short_continuous_still_uses_list(self):
        """Sets shorter than 5 retain internal values even when consecutive."""
        text = format_duration_constraint([4, 5, 6], default_duration=None)
        assert "5" in text


class TestBuildersRequireDurations:
    """After fallback removal, None/empty lists must not be silently refilled."""

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
    """Non-None episode targets inject shared soft constraints; omitted targets yield prompts identical to calls without the argument."""

    def test_drama_prompt_carries_the_shared_rule(self):
        prompt = _drama_prompt(episode_target_duration=120)
        assert _SHARED_TARGET_RULE.format(seconds=120) in prompt

    def test_narration_prompt_carries_the_shared_rule(self):
        prompt = _narration_prompt(episode_target_duration=120)
        assert _SHARED_TARGET_RULE.format(seconds=120) in prompt

    def test_drama_prompt_omits_the_rule_without_a_target(self):
        assert _RULE_HEAD not in _drama_prompt()

    def test_narration_prompt_omits_the_rule_without_a_target(self):
        assert _RULE_HEAD not in _narration_prompt()

    def test_the_rule_coexists_with_the_default_duration_preference(self):
        """Both scales, episode volume versus scene seconds, coexist rather than replace one another."""
        prompt = _drama_prompt(default_duration=8, episode_target_duration=120)
        assert _SHARED_TARGET_RULE.format(seconds=120) in prompt
        assert "default 8 seconds" in prompt


@pytest.mark.parametrize("target", [None, 120])
@pytest.mark.parametrize("instructions", [None, "", "保留末尾停顿。"])
def test_narration_optional_target_and_instructions(target, instructions):
    prompt = _narration_prompt(episode_target_duration=target, instructions=instructions)
    assert ("The target finished duration of this episode is approximately 120 seconds" in prompt) is bool(target)
    assert ("# Additional instructions" in prompt) is bool(instructions)
    if target:
        assert "do not pad or fragment content to fill it, and do not remove necessary plot to fit it" in prompt
        assert "not enough content to support the target" not in prompt
    if instructions:
        assert prompt.endswith("\n\n# Additional instructions\n保留末尾停顿。")
    assert "None" not in prompt
