"""内置供应商的供应商模型经调用端点装配：每个「供应商 + 媒体类型」声明可选端点与默认端点，模型挂哪个端点决定请求。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace

import pytest

from arcreel_catalog_core import BuiltinProviderListing
from lib.backends.backend_assembly import specs
from lib.backends.backend_assembly.loaded_config import LoadedConfig
from lib.backends.backend_assembly.specs import (
    PROVIDER_ENDPOINTS,
    ProviderEndpoints,
    builtin_model_spec,
    declared_builtin_video_capabilities,
    infer_builtin_endpoint,
    validate_endpoint_declarations,
)
from lib.catalog import query as catalog
from lib.config.registry import PROVIDER_REGISTRY
from lib.custom_provider.endpoints import ENDPOINT_REGISTRY


@pytest.fixture
def user_added() -> Iterator[None]:
    yield
    catalog.install_user_added_models(())


def _install(*models: catalog.UserAddedModel) -> None:
    catalog.install_user_added_models(models)


def _built_endpoint_and_text_to_video(provider_id: str, model_id: str):
    spec = builtin_model_spec(provider_id, "video", model_id)
    backend = spec.build_backend(
        LoadedConfig(
            credentials={"api_key": "sk-test"}, provider_meta=PROVIDER_REGISTRY[provider_id], rate_limiter=None
        ),
        model_id,
    )
    assert backend.model == model_id
    return spec.endpoint, backend.video_capabilities.text_to_video


class TestDeclarations:
    def test_every_builtin_provider_declares_endpoints_for_each_media_type_it_lists(self):
        for provider_id in PROVIDER_REGISTRY:
            for media_type in catalog.provider_media_types(provider_id):
                declared = PROVIDER_ENDPOINTS[(provider_id, media_type)]
                assert declared.default in declared.endpoints
        validate_endpoint_declarations()

    def test_a_default_outside_the_options_is_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(
            PROVIDER_ENDPOINTS,
            ("ark", "video"),
            ProviderEndpoints("ark", "video", ("ark-seedance-2",), "kling-video-pro-silent"),
        )
        with pytest.raises(ValueError, match="default endpoint 'kling-video-pro-silent'"):
            specs._validate_provider_specs()

    def test_an_endpoint_of_another_media_type_is_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(
            PROVIDER_ENDPOINTS,
            ("ark", "video"),
            ProviderEndpoints("ark", "video", ("ark-seedance-2", "ark-seedream-4"), "ark-seedance-2"),
        )
        with pytest.raises(ValueError, match="'ark-seedream-4'"):
            validate_endpoint_declarations()

    def test_shared_endpoint_names_are_builtin_endpoints_of_the_registry(self):
        for declared in PROVIDER_ENDPOINTS.values():
            for endpoint in declared.endpoints:
                if endpoint not in specs.PROVIDER_ONLY_ENDPOINTS:
                    assert ENDPOINT_REGISTRY[endpoint].source == "builtin"


class TestAssemblyFollowsTheEndpoint:
    def test_official_model_assembly_follows_synced_endpoint_not_model_name(self):
        original = catalog.factory_snapshot()
        listing = original.builtin_providers["minimax"]
        model_id = "MiniMax-H3"
        try:
            for endpoint, expected in (("minimax-h3", True), ("minimax-hailuo-v1-fast", False)):
                models = tuple(
                    replace(model, endpoint=endpoint) if model.api_model_name == model_id else model
                    for model in listing.models
                )
                catalog.install_synced_data(
                    replace(
                        original,
                        dataset_version="2099-01-01T00:00:00Z",
                        builtin_providers={
                            **original.builtin_providers,
                            "minimax": BuiltinProviderListing(models, listing.defaults),
                        },
                    )
                )
                assert _built_endpoint_and_text_to_video("minimax", model_id) == (endpoint, expected)
                assert declared_builtin_video_capabilities("minimax", model_id).text_to_video is expected
        finally:
            catalog.install_synced_data(None)

    def test_a_user_added_model_is_built_through_the_endpoint_it_chose(self, user_added: None):
        _install(catalog.UserAddedModel("minimax", "acme-video", "video", endpoint="minimax-h3"))

        assert _built_endpoint_and_text_to_video("minimax", "acme-video") == ("minimax-h3", True)
        assert declared_builtin_video_capabilities("minimax", "acme-video").text_to_video is True

        _install(catalog.UserAddedModel("minimax", "acme-video", "video", endpoint="minimax-hailuo-v1-fast"))

        assert _built_endpoint_and_text_to_video("minimax", "acme-video") == ("minimax-hailuo-v1-fast", False)
        # Fast 只接受图生视频：通道能力随端点变，与模型名无关
        assert declared_builtin_video_capabilities("minimax", "acme-video").text_to_video is False

    def test_the_same_endpoint_declares_the_same_capabilities_whatever_the_model_name(self, user_added: None):
        _install(
            catalog.UserAddedModel("minimax", "relay-a", "video", endpoint="minimax-h3"),
            catalog.UserAddedModel("minimax", "MiniMax-Hailuo-2.3-Fast-relay", "video", endpoint="minimax-h3"),
        )

        assert _built_endpoint_and_text_to_video("minimax", "relay-a") == _built_endpoint_and_text_to_video(
            "minimax", "MiniMax-Hailuo-2.3-Fast-relay"
        )

    def test_a_user_added_model_without_a_recorded_endpoint_uses_the_provider_default(self, user_added: None):
        _install(catalog.UserAddedModel("minimax", "proxy/minimax-h3", "video"))

        assert _built_endpoint_and_text_to_video("minimax", "proxy/minimax-h3") == ("minimax-hailuo-v1", True)

    @pytest.mark.parametrize("provider_id", ["ark", "ark-agent-plan"])
    def test_both_ark_providers_offer_the_three_seedance_endpoints(self, provider_id: str):
        declared = PROVIDER_ENDPOINTS[(provider_id, "video")]
        assert declared.endpoints == ("ark-seedance-1", "ark-seedance-2", "ark-seedance-2-5")
        assert declared.default == "ark-seedance-2"

    def test_a_user_added_ark_model_is_built_through_the_seedance_endpoint_it_chose(self, user_added: None):
        """不透明 ID 挂哪一代端点，就按哪一代的请求结构与计量；通道声明随端点变，与模型名无关。"""
        from lib.backends.video_backends.ark import SEEDANCE_BACKENDS

        for endpoint, backend in SEEDANCE_BACKENDS.items():
            _install(catalog.UserAddedModel("ark", "ep-20261104-opaque", "video", endpoint=endpoint))
            spec = builtin_model_spec("ark", "video", "ep-20261104-opaque")
            assert (spec.endpoint, spec.registry_backend) == (endpoint, endpoint)
            assert declared_builtin_video_capabilities(
                "ark", "ep-20261104-opaque"
            ) == backend.video_capabilities_for_model("ep-20261104-opaque")

    def test_an_unrecorded_endpoint_uses_the_default_regardless_of_entry_binding(self, user_added: None):
        """没有记下端点时用默认端点；条目绑定只决定模型事实。"""
        _install(
            catalog.UserAddedModel("ark", "ep-bound", "video", entry_binding="bytedance/doubao-seedance-2.5"),
            catalog.UserAddedModel("ark", "ep-unbound", "video"),
        )

        assert builtin_model_spec("ark", "video", "ep-bound").endpoint == "ark-seedance-2"
        assert builtin_model_spec("ark", "video", "ep-unbound").endpoint == "ark-seedance-2"


class TestPrefill:
    @pytest.mark.parametrize(
        ("provider_id", "media_type", "model_id", "expected"),
        [
            ("minimax", "video", "MiniMax-H3-Preview", "minimax-h3"),
            ("minimax", "video", "S2V-01", "minimax-s2v-01"),
            ("minimax", "video", "acme-video", "minimax-hailuo-v1"),
            # 名字推出的是别的供应商的端点：落回这个供应商的默认端点
            ("grok", "video", "grok-imagine-video-2", "grok-video-classic"),
            ("grok", "video", "grok-imagine-video", "grok-video-classic"),
            ("vidu", "video", "viduq2", "vidu-video-q2"),
            ("vidu", "video", "viduq3-pro", "vidu-video-q3-pro"),
            ("kling", "video", "kling-v2-6", "kling-video-pro-audio"),
            # 只在新版 API 上提供的型号
            ("kling", "video", "kling-4.0", "kling-video-model-path"),
            ("kling", "video", "kling-4.0-flash", "kling-video-model-path"),
            ("kling", "video", "kling-3.0-turbo", "kling-video-model-path"),
            ("ark", "image", "kling-image-o2", "ark-seedream-4"),
            ("ark", "video", "doubao-seedance-2-5-260628", "ark-seedance-2-5"),
            ("ark-agent-plan", "video", "doubao-seedance-2.0-mini", "ark-seedance-2"),
            ("ark", "video", "doubao-seedance-1-5-pro-251215", "ark-seedance-1"),
            ("ark", "video", "ep-20261104-opaque", "ark-seedance-2"),
            # 百炼语音：qwen-audio-*-tts 走 SpeechSynthesizer，其余落回默认的 multimodal 端点
            ("dashscope", "audio", "qwen-audio-3.1-tts-flash", "dashscope-speech-synthesizer"),
            ("dashscope", "audio", "qwen-audio-3.0-tts-plus", "dashscope-speech-synthesizer"),
            ("dashscope", "audio", "qwen3-tts-flash", "dashscope-tts"),
            ("dashscope", "audio", "cosyvoice-v3", "dashscope-tts"),
            ("gemini-aistudio", "video", "gemini-omni-1.1-flash", "gemini-interactions-video"),
            ("gemini-aistudio", "video", "gemini-omni-1.2-flash", "gemini-interactions-video"),
            ("gemini-vertex", "video", "gemini-omni-1.2-flash", "gemini-vertex-interactions-video"),
            ("gemini-vertex", "video", "gemini-omni-1.1-flash-preview", "gemini-vertex-interactions-video"),
            ("gemini-aistudio", "video", "veo-3.1-fast-generate-001", "gemini-video"),
            ("gemini-vertex", "video", "veo-3.1-generate-001", "gemini-vertex-video"),
        ],
    )
    def test_the_prefilled_endpoint_is_inferred_by_name_within_the_provider(
        self, provider_id: str, media_type: str, model_id: str, expected: str
    ):
        assert infer_builtin_endpoint(provider_id, media_type, model_id) == expected


@pytest.mark.parametrize(
    ("provider", "model", "endpoint"),
    [
        ("ark", "doubao-seedream-3-0-t2i", "ark-seedream-3"),
        ("ark-agent-plan", "doubao-seedream-4-5", "ark-seedream-4"),
        ("dashscope", "wan2.7-image-pro", "dashscope-wan-image"),
        ("dashscope", "qwen-image-edit-plus", "dashscope-qwen-image-edit"),
        ("dashscope", "qwen-image-2.0", "dashscope-qwen-image"),
        ("agnes", "agnes-image-2.5-flash", "agnes-image-tier"),
        ("agnes", "agnes-image-2.1-flash", "agnes-image-pixels"),
    ],
)
def test_new_image_model_prefill_preserves_the_name_rule(provider: str, model: str, endpoint: str):
    assert infer_builtin_endpoint(provider, "image", model) == endpoint
