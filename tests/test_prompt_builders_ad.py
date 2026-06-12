"""广告/短片模式剧本生成 prompt 构建器测试。

配比表数字为审定真相源，断言逐字匹配（不重新计算、不近似）。
"""

import pytest

from lib.prompt_builders_ad import build_ad_prompt


def _build(**overrides):
    kwargs = dict(
        project_overview={"synopsis": "速干杯带货短片", "genre": "带货", "theme": "便捷", "world_setting": ""},
        style="实拍",
        style_description="真实质感",
        characters={"小美": {"description": "都市白领"}},
        scenes={"厨房": {"description": "明亮现代厨房"}},
        props={},
        products={
            "速干杯": {
                "description": "30 秒速干的随行杯",
                "brand": "DryGo",
                "selling_points": ["30 秒速干", "一键开合"],
            }
        },
        brief="突出速干卖点，面向通勤人群",
        target_duration=30,
        generation_mode="storyboard",
        supported_durations=[4, 6, 8],
    )
    kwargs.update(overrides)
    return build_ad_prompt(**kwargs)


class TestTierTableVerbatim:
    def test_30s_tier_table_rows_verbatim(self):
        """30 秒档（默认推荐档）八段全保，表格行逐字出现。"""
        prompt = _build(target_duration=30)
        for row in (
            "| hook | 3 | 0-3 | 1 |",
            "| pain_point | 4 | 3-7 | 1-2 |",
            "| product_reveal | 3 | 7-10 | 1 |",
            "| selling_point（1-2 个） | 6 | 10-16 | 2 |",
            "| demo | 6 | 16-22 | 2 |",
            "| trust（一句话社证） | 3 | 22-25 | 1 |",
            "| price_promo | 2 | 25-27 | 1 |",
            "| cta | 3 | 27-30 | 1 |",
        ):
            assert row in prompt
        assert "8-10 镜头" in prompt

    def test_15s_tier_collapses_to_five_sections(self):
        """15 秒档只保五段：pain_point 并入 hook、trust 砍掉、price_promo 并入 cta。"""
        prompt = _build(target_duration=15)
        for row in (
            "| hook（兼任 pain_point） | 3 | 0-3 | 1 |",
            "| product_reveal | 2 | 3-5 | 1 |",
            "| selling_point（1 个核心卖点） | 3 | 5-8 | 1 |",
            "| demo | 4 | 8-12 | 1-2 |",
            "| cta（可带一句促销词兼任 price_promo） | 3 | 12-15 | 1 |",
        ):
            assert row in prompt
        assert "只保五段" in prompt
        # 折叠后不应再出现独立的 trust / pain_point / price_promo 表行
        assert "| trust" not in prompt
        assert "| pain_point" not in prompt
        assert "| price_promo" not in prompt

    def test_60s_tier_table_rows_verbatim(self):
        prompt = _build(target_duration=60)
        for row in (
            "| hook | 3 | 0-3 | 1 |",
            "| pain_point（1-2 个具体情境） | 7 | 3-10 | 2 |",
            "| product_reveal | 5 | 10-15 | 1-2 |",
            "| selling_point（2-3 个，每个 4-6s 一镜） | 12 | 15-27 | 3 |",
            "| demo（多角度/前后对比） | 15 | 27-42 | 3-4 |",
            "| trust（评价+销量/资质） | 8 | 42-50 | 2 |",
            "| price_promo（原价锚定→到手价→限时） | 5 | 50-55 | 1-2 |",
            "| cta（行动指令+重申核心利益） | 5 | 55-60 | 1 |",
        ):
            assert row in prompt
        assert "13-16 镜头" in prompt

    def test_90s_tier_table_rows_verbatim(self):
        prompt = _build(target_duration=90)
        for row in (
            "| hook（可用悬念/故事钩） | 4 | 0-4 | 1 |",
            "| pain_point（人物+冲突小叙事） | 10 | 4-14 | 2-3 |",
            "| product_reveal（转折点：遇见产品） | 6 | 14-20 | 1-2 |",
            "| selling_point（3 个，逐个展开） | 20 | 20-40 | 3-4 |",
            "| demo（多场景使用过程，核心块） | 24 | 40-64 | 4-6 |",
            "| trust（证言/检测报告/销量） | 12 | 64-76 | 2-3 |",
            "| price_promo（价格锚定+优惠拆解） | 8 | 76-84 | 2 |",
            "| cta（紧迫感收口） | 6 | 84-90 | 1 |",
        ):
            assert row in prompt
        assert "18-22 镜头" in prompt

    def test_general_rules_present_in_every_tier(self):
        for target in (15, 30, 60, 90):
            prompt = _build(target_duration=target)
            for rule in (
                "hook 与 cta 是绝对时长段（hook 2-4s、cta 3-6s）",
                "price_promo 永远紧贴 cta 构成「促单收尾块」",
                "产品也应在前 3 秒内入画（文字/局部/手持均可）",
                "单 section 超过 6 秒必须拆成多个镜头；全片平均 3-5 秒/镜，开头允许 2-3 秒快切",
                "30 秒档为默认推荐档；90 秒档用「小故事」组织而非平铺卖点",
                "中文口播按约 4 字/秒折算台词长度（15s≈60 字 / 30s≈120 字 / 60s≈240 字 / 90s≈360 字",
            ):
                assert rule in prompt


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
    def test_nearest_tier_with_proportional_adaptation_note(self, target, expected_tier):
        prompt = _build(target_duration=target)
        assert f"按最近档位 {expected_tier} 秒的配比模板" in prompt
        assert f"按比例适配到 {target} 秒" in prompt

    def test_exact_tier_has_no_adaptation_note(self):
        prompt = _build(target_duration=60)
        assert "按比例适配" not in prompt

    def test_invalid_target_duration_rejected(self):
        with pytest.raises(ValueError):
            _build(target_duration=0)


class TestProductsInjection:
    def test_products_block_carries_brand_description_selling_points(self):
        prompt = _build()
        assert "<products>" in prompt
        assert "### 速干杯" in prompt
        assert "品牌：DryGo" in prompt
        assert "描述：30 秒速干的随行杯" in prompt
        assert "- 30 秒速干" in prompt
        assert "- 一键开合" in prompt

    def test_products_in_shot_candidates_listed(self):
        prompt = _build(products={"速干杯": {"description": "x"}, "保温壶": {"description": "y"}})
        assert "候选 products：[速干杯, 保温壶]" in prompt

    def test_asset_candidates_listed(self):
        prompt = _build()
        assert "候选 characters：[小美]" in prompt
        assert "候选 scenes：[厨房]" in prompt
        assert "候选 props：[（无）]" in prompt

    def test_brief_injected(self):
        prompt = _build()
        assert "突出速干卖点，面向通勤人群" in prompt

    def test_voiceover_text_is_first_class_field(self):
        prompt = _build()
        assert "voiceover_text" in prompt
        assert "完整可照稿配音" in prompt

    def test_section_eight_values_guidance(self):
        prompt = _build()
        assert "hook → pain_point → product_reveal → selling_point → demo → trust → price_promo → cta" in prompt


class TestGenericFallback:
    """products 为空 → 通用短片 prompt 自动分流（无带货框架，不设显式子模式开关）。"""

    def test_no_products_drops_selling_framework(self):
        prompt = _build(products={})
        assert "带货八段框架" not in prompt
        assert "| hook" not in prompt
        assert "<products>" not in prompt
        assert "通用短片" in prompt

    def test_no_products_keeps_target_duration_and_voiceover(self):
        prompt = _build(products={}, target_duration=45)
        assert "45 秒" in prompt
        assert "voiceover_text" in prompt
        assert "products_in_shot" in prompt  # 引导一律填空数组


class TestDurationConstraint:
    def test_storyboard_path_enumerates_supported_durations(self):
        prompt = _build(generation_mode="storyboard", supported_durations=[4, 6, 8])
        assert "从 [4, 6, 8] 秒中选择" in prompt

    def test_storyboard_path_requires_supported_durations(self):
        with pytest.raises(ValueError):
            _build(generation_mode="storyboard", supported_durations=None)

    def test_reference_path_allows_free_integers_1_to_15(self):
        prompt = _build(generation_mode="reference_video", supported_durations=None)
        assert "1 到 15 秒间整数任选" in prompt
        assert "从 [" not in prompt


class TestEpisodeConstraint:
    def test_shot_id_format_pinned_to_episode_1(self):
        prompt = _build()
        assert "E1S{两位序号}" in prompt
        assert "E1S01" in prompt
