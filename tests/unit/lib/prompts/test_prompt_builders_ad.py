"""Ad/short-film script-generation prompt builders."""

import pytest

from lib.prompts import prompt_builders_ad as ad_prompts
from lib.prompts.prompt_builders_ad import _shot_duration_constraint, build_ad_prompt, nearest_ad_tier
from lib.speech.speech_rate import speech_rate_units_per_second


def _build(**overrides):
    kwargs = {
        "project_overview": {"synopsis": "速干杯带货短片", "genre": "带货", "theme": "便捷", "world_setting": ""},
        "style": "实拍",
        "style_description": "真实质感",
        "characters": {"小美": {"description": "都市白领"}},
        "scenes": {"厨房": {"description": "明亮现代厨房"}},
        "props": {},
        "products": {
            "速干杯": {
                "description": "30 秒速干的随行杯",
                "brand": "DryGo",
                "selling_points": ["30 秒速干", "一键开合"],
            }
        },
        "brief": "突出速干卖点，面向通勤人群",
        "target_duration": 30,
        "generation_mode": "storyboard",
        "supported_durations": [4, 6, 8],
    }
    kwargs.update(overrides)
    return build_ad_prompt(**kwargs)


class TestTierSelection:
    @pytest.mark.parametrize(
        ("target", "expected_tier"),
        [
            (20, 15),  # Closer to 15.
            (25, 30),
            (45, 30),  # Equidistant 30/60: choose 30, closer to default recommendation 30.
            (75, 60),  # Equidistant 60/90: choose 60, closer to 30.
            (100, 90),
            (8, 15),
        ],
    )
    def test_nearest_tier(self, target, expected_tier):
        assert nearest_ad_tier(target) == expected_tier

    def test_invalid_target_duration_rejected(self):
        with pytest.raises(ValueError, match=r"target_duration 必须为正整数秒"):
            _build(target_duration=0)


class TestProductsInjection:
    def test_products_block_carries_brand_description_selling_points(self):
        prompt = _build()
        assert "速干杯" in prompt
        assert "DryGo" in prompt
        assert "30 秒速干的随行杯" in prompt
        assert "30 秒速干" in prompt
        assert "一键开合" in prompt

    def test_products_in_shot_candidates_listed(self):
        prompt = _build(products={"速干杯": {"description": "x"}, "保温壶": {"description": "y"}})
        assert "速干杯" in prompt
        assert "保温壶" in prompt

    def test_asset_candidates_listed(self):
        prompt = _build()
        assert "小美" in prompt
        assert "厨房" in prompt

    @pytest.mark.parametrize("products", [{}, {"速干杯": {"description": "x"}}])
    def test_every_empty_asset_block_is_kept(self, products):
        prompt = _build(characters={}, scenes={}, props={}, products=products)
        for tag in ("characters", "scenes", "props"):
            assert f"<{tag}>\n(none yet)\n</{tag}>" in prompt

    def test_brief_injected(self):
        prompt = _build()
        assert "突出速干卖点，面向通勤人群" in prompt

    def test_voiceover_rate_injected_from_single_source(self):
        """Spoken-text duration rates inject from lib.speech.speech_rate, never hardcoded; commerce/general short-film branches share this source."""
        # Default target_language 中文 is not a language code, so use default zh rate and 字 units.
        default_rate = speech_rate_units_per_second(None)
        for prompt in (_build(), _build(products={})):
            assert f"approximately {default_rate:g} 字/second" in prompt
        # Rates/units follow target_language (en counts words), proving injection rather than hardcoding.
        en_rate = speech_rate_units_per_second("en")
        assert en_rate != default_rate
        assert f"approximately {en_rate:g} 词/second" in _build(target_language="en")

    def test_project_override_wins_over_language_default(self):
        """Effective project-rate overrides inject overridden values, while units still follow language."""
        assert "approximately 7.5 字/second" in _build(speech_rate_override=7.5)
        assert "approximately 7.5 词/second" in _build(target_language="en", speech_rate_override=7.5)


class TestGenericFallback:
    """Empty products automatically routes to general short-film prompts without commerce frameworks or explicit submode switches."""

    def test_no_products_drops_selling_framework(self):
        generic_prompt = _build(products={})
        selling_prompt = _build(products={"测试商品Z": {"description": "独特商品描述"}})
        assert generic_prompt != selling_prompt
        assert "测试商品Z" not in generic_prompt
        assert "测试商品Z" in selling_prompt

    def test_no_products_keeps_target_duration(self):
        prompt = _build(products={}, target_duration=45)
        assert "45" in prompt


class TestDurationConstraint:
    def test_storyboard_path_enumerates_supported_durations(self):
        constraint = _shot_duration_constraint("storyboard", [17, 23])
        assert "17" in constraint
        assert "23" in constraint

    def test_storyboard_path_requires_supported_durations(self):
        with pytest.raises(ValueError, match=r"storyboard 路径必须提供 supported_durations"):
            _build(generation_mode="storyboard", supported_durations=None)

    def test_reference_path_cannot_use_storyboard_prompt(self):
        with pytest.raises(ValueError, match="build_ad_reference_prompt"):
            _shot_duration_constraint("reference_video", None)

    def test_reference_prompt_injects_structural_duration_range(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(ad_prompts, "REFERENCE_UNIT_DURATION_RANGE", (7, 91))

        prompt = ad_prompts.build_ad_reference_prompt(
            project_overview={},
            style="实拍",
            style_description="真实质感",
            characters={},
            scenes={},
            props={},
            products={},
            brief="通用短片",
            target_duration=30,
        )

        assert '"duration_seconds": 7' in prompt
        assert "an integer from 7 to 91" in prompt


class TestEpisodeConstraint:
    def test_episode_number_is_injected(self):
        prompt = _build(episode=37)
        assert "E37S" in prompt


def _build_reference(**overrides):
    kwargs = {
        "project_overview": {"synopsis": "速干杯带货短片", "genre": "带货", "theme": "便捷"},
        "style": "实拍",
        "style_description": "真实质感",
        "characters": {"小美": {"description": "都市白领"}},
        "scenes": {},
        "props": {},
        "products": {"速干杯": {"description": "30 秒速干的随行杯"}},
        "brief": "突出速干卖点",
        "target_duration": 30,
    }
    kwargs.update(overrides)
    return ad_prompts.build_ad_reference_prompt(**kwargs)


class TestCandidateNames:
    @pytest.mark.parametrize("build", [_build, _build_reference])
    def test_both_routes_list_registered_derivatives(self, build):
        characters = {"小美": {"description": "都市白领", "derivatives": {"运动装": {"description": "换上运动装"}}}}
        prompt = build(characters=characters)
        assert "小美, 小美/运动装" in prompt


class TestPacingTiers:
    @pytest.mark.parametrize("build", [_build, _build_reference])
    @pytest.mark.parametrize("tier", [15, 30, 60, 90])
    def test_tier_target_renders_its_table_without_adaptation_note(self, build, tier):
        prompt = build(target_duration=tier)
        assert "General rules (all tiers)" in prompt
        assert f"\n{tier}-second tier (" in prompt
        assert [t for t in (15, 30, 60, 90) if f"\n{t}-second tier (" in prompt] == [tier]
        assert "outside the approved tiers" not in prompt

    @pytest.mark.parametrize("build", [_build, _build_reference])
    def test_off_tier_target_adapts_nearest_table(self, build):
        prompt = build(target_duration=45)
        assert "\n30-second tier (" in prompt
        assert (
            "The target total duration of 45 seconds is outside the approved tiers. Proportionally adapt the nearest tier’s allocation template (30 seconds) to 45 seconds:"
            in prompt
        )

    def test_generic_reference_prompt_drops_pacing_tables(self):
        prompt = _build_reference(products={}, target_duration=45)
        assert "Organize content as opening, development, climax, and conclusion." in prompt
        assert "General rules (all tiers)" not in prompt
        assert "(no products; create a general short film)" in prompt


class TestSharedWording:
    def test_reference_route_uses_shared_writing_syntax(self):
        prompt = _build_reference()
        assert "Consecutive units at the same location must **repeat the same scene reference in every unit**" in prompt

    @pytest.mark.parametrize("build", [_build, _build_reference])
    def test_both_routes_use_converged_action_guide(self, build):
        prompt = build()
        assert (
            "these words can cause reference-video generation to be mistaken for video editing or extension." in prompt
        )
        assert "the task is queued and billed" not in prompt

    @pytest.mark.parametrize("products", [{}, {"速干杯": {"description": "x"}}])
    @pytest.mark.parametrize("instructions", [None, "", "全片用第一人称口播"])
    def test_storyboard_instructions_are_an_optional_section(self, products, instructions):
        prompt = _build(products=products, instructions=instructions)
        if instructions:
            assert prompt.endswith("\n\n# Additional instructions\n全片用第一人称口播")
            assert "\n\n\n# Additional instructions" not in prompt
            assert prompt.count("# Additional instructions") == 1
        else:
            assert "# Additional instructions" not in prompt
            assert not prompt.endswith("\n")
        assert "None" not in prompt

    @pytest.mark.parametrize("instructions", [None, "", "节奏再快一点"])
    def test_reference_instructions_are_an_optional_section(self, instructions):
        prompt = _build_reference(instructions=instructions)
        if instructions:
            assert prompt.endswith("\n\n# Additional instructions\n节奏再快一点")
            assert "\n\n\n# Additional instructions" not in prompt
        else:
            assert "# Additional instructions" not in prompt
        assert "None" not in prompt
