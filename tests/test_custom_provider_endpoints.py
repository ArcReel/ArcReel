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
            "dashscope-image",
            "dashscope-async-video",
            "openai-tts",
        }

    def test_each_spec_has_required_fields(self):
        for key, spec in ENDPOINT_REGISTRY.items():
            assert spec.key == key
            assert spec.media_type in {"text", "image", "video", "audio"}
            assert spec.family in {"openai", "google", "newapi", "v2", "ark", "vidu", "dashscope"}
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
        """v2/ark/vidu/dashscope 不在 endpoint 维度声明上限，由 resolver 调 backend 纯 caps 函数读取。"""
        for key in ("v2-video-generations", "ark-seedance", "vidu-video", "dashscope-async-video"):
            assert ENDPOINT_REGISTRY[key].video_max_reference_images is None
        # 既有显式 int 保留，行为零变化
        assert ENDPOINT_REGISTRY["openai-video"].video_max_reference_images == 1
        assert ENDPOINT_REGISTRY["newapi-video"].video_max_reference_images == 0

    def test_video_caps_declaration_bindings(self):
        """每个 video endpoint 选对了上限来源：None-cap 的绑 caps_fn、显式 int 的不绑。

        全注册表 XOR/非负不变式由 endpoints.py 的 module-load `_validate_video_caps_declarations()`
        在 import 期保证（违反则本文件根本 import 不进来），故此处只断言「具体哪个 endpoint 选了哪条
        路径」——这是 XOR 校验抓不到的（换机制仍满足 XOR），是真正的回归护栏。"""
        # None-cap 的 video endpoint 必须绑定纯 caps 函数
        for key in ("v2-video-generations", "ark-seedance", "vidu-video", "dashscope-async-video"):
            assert ENDPOINT_REGISTRY[key].video_caps_for_model is not None
        # 显式 int 的 video endpoint 不应再绑 caps 函数
        for key in ("openai-video", "newapi-video"):
            assert ENDPOINT_REGISTRY[key].video_caps_for_model is None

    def test_dashscope_caps_fn_reads_per_model_limit_without_client(self):
        """dashscope-async-video 的 caps_fn 是纯函数：按 model_id 返回真实参考图上限
        （happyhorse-r2v=9 / wan2.7-r2v=5），resolver 据此解析而无需构造 backend / api_key。"""
        caps_fn = ENDPOINT_REGISTRY["dashscope-async-video"].video_caps_for_model
        assert caps_fn is not None
        assert caps_fn("happyhorse-1.0-r2v").max_reference_images == 9
        assert caps_fn("wan2.7-r2v").max_reference_images == 5

    def test_negative_int_cap_rejected_at_validation(self, monkeypatch: pytest.MonkeyPatch):
        """import 期不变式拒绝负数 int cap：下游 references[:-1] 会误丢最后一张而非裁成 0 张。"""
        import dataclasses

        from lib.custom_provider.endpoints import _validate_video_caps_declarations

        bad = dataclasses.replace(
            ENDPOINT_REGISTRY["openai-video"], video_max_reference_images=-1, video_caps_for_model=None
        )
        monkeypatch.setitem(ENDPOINT_REGISTRY, "openai-video", bad)
        with pytest.raises(ValueError, match="negative video_max_reference_images"):
            _validate_video_caps_declarations()

    def test_non_callable_caps_fn_rejected_at_validation(self, monkeypatch: pytest.MonkeyPatch):
        """import 期不变式拒绝非 callable 的 video_caps_for_model：否则误填字符串/整数会放行到
        request 期才在 resolver `caps_fn(model_id)` 处炸，违背 fail-fast 初衷。"""
        import dataclasses

        from lib.custom_provider.endpoints import _validate_video_caps_declarations

        # 非 callable 真值（字符串）冒充 caps_fn；同时清掉 int cap 避免先撞 XOR 校验
        bad = dataclasses.replace(
            ENDPOINT_REGISTRY["ark-seedance"],
            video_max_reference_images=None,
            video_caps_for_model="not-callable",
        )
        monkeypatch.setitem(ENDPOINT_REGISTRY, "ark-seedance", bad)
        with pytest.raises(ValueError, match="non-callable video_caps_for_model"):
            _validate_video_caps_declarations()

    def test_audio_endpoint_spec(self):
        spec = ENDPOINT_REGISTRY["openai-tts"]
        assert spec.media_type == "audio"
        assert spec.family == "openai"
        assert spec.request_path_template == "/v1/audio/speech"
        # 非 video/image endpoint：不声明 video caps / image capabilities
        assert spec.video_max_reference_images is None
        assert spec.video_caps_for_model is None
        assert spec.image_capabilities is None

    def test_media_type_groups(self):
        text_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "text"}
        image_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "image"}
        video_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "video"}
        audio_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "audio"}
        assert audio_keys == {"openai-tts"}
        assert text_keys == {"openai-chat", "gemini-generate"}
        assert image_keys == {
            "openai-images",
            "openai-images-generations",
            "openai-images-edits",
            "gemini-image",
            "dashscope-image",
        }
        assert video_keys == {
            "openai-video",
            "newapi-video",
            "v2-video-generations",
            "ark-seedance",
            "vidu-video",
            "dashscope-async-video",
        }


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
            # ── audio（TTS）识别：precedence 在 text 默认之前 ──
            ("tts-1", "openai", "openai-tts"),
            ("tts-1-hd", "openai", "openai-tts"),
            ("gpt-4o-mini-tts", "openai", "openai-tts"),
            ("vidu-tts", "openai", "openai-tts"),  # tts 尾缀优先于 text 默认
            ("speech-1.5", "openai", "openai-tts"),  # Fish Audio 风格 id
            ("cosyvoice-v2", "openai", "openai-tts"),
            # audio endpoint 仅 OpenAI 兼容一条，google 发现格式同样归 openai-tts
            ("tts-1", "google", "openai-tts"),
            # 不应误伤：含 audio 字样的 chat 模型、ASR（语音转文字）、视频/图像家族仍按原分支
            ("gpt-4o-audio-preview", "openai", "openai-chat"),
            ("whisper-1", "openai", "openai-chat"),
            ("speech-to-text-1", "openai", "openai-chat"),
            ("transcribe-speech-1", "openai", "openai-chat"),
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


def test_image_endpoint_registry_entries():
    from lib.custom_provider.endpoints import ENDPOINT_KEYS_BY_MEDIA_TYPE

    image_keys = set(ENDPOINT_KEYS_BY_MEDIA_TYPE["image"])
    assert image_keys == {
        "openai-images",
        "openai-images-generations",
        "openai-images-edits",
        "gemini-image",
        "dashscope-image",
    }


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
