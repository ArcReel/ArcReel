"""广告/短片剧本生成 prompt 构建器测试。"""

import pytest

from lib import prompt_builders_ad as ad_prompts
from lib.prompt_builders_ad import _shot_duration_constraint, build_ad_prompt, nearest_ad_tier
from lib.speech_rate import speech_rate_units_per_second


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
            (20, 15),  # 距 15 更近
            (25, 30),
            (45, 30),  # 等距 30/60，取更接近默认推荐档 30 的一侧
            (75, 60),  # 等距 60/90，取更接近 30 的 60
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
            assert f"<{tag}>\n（暂无）\n</{tag}>" in prompt

    def test_brief_injected(self):
        prompt = _build()
        assert "突出速干卖点，面向通勤人群" in prompt

    def test_voiceover_rate_injected_from_single_source(self):
        """口播字数→时长折算语速由 lib.speech_rate 注入，不写死数字；带货与通用短片两分支同源。"""
        # 默认 target_language「中文」不在语言代码表内 → 回退默认语速（zh 口径，量词「字」）
        default_rate = speech_rate_units_per_second(None)
        for prompt in (_build(), _build(products={})):
            assert f"约 {default_rate:g} 字/秒" in prompt
        # 语速与量词随 target_language 切换（en 计词），证明是注入而非写死
        en_rate = speech_rate_units_per_second("en")
        assert en_rate != default_rate
        assert f"约 {en_rate:g} 词/秒" in _build(target_language="en")

    def test_project_override_wins_over_language_default(self):
        """项目级语速覆盖生效时注入覆盖值；量词仍随语言。"""
        assert "约 7.5 字/秒" in _build(speech_rate_override=7.5)
        assert "约 7.5 词/秒" in _build(target_language="en", speech_rate_override=7.5)


class TestGenericFallback:
    """products 为空 → 通用短片 prompt 自动分流（无带货框架，不设显式子模式开关）。"""

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
        assert "取 7-91 的整数" in prompt


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
        assert "通用规则（适用于全部档位）" in prompt
        assert f"\n{tier} 秒档（" in prompt
        assert [t for t in (15, 30, 60, 90) if f"\n{t} 秒档（" in prompt] == [tier]
        assert "不在审定档位内" not in prompt

    @pytest.mark.parametrize("build", [_build, _build_reference])
    def test_off_tier_target_adapts_nearest_table(self, build):
        prompt = build(target_duration=45)
        assert "\n30 秒档（" in prompt
        assert "目标总时长 45 秒不在审定档位内，按距离最小的档位 30 秒的配比模板按比例适配到 45 秒：" in prompt

    def test_generic_reference_prompt_drops_pacing_tables(self):
        prompt = _build_reference(products={}, target_duration=45)
        assert "按开场、发展、高潮、收束组织内容。" in prompt
        assert "通用规则（适用于全部档位）" not in prompt
        assert "（无商品，按通用短片创作）" in prompt


class TestSharedWording:
    def test_reference_route_uses_shared_writing_syntax(self):
        prompt = _build_reference()
        assert "同一地点的连续单元**逐条重复引用**同一个场景资产" in prompt

    @pytest.mark.parametrize("build", [_build, _build_reference])
    def test_both_routes_use_converged_action_guide(self, build):
        prompt = build()
        assert "带这些词会把参考生视频误判成视频编辑或视频延长。" in prompt
        assert "任务已排队、已计费" not in prompt

    @pytest.mark.parametrize("products", [{}, {"速干杯": {"description": "x"}}])
    @pytest.mark.parametrize("instructions", [None, "", "全片用第一人称口播"])
    def test_storyboard_instructions_are_an_optional_section(self, products, instructions):
        prompt = _build(products=products, instructions=instructions)
        if instructions:
            assert prompt.endswith("\n\n# 附加指令\n全片用第一人称口播")
            assert "\n\n\n# 附加指令" not in prompt
            assert prompt.count("# 附加指令") == 1
        else:
            assert "# 附加指令" not in prompt
            assert not prompt.endswith("\n")
        assert "None" not in prompt

    @pytest.mark.parametrize("instructions", [None, "", "节奏再快一点"])
    def test_reference_instructions_are_an_optional_section(self, instructions):
        prompt = _build_reference(instructions=instructions)
        if instructions:
            assert prompt.endswith("\n\n# 附加指令\n节奏再快一点")
            assert "\n\n\n# 附加指令" not in prompt
        else:
            assert "# 附加指令" not in prompt
        assert "None" not in prompt
