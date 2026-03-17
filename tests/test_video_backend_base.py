from pathlib import Path

from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
    VideoGenerationResult,
)


class TestVideoCapability:
    def test_enum_values(self):
        assert VideoCapability.TEXT_TO_VIDEO == "text_to_video"
        assert VideoCapability.IMAGE_TO_VIDEO == "image_to_video"
        assert VideoCapability.GENERATE_AUDIO == "generate_audio"
        assert VideoCapability.NEGATIVE_PROMPT == "negative_prompt"
        assert VideoCapability.VIDEO_EXTEND == "video_extend"
        assert VideoCapability.SEED_CONTROL == "seed_control"
        assert VideoCapability.FLEX_TIER == "flex_tier"

    def test_enum_is_str(self):
        assert isinstance(VideoCapability.TEXT_TO_VIDEO, str)


class TestVideoGenerationRequest:
    def test_defaults(self):
        req = VideoGenerationRequest(prompt="test", output_path=Path("/tmp/out.mp4"))
        assert req.aspect_ratio == "9:16"
        assert req.duration_seconds == 5
        assert req.resolution == "1080p"
        assert req.start_image is None
        assert req.generate_audio is True
        assert req.negative_prompt is None
        assert req.service_tier == "default"
        assert req.seed is None

    def test_all_fields(self):
        req = VideoGenerationRequest(
            prompt="action",
            output_path=Path("/tmp/out.mp4"),
            aspect_ratio="16:9",
            duration_seconds=8,
            resolution="720p",
            start_image=Path("/tmp/frame.png"),
            generate_audio=False,
            negative_prompt="no music",
            service_tier="flex",
            seed=42,
        )
        assert req.duration_seconds == 8
        assert req.seed == 42
        assert req.service_tier == "flex"


class TestVideoGenerationResult:
    def test_required_fields(self):
        result = VideoGenerationResult(
            video_path=Path("/tmp/out.mp4"),
            provider="gemini",
            model="veo-3.1-generate-001",
            duration_seconds=8,
        )
        assert result.video_uri is None
        assert result.seed is None
        assert result.usage_tokens is None
        assert result.task_id is None

    def test_optional_fields(self):
        result = VideoGenerationResult(
            video_path=Path("/tmp/out.mp4"),
            provider="seedance",
            model="doubao-seedance-1-5-pro-251215",
            duration_seconds=5,
            video_uri="https://cdn.example.com/video.mp4",
            seed=58944,
            usage_tokens=246840,
            task_id="cgt-20250101",
        )
        assert result.usage_tokens == 246840
        assert result.task_id == "cgt-20250101"
