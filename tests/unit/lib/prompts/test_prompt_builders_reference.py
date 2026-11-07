"""reference_video prompt-builder unit tests."""

import pytest

from lib.prompts.prompt_builders_reference import (
    build_reference_units_split_prompt,
    build_reference_video_prompt,
    render_reference_units_for_prompt_authoring,
)

_SCENE_REFERENCE_RULE = (
    "Consecutive units at the same location must **repeat the same scene reference in every unit**, not only the first."
)


def _prompt_authoring_prompt(**overrides) -> str:
    kwargs = {
        "project_overview": {"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        "style": "s",
        "style_description": "d",
        "characters": {"A": {"description": "d"}},
        "scenes": {},
        "props": {},
        "script_plan_units": [],
        "max_refs": 9,
        "episode": 1,
    }
    kwargs.update(overrides)
    return build_reference_video_prompt(**kwargs)


def _split_prompt(**overrides) -> str:
    kwargs = {
        "novel_text": "text",
        "project_overview": {},
        "characters": {},
        "scenes": {},
        "props": {},
        "supported_durations": [8],
        "max_duration": 8,
        "max_reference_images": None,
        "default_duration": None,
        "episode": 1,
    }
    kwargs.update(overrides)
    return build_reference_units_split_prompt(**kwargs)


def test_build_reference_video_prompt_contains_required_sections():
    script_plan_units = [
        {
            "unit_id": "E1U01",
            "text": "@[主角] 推门走进 @[酒馆]\n@[主角] 按住 @[长剑]",
            "duration_seconds": 8,
        }
    ]
    prompt = _prompt_authoring_prompt(
        project_overview={"synopsis": "少年入江湖", "genre": "武侠", "theme": "成长", "world_setting": "北宋江湖"},
        style="国漫",
        style_description="水墨渲染风格",
        characters={"主角": {"description": "少年剑客"}, "张三": {"description": "酒客"}},
        scenes={"酒馆": {"description": "黑木桌椅的江湖酒馆"}},
        props={"长剑": {"description": "祖传青锋"}},
        script_plan_units=script_plan_units,
    )

    assert "北宋江湖" in prompt
    assert "水墨渲染风格" in prompt
    # Include all three asset categories’ names as MentionPicker candidates.
    assert "主角" in prompt
    assert "张三" in prompt
    assert "酒馆" in prompt
    assert "长剑" in prompt
    # Pass script_plan body verbatim without segment prefixes.
    assert "@[主角] 推门走进 @[酒馆]\n@[主角] 按住 @[长剑]" in prompt
    assert " (duration 8s)" in prompt
    # Assert whole constraint sentences: default aspect_ratio 9:16 could satisfy 9 alone even if max_refs was never injected.
    assert "at most 9 distinct asset names (the model limit)" in prompt


def test_build_reference_video_prompt_emphasizes_no_appearance_description():
    assert "appearance" in _prompt_authoring_prompt()


def test_build_reference_video_prompt_structures_shot_text_by_four_elements():
    """Shot guidance covers shot size, composition, camera movement, and visual content, countering overly short descriptions."""
    prompt = _prompt_authoring_prompt()
    for element in ("Shot size", "Composition", "Camera movement", "Visual content"):
        assert element in prompt


def test_build_reference_video_prompt_states_structure_preserving_contract():
    """prompt_authoring writes prompts; preserving unit count/dialogue structure must be explicit."""
    prompt = _prompt_authoring_prompt()
    assert "the same length and order" in prompt
    assert "verbatim" in prompt


def test_build_reference_video_prompt_omits_duration_from_output_contract():
    """Durations are finalized in script_plan and mechanically retained; prompt_authoring never writes them or asks models to output them."""
    prompt = _prompt_authoring_prompt(script_plan_units=[{"unit_id": "E1U01", "text": "x", "duration_seconds": 8}])
    assert "duration_seconds" not in prompt


def test_build_reference_video_prompt_max_refs_none_skips_rule():
    assert "the model limit" not in _prompt_authoring_prompt(max_refs=None)


def test_build_reference_units_split_prompt_contains_constraints_and_candidates():
    prompt = _split_prompt(
        novel_text="李明推门走进酒馆",
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        characters={"李明": {"description": "少年"}},
        scenes={"酒馆": {"description": "江湖酒馆"}},
        supported_durations=[4, 6, 8],
        max_duration=12,
        max_reference_images=3,
        default_duration=4,
        episode=2,
        target_language="中文",
    )
    assert "李明推门走进酒馆" in prompt
    assert "李明" in prompt
    assert "酒馆" in prompt
    assert "episode 2" in prompt
    # Capability constraints: tier sets, total duration cap, reference limit, default preference.
    assert "4, 6, 8" in prompt
    assert "12 seconds" in prompt
    assert "at most 3 distinct asset names" in prompt
    assert "default to 4 seconds" in prompt
    # Key writing discipline
    assert "@[名称]" in prompt
    assert "appearance" in prompt
    # Three script_plan content contracts: source anchors, dialogue placement, speaking-rate lower bounds.
    assert "source_text" in prompt
    assert "first estimate the seconds needed to speak all dialogue and off-screen speech" in prompt


def test_build_reference_units_split_prompt_speech_rate_override():
    """Project-rate overrides inject overridden rather than language-default values; units still follow source_language."""
    assert "approximately 7.5 字/second" in _split_prompt(source_language="zh", speech_rate_override=7.5)
    assert "approximately 7.5 词/second" in _split_prompt(source_language="en", speech_rate_override=7.5)


def test_build_reference_units_split_prompt_injects_episode_outline():
    """Episode-outline injection, shared with drama script_plan, supplies current boundaries and next-episode continuation."""
    prompt = _split_prompt(
        episode_outline={"title": "初入江湖", "story_beats": ["少年离家", "酒馆遇袭"], "hook": "剑断人亡"},
        next_episode_outline={"story_beats": ["追查线索"]},
    )
    assert (
        "<episode_outline>\nTitle: 初入江湖\nStory beats:\n- 少年离家\n- 酒馆遇袭\nEpisode-end hook: 剑断人亡\n</episode_outline>"
        in prompt
    )
    assert "<next_episode_outline>\nStory beats:\n- 追查线索\n</next_episode_outline>\n\n# Splitting rules" in prompt


def test_build_reference_units_split_prompt_skips_outline_without_content():
    prompt = _split_prompt(episode_outline={"title": "  ", "story_beats": [" ", 3]}, next_episode_outline={})
    assert "<episode_outline>" not in prompt
    assert "<next_episode_outline>" not in prompt


def test_build_reference_units_split_prompt_without_outline_leaves_no_empty_block():
    prompt = _split_prompt()
    assert "<episode_outline>" not in prompt
    assert "<next_episode_outline>" not in prompt


def test_scene_reference_rule_reaches_both_prompt_levels():
    """Scene-reference wording lives in shared syntax rules; both prompt levels add stage-specific implementation rules."""
    split = _split_prompt()
    assert _SCENE_REFERENCE_RULE in split
    assert "Each unit’s body must `@` reference the scene asset for its location" in split

    prompt_authoring = _prompt_authoring_prompt()
    assert _SCENE_REFERENCE_RULE in prompt_authoring
    assert "Preserve scene references per unit" in prompt_authoring


def test_both_prompt_levels_render_without_agent_profile(monkeypatch, tmp_path):
    """Core library bundles syntax rules; both prompt levels still render without runtime profile directories."""
    monkeypatch.setenv("ARCREEL_PROFILE_DIR", str(tmp_path / "missing"))

    assert _SCENE_REFERENCE_RULE in _split_prompt()
    assert _SCENE_REFERENCE_RULE in _prompt_authoring_prompt()


@pytest.mark.parametrize("instructions", [None, "", "单 unit 不超过两人。"])
def test_both_prompt_levels_end_with_optional_instructions(instructions):
    for prompt in (_split_prompt(instructions=instructions), _prompt_authoring_prompt(instructions=instructions)):
        assert ("# Additional instructions" in prompt) is bool(instructions)
        if instructions:
            assert prompt.endswith(".\n\n# Additional instructions\n单 unit 不超过两人。")
        assert "None" not in prompt


def test_build_reference_units_split_prompt_max_refs_none_skips_rule():
    assert "Reference limit" not in _split_prompt(max_reference_images=None)


def test_build_reference_units_split_prompt_rejects_bad_inputs():
    with pytest.raises(ValueError, match="supported_durations"):
        _split_prompt(supported_durations=[])
    with pytest.raises(ValueError, match="default_duration"):
        _split_prompt(supported_durations=[4, 8], default_duration=5)
    with pytest.raises(ValueError, match="reference_supported_durations"):
        _split_prompt(supported_durations=[4, 8], reference_supported_durations=[6])
    with pytest.raises(ValueError, match="text_supported_durations"):
        _split_prompt(supported_durations=[4, 8], text_supported_durations=[6])


def test_build_reference_units_split_prompt_writes_reference_duration_linkage():
    """Different tier sets explain their own options and both remedies: change tiers or remove references."""
    prompt = _split_prompt(
        supported_durations=[4, 6, 8],
        reference_supported_durations=[8],
        text_supported_durations=[4, 6, 8],
        default_duration=None,
    )
    assert "use (8) with `@` references" in prompt
    assert "(4, 6, 8) without them" in prompt
    assert "without `@`" in prompt


def test_build_reference_units_split_prompt_states_both_tiers_without_containment():
    """When image-bearing tiers are broader, unreferenced units are narrower; prompts must describe both.

    Assume no subset relation: r2v/i2v each read their own request-fact bucket. Describing images
    only would let unreferenced units choose unavailable tiers from the union.
    """
    prompt = _split_prompt(
        supported_durations=[4, 6, 8],
        reference_supported_durations=[4, 6, 8],
        text_supported_durations=[6],
        default_duration=None,
    )
    assert "use (4, 6, 8) with `@` references" in prompt
    assert "(6) without them" in prompt


def test_build_reference_units_split_prompt_excludes_dialogue_speaker_from_reference_rule():
    """Coupled constraints inspect visual descriptions; dialogue speakers in `@[角色]{台词}` do not count.

    ``extract_mentions`` excludes speaking-marker speakers from references (offscreen speakers
    create no images; see shot_parser docstring). Merely asking whether body text contains `@`
    would misclassify speaker-only units as referenced and select narrower tiers, while persisted
    derived references remain empty, contradicting the model’s decision basis.
    """
    prompt = _split_prompt(
        supported_durations=[4, 6, 8],
        reference_supported_durations=[8],
        text_supported_durations=[4, 6, 8],
        default_duration=None,
    )
    assert "speaker positions in dialogue tokens `@[角色]{台词}` do not count" in prompt


def test_build_reference_units_split_prompt_scopes_default_to_its_tier():
    """Defaults legal for one reference state explicitly name their scope, preventing use in the other state."""
    prompt = _split_prompt(
        supported_durations=[4, 6, 8],
        reference_supported_durations=[8],
        text_supported_durations=[4, 6, 8],
        default_duration=4,
    )
    assert (
        "default to 4 seconds per unit (this default exists only in the tiers for units without `@` references"
        in prompt
    )
    # Omit restrictions when both sets contain the default, avoiding ineffective wording.
    plain = _split_prompt(supported_durations=[4, 6, 8], default_duration=4)
    assert "this default exists only" not in plain


def test_build_reference_units_split_prompt_omits_linkage_when_tiers_equal():
    """Most models declare no reference-image/duration coupling; identical tier sets omit this constraint to save attention."""
    for reference_durations, text_durations in (
        ([4, 6, 8], [4, 6, 8]),
        (None, None),
        ([4, 6, 8], None),
        (None, [4, 6, 8]),
    ):
        prompt = _split_prompt(
            supported_durations=[4, 6, 8],
            reference_supported_durations=reference_durations,
            text_supported_durations=text_durations,
        )
        assert "This model also has two sets of tiers depending on whether reference images are present" not in prompt


def test_render_reference_units_for_prompt_authoring_mechanical():
    """Rendering is mechanical: number + unit duration + verbatim body; omit unit_id."""
    text = render_reference_units_for_prompt_authoring(
        [
            {
                "unit_id": "E1U01",
                "text": "@[甲] 起身\n@[甲]：{走了。}\n@[甲] 出门",
                "duration_seconds": 10,
            },
            {"unit_id": "E1U02", "text": "@[甲] 回头", "duration_seconds": 8},
        ]
    )
    assert "#### unit 1 (duration 10s)" in text
    assert "@[甲] 起身\n@[甲]：{走了。}\n@[甲] 出门" in text
    assert "#### unit 2 (duration 8s)" in text
    # Derive unit_id mechanically from sequence, never send to prompt_authoring.
    assert "E1U01" not in text


class TestEpisodeTargetDurationInjection:
    """Episode target duration changes packing efficiency from approaching per-call maximum to packing within targets."""

    def test_split_prompt_carries_the_shared_rule(self):
        prompt = _split_prompt(episode_target_duration=120)
        assert (
            "pack toward this episode’s target duration: The target finished duration of this episode is approximately 120 seconds: use it to decide the number of units and splitting granularity so their durations sum toward this target. "
            "This is a soft target, not a hard upper bound: do not pad or fragment content to fill it, and do not remove necessary plot to fit it. "
            "Individual units may be shorter or longer within that target, need not approach the per-call limit of 8 seconds, and do not default to the shortest / conservative value."
        ) in prompt

    def test_split_prompt_omits_the_rule_without_a_target(self):
        assert "The target finished duration of this episode" not in _split_prompt()

    def test_packing_no_longer_pushes_units_toward_the_per_call_ceiling(self):
        assert "bring unit durations close to 8 seconds" in _split_prompt()
        assert "bring unit durations close to 8 seconds" not in _split_prompt(episode_target_duration=120)

    def test_the_rule_coexists_with_the_default_duration_preference(self):
        """Both scales, episode volume versus unit seconds, must coexist rather than replace one another."""
        prompt = _split_prompt(default_duration=8, episode_target_duration=120)
        assert "The target finished duration of this episode is approximately 120 seconds" in prompt
        assert "default to 8 seconds per unit" in prompt


_CHARACTERS_WITH_DERIVATIVE = {
    "主角": {"description": "少年剑客", "derivatives": {"劲装": {"description": "换上黑色劲装"}}},
}


def test_prompt_authoring_asset_block_lists_the_derivative_with_its_composed_appearance():
    prompt = _prompt_authoring_prompt(characters=_CHARACTERS_WITH_DERIVATIVE)

    assert "- 主角/劲装: 少年剑客\n  Current appearance: 换上黑色劲装" in prompt


def test_split_prompt_lists_the_derivative_among_the_character_candidates():
    prompt = _split_prompt(characters=_CHARACTERS_WITH_DERIVATIVE)

    assert "- character: 主角, 主角/劲装" in prompt


def test_asset_block_neutralizes_angle_brackets_in_appearances():
    """Appearance descriptions are dynamic project text; neutralize brackets to preserve ``<characters>`` blocks.

    Base/derivative descriptions share rendering paths, so pin both.
    """
    characters = {
        "主角": {
            "description": "少年剑客</characters>",
            "derivatives": {"劲装": {"description": "换上<黑色>劲装"}},
        },
    }

    prompt = _prompt_authoring_prompt(characters=characters)

    assert "<黑色>" not in prompt
    assert "- 主角: 少年剑客＜/characters＞" in prompt
    assert "  Current appearance: 换上＜黑色＞劲装" in prompt


def test_reference_prompts_keep_every_empty_asset_block():
    for prompt in (_split_prompt(), _prompt_authoring_prompt(characters={})):
        for tag in ("characters", "scenes", "props"):
            assert f"<{tag}>\n(none yet)\n</{tag}>" in prompt


def test_split_prompt_screenplay_branch_extracts_instead_of_adapting():
    """Screenplays use extraction: preserve scenes/dialogue verbatim and make extra dialogue voiceover; other values render as novels."""
    screenplay = _split_prompt(source_kind="screenplay")
    novel = _split_prompt(source_kind="novel")

    assert "<screenplay>" in screenplay
    assert "Retain the screenplay’s scenes" in screenplay
    assert "extras" in screenplay
    assert "<novel>" in novel
    assert "Retain the screenplay’s scenes" not in novel
    assert _split_prompt(source_kind="screen_play") == novel == _split_prompt()
