from lib.prompt_builders_script import (
    _format_character_names,
    _format_clue_names,
    build_drama_prompt,
    build_narration_prompt,
)


class TestPromptBuildersScript:
    def test_formatters_emit_bullet_lists(self):
        assert _format_character_names({"A": {}, "B": {}}) == "- A\n- B"
        assert _format_clue_names({"jade-pendant": {}, "ancestral-hall": {}}) == "- jade-pendant\n- ancestral-hall"

    def test_build_narration_prompt_contains_dynamic_durations(self):
        prompt = build_narration_prompt(
            project_overview={"synopsis": "Story", "genre": "Mystery", "theme": "Truth", "world_setting": "Ancient"},
            style="historical",
            style_description="cinematic",
            characters={"Character A": {}},
            clues={"jade-pendant": {}},
            segments_md="E1S01 | text",
            supported_durations=[4, 6, 8],
            default_duration=4,
            aspect_ratio="9:16",
        )
        assert "4, 6, 8" in prompt
        assert "default is 4 seconds" in prompt

    def test_build_narration_prompt_auto_duration(self):
        prompt = build_narration_prompt(
            project_overview={"synopsis": "Story", "genre": "Mystery", "theme": "Truth", "world_setting": "Ancient"},
            style="historical",
            style_description="cinematic",
            characters={"Character A": {}},
            clues={"jade-pendant": {}},
            segments_md="E1S01 | text",
            supported_durations=[5, 10],
            default_duration=None,
            aspect_ratio="9:16",
        )
        assert "5, 10" in prompt
        assert "content pacing" in prompt

    def test_build_drama_prompt_uses_dynamic_aspect_ratio(self):
        prompt = build_drama_prompt(
            project_overview={"synopsis": "Action", "genre": "Action", "theme": "Growth", "world_setting": "Near Future"},
            style="cyber",
            style_description="high contrast",
            characters={"Lin": {}},
            clues={"chip": {}},
            scenes_md="E1S01 | chase",
            supported_durations=[4, 8, 12],
            default_duration=8,
            aspect_ratio="9:16",
        )
        # portrait mode should not contain "landscape composition"
        assert "landscape composition" not in prompt
        assert "portrait composition" in prompt

    def test_build_drama_prompt_landscape(self):
        prompt = build_drama_prompt(
            project_overview={"synopsis": "Action", "genre": "Action", "theme": "Growth", "world_setting": "Near Future"},
            style="cyber",
            style_description="high contrast",
            characters={"Lin": {}},
            clues={"chip": {}},
            scenes_md="E1S01 | chase",
            supported_durations=[4, 6, 8],
            default_duration=8,
            aspect_ratio="16:9",
        )
        assert "landscape composition" in prompt
