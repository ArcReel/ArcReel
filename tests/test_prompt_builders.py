from lib.prompt_builders import (
    append_product_fidelity_tail,
    append_video_negative_tail,
    build_character_prompt,
    build_prop_prompt,
    build_scene_prompt,
    build_storyboard_suffix,
)


class TestCharacterPrompt:
    def test_includes_name_description_and_quad_layout(self):
        prompt = build_character_prompt(
            "姜月茴",
            "黑发，冷静神态。",
            style="古风",
            style_description="Cinematic, low-key lighting",
        )
        assert "姜月茴" in prompt
        assert "黑发，冷静神态。" in prompt
        # 四视图 16:9 布局（issue #353）
        assert "16:9" in prompt
        assert "四格" in prompt
        assert "胸像特写" in prompt or "胸部以上" in prompt
        assert "正面" in prompt and "侧面" in prompt and "背面" in prompt
        # 风格前缀
        assert "古风" in prompt
        assert "Cinematic, low-key lighting" in prompt
        # 反向提示尾部
        assert "画面避免" in prompt

    def test_no_negative_prompt_field_returned(self):
        # build_character_prompt 仅返回字符串；反向提示已 inline 到末尾
        prompt = build_character_prompt("张三", "短发青年")
        assert isinstance(prompt, str)
        assert "画面避免" in prompt
        assert "水印" in prompt


class TestScenePromptAndPropPrompt:
    def test_prop_three_views(self):
        prompt = build_prop_prompt("玉佩", "古朴温润")
        assert "玉佩" in prompt
        assert "古朴温润" in prompt
        assert "三视图" in prompt or "三个视图" in prompt
        assert "画面避免" in prompt

    def test_scene_main_detail_layout(self):
        prompt = build_scene_prompt("祠堂", "昏暗古朴")
        assert "祠堂" in prompt
        assert "昏暗古朴" in prompt
        assert "主画面" in prompt
        assert "画面避免" in prompt


class TestStoryboardSuffix:
    def test_by_aspect_ratio(self):
        assert build_storyboard_suffix(aspect_ratio="9:16") == "竖屏构图。"
        assert build_storyboard_suffix(aspect_ratio="16:9") == "横屏构图。"
        # 向后兼容：不传 aspect_ratio 时默认按 narration → 竖屏
        assert build_storyboard_suffix() == "竖屏构图。"


class TestVideoNegativeTail:
    def test_appends_when_missing(self):
        result = append_video_negative_tail("林清缓缓抬头")
        assert "林清缓缓抬头" in result
        assert "BGM" in result

    def test_idempotent(self):
        once = append_video_negative_tail("林清缓缓抬头")
        twice = append_video_negative_tail(once)
        assert once == twice

    def test_handles_empty_input(self):
        result = append_video_negative_tail("")
        assert "BGM" in result

    def test_handles_whitespace_only_input(self):
        # 纯空白等同空：避免拼出前导空行 + 尾词的怪异输出
        for blank in ("   ", "\n\n", "\t \n"):
            result = append_video_negative_tail(blank)
            assert result.startswith("禁止出现"), f"input={blank!r} → {result!r}"


class TestProductFidelityTail:
    def test_appends_instruction_with_product_names(self):
        result = append_product_fidelity_tail("手持保温杯特写", ["保温杯"])
        assert result.startswith("手持保温杯特写")
        assert "「保温杯」" in result
        assert "参考图" in result

    def test_idempotent(self):
        once = append_product_fidelity_tail("手持保温杯特写", ["保温杯"])
        twice = append_product_fidelity_tail(once, ["保温杯"])
        assert once == twice

    def test_no_products_returns_prompt_unchanged(self):
        assert append_product_fidelity_tail("氛围镜头", []) == "氛围镜头"
        # None 等同为空：早退返回原 prompt，不抛 TypeError
        assert append_product_fidelity_tail("氛围镜头", None) == "氛围镜头"

    def test_multiple_products_all_named(self):
        result = append_product_fidelity_tail("双产品同框", ["保温杯", "杯刷"])
        assert "「保温杯」" in result
        assert "「杯刷」" in result

    def test_single_string_treated_as_single_product(self):
        """误传单字符串按单产品名处理，而非逐字符迭代拼出畸形指令。"""
        result = append_product_fidelity_tail("产品特写", "保温杯")
        assert "「保温杯」" in result
        assert "「保」" not in result
