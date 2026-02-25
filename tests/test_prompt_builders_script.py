from lib.prompt_builders_script import (
    _format_character_names,
    _format_clue_names,
    build_drama_prompt,
    build_narration_prompt,
)


class TestPromptBuildersScript:
    def test_formatters_emit_bullet_lists(self):
        assert _format_character_names({"A": {}, "B": {}}) == "- A\n- B"
        assert _format_clue_names({"玉佩": {}, "祠堂": {}}) == "- 玉佩\n- 祠堂"

    def test_build_narration_prompt_contains_constraints_and_inputs(self):
        prompt = build_narration_prompt(
            project_overview={
                "synopsis": "一段悬疑故事",
                "genre": "悬疑",
                "theme": "真相",
                "world_setting": "古代县城",
            },
            style="古风",
            style_description="misty, cinematic",
            characters={"姜月茴": {}, "沈砚": {}},
            clues={"玉佩": {}, "药包": {}},
            segments_md="E1S01 | 文本",
        )

        assert "所有输出内容必须使用中文" in prompt
        assert "<segments>" in prompt
        assert "姜月茴" in prompt
        assert "玉佩" in prompt
        assert "E1S01 | 文本" in prompt

    def test_build_drama_prompt_mentions_16_9_and_scene_fields(self):
        prompt = build_drama_prompt(
            project_overview={
                "synopsis": "动作戏",
                "genre": "动作",
                "theme": "成长",
                "world_setting": "近未来",
            },
            style="赛博",
            style_description="high contrast",
            characters={"林": {}},
            clues={"芯片": {}},
            scenes_md="E1S01 | 追逐",
        )

        assert "16:9 横屏构图" in prompt
        assert "characters_in_scene" in prompt
        assert "clues_in_scene" in prompt
        assert "E1S01 | 追逐" in prompt
