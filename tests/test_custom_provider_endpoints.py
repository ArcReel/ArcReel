"""ENDPOINT_REGISTRY 完整性与工具函数单测。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    endpoint_to_media_type,
    get_endpoint_spec,
    infer_endpoint,
    list_endpoints_by_media_type,
)


class TestRegistry:
    def test_six_endpoints(self):
        assert set(ENDPOINT_REGISTRY.keys()) == {
            "openai-chat",
            "gemini-generate",
            "openai-images",
            "gemini-image",
            "openai-video",
            "newapi-video",
        }

    def test_each_spec_has_required_fields(self):
        for key, spec in ENDPOINT_REGISTRY.items():
            assert spec.key == key
            assert spec.media_type in {"text", "image", "video"}
            assert spec.family in {"openai", "google", "newapi"}
            assert spec.display_name_key.startswith("endpoint_")
            assert callable(spec.build_backend)

    def test_media_type_groups(self):
        text_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "text"}
        image_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "image"}
        video_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "video"}
        assert text_keys == {"openai-chat", "gemini-generate"}
        assert image_keys == {"openai-images", "gemini-image"}
        assert video_keys == {"openai-video", "newapi-video"}


class TestHelpers:
    def test_get_endpoint_spec(self):
        spec = get_endpoint_spec("openai-chat")
        assert spec.media_type == "text"

    def test_get_endpoint_spec_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown endpoint"):
            get_endpoint_spec("anthropic-messages")

    def test_endpoint_to_media_type(self):
        assert endpoint_to_media_type("newapi-video") == "video"
        assert endpoint_to_media_type("gemini-image") == "image"

    def test_endpoint_to_media_type_unknown_raises(self):
        with pytest.raises(ValueError):
            endpoint_to_media_type("nope")

    def test_list_endpoints_by_media_type(self):
        text = list_endpoints_by_media_type("text")
        assert {s.key for s in text} == {"openai-chat", "gemini-generate"}


class TestInferEndpoint:
    @pytest.mark.parametrize(
        "model_id,discovery_format,expected",
        [
            ("gpt-4o", "openai", "openai-chat"),
            ("gemini-2.5-flash", "google", "gemini-generate"),
            ("gemini-2.5-flash", "openai", "openai-chat"),  # 中转站常见
            ("claude-sonnet-4.5", "openai", "openai-chat"),
            ("dall-e-3", "openai", "openai-images"),
            ("gpt-image-1", "openai", "openai-images"),
            ("imagen-4", "google", "gemini-image"),
            ("imagen-4", "openai", "openai-images"),
            ("flux-pro", "openai", "openai-images"),
            ("sora-2", "openai", "openai-video"),
            ("kling-v2", "openai", "newapi-video"),
            ("veo-3", "openai", "newapi-video"),
            ("veo-3", "google", "newapi-video"),  # google 直连无视频端点 → 兜底 newapi
            ("seedance-1.0", "openai", "newapi-video"),
            ("hailuo-02", "openai", "newapi-video"),
            ("seedream-3.0", "openai", "openai-images"),
            ("jimeng-3.0", "openai", "openai-images"),
            ("jimeng-video-3.0", "openai", "newapi-video"),
            ("jimengvideo-3.0", "openai", "newapi-video"),
            ("SORA-2", "openai", "openai-video"),
        ],
    )
    def test_infer(self, model_id, discovery_format, expected):
        assert infer_endpoint(model_id, discovery_format) == expected
