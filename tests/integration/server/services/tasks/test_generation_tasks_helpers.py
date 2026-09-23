"""Tests for generation_tasks_helpers."""

import pytest

from lib.prompts.prompt_utils import image_prompt_to_yaml
from server.services.tasks import generation_tasks


class TestGenerationTasks:
    def test_helper_functions(self, tmp_path):
        from lib.script.storyboard_sequence import get_storyboard_items

        mode_items = get_storyboard_items({"content_mode": "drama", "scenes": []})
        assert mode_items[1] == "scene_id"

        prompt = generation_tasks._normalize_storyboard_prompt("text", "Anime", "cinematic")
        assert prompt == "Style: Anime\nVisual style: cinematic\n\ntext\n\nAvoid: 水印、多余文字、Logo"
        assert generation_tasks._normalize_storyboard_prompt(prompt, "Anime", "cinematic") == prompt

        structured_input = {
            "scene": "林清坐在窗边",
            "composition": {"shot_type": "Close-up", "lighting": "暖光", "ambiance": "薄雾"},
        }
        structured = generation_tasks._normalize_storyboard_prompt(structured_input, "Anime", "cinematic")
        assert structured == image_prompt_to_yaml(structured_input, "Anime", style_description="cinematic").rstrip()
        assert structured.startswith("Style: Anime\nVisual style: cinematic\nScene:")
        assert structured.endswith("\nAvoid: 水印、多余文字、Logo")

        with pytest.raises(ValueError, match=r"image_prompt\.scene must be a non-empty string"):
            generation_tasks._normalize_storyboard_prompt({"scene": ""}, "Anime")

        with pytest.raises(ValueError, match=r"image_prompt must not be empty"):
            generation_tasks._normalize_storyboard_prompt("", "Anime")

        with pytest.raises(ValueError, match=r"image_prompt must not be empty"):
            generation_tasks._normalize_storyboard_prompt("   ", "Anime")
