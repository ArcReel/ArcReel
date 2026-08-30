"""Tests for generation_tasks_helpers."""

import asyncio
import threading

import pytest

from lib.prompt_builders import append_image_negative_tail
from lib.prompt_utils import image_prompt_to_yaml
from server.services import generation_tasks


class TestGenerationTasks:
    async def test_formal_finalizer_without_task_id_defers_cancellation(self):
        started = threading.Event()
        release = threading.Event()

        def _finalize() -> str:
            started.set()
            assert release.wait(timeout=5)
            return "committed"

        task = asyncio.create_task(generation_tasks.run_formal_task_finalizer(_finalize, task_id=None))
        assert await asyncio.to_thread(started.wait, 5)
        task.cancel()
        release.set()

        assert await task == "committed"

    def test_helper_functions(self, tmp_path):
        from lib.storyboard_sequence import get_storyboard_items

        mode_items = get_storyboard_items({"content_mode": "drama", "scenes": []})
        assert mode_items[1] == "scene_id"

        prompt = generation_tasks._normalize_storyboard_prompt("text", "Anime", "cinematic")
        assert prompt == append_image_negative_tail("Style: Anime\nVisual style: cinematic\n\ntext")
        assert generation_tasks._normalize_storyboard_prompt(prompt, "Anime", "cinematic") == prompt

        structured_input = {
            "scene": "林清坐在窗边",
            "composition": {"shot_type": "Close-up", "lighting": "暖光", "ambiance": "薄雾"},
        }
        structured = generation_tasks._normalize_storyboard_prompt(structured_input, "Anime", "cinematic")
        assert structured == append_image_negative_tail(
            f"Visual style: cinematic\n\n{image_prompt_to_yaml(structured_input, 'Anime')}"
        )

        with pytest.raises(ValueError):
            generation_tasks._normalize_storyboard_prompt({"scene": ""}, "Anime")

        with pytest.raises(ValueError):
            generation_tasks._normalize_storyboard_prompt("", "Anime")

        with pytest.raises(ValueError):
            generation_tasks._normalize_storyboard_prompt("   ", "Anime")
