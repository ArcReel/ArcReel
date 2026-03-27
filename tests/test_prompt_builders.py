from lib.prompt_builders import (
    build_character_prompt,
    build_clue_prompt,
    build_location_prompt,
    build_prop_prompt,
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

    def test_build_clue_prompt_dispatches_by_type(self):
        prop_prompt = build_clue_prompt("玉佩", "古朴", clue_type="prop", style="写实")
        location_prompt = build_clue_prompt("祠堂", "昏暗", clue_type="location", style="写实")

        assert prop_prompt == build_prop_prompt("玉佩", "古朴", "写实", "")
        assert location_prompt == build_location_prompt("祠堂", "昏暗", "写实", "")

    def test_build_storyboard_suffix(self):
        assert build_storyboard_suffix("narration") == "竖屏构图。"
        assert build_storyboard_suffix("drama") == ""

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
