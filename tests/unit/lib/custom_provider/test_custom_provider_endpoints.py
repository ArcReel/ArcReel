"""ENDPOINT_REGISTRY 完整性与工具函数单测。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    declarative_requests,
    declarative_requires_base_url,
    endpoint_spec_to_dict,
    endpoint_to_media_type,
    get_endpoint_spec,
    infer_endpoint,
    list_endpoints_by_media_type,
    validate_video_caps_declaration,
)


class TestMeteringBackend:
    """计价读 ``metering_backend`` 类上的计量声明：它必须就是 ``build_backend`` 包装的那个调用通道类。"""

    @pytest.mark.parametrize(
        "key",
        sorted(
            key
            for key, spec in ENDPOINT_REGISTRY.items()
            if spec.media_type in {"image", "video"} and spec.definition is None
        ),
    )
    def test_python_media_endpoints_declare_the_delegate_class(self, key):
        from types import SimpleNamespace

        spec = ENDPOINT_REGISTRY[key]
        provider = SimpleNamespace(
            id=7,
            provider_id="custom-7",
            api_key="sk",
            base_url="https://relay.example.com/v1",
            discovery_format="openai",
        )

        backend = spec.build_backend(provider, "some-model")

        assert spec.metering_backend is type(backend._delegate)

    def test_declarative_endpoints_meter_by_their_definition(self):
        declarative = [spec for spec in ENDPOINT_REGISTRY.values() if spec.definition is not None]
        assert declarative
        assert all(spec.metering_backend is None for spec in declarative)


class TestRegistry:
    def test_endpoint_count(self):
        assert set(ENDPOINT_REGISTRY.keys()) == {
            "openai-chat",
            "openai-responses",
            "gemini-generate",
            "anthropic-messages",
            "openai-images",
            "openai-images-generations",
            "openai-images-edits",
            "openai-chat-image",
            "openrouter-images",
            "gemini-image",
            "openai-video",
            "newapi-video",
            "v2-video-generations",
            "ark-seedance-1",
            "ark-seedance-2",
            "ark-seedance-2-5",
            "ark-seedream-3",
            "ark-seedream-4",
            "vidu-video-q3-turbo",
            "vidu-video-generic",
            "vidu-video-q3-pro",
            "vidu-video-q3-fast",
            "vidu-video-q3-reference",
            "vidu-video-q3-drama",
            "vidu-video-q3-mix",
            "vidu-video-q3-ad",
            "vidu-video-q2",
            "vidu-video-q2-pro",
            "vidu-video-q2-fast",
            "vidu-video-q1",
            "vidu-video-q1-classic",
            "vidu-video-2",
            "vidu-video-q4",
            "openrouter-video",
            "xai-video",
            "dashscope-qwen-image",
            "dashscope-wan-image",
            "dashscope-qwen-image-edit",
            "dashscope-video",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-wan3-video",
            "minimax-image",
            "minimax-h3-image",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "minimax-h3",
            "kling-image",
            "kling-image-single",
            "kling-video-pro-silent",
            "kling-video-pro-audio",
            "kling-video-audio",
            "kling-video-silent",
            "kling-video-model-path",
            "gemini-video",
            "gemini-interactions-video",
            "openai-tts",
            "dashscope-tts",
            "dashscope-speech-synthesizer",
            "minimax-tts",
            "gemini-tts",
        }

    def test_each_spec_has_required_fields(self):
        for key, spec in ENDPOINT_REGISTRY.items():
            assert spec.key == key
            assert spec.media_type in {"text", "image", "video", "audio"}
            assert spec.family in {
                "openai",
                "google",
                "anthropic",
                "newapi",
                "v2",
                "ark",
                "vidu",
                "dashscope",
                "minimax",
                "kling",
                "openrouter",
                "xai",
            }
            assert spec.source == "builtin"
            if spec.kind == "declarative":
                assert spec.display_name_key == ""
                assert spec.display_name
            else:
                assert spec.display_name_key.startswith("endpoint_")
                assert spec.display_name is None
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
            "kind": "python",
            "display_name_key": "endpoint_openai_chat_display",
            "display_name": None,
            "source": "builtin",
            "request_method": "POST",
            "request_path_template": "/v1/chat/completions",
            "image_capabilities": None,
            "video_max_reference_images": None,
            "end_image_capable": False,
            "reference_audio_capable": False,
            "size_fixed": False,
            "duration_fixed": False,
            "duration_frame_rate_missing": False,
            "duration_tier_empty": False,
            "native_resolution": None,
            "requests": (
                {
                    "name": "generate",
                    "display_name_key": "cp_request_generate",
                    "default_path": "/chat/completions",
                    "placeholders": ("model",),
                    "base_url_rule": "append_v1",
                },
            ),
        }

    def test_new_video_endpoints_have_unset_cap(self):
        """v2/ark/vidu/dashscope/minimax/kling 不在 endpoint 维度声明上限，由 resolver 调 backend 纯 caps 函数读取。"""
        for key in (
            "v2-video-generations",
            "ark-seedance-1",
            "ark-seedance-2",
            "ark-seedance-2-5",
            "vidu-video-q3-turbo",
            "dashscope-video",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-wan3-video",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "minimax-h3",
            "kling-video-pro-silent",
            "gemini-video",
            "gemini-interactions-video",
        ):
            assert ENDPOINT_REGISTRY[key].video_max_reference_images is None
        assert ENDPOINT_REGISTRY["openai-video"].video_max_reference_images is None
        assert ENDPOINT_REGISTRY["newapi-video"].video_max_reference_images is None

    def test_video_caps_declaration_bindings(self):
        """每个 video endpoint 选对了上限来源：None-cap 的绑 caps_fn、显式 int 的不绑。

        全注册表 XOR/非负不变式由 endpoints.py 的 module-load `_validate_registry()`
        在 import 期保证（违反则本文件根本 import 不进来），故此处只断言「具体哪个 endpoint 选了哪条
        路径」——这是 XOR 校验抓不到的（换机制仍满足 XOR），是真正的回归护栏。"""
        for key in (
            "v2-video-generations",
            "ark-seedance-1",
            "ark-seedance-2",
            "ark-seedance-2-5",
            "vidu-video-q3-turbo",
            "dashscope-video",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-wan3-video",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "minimax-h3",
            "kling-video-pro-silent",
            "gemini-video",
            "gemini-interactions-video",
        ):
            assert ENDPOINT_REGISTRY[key].video_caps_for_model is not None
        assert ENDPOINT_REGISTRY["openai-video"].video_caps_for_model is not None

    def test_dashscope_caps_fn_reads_channel_declaration_without_client(self):
        """百炼异步视频端点 的 caps_fn 是纯函数：给出通道声明（不设参考图上限、按名字认的请求形态），
        resolver 据此与条目两侧求值而无需构造 backend / api_key。"""
        caps_fn = ENDPOINT_REGISTRY["dashscope-reference-voice-video"].video_caps_for_model
        assert caps_fn is not None
        assert caps_fn("happyhorse-1.0-r2v", None).max_reference_images is None
        assert caps_fn("wan2.7-r2v", None).reference_audio_per_image is True

    def test_minimax_declarative_endpoints_expose_definition_capabilities(self):
        s2v = ENDPOINT_REGISTRY["minimax-s2v-01"].video_caps_for_model
        hailuo = ENDPOINT_REGISTRY["minimax-hailuo-v1"].video_caps_for_model
        fast = ENDPOINT_REGISTRY["minimax-hailuo-v1-fast"].video_caps_for_model
        assert s2v is not None
        assert s2v("S2V-01", None).max_reference_images == 1
        assert hailuo is not None
        assert hailuo("MiniMax-Hailuo-2.3", None).first_frame is True
        assert hailuo("MiniMax-Hailuo-2.3", None).text_to_video is True
        assert fast is not None
        assert fast("MiniMax-Hailuo-2.3-Fast", None).text_to_video is False

    def test_kling_caps_fn_reads_channel_declaration_without_client(self):
        """kling-video 的 caps_fn 是纯函数：多图主体子路径的通道上限 4 与型号无关，旧型号在无档位上下文时
        保守关尾帧；各型号接不接受参考图由条目决定，resolver 据此两侧求值而无需构造 backend / api_key。"""
        from lib.custom_provider.capabilities import two_sided_video_capabilities

        caps_fn = ENDPOINT_REGISTRY["kling-video-pro-silent"].video_caps_for_model
        assert caps_fn is not None
        for model_id in ("kling-v3-omni", "kling-v2-5-turbo", "kling-v4", "vendor/some-unknown-model"):
            assert caps_fn(model_id, None).max_reference_images == 4
        assert caps_fn("kling-v2-5-turbo", None).last_frame is False

        def effective(model_id: str):
            return two_sided_video_capabilities(endpoint="kling-video-pro-silent", model_id=model_id)

        assert effective("kling-v3-omni").max_reference_images == 4
        assert effective("kling-video-o1").max_reference_images == 4
        assert effective("kling-v2-5-turbo").max_reference_images == 0
        for prefixed_id in ("vendor/Kling-V3-Omni", "provider:kling-v3-omni"):
            assert effective(prefixed_id).max_reference_images == 4
        unknown = effective("kling-v4")
        assert unknown.max_reference_images == 4
        assert unknown.last_frame is False

    def test_negative_int_cap_rejected_at_validation(self):
        """import 期不变式拒绝负数 int cap：下游 references[:-1] 会误丢最后一张而非裁成 0 张。"""
        import dataclasses

        bad = dataclasses.replace(
            ENDPOINT_REGISTRY["openai-video"], video_max_reference_images=-1, video_caps_for_model=None
        )
        with pytest.raises(ValueError, match="negative video_max_reference_images"):
            validate_video_caps_declaration(bad)

    def test_non_callable_caps_fn_rejected_at_validation(self):
        """import 期不变式拒绝非 callable 的 video_caps_for_model：否则误填字符串/整数会放行到
        request 期才在 resolver `caps_fn(model_id)` 处炸，违背 fail-fast 初衷。"""
        import dataclasses

        bad = dataclasses.replace(
            ENDPOINT_REGISTRY["ark-seedance-2"],
            video_max_reference_images=None,
            video_caps_for_model="not-callable",
        )
        with pytest.raises(ValueError, match="non-callable video_caps_for_model"):
            validate_video_caps_declaration(bad)

    def test_non_video_endpoint_declaring_reference_audio_rejected(self):
        """import 期不变式：reference_audio_capable 只对 video 类有意义，非 video 类声明即 misconfig。"""
        import dataclasses

        bad = dataclasses.replace(ENDPOINT_REGISTRY["openai-chat"], reference_audio_capable=True)
        with pytest.raises(ValueError, match="must not declare reference_audio_capable"):
            validate_video_caps_declaration(bad)

    def test_audio_capable_endpoints_match_backends_that_send_audio(self):
        """运输声明与 backend 实现同步：声明 True 的 endpoint 必须真的组装参考音频。"""
        audio_capable = {k for k, s in ENDPOINT_REGISTRY.items() if s.reference_audio_capable}
        assert audio_capable == {
            "ark-seedance-1",
            "ark-seedance-2",
            "ark-seedance-2-5",
            "dashscope-video",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-wan3-video",
            "minimax-h3",
            "vidu-video-q3-drama",
            "vidu-video-q4",
        }

    def test_audio_endpoint_spec(self):
        spec = ENDPOINT_REGISTRY["openai-tts"]
        assert spec.media_type == "audio"
        assert spec.family == "openai"
        assert spec.request_path_template == "/v1/audio/speech"
        assert spec.video_max_reference_images is None
        assert spec.video_caps_for_model is None
        assert spec.image_capabilities is None

    def test_media_type_groups(self):
        text_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "text"}
        image_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "image"}
        video_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "video"}
        audio_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "audio"}
        assert audio_keys == {
            "openai-tts",
            "dashscope-tts",
            "dashscope-speech-synthesizer",
            "minimax-tts",
            "gemini-tts",
        }
        assert text_keys == {"openai-chat", "openai-responses", "gemini-generate", "anthropic-messages"}
        assert image_keys == {
            "openai-images",
            "openai-images-generations",
            "openai-images-edits",
            "openai-chat-image",
            "openrouter-images",
            "gemini-image",
            "dashscope-qwen-image",
            "dashscope-wan-image",
            "dashscope-qwen-image-edit",
            "minimax-image",
            "minimax-h3-image",
            "kling-image",
            "kling-image-single",
            "ark-seedream-3",
            "ark-seedream-4",
        }
        assert video_keys == {
            "openai-video",
            "newapi-video",
            "v2-video-generations",
            "ark-seedance-1",
            "ark-seedance-2",
            "ark-seedance-2-5",
            "vidu-video-q3-turbo",
            "vidu-video-generic",
            "vidu-video-q3-pro",
            "vidu-video-q3-fast",
            "vidu-video-q3-reference",
            "vidu-video-q3-drama",
            "vidu-video-q3-mix",
            "vidu-video-q3-ad",
            "vidu-video-q2",
            "vidu-video-q2-pro",
            "vidu-video-q2-fast",
            "vidu-video-q1",
            "vidu-video-q1-classic",
            "vidu-video-2",
            "vidu-video-q4",
            "openrouter-video",
            "xai-video",
            "dashscope-video",
            "dashscope-always-on-video",
            "dashscope-reference-video",
            "dashscope-reference-voice-video",
            "dashscope-wan3-video",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "minimax-h3",
            "kling-video-pro-silent",
            "kling-video-pro-audio",
            "kling-video-audio",
            "kling-video-silent",
            "kling-video-model-path",
            "gemini-video",
            "gemini-interactions-video",
        }


class TestHelpers:
    def test_get_endpoint_spec(self):
        spec = get_endpoint_spec("openai-chat")
        assert spec.media_type == "text"

    def test_get_endpoint_spec_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown endpoint"):
            get_endpoint_spec("acme-unknown")

    def test_endpoint_to_media_type(self):
        assert endpoint_to_media_type("newapi-video") == "video"
        assert endpoint_to_media_type("gemini-image") == "image"

    def test_endpoint_to_media_type_unknown_raises(self):
        with pytest.raises(ValueError, match=r"unknown endpoint"):
            endpoint_to_media_type("nope")

    def test_list_endpoints_by_media_type(self):
        text = list_endpoints_by_media_type("text")
        assert {s.key for s in text} == {"openai-chat", "openai-responses", "gemini-generate", "anthropic-messages"}

    @pytest.mark.parametrize(
        ("definition", "required"),
        [
            ({"submit": {"url": "{{ base_url }}/v1/video"}}, True),
            (
                {"submit": {"url": "https://fixed.test/v1/video"}, "poll": {"url": "{{ base_url }}/v1/task"}},
                True,
            ),
            ({"submit": {"url": "https://fixed.test/v1/video"}, "result": {"url": "{{ base_url }}/v1/file"}}, True),
            ({"submit": {"url": "https://fixed.test/v1/video"}, "poll": {"url": "https://fixed.test/v1/task"}}, False),
            ({"submit": {"url": "https://fixed.test/base_url/video"}}, False),
        ],
    )
    def test_declarative_requires_base_url_scans_every_request_section(self, definition, required):
        assert declarative_requires_base_url(definition) is required


INFER_RULE_CASES = [
    ("imagen-4", "openai", "openai-images"),
    ("imagen-4", "google", "gemini-image"),
    ("gemini-imagen-3", "openai", "gemini-image"),
    ("qwen-tts", "openai", "dashscope-tts"),
    ("qwen3-tts-flash", "openai", "dashscope-tts"),
    ("Qwen3-TTS-Flash", "google", "dashscope-tts"),
    ("proxy/qwen3-tts-instruct-flash", "openai", "dashscope-tts"),
    ("qwen-audio-3.1-tts-flash", "openai", "dashscope-speech-synthesizer"),
    ("qwen-audio-3.0-tts-plus", "google", "dashscope-speech-synthesizer"),
    ("proxy/Qwen-Audio-3.1-TTS-Plus", "openai", "dashscope-speech-synthesizer"),
    ("tts-1", "openai", "openai-tts"),
    ("cosyvoice-v2", "openai", "openai-tts"),
    ("veo-3", "google", "gemini-video"),
    ("veo-3.1-generate-preview", "google", "gemini-video"),
    ("veo-3", "openai", "openai-video"),
    ("sora-2", "google", "openai-video"),
    ("speech-2.8-hd", "openai", "minimax-tts"),
    ("speech-02-turbo", "google", "minimax-tts"),
    ("minimax/Speech-2.6-HD", "openai", "minimax-tts"),
    ("speech-1.5", "openai", "openai-tts"),
    ("gemini-3.8-flash-tts", "google", "gemini-tts"),
    ("gemini-2.5-flash-preview-tts", "openai", "gemini-tts"),
    ("models/gemini-3.8-flash-lite-tts", "google", "gemini-tts"),
    ("gemini-3-flash-preview", "google", "gemini-generate"),
]


class TestDeclarativeRequests:
    def test_versioned_definition_declares_submit_and_poll_with_their_default_paths(self):
        requests = {r.name: r for r in get_endpoint_spec("xai-video").requests}

        assert set(requests) == {"submit", "poll"}
        assert requests["submit"].default_path == "/v1/videos/generations"
        assert requests["poll"].default_path == "/v1/videos/{{ task_id }}"
        assert requests["poll"].placeholders == ("model", "task_id")
        assert requests["poll"].base_url_rule == "strip_version"

    def test_request_with_an_absolute_url_is_not_overridable(self):
        definition = dict(ENDPOINT_REGISTRY["openrouter-video"].definition or {})
        definition["poll"] = {**definition["poll"], "url": "https://status.test/videos/{{ task_id }}"}

        assert [r.name for r in declarative_requests(definition)] == ["submit"]


class TestInferEndpoint:
    @pytest.mark.parametrize(
        ("model_id", "discovery_format", "expected"),
        [
            ("gemini-2.5-flash", "openai", "gemini-generate"),
            ("gemini-2.5-flash", "google", "gemini-generate"),
            ("gemini-2.5-flash-image", "openai", "gemini-image"),
            ("gemini-2.5-flash-image", "google", "gemini-image"),
            ("gemini-2.0-flash-exp-image-generation", "openai", "gemini-image"),
            ("gemini-3-pro-image-preview", "openai", "gemini-image"),
            ("seedance-1.0", "openai", "ark-seedance-1"),
            ("doubao-seedance-2-0", "openai", "ark-seedance-2"),
            ("dreamina-seedance-2.5-260628", "openai", "ark-seedance-2-5"),
            ("viduq3", "openai", "vidu-video-q3-reference"),
            ("viduq3-drama", "openai", "vidu-video-q3-drama"),
            ("viduq3-mix", "openai", "vidu-video-q3-mix"),
            ("viduq3-ad", "openai", "vidu-video-q3-ad"),
            ("viduq3-pro", "openai", "vidu-video-q3-pro"),
            ("viduq3-turbo", "openai", "vidu-video-q3-turbo"),
            ("viduq4-preview", "openai", "vidu-video-q4"),
            ("viduq3-i2v", "openai", "vidu-video-generic"),
            ("proxy/viduq3-turbo", "openai", "vidu-video-q3-turbo"),
            ("wan2.7-i2v", "openai", "dashscope-always-on-video"),
            ("wan2.7-t2v", "openai", "dashscope-always-on-video"),
            ("wan-2.7-i2v", "openai", "dashscope-always-on-video"),
            ("wan_2.7-t2v", "openai", "dashscope-always-on-video"),
            ("wan3.0-video", "openai", "dashscope-wan3-video"),
            ("Wan3.0-Video", "openai", "dashscope-wan3-video"),
            ("proxy/wan3.0-video", "openai", "dashscope-wan3-video"),
            ("wan-3-turbo", "openai", "dashscope-wan3-video"),
            ("wan3-turbo", "openai", "dashscope-wan3-video"),
            ("wan2.7-image", "openai", "openai-images"),
            ("wan-2.7-image", "openai", "openai-images"),
            ("wan3.0-video-image", "openai", "openai-images"),
            ("wan-3-turbo-image", "openai", "openai-images"),
            ("wan-3-turbo-image-to-video", "openai", "dashscope-wan3-video"),
            ("wan3-image2video", "openai", "dashscope-wan3-video"),
            ("wan-2.7-image-to-video", "openai", "dashscope-always-on-video"),
            ("wan3.0-image-edit", "openai", "openai-images"),
            ("wan-3-turbo-image-preview", "openai", "openai-images"),
            ("wan3.0-video-image-20260801", "openai", "openai-images"),
            ("wan_3_turbo", "openai", "dashscope-wan3-video"),
            ("wan_3.0-image", "openai", "openai-images"),
            ("WAN_3_TURBO_IMAGE_TO_VIDEO", "openai", "dashscope-wan3-video"),
            ("swan3", "openai", "openai-video"),
            ("vendorwan3", "openai", "openai-video"),
            ("wan30", "openai", "openai-video"),
            ("swan2", "openai", "openai-video"),
            ("vendorwan2", "openai", "openai-video"),
            ("wan20", "openai", "openai-video"),
            ("swan2.7-r2v", "openai", "openai-video"),
            ("vendorwan2.7-t2v", "openai", "openai-video"),
            ("wan-2.1-kf2v", "openai", "openai-video"),
            ("wan_2.2-t2v", "openai", "openai-video"),
            ("wan2.1-kf2v", "openai", "dashscope-video"),
            ("wan-2.7-videoedit", "openai", "openai-video"),
            ("wan_2.7-videoedit", "openai", "openai-video"),
            ("wan2.7-videoedit", "openai", "openai-video"),
            ("myhappyhorse-1.0-r2v", "openai", "openai-chat"),
            ("happyhorse-1.0-r2v", "openai", "dashscope-reference-video"),
            ("gpt-4o", "openai", "openai-chat"),
            ("claude-sonnet-4.5", "openai", "openai-chat"),
            ("dall-e-3", "openai", "openai-images"),
            ("gpt-image-1", "openai", "openai-images"),
            ("flux-pro", "openai", "openai-images"),
            ("sora-2", "openai", "openai-video"),
            ("SORA-2", "openai", "openai-video"),
            ("veo-3", "openai", "openai-video"),
            ("MiniMax-Hailuo-2.3", "openai", "minimax-hailuo-v1"),
            ("MiniMax-Hailuo-2.3-Fast", "openai", "minimax-hailuo-v1-fast"),
            ("minimax-hailuo-2.3-fast", "openai", "minimax-hailuo-v1-fast"),
            ("minimax-hailuo-2.3", "openai", "minimax-hailuo-v1"),
            (
                "hailuo-02",
                "openai",
                "minimax-hailuo-v1",
            ),
            ("proxy/MiniMax-Hailuo-2.3-Fast", "openai", "minimax-hailuo-v1-fast"),
            ("proxy/MiniMax-Hailuo-2.3", "openai", "minimax-hailuo-v1"),
            ("vendor:MiniMax-Hailuo-2.3-Fast", "openai", "minimax-hailuo-v1-fast"),
            ("openrouter/minimax/MiniMax-Hailuo-2.3-Fast", "openai", "minimax-hailuo-v1-fast"),
            ("proxy/vendor:S2V-01", "openai", "minimax-s2v-01"),
            ("MiniMax-Hailuo-2.3-Fast-preview", "openai", "minimax-hailuo-v1"),
            ("hailuo-fast", "openai", "minimax-hailuo-v1"),
            ("S2V-01", "openai", "minimax-s2v-01"),
            ("minimax-s2v-01", "openai", "minimax-s2v-01"),
            ("proxy/S2V-01", "openai", "minimax-s2v-01"),
            ("wan2.7-s2v", "openai", "openai-video"),
            ("wan-2.2-s2v", "openai", "openai-video"),
            ("vendor-s2v-custom", "openai", "openai-chat"),
            ("MiniMax-H3", "openai", "minimax-h3"),
            ("minimax-h3", "openai", "minimax-h3"),
            ("MiniMax-H3-Image", "openai", "minimax-h3-image"),
            ("MiniMax-H3-Image-Highspeed", "openai", "minimax-h3-image"),
            ("minimax-h3-image", "openai", "minimax-h3-image"),
            ("proxy/MiniMax-H3-Image-Highspeed", "openai", "minimax-h3-image"),
            ("h3", "openai", "openai-chat"),
            ("other-vendor-h3", "openai", "openai-chat"),
            ("image-01", "openai", "minimax-image"),
            ("minimax/image-01", "openai", "minimax-image"),
            ("S2V-01", "google", "minimax-s2v-01"),
            ("kling-v2-5-turbo", "openai", "kling-video-pro-silent"),
            ("kling-v2", "openai", "kling-video-silent"),
            ("kling-v3", "openai", "kling-video-audio"),
            ("kling-v2-6", "openai", "kling-video-pro-audio"),
            ("proxy/kling-v2-5-turbo", "openai", "kling-video-pro-silent"),
            ("KLING-V3", "openai", "kling-video-audio"),
            ("kling-v3-omni", "openai", "kling-video-audio"),
            ("kling-4.0", "openai", "kling-video-model-path"),
            ("proxy/kling-3.0-turbo", "openai", "kling-video-model-path"),
            ("kling-image-o1", "openai", "kling-image"),
            ("kling-v3-omni-image", "openai", "kling-image"),
            ("proxy/kling-image-o1", "openai", "kling-image"),
            ("kling-image-o1", "google", "kling-image"),
            ("kling-image2video", "openai", "kling-video-silent"),
            ("kling-img2video", "openai", "kling-video-silent"),
            ("proxy/kling-image2video", "openai", "kling-video-silent"),
            ("seedream-3.0", "openai", "ark-seedream-3"),
            ("doubao-seedream-5-0-260128", "openai", "ark-seedream-4"),
            ("proxy/Doubao-Seedream-4.5", "openai", "ark-seedream-4"),
            ("doubao-seedream-4-0-250828", "google", "ark-seedream-4"),
            ("jimeng-3.0", "openai", "openai-images"),
            ("jimeng-video-3.0", "openai", "openai-video"),
            ("jimengvideo-3.0", "openai", "openai-video"),
            ("MiniMax-M2.7", "openai", "openai-chat"),
            ("minimax-abab-6.5-chat", "openai", "openai-chat"),
            ("MiniMax-M2.7", "google", "gemini-generate"),
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
            ("tts-1", "openai", "openai-tts"),
            ("tts-1-hd", "openai", "openai-tts"),
            ("gpt-4o-mini-tts", "openai", "openai-tts"),
            ("vidu-tts", "openai", "openai-tts"),
            ("speech-1.5", "openai", "openai-tts"),
            ("cosyvoice-v2", "openai", "openai-tts"),
            ("tts-1", "google", "openai-tts"),
            ("gpt-4o-audio-preview", "openai", "openai-chat"),
            ("whisper-1", "openai", "openai-chat"),
            ("speech-to-text-1", "openai", "openai-chat"),
            ("transcribe-speech-1", "openai", "openai-chat"),
        ],
    )
    def test_infer(self, model_id, discovery_format, expected):
        assert infer_endpoint(model_id, discovery_format) == expected

    @pytest.mark.parametrize(("model_id", "discovery_format", "expected"), INFER_RULE_CASES)
    def test_infer_rule_table(self, model_id, discovery_format, expected):
        assert infer_endpoint(model_id, discovery_format) == expected

    @pytest.mark.parametrize(
        ("model_id", "discovery_format"),
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

    @pytest.mark.parametrize(
        "model_id",
        ["gemini-2.5-flash-image", "google/gemini-3-pro-image-preview", "gpt-5-image", "gpt-4o"],
    )
    @pytest.mark.parametrize("discovery_format", ["openai", "google"])
    def test_chat_image_never_auto_inferred(self, model_id, discovery_format):
        """出图模型名与 openai-chat / openai-images 的模型名无从区分，不自动推断，由用户在获取或编辑模型时选择。"""
        assert infer_endpoint(model_id, discovery_format) != "openai-chat-image"

    @pytest.mark.parametrize(
        ("model_id", "discovery_format", "media_type", "expected"),
        [
            ("seedance-2.0-pro", "openai", "video", "ark-seedance-2"),
            ("gpt-5.5", "openai", "text", "openai-chat"),
            ("doubao-seedream-5-0-260128", "openai", "image", "ark-seedream-4"),
            ("kling-v3-omni", "openai", "image", "kling-image"),
            ("gemini-3-pro-image", "openai", "text", "gemini-generate"),
            ("house-painter", "openai", "image", "openai-images"),
            ("house-painter", "google", "image", "gemini-image"),
            ("house-motion", "openai", "video", "openai-video"),
            ("house-motion", "google", "video", "gemini-video"),
            ("house-voice", "openai", "audio", "openai-tts"),
        ],
    )
    def test_infer_within_known_media_type(self, model_id, discovery_format, media_type, expected):
        assert infer_endpoint(model_id, discovery_format, media_type) == expected


def test_image_endpoint_registry_entries():
    from lib.custom_provider.endpoints import ENDPOINT_KEYS_BY_MEDIA_TYPE

    image_keys = set(ENDPOINT_KEYS_BY_MEDIA_TYPE["image"])
    assert image_keys == {
        "openai-images",
        "openai-images-generations",
        "openai-images-edits",
        "openai-chat-image",
        "openrouter-images",
        "gemini-image",
        "dashscope-qwen-image",
        "dashscope-wan-image",
        "dashscope-qwen-image-edit",
        "minimax-image",
        "minimax-h3-image",
        "kling-image",
        "kling-image-single",
        "ark-seedream-3",
        "ark-seedream-4",
    }


def test_split_endpoints_have_single_capability():
    from lib.backends.image_backends import ImageCapability
    from lib.custom_provider.endpoints import endpoint_to_image_capabilities

    assert endpoint_to_image_capabilities("openai-images-generations") == frozenset({ImageCapability.TEXT_TO_IMAGE})
    assert endpoint_to_image_capabilities("openai-images-edits") == frozenset({ImageCapability.IMAGE_TO_IMAGE})


def test_existing_image_endpoints_have_full_capabilities():
    """EndpointSpec 新增 image_capabilities 字段；已存在的 image entry 默认填两个能力。"""
    from lib.backends.image_backends import ImageCapability
    from lib.custom_provider.endpoints import (
        ENDPOINT_REGISTRY,
        endpoint_spec_to_dict,
        endpoint_to_image_capabilities,
    )

    full = frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE})
    assert ENDPOINT_REGISTRY["openai-images"].image_capabilities == full
    assert ENDPOINT_REGISTRY["gemini-image"].image_capabilities == full
    assert ENDPOINT_REGISTRY["openai-chat"].image_capabilities is None
    assert endpoint_to_image_capabilities("openai-images") == full

    with pytest.raises(ValueError, match=r"is not an image endpoint"):
        endpoint_to_image_capabilities("openai-chat")

    serialized = endpoint_spec_to_dict(ENDPOINT_REGISTRY["openai-images"])
    assert serialized["image_capabilities"] == ["image_to_image", "text_to_image"]


@pytest.mark.parametrize("discovery_format", ["google", "openai"])
@pytest.mark.parametrize(
    "model", ["gemini-omni-1.1-flash", "gemini-omni-1.2-flash", "proxy/Gemini-Omni-1.2-flash-preview"]
)
def test_omni_names_prefill_the_interactions_video_endpoint(model: str, discovery_format: str):
    from lib.custom_provider.endpoints import infer_media_type

    assert infer_endpoint(model, discovery_format) == "gemini-interactions-video"
    assert infer_endpoint(model, discovery_format, "video") == "gemini-interactions-video"
    assert infer_media_type(model, discovery_format) == "video"


def test_vertex_interactions_is_not_a_custom_provider_endpoint():
    from lib.backends.backend_assembly.specs import PROVIDER_ONLY_ENDPOINTS

    assert "gemini-vertex-interactions-video" in PROVIDER_ONLY_ENDPOINTS
    assert "gemini-vertex-interactions-video" not in ENDPOINT_REGISTRY
    assert "gemini-interactions-video" not in PROVIDER_ONLY_ENDPOINTS
