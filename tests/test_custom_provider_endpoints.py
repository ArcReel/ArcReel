"""ENDPOINT_REGISTRY 完整性与工具函数单测。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    endpoint_spec_to_dict,
    endpoint_to_media_type,
    get_endpoint_spec,
    infer_endpoint,
    list_endpoints_by_media_type,
)


class TestRegistry:
    def test_endpoint_count(self):
        assert set(ENDPOINT_REGISTRY.keys()) == {
            "openai-chat",
            "gemini-generate",
            "openai-images",
            "openai-images-generations",
            "openai-images-edits",
            "gemini-image",
            "openai-video",
            "newapi-video",
            "v2-video-generations",
            "ark-seedance",
            "vidu-video",
        }

    def test_each_spec_has_required_fields(self):
        for key, spec in ENDPOINT_REGISTRY.items():
            assert spec.key == key
            assert spec.media_type in {"text", "image", "video"}
            assert spec.family in {"openai", "google", "newapi", "v2", "ark", "vidu"}
            assert spec.display_name_key.startswith("endpoint_")
            assert callable(spec.build_backend)
            assert spec.request_method == "POST"
            assert spec.request_path_template.startswith("/")

    def test_endpoint_spec_to_dict_drops_closure(self):
        spec = ENDPOINT_REGISTRY["openai-chat"]
        d = endpoint_spec_to_dict(spec)
        assert "build_backend" not in d
        assert d == {
            "key": "openai-chat",
            "media_type": "text",
            "family": "openai",
            "display_name_key": "endpoint_openai_chat_display",
            "request_method": "POST",
            "request_path_template": "/v1/chat/completions",
            "image_capabilities": None,
            # 未声明的 endpoint cap 序列化为 None（resolver fallthrough 到 backend caps）
            "video_max_reference_images": None,
        }

    def test_new_video_endpoints_have_unset_cap(self):
        """v2/ark/vidu 不在 endpoint 维度声明上限，由 resolver fallthrough 到 backend caps。"""
        for key in ("v2-video-generations", "ark-seedance", "vidu-video"):
            assert ENDPOINT_REGISTRY[key].video_max_reference_images is None
        # 既有显式 int 保留，行为零变化
        assert ENDPOINT_REGISTRY["openai-video"].video_max_reference_images == 1
        assert ENDPOINT_REGISTRY["newapi-video"].video_max_reference_images == 0

    def test_media_type_groups(self):
        text_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "text"}
        image_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "image"}
        video_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "video"}
        assert text_keys == {"openai-chat", "gemini-generate"}
        assert image_keys == {"openai-images", "openai-images-generations", "openai-images-edits", "gemini-image"}
        assert video_keys == {"openai-video", "newapi-video", "v2-video-generations", "ark-seedance", "vidu-video"}


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
            # ── content-first 纠偏（中转站普遍 discovery_format="openai" 却夹带原生 id）──
            ("gemini-2.5-flash", "openai", "gemini-generate"),  # 不再被错推到 openai-chat
            ("gemini-2.5-flash", "google", "gemini-generate"),
            ("imagen-4", "openai", "gemini-image"),  # imagen 一律 gemini-image
            ("imagen-4", "google", "gemini-image"),
            ("gemini-imagen-3", "openai", "gemini-image"),  # imagen 优先于 gemini 文本
            # gemini 原生图像模型也按内容纠偏到 gemini-image（不被错推到 openai-images）
            ("gemini-2.5-flash-image", "openai", "gemini-image"),
            ("gemini-2.5-flash-image", "google", "gemini-image"),
            ("gemini-2.0-flash-exp-image-generation", "openai", "gemini-image"),
            ("gemini-3-pro-image-preview", "openai", "gemini-image"),
            # ── 新视频分支路由 ──
            ("seedance-1.0", "openai", "ark-seedance"),
            ("doubao-seedance-2-0", "openai", "ark-seedance"),
            ("viduq3", "openai", "vidu-video"),
            ("viduq3-mix", "openai", "vidu-video"),
            ("viduq3-pro", "openai", "vidu-video"),
            ("viduq3-turbo", "openai", "vidu-video"),
            ("viduq3-i2v", "openai", "vidu-video"),
            ("proxy/viduq3-turbo", "openai", "vidu-video"),
            # ── 向后兼容（行为不变）──
            ("gpt-4o", "openai", "openai-chat"),
            ("claude-sonnet-4.5", "openai", "openai-chat"),
            ("dall-e-3", "openai", "openai-images"),
            ("gpt-image-1", "openai", "openai-images"),
            ("flux-pro", "openai", "openai-images"),
            ("sora-2", "openai", "openai-video"),
            ("SORA-2", "openai", "openai-video"),
            ("kling-v2", "openai", "openai-video"),
            ("veo-3", "openai", "openai-video"),
            ("veo-3", "google", "openai-video"),  # 非 seedance/viduq3 视频 → openai-video
            ("hailuo-02", "openai", "openai-video"),
            ("seedream-3.0", "openai", "openai-images"),
            ("jimeng-3.0", "openai", "openai-images"),
            ("jimeng-video-3.0", "openai", "openai-video"),
            ("jimengvideo-3.0", "openai", "openai-video"),
            # viduq1/viduq2 是 vidu 早期图像版本 → 维持 image 推断不变
            ("viduq1", "openai", "openai-images"),
            ("viduq1-classic", "openai", "openai-images"),
            ("my-proxy/viduq1", "openai", "openai-images"),
            ("viduq2", "openai", "openai-images"),
            ("viduq2-pro", "openai", "openai-images"),
            ("viduq2-turbo", "openai", "openai-images"),
            ("provider:viduq2-turbo", "openai", "openai-images"),
            ("vidu2", "openai", "openai-video"),
            ("vidu2.0", "openai", "openai-video"),
            ("provider:vidu2.0", "openai", "openai-video"),
            ("vidu-tts", "openai", "openai-chat"),
        ],
    )
    def test_infer(self, model_id, discovery_format, expected):
        assert infer_endpoint(model_id, discovery_format) == expected

    @pytest.mark.parametrize(
        "model_id,discovery_format",
        [
            ("seedance-1.0", "openai"),
            ("viduq3-turbo", "openai"),
            ("kling-v2", "openai"),
            ("some-v2-model", "openai"),
            ("gpt-4o", "openai"),
        ],
    )
    def test_v2_never_auto_inferred(self, model_id, discovery_format):
        """v2-video-generations 命名碎片化无法可靠识别，永不自动推断，留用户手选。"""
        assert infer_endpoint(model_id, discovery_format) != "v2-video-generations"


def test_image_endpoint_registry_has_four_entries():
    from lib.custom_provider.endpoints import ENDPOINT_KEYS_BY_MEDIA_TYPE

    image_keys = set(ENDPOINT_KEYS_BY_MEDIA_TYPE["image"])
    assert image_keys == {"openai-images", "openai-images-generations", "openai-images-edits", "gemini-image"}


def test_split_endpoints_have_single_capability():
    from lib.custom_provider.endpoints import endpoint_to_image_capabilities
    from lib.image_backends import ImageCapability

    assert endpoint_to_image_capabilities("openai-images-generations") == frozenset({ImageCapability.TEXT_TO_IMAGE})
    assert endpoint_to_image_capabilities("openai-images-edits") == frozenset({ImageCapability.IMAGE_TO_IMAGE})


def test_existing_image_endpoints_have_full_capabilities():
    """EndpointSpec 新增 image_capabilities 字段；已存在的 image entry 默认填两个能力。"""
    from lib.custom_provider.endpoints import (
        ENDPOINT_REGISTRY,
        endpoint_spec_to_dict,
        endpoint_to_image_capabilities,
    )
    from lib.image_backends import ImageCapability

    full = frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE})
    assert ENDPOINT_REGISTRY["openai-images"].image_capabilities == full
    assert ENDPOINT_REGISTRY["gemini-image"].image_capabilities == full
    assert ENDPOINT_REGISTRY["openai-chat"].image_capabilities is None
    assert endpoint_to_image_capabilities("openai-images") == full

    with pytest.raises(ValueError):
        endpoint_to_image_capabilities("openai-chat")

    # Verify endpoint_spec_to_dict serializes capabilities to sorted list[str]
    serialized = endpoint_spec_to_dict(ENDPOINT_REGISTRY["openai-images"])
    assert serialized["image_capabilities"] == ["image_to_image", "text_to_image"]
