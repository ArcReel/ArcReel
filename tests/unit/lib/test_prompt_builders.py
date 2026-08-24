from lib.prompt_builders import (
    append_image_negative_tail,
    append_product_fidelity_tail,
    append_video_negative_tail,
    build_character_prompt,
    build_product_prompt,
    build_prop_prompt,
    build_scene_prompt,
)


class TestCharacterPrompt:
    def test_includes_supplied_character_details(self):
        prompt = build_character_prompt(
            "姜月茴",
            "黑发，冷静神态。",
            style="古风",
            style_description="Cinematic, low-key lighting",
        )
        assert "姜月茴" in prompt
        assert "黑发，冷静神态。" in prompt
        assert "古风" in prompt
        assert "Cinematic, low-key lighting" in prompt


class TestScenePromptAndPropPrompt:
    def test_prop_includes_supplied_details(self):
        prompt = build_prop_prompt("玉佩", "古朴温润")
        assert "玉佩" in prompt
        assert "古朴温润" in prompt

    def test_scene_includes_supplied_details(self):
        prompt = build_scene_prompt("祠堂", "昏暗古朴")
        assert "祠堂" in prompt
        assert "昏暗古朴" in prompt

    def test_empty_prop_guard_does_not_add_a_blank_paragraph(self):
        assert "\n\n\n" not in build_prop_prompt("玉佩", "古朴温润")


class TestFigureExclusion:
    """展示环境或物件的图种排除人物；画面主体本身是人物的图种不排除。"""

    # 断言完整片段而非「人物」二字：正文里的普通描述也可能出现该词，按关键词断言会误判。
    _EXCLUSION = "画面避免：出镜人物"

    def test_environment_and_object_sheets_exclude_people(self):
        assert self._EXCLUSION in build_scene_prompt("祠堂", "昏暗古朴")
        assert self._EXCLUSION in build_prop_prompt("玉佩", "古朴温润")
        assert self._EXCLUSION in build_product_prompt("护手霜", "白色管装，哑光质感")

    def test_character_and_storyboard_keep_people(self):
        # 四类资产的反向提示词各自定义而非共用，避免把人物排除项误加到主体为人物的图种上。
        assert self._EXCLUSION not in build_character_prompt("张三", "短发青年")
        assert self._EXCLUSION not in append_image_negative_tail("林清坐在窗边木桌前")


class TestVideoNegativeTail:
    def test_appends_when_missing(self):
        result = append_video_negative_tail("林清缓缓抬头")
        assert result.startswith("林清缓缓抬头")
        assert result != "林清缓缓抬头"

    def test_idempotent(self):
        once = append_video_negative_tail("林清缓缓抬头")
        twice = append_video_negative_tail(once)
        assert once == twice

    def test_handles_empty_input(self):
        assert append_video_negative_tail("")

    def test_handles_whitespace_only_input(self):
        expected = append_video_negative_tail("")
        for blank in ("   ", "\n\n", "\t \n"):
            assert append_video_negative_tail(blank) == expected


class TestImageNegativeTail:
    def test_appends_when_missing(self):
        result = append_image_negative_tail("林清坐在窗边木桌前")
        assert result.startswith("林清坐在窗边木桌前")
        assert result != "林清坐在窗边木桌前"

    def test_idempotent(self):
        once = append_image_negative_tail("林清坐在窗边木桌前")
        twice = append_image_negative_tail(once)
        assert once == twice

    def test_handles_empty_and_whitespace_input(self):
        expected = append_image_negative_tail("")
        for blank in ("", "   ", "\n\n", "\t \n"):
            assert append_image_negative_tail(blank) == expected


class TestProductFidelityTail:
    def test_appends_instruction_with_product_names(self):
        result = append_product_fidelity_tail("手持保温杯特写", ["保温杯"])
        assert result.startswith("手持保温杯特写")
        assert "「保温杯」" in result

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
