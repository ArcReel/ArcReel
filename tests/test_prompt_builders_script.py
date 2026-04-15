from lib.prompt_builders_script import (
    _format_asset_names,
    _format_character_names,
    build_drama_prompt,
    build_narration_prompt,
)


class TestPromptBuildersScript:
    def test_formatters_emit_bullet_lists(self):
        assert _format_character_names({"A": {}, "B": {}}) == "- A\n- B"
        assert _format_asset_names({"玉佩": {}, "祠堂": {}}) == "- 玉佩\n- 祠堂"

    def test_build_narration_prompt_contains_dynamic_durations(self):
        prompt = build_narration_prompt(
            project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            style="古风",
            style_description="cinematic",
            characters={"姜月茴": {}},
            scenes={"祠堂": {}},
            props={"玉佩": {}},
            segments_md="E1S01 | 文本",
            supported_durations=[4, 6, 8],
            default_duration=4,
            aspect_ratio="9:16",
        )
        assert "4, 6, 8" in prompt
        assert "默认使用 4 秒" in prompt
        assert "祠堂" in prompt
        assert "玉佩" in prompt

    def test_build_narration_prompt_auto_duration(self):
        prompt = build_narration_prompt(
            project_overview={"synopsis": "故事", "genre": "悬疑", "theme": "真相", "world_setting": "古代"},
            style="古风",
            style_description="cinematic",
            characters={"姜月茴": {}},
            scenes={},
            props={"玉佩": {}},
            segments_md="E1S01 | 文本",
            supported_durations=[5, 10],
            default_duration=None,
            aspect_ratio="9:16",
        )
        assert "5, 10" in prompt
        assert "根据内容节奏自行决定" in prompt

    def test_build_drama_prompt_uses_dynamic_aspect_ratio(self):
        prompt = build_drama_prompt(
            project_overview={"synopsis": "动作", "genre": "动作", "theme": "成长", "world_setting": "近未来"},
            style="赛博",
            style_description="high contrast",
            characters={"林": {}},
            scenes={"天台": {}},
            props={"芯片": {}},
            scenes_md="E1S01 | 追逐",
            supported_durations=[4, 8, 12],
            default_duration=8,
            aspect_ratio="9:16",
        )
        # 传入竖屏时不应出现 "16:9 横屏构图"
        assert "16:9 横屏构图" not in prompt
        assert "竖屏构图" in prompt

    def test_build_drama_prompt_landscape(self):
        prompt = build_drama_prompt(
            project_overview={"synopsis": "动作", "genre": "动作", "theme": "成长", "world_setting": "近未来"},
            style="赛博",
            style_description="high contrast",
            characters={"林": {}},
            scenes={"天台": {}},
            props={"芯片": {}},
            scenes_md="E1S01 | 追逐",
            supported_durations=[4, 6, 8],
            default_duration=8,
            aspect_ratio="16:9",
        )
        assert "横屏构图" in prompt
