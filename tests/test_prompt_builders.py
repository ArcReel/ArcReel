from lib.prompt_builders import (
    build_character_prompt,
    build_prop_prompt,
    build_scene_prompt,
    build_storyboard_suffix,
    build_style_prompt,
)


class TestPromptBuilders:
    def test_build_character_prompt_includes_style_and_description(self):
        prompt = build_character_prompt(
            "姜月茴",
            "黑发，冷静神态。",
            style="古风",
            style_description="Cinematic, low-key lighting",
        )
        assert "Visual style: Cinematic, low-key lighting" in prompt
        assert "角色设计参考图，古风" in prompt
        assert "姜月茴" in prompt
        assert "黑发，冷静神态。" in prompt

    def test_build_prop_prompt_has_three_view_layout(self):
        prompt = build_prop_prompt("玉佩", "古朴温润", style="写实", style_description="")
        assert "玉佩" in prompt
        assert "古朴温润" in prompt
        assert "道具" in prompt
        assert "三个视图" in prompt

    def test_build_scene_prompt_has_main_detail_layout(self):
        prompt = build_scene_prompt("祠堂", "昏暗古朴", style="写实", style_description="")
        assert "祠堂" in prompt
        assert "昏暗古朴" in prompt
        assert "场景" in prompt
        assert "主画面占据四分之三" in prompt

    def test_build_storyboard_suffix_by_aspect_ratio(self):
        assert build_storyboard_suffix(aspect_ratio="9:16") == "竖屏构图。"
        assert build_storyboard_suffix(aspect_ratio="16:9") == "横屏构图。"
        # 向后兼容：不传 aspect_ratio 时默认竖屏
        assert build_storyboard_suffix() == "竖屏构图。"

    def test_build_style_prompt_combines_available_parts(self):
        project_data = {
            "style": "Anime",
            "style_description": "soft pastel, hand-drawn",
        }
        result = build_style_prompt(project_data)
        assert "Style: Anime" in result
        assert "Visual style: soft pastel, hand-drawn" in result

    def test_build_style_prompt_handles_empty_values(self):
        assert build_style_prompt({}) == ""
        assert build_style_prompt({"style": ""}) == ""
