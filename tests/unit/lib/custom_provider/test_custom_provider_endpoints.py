"""Unit tests for ENDPOINT_REGISTRY completeness and helpers."""

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
    """Pricing reads metering declarations on ``metering_backend``; that class must be the channel wrapped by ``build_backend``."""

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
            # Registry endpoints are built-in, always source builtin; user endpoints stay outside it and construct dynamically from ce- keys.
            assert spec.source == "builtin"
            # Exactly one display-name source: Python built-ins use i18n keys; declarative endpoints use definition meta.name.
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
            # Only declarative endpoints carry names from definitions; Python built-ins resolve i18n through display_name_key.
            "display_name": None,
            "source": "builtin",
            "request_method": "POST",
            "request_path_template": "/v1/chat/completions",
            "image_capabilities": None,
            # Undeclared endpoint caps serialize as None (resolver falls through to backend caps).
            "video_max_reference_images": None,
            "end_image_capable": False,
            "reference_audio_capable": False,
            # Only ComfyUI has nondefault values for the five parameter constraints; other endpoint sizes/durations follow request parameters.
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
        """v2/ark/vidu/dashscope/minimax/kling declare no endpoint-level limit; the resolver reads backend pure caps functions."""
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
        # Preserve existing explicit ints with unchanged behavior.
        assert ENDPOINT_REGISTRY["openai-video"].video_max_reference_images == 1
        assert ENDPOINT_REGISTRY["newapi-video"].video_max_reference_images is None

    def test_video_caps_declaration_bindings(self):
        """Each video endpoint selects the right limit source: None-cap binds caps_fn; explicit int does not.

        endpoints.py module-load `_validate_registry()` guarantees registry-wide XOR/nonnegative invariants at import
        (violations prevent this file importing), so assert which mechanism each endpoint selects. XOR validation cannot detect
        a mechanism switch that still satisfies XOR; this is the actual regression guard."""
        # None-cap video endpoints must bind pure caps functions.
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
        # Explicit-int video endpoints must not additionally bind caps functions.
        for key in ("openai-video",):
            assert ENDPOINT_REGISTRY[key].video_caps_for_model is None

    def test_dashscope_caps_fn_reads_channel_declaration_without_client(self):
        """Dashscope asynchronous-video caps_fn is pure: declare channel capabilities (unlimited reference images and name-based request shape),
        letting the resolver evaluate them with entries without constructing a backend / api_key."""
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
        # Fast differs from 2.3 only here: required first frame implies text_to_video=False.
        assert hailuo("MiniMax-Hailuo-2.3", None).text_to_video is True
        assert fast is not None
        assert fast("MiniMax-Hailuo-2.3-Fast", None).text_to_video is False

    def test_kling_caps_fn_reads_channel_declaration_without_client(self):
        """kling-video caps_fn is pure: the multi-image subject subpath limit is 4 regardless of model; legacy models conservatively
        disable last frames without tier context. Entries determine reference-image support; the resolver evaluates both without backend construction / api_key."""
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
        # Proxy model_id has a vendor prefix (existing / and : conventions) plus noncanonical case; normalize to match entries.
        for prefixed_id in ("vendor/Kling-V3-Omni", "provider:kling-v3-omni"):
            assert effective(prefixed_id).max_reference_images == 4
        # Unmatched models follow only channel constraints, with conservative last-frame fallback off.
        unknown = effective("kling-v4")
        assert unknown.max_reference_images == 4
        assert unknown.last_frame is False

    def test_negative_int_cap_rejected_at_validation(self):
        """Import invariants reject negative int caps: downstream references[:-1] would drop the last image instead of trimming to zero."""
        import dataclasses

        bad = dataclasses.replace(
            ENDPOINT_REGISTRY["openai-video"], video_max_reference_images=-1, video_caps_for_model=None
        )
        with pytest.raises(ValueError, match="negative video_max_reference_images"):
            validate_video_caps_declaration(bad)

    def test_non_callable_caps_fn_rejected_at_validation(self):
        """Import invariants reject noncallable video_caps_for_model; otherwise strings/integers would survive until
        request-time resolver `caps_fn(model_id)` failure, defeating fail-fast intent."""
        import dataclasses

        # A truthy noncallable string masquerades as caps_fn; clear int cap to avoid failing XOR first.
        bad = dataclasses.replace(
            ENDPOINT_REGISTRY["ark-seedance-2"],
            video_max_reference_images=None,
            video_caps_for_model="not-callable",
        )
        with pytest.raises(ValueError, match="non-callable video_caps_for_model"):
            validate_video_caps_declaration(bad)

    def test_non_video_endpoint_declaring_reference_audio_rejected(self):
        """Import invariant: reference_audio_capable is meaningful only for video; declarations on nonvideo are misconfiguration."""
        import dataclasses

        bad = dataclasses.replace(ENDPOINT_REGISTRY["openai-chat"], reference_audio_capable=True)
        with pytest.raises(ValueError, match="must not declare reference_audio_capable"):
            validate_video_caps_declaration(bad)

    def test_audio_capable_endpoints_match_backends_that_send_audio(self):
        """Transport declarations match backend implementation: endpoints declaring True must actually assemble reference audio."""
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
            "vidu-video-q4",
        }

    def test_audio_endpoint_spec(self):
        spec = ENDPOINT_REGISTRY["openai-tts"]
        assert spec.media_type == "audio"
        assert spec.family == "openai"
        assert spec.request_path_template == "/v1/audio/speech"
        # Nonvideo/nonimage endpoints declare neither video caps nor image capabilities.
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
            # Hardcoded absolute submission URL with base_url only in polling: checking submission alone delays failure until after paid submission.
            (
                {"submit": {"url": "https://fixed.test/v1/video"}, "poll": {"url": "{{ base_url }}/v1/task"}},
                True,
            ),
            ({"submit": {"url": "https://fixed.test/v1/video"}, "result": {"url": "{{ base_url }}/v1/file"}}, True),
            ({"submit": {"url": "https://fixed.test/v1/video"}, "poll": {"url": "https://fixed.test/v1/task"}}, False),
            # A hardcoded address containing base_url text is not a placeholder.
            ({"submit": {"url": "https://fixed.test/base_url/video"}}, False),
        ],
    )
    def test_declarative_requires_base_url_scans_every_request_section(self, definition, required):
        assert declarative_requires_base_url(definition) is required


# infer_endpoint family rules; rows are (model_id, discovery_format, expected endpoint).
INFER_RULE_CASES = [
    # No imagen-specific rule; generic image-family inference follows discovery_format.
    ("imagen-4", "openai", "openai-images"),
    ("imagen-4", "google", "gemini-image"),
    ("gemini-imagen-3", "openai", "gemini-image"),  # Uses gemini + image semantics, unrelated to imagen rules.
    # qwen-tts / qwen3-tts families → dashscope-tts, independent of discovery_format.
    ("qwen-tts", "openai", "dashscope-tts"),
    ("qwen3-tts-flash", "openai", "dashscope-tts"),
    ("Qwen3-TTS-Flash", "google", "dashscope-tts"),
    ("proxy/qwen3-tts-instruct-flash", "openai", "dashscope-tts"),
    # qwen-audio-<版本>-tts family → dashscope-speech-synthesizer, independent of discovery_format.
    ("qwen-audio-3.1-tts-flash", "openai", "dashscope-speech-synthesizer"),
    ("qwen-audio-3.0-tts-plus", "google", "dashscope-speech-synthesizer"),
    ("proxy/Qwen-Audio-3.1-TTS-Plus", "openai", "dashscope-speech-synthesizer"),
    ("tts-1", "openai", "openai-tts"),  # Other TTS remains openai-tts.
    ("cosyvoice-v2", "openai", "openai-tts"),
    # Veo infers gemini-video only for google discovery; otherwise remains openai-video.
    ("veo-3", "google", "gemini-video"),
    ("veo-3.1-generate-preview", "google", "gemini-video"),
    ("veo-3", "openai", "openai-video"),
    ("sora-2", "google", "openai-video"),
    # MiniMax speech-<版本>-hd|turbo → minimax-tts, independent of discovery_format.
    ("speech-2.8-hd", "openai", "minimax-tts"),
    ("speech-02-turbo", "google", "minimax-tts"),
    ("minimax/Speech-2.6-HD", "openai", "minimax-tts"),
    ("speech-1.5", "openai", "openai-tts"),  # Without tier suffix (Fish Audio style), remain openai-tts.
    # Gemini TTS → gemini-tts, independent of discovery_format.
    ("gemini-3.8-flash-tts", "google", "gemini-tts"),
    ("gemini-2.5-flash-preview-tts", "openai", "gemini-tts"),
    ("models/gemini-3.8-flash-lite-tts", "google", "gemini-tts"),
    ("gemini-3-flash-preview", "google", "gemini-generate"),  # Non-TTS Gemini text is unaffected.
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
            # Content-first correction: gateways commonly advertise discovery_format="openai" while including native IDs.
            ("gemini-2.5-flash", "openai", "gemini-generate"),  # No longer incorrectly infer openai-chat.
            ("gemini-2.5-flash", "google", "gemini-generate"),
            # Native Gemini images also correct by content to gemini-image instead of incorrectly selecting openai-images.
            ("gemini-2.5-flash-image", "openai", "gemini-image"),
            ("gemini-2.5-flash-image", "google", "gemini-image"),
            ("gemini-2.0-flash-exp-image-generation", "openai", "gemini-image"),
            ("gemini-3-pro-image-preview", "openai", "gemini-image"),
            # New video-branch routing.
            ("seedance-1.0", "openai", "ark-seedance-1"),
            ("doubao-seedance-2-0", "openai", "ark-seedance-2"),
            ("dreamina-seedance-2.5-260628", "openai", "ark-seedance-2-5"),
            ("viduq3", "openai", "vidu-video-q3-reference"),
            ("viduq3-mix", "openai", "vidu-video-q3-reference"),
            ("viduq3-pro", "openai", "vidu-video-q3-pro"),
            ("viduq3-turbo", "openai", "vidu-video-q3-turbo"),
            ("viduq4-preview", "openai", "vidu-video-q4"),
            ("viduq3-i2v", "openai", "vidu-video-generic"),
            ("proxy/viduq3-turbo", "openai", "vidu-video-q3-turbo"),
            # Alibaba Dashscope wan2.x / wan3.0 → Dashscope asynchronous video (image variants unaffected).
            ("wan2.7-i2v", "openai", "dashscope-always-on-video"),
            ("wan2.7-t2v", "openai", "dashscope-always-on-video"),
            ("wan-2.7-i2v", "openai", "dashscope-always-on-video"),  # Hyphen and dot forms match with equal breadth.
            ("wan_2.7-t2v", "openai", "dashscope-always-on-video"),
            ("wan3.0-video", "openai", "dashscope-wan3-video"),
            ("Wan3.0-Video", "openai", "dashscope-wan3-video"),  # Case-insensitive.
            ("proxy/wan3.0-video", "openai", "dashscope-wan3-video"),
            ("wan-3-turbo", "openai", "dashscope-wan3-video"),  # Hyphen and dot forms match with equal breadth.
            ("wan3-turbo", "openai", "dashscope-wan3-video"),
            ("wan2.7-image", "openai", "openai-images"),  # Image variants remain unaffected.
            ("wan-2.7-image", "openai", "openai-images"),  # Hyphenated image variants also select image endpoints.
            ("wan3.0-video-image", "openai", "openai-images"),  # Image semantics remain unaffected.
            ("wan-3-turbo-image", "openai", "openai-images"),  # Hyphenated image variants also remain unaffected.
            # image-to-video aliases contain "image" but are video, so retain the Dashscope asynchronous-video endpoint (the same
            # video-before-image principle as kling-image2video), rather than broad is_image misclassification.
            ("wan-3-turbo-image-to-video", "openai", "dashscope-wan3-video"),
            ("wan3-image2video", "openai", "dashscope-wan3-video"),
            ("wan-2.7-image-to-video", "openai", "dashscope-always-on-video"),
            # Inverse trap: real image aliases need not end in "image" (version/date/variant suffixes), so terminal spelling
            # cannot determine image semantics. Only explicit image-to-video continuation is a video exception; other image
            # semantics still mean images.
            ("wan3.0-image-edit", "openai", "openai-images"),
            ("wan-3-turbo-image-preview", "openai", "openai-images"),
            ("wan3.0-video-image-20260801", "openai", "openai-images"),
            # Mixed separators: underscores and hyphens match equally broadly (WAN3_PATTERN accepts both).
            ("wan_3_turbo", "openai", "dashscope-wan3-video"),
            ("wan_3.0-image", "openai", "openai-images"),
            ("WAN_3_TURBO_IMAGE_TO_VIDEO", "openai", "dashscope-wan3-video"),
            # Identifier boundaries: do not classify nonfamily model names merely containing "wan3"/"wan2"; WAN2_PATTERN /
            # WAN3_PATTERN require nonalphanumeric boundaries on both sides; bare "wan" still matches _VIDEO_PATTERN for generic video.
            ("swan3", "openai", "openai-video"),
            ("vendorwan3", "openai", "openai-video"),
            ("wan30", "openai", "openai-video"),
            ("swan2", "openai", "openai-video"),
            ("vendorwan2", "openai", "openai-video"),
            ("wan20", "openai", "openai-video"),
            # Same boundary trap, but a letter-adjoining prefix followed by full dot form + version + modality: _WAN_DOT_FORM_PATTERN
            # without left-boundary checks would match "swan2.7-r2v", whose first-character removal equals "wan2.7-r2v".
            ("swan2.7-r2v", "openai", "openai-video"),
            ("vendorwan2.7-t2v", "openai", "openai-video"),
            # Wan 2.x minor-version boundary: WAN2_PATTERN recognizes only 2.7; other 2.x (2.1/2.2 etc.) hyphen/underscore
            # forms do not select native endpoints, because this backend's fixed video-generation/video-synthesis endpoint differs
            # from their actual protocols (see WAN2_PATTERN in dashscope.py). Dot forms have a separate literal
            # rule unaffected by WAN2_PATTERN version anchoring.
            ("wan-2.1-kf2v", "openai", "openai-video"),  # Hyphen + non-2.7 → generic endpoint.
            ("wan_2.2-t2v", "openai", "openai-video"),  # Underscore + non-2.7 → generic endpoint.
            ("wan2.1-kf2v", "openai", "dashscope-video"),  # Dot form uses literal classification, not this regex.
            # wan2.7 videoedit matches family regexes, but DashScopeVideoBackend lacks its request construction;
            # exclude native routing (see _WAN_VIDEOEDIT_PATTERN).
            ("wan-2.7-videoedit", "openai", "openai-video"),
            ("wan_2.7-videoedit", "openai", "openai-video"),
            ("wan2.7-videoedit", "openai", "openai-video"),
            # happyhorse has the same boundary requirement: maintain equal boundaries for the same key as the fallback substring matching
            # in DashScopeVideoBackend._profile_for_model, avoiding native routing with no matching capability profile.
            ("myhappyhorse-1.0-r2v", "openai", "openai-chat"),
            ("happyhorse-1.0-r2v", "openai", "dashscope-reference-video"),
            # Backward compatibility (unchanged behavior).
            ("gpt-4o", "openai", "openai-chat"),
            ("claude-sonnet-4.5", "openai", "openai-chat"),
            ("dall-e-3", "openai", "openai-images"),
            ("gpt-image-1", "openai", "openai-images"),
            ("flux-pro", "openai", "openai-images"),
            ("sora-2", "openai", "openai-video"),
            ("SORA-2", "openai", "openai-video"),
            ("veo-3", "openai", "openai-video"),
            # Secondary routing for native MiniMax tokens.
            ("MiniMax-Hailuo-2.3", "openai", "minimax-hailuo-v1"),
            ("MiniMax-Hailuo-2.3-Fast", "openai", "minimax-hailuo-v1-fast"),
            ("minimax-hailuo-2.3-fast", "openai", "minimax-hailuo-v1-fast"),
            ("minimax-hailuo-2.3", "openai", "minimax-hailuo-v1"),
            (
                "hailuo-02",
                "openai",
                "minimax-hailuo-v1",
            ),
            # Fast versus non-Fast prefix collision: recognize Fast only by exact model name after stripping namespace prefixes.
            # Nonexact Fast aliases do not establish upstream Fast behavior; select generic Hailuo (no input requirement),
            # not the first-frame-required Fast definition, matching migration 8c2b1e7d4a90.
            ("proxy/MiniMax-Hailuo-2.3-Fast", "openai", "minimax-hailuo-v1-fast"),
            ("proxy/MiniMax-Hailuo-2.3", "openai", "minimax-hailuo-v1"),
            ("vendor:MiniMax-Hailuo-2.3-Fast", "openai", "minimax-hailuo-v1-fast"),
            # Strip multiple namespace levels to the last segment, which must equal the official model ID exactly.
            ("openrouter/minimax/MiniMax-Hailuo-2.3-Fast", "openai", "minimax-hailuo-v1-fast"),
            ("proxy/vendor:S2V-01", "openai", "minimax-s2v-01"),
            ("MiniMax-Hailuo-2.3-Fast-preview", "openai", "minimax-hailuo-v1"),
            ("hailuo-fast", "openai", "minimax-hailuo-v1"),
            ("S2V-01", "openai", "minimax-s2v-01"),
            ("minimax-s2v-01", "openai", "minimax-s2v-01"),
            ("proxy/S2V-01", "openai", "minimax-s2v-01"),
            # Nonexact s2v spellings no longer incorrectly select the reference-image-required MiniMax S2V protocol.
            ("wan2.7-s2v", "openai", "openai-video"),
            ("wan-2.2-s2v", "openai", "openai-video"),
            ("vendor-s2v-custom", "openai", "openai-chat"),
            ("MiniMax-H3", "openai", "minimax-h3"),
            ("minimax-h3", "openai", "minimax-h3"),
            ("h3", "openai", "openai-chat"),  # Bare "h3" must not match; prevent overly broad substring regression.
            ("other-vendor-h3", "openai", "openai-chat"),  # Other vendors containing h3 must not be misrouted either.
            ("image-01", "openai", "minimax-image"),  # "image" would otherwise select generic images.
            ("minimax/image-01", "openai", "minimax-image"),
            ("S2V-01", "google", "minimax-s2v-01"),  # MiniMax routing is independent of discovery_format.
            # Secondary native Kling proxy routing: video families containing kling select kling-video-*, not openai-video.
            ("kling-v2-5-turbo", "openai", "kling-video-pro-silent"),
            ("kling-v2", "openai", "kling-video-silent"),  # Unlisted request-profile names select silent.
            ("kling-v3", "openai", "kling-video-audio"),
            ("kling-v2-6", "openai", "kling-video-pro-audio"),
            ("proxy/kling-v2-5-turbo", "openai", "kling-video-pro-silent"),
            ("KLING-V3", "openai", "kling-video-audio"),  # Case-insensitive.
            ("kling-v3-omni", "openai", "kling-video-audio"),  # Ambiguous image/video names default to video.
            ("kling-4.0", "openai", "kling-video-model-path"),  # Models available only on the new API.
            ("proxy/kling-3.0-turbo", "openai", "kling-video-model-path"),
            # Kling images with image semantics → kling-image before generic image-family matching, avoiding openai-images.
            ("kling-image-o1", "openai", "kling-image"),
            ("kling-v3-omni-image", "openai", "kling-image"),
            ("proxy/kling-image-o1", "openai", "kling-image"),
            ("kling-image-o1", "google", "kling-image"),  # Kling routing is independent of discovery_format.
            # image-to-video contains image semantics but is video: prioritize video over image and select kling-video.
            ("kling-image2video", "openai", "kling-video-silent"),
            ("kling-img2video", "openai", "kling-video-silent"),
            ("proxy/kling-image2video", "openai", "kling-video-silent"),
            # Ark Seedream has no /images/edits; references also use /images/generations, independent of discovery_format.
            ("seedream-3.0", "openai", "ark-seedream-3"),
            ("doubao-seedream-5-0-260128", "openai", "ark-seedream-4"),
            ("proxy/Doubao-Seedream-4.5", "openai", "ark-seedream-4"),
            ("doubao-seedream-4-0-250828", "google", "ark-seedream-4"),
            ("jimeng-3.0", "openai", "openai-images"),
            ("jimeng-video-3.0", "openai", "openai-video"),
            ("jimengvideo-3.0", "openai", "openai-video"),
            # Text-only MiniMax models select text endpoints, without bare minimax incorrectly implying video.
            ("MiniMax-M2.7", "openai", "openai-chat"),
            ("minimax-abab-6.5-chat", "openai", "openai-chat"),
            ("MiniMax-M2.7", "google", "gemini-generate"),  # discovery_format=google → gemini-generate
            # viduq1/viduq2 are early Vidu image versions; preserve image inference.
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
            # Audio (TTS) detection precedes the text default.
            ("tts-1", "openai", "openai-tts"),
            ("tts-1-hd", "openai", "openai-tts"),
            ("gpt-4o-mini-tts", "openai", "openai-tts"),
            ("vidu-tts", "openai", "openai-tts"),  # tts suffix takes priority over the text default.
            ("speech-1.5", "openai", "openai-tts"),  # Fish Audio-style ID.
            ("cosyvoice-v2", "openai", "openai-tts"),
            # Audio has only the OpenAI-compatible endpoint; google discovery also selects openai-tts.
            ("tts-1", "google", "openai-tts"),
            # Do not disturb chat models containing audio, ASR (speech-to-text), or video/image families; retain their branches.
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
        """Fragmented v2-video-generations names cannot be reliably recognized; never infer automatically, leaving manual selection to users."""
        assert infer_endpoint(model_id, discovery_format) != "v2-video-generations"

    @pytest.mark.parametrize(
        "model_id",
        ["gemini-2.5-flash-image", "google/gemini-3-pro-image-preview", "gpt-5-image", "gpt-4o"],
    )
    @pytest.mark.parametrize("discovery_format", ["openai", "google"])
    def test_chat_image_never_auto_inferred(self, model_id, discovery_format):
        """Image model names cannot distinguish openai-chat from openai-images; do not infer automatically, letting users choose during model discovery/editing."""
        assert infer_endpoint(model_id, discovery_format) != "openai-chat-image"

    @pytest.mark.parametrize(
        ("model_id", "discovery_format", "media_type", "expected"),
        [
            # If name-based inference belongs to this media type, retain it.
            ("seedance-2.0-pro", "openai", "video", "ark-seedance-2"),
            ("gpt-5.5", "openai", "text", "openai-chat"),
            ("doubao-seedream-5-0-260128", "openai", "image", "ark-seedream-4"),
            # Dual-modality models select within their name family by entry media type.
            ("kling-v3-omni", "openai", "image", "kling-image"),
            ("gemini-3-pro-image", "openai", "text", "gemini-generate"),
            # Without a family match, use the media type's default endpoint for the discovery protocol.
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
    """EndpointSpec adds image_capabilities; existing image entries default to both capabilities."""
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

    # Verify endpoint_spec_to_dict serializes capabilities to sorted list[str]
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
