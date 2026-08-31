"""Tests for get_aspect_ratio."""

from server.services import generation_tasks


class TestGetAspectRatio:
    def test_reads_top_level_aspect_ratio(self):
        project = {"aspect_ratio": "16:9", "content_mode": "narration"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "16:9"
        assert generation_tasks.get_aspect_ratio(project, "storyboards") == "16:9"

    def test_fallback_to_content_mode_narration(self):
        project = {"content_mode": "narration"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "9:16"

    def test_fallback_to_content_mode_drama(self):
        project = {"content_mode": "drama"}
        assert generation_tasks.get_aspect_ratio(project, "videos") == "16:9"

    def test_characters_always_16_9(self):
        project = {"aspect_ratio": "9:16"}
        assert generation_tasks.get_aspect_ratio(project, "characters") == "16:9"

    def test_design_assets_always_16_9(self):
        project = {"aspect_ratio": "9:16"}
        assert generation_tasks.get_aspect_ratio(project, "scenes") == "16:9"
        assert generation_tasks.get_aspect_ratio(project, "props") == "16:9"
        assert generation_tasks.get_aspect_ratio(project, "products") == "16:9"
