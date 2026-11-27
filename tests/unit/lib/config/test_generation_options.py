from copy import deepcopy

import pytest
from pydantic import ValidationError

from lib.config.generation_options import GenerationOptions, apply_generation_options


def test_request_options_override_only_the_execution_snapshot():
    project = {
        "video_backend": "old/model",
        "video_provider_r2v": "old/reference",
        "model_settings": {"old/model": {"resolution": "720p"}},
        "aspect_ratio": "16:9",
    }
    before = deepcopy(project)
    result = apply_generation_options(
        project,
        {
            "generation_options": {
                "backend": "new/model",
                "resolution": "1080p",
                "aspect_ratio": "9:16",
                "generate_audio": False,
            }
        },
        "video",
    )
    assert project == before
    assert result["video_backend"] == result["video_provider_r2v"] == result["video_provider_i2v"] == "new/model"
    assert result["model_settings"]["new/model"] == {"resolution": "1080p"}
    assert result["model_settings"]["old/model"] == {"resolution": "720p"}
    assert result["aspect_ratio"] == "9:16"
    assert result["video_generate_audio"] is False


def test_image_options_override_both_buckets_without_changing_video():
    project = {"video_backend": "video/model"}
    result = apply_generation_options(project, {"generation_options": {"backend": "image/model"}}, "image")
    assert (
        result["default_image_backend"] == result["image_provider_t2i"] == result["image_provider_i2i"] == "image/model"
    )
    assert result["video_backend"] == "video/model"
    assert apply_generation_options(project, None, "image") is project


@pytest.mark.parametrize(
    "options",
    [
        {"backend": "missing-model"},
        {"backend": "provider/model with spaces"},
        {"duration_seconds": 0},
        {"duration_seconds": 301},
        {"unknown": True},
    ],
)
def test_reject_invalid_request_options(options):
    with pytest.raises(ValidationError):
        GenerationOptions.model_validate(options)
