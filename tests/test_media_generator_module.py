from pathlib import Path

import pytest

from lib.media_generator import MediaGenerator


class _FakeGemini:
    IMAGE_MODEL = "img-model"
    VIDEO_MODEL = "video-model"

    def __init__(self):
        self.image_calls = []
        self.video_calls = []

    def generate_image(self, **kwargs):
        self.image_calls.append(kwargs)

    async def generate_image_async(self, **kwargs):
        self.image_calls.append(kwargs)

    def generate_video(self, **kwargs):
        self.video_calls.append(kwargs)
        return None, "video-ref", "video-uri"

    async def generate_video_async(self, **kwargs):
        self.video_calls.append(kwargs)
        return None, "video-ref", "video-uri"


class _FakeVersions:
    def __init__(self):
        self.ensure_calls = []
        self.add_calls = []

    def ensure_current_tracked(self, **kwargs):
        self.ensure_calls.append(kwargs)

    def add_version(self, **kwargs):
        self.add_calls.append(kwargs)
        return len(self.add_calls)

    def get_versions(self, resource_type, resource_id):
        return {
            "current_version": len(self.add_calls),
            "versions": [{"created_at": "2026-01-01T00:00:00Z"}] * max(1, len(self.add_calls)),
        }


class _FakeUsage:
    def __init__(self):
        self.started = []
        self.finished = []

    async def start_call(self, **kwargs):
        self.started.append(kwargs)
        return len(self.started)

    async def finish_call(self, **kwargs):
        self.finished.append(kwargs)


def _build_generator(tmp_path: Path) -> MediaGenerator:
    gen = object.__new__(MediaGenerator)
    gen.project_path = tmp_path / "projects" / "demo"
    gen.project_path.mkdir(parents=True, exist_ok=True)
    gen.project_name = "demo"
    gen._rate_limiter = None
    gen.image_backend = "aistudio"
    gen._gemini_video_backend_type = "aistudio"
    gen._video_backend = None
    gen._user_id = "default"
    gen._video_generate_audio = None
    gen._gemini_api_key = None
    gen._gemini_base_url = None
    gen._gemini_image_model = None
    gen._gemini_video_model = None
    fake = _FakeGemini()
    gen._gemini_image = fake
    gen._gemini_video = fake
    gen.versions = _FakeVersions()
    gen.usage_tracker = _FakeUsage()
    return gen


class TestMediaGenerator:
    def test_get_output_path_and_invalid_type(self, tmp_path):
        gen = _build_generator(tmp_path)
        assert gen._get_output_path("storyboards", "E1S01").name == "scene_E1S01.png"
        assert gen._get_output_path("videos", "E1S01").name == "scene_E1S01.mp4"
        assert gen._get_output_path("characters", "Alice").name == "Alice.png"
        with pytest.raises(ValueError):
            gen._get_output_path("bad", "x")

    def test_generate_image_success_and_failure(self, tmp_path):
        gen = _build_generator(tmp_path)
        output_path, version = gen.generate_image(
            prompt="p",
            resource_type="storyboards",
            resource_id="E1S01",
            aspect_ratio="9:16",
        )

        assert output_path.name == "scene_E1S01.png"
        assert version == 1
        assert gen.usage_tracker.started[0]["call_type"] == "image"
        assert gen.usage_tracker.finished[0]["status"] == "success"

        def _raise(**kwargs):
            raise RuntimeError("boom")

        gen._gemini_image.generate_image = _raise
        with pytest.raises(RuntimeError):
            gen.generate_image(prompt="p", resource_type="characters", resource_id="A")

        assert any(item["status"] == "failed" for item in gen.usage_tracker.finished)

    @pytest.mark.asyncio
    async def test_generate_video_sync_and_async(self, tmp_path):
        gen = _build_generator(tmp_path)

        video_path, version, video_ref, video_uri = gen.generate_video(
            prompt="p",
            resource_type="videos",
            resource_id="E1S01",
            duration_seconds="bad",
        )
        assert video_path.name == "scene_E1S01.mp4"
        assert version == 1
        assert video_ref == "video-ref"
        assert video_uri == "video-uri"

        video_path2, version2, _, _ = await gen.generate_video_async(
            prompt="p",
            resource_type="videos",
            resource_id="E1S02",
            duration_seconds="6",
        )
        assert video_path2.name == "scene_E1S02.mp4"
        assert version2 == 2
        assert gen.usage_tracker.started[-1]["call_type"] == "video"
