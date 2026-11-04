"""内置供应商的供应商模型经调用端点装配：每个「供应商 + 媒体类型」声明可选端点与默认端点，模型挂哪个端点决定请求。"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

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


def _built_definition(provider_id: str, model_id: str):
    backend = builtin_model_spec(provider_id, "video", model_id).build_backend(
        LoadedConfig(
            credentials={"api_key": "sk-test"}, provider_meta=PROVIDER_REGISTRY[provider_id], rate_limiter=None
        ),
        model_id,
    )
    assert backend.model == model_id
    return backend._definition


class TestDeclarations:
    def test_every_builtin_provider_declares_endpoints_for_each_media_type_it_lists(self):
        for provider_id in PROVIDER_REGISTRY:
            for media_type in catalog.provider_media_types(provider_id):
                declared = PROVIDER_ENDPOINTS[(provider_id, media_type)]
                assert declared.default in declared.endpoints
        validate_endpoint_declarations()

    def test_a_default_outside_the_options_is_rejected(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setitem(
            PROVIDER_ENDPOINTS, ("ark", "video"), ProviderEndpoints("ark", "video", ("ark-seedance-2",), "kling-video")
        )
        with pytest.raises(ValueError, match="default endpoint 'kling-video'"):
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
    def test_official_minimax_models_keep_choosing_their_endpoint_by_name(self):
        assert _built_definition("minimax", "MiniMax-H3") is ENDPOINT_REGISTRY["minimax-h3"].definition
        assert (
            _built_definition("minimax", "MiniMax-Hailuo-2.3-Fast")
            is ENDPOINT_REGISTRY["minimax-hailuo-v1-fast"].definition
        )

    def test_a_user_added_model_is_built_through_the_endpoint_it_chose(self, user_added: None):
        _install(catalog.UserAddedModel("minimax", "acme-video", "video", endpoint="minimax-h3"))

        assert _built_definition("minimax", "acme-video") is ENDPOINT_REGISTRY["minimax-h3"].definition
        assert declared_builtin_video_capabilities("minimax", "acme-video").text_to_video is True

        _install(catalog.UserAddedModel("minimax", "acme-video", "video", endpoint="minimax-hailuo-v1-fast"))

        assert _built_definition("minimax", "acme-video") is ENDPOINT_REGISTRY["minimax-hailuo-v1-fast"].definition
        # Fast 只接受图生视频：通道能力随端点变，与模型名无关
        assert declared_builtin_video_capabilities("minimax", "acme-video").text_to_video is False

    def test_the_same_endpoint_sends_the_same_request_shape_whatever_the_model_name(self, user_added: None):
        _install(
            catalog.UserAddedModel("minimax", "relay-a", "video", endpoint="minimax-h3"),
            catalog.UserAddedModel("minimax", "MiniMax-Hailuo-2.3-Fast-relay", "video", endpoint="minimax-h3"),
        )

        assert _built_definition("minimax", "relay-a") is _built_definition("minimax", "MiniMax-Hailuo-2.3-Fast-relay")

    def test_a_user_added_model_without_a_recorded_endpoint_keeps_the_name_rule(self, user_added: None):
        _install(catalog.UserAddedModel("minimax", "proxy/minimax-h3", "video"))

        assert _built_definition("minimax", "proxy/minimax-h3") is ENDPOINT_REGISTRY["minimax-h3"].definition

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

    def test_a_user_added_ark_model_without_a_recorded_endpoint_follows_its_bound_entry(self, user_added: None):
        """没有记下端点时与官方收录的模型同一规则：先看所对应条目的名字（含条目绑定），再看 API 模型名。"""
        _install(
            catalog.UserAddedModel("ark", "ep-bound", "video", entry_binding="bytedance/doubao-seedance-2.5"),
            catalog.UserAddedModel("ark", "ep-unbound", "video"),
        )

        assert builtin_model_spec("ark", "video", "ep-bound").endpoint == "ark-seedance-2-5"
        assert builtin_model_spec("ark", "video", "ep-unbound").endpoint == "ark-seedance-1"


class TestPrefill:
    @pytest.mark.parametrize(
        ("provider_id", "media_type", "model_id", "expected"),
        [
            ("minimax", "video", "MiniMax-H3-Preview", "minimax-h3"),
            ("minimax", "video", "S2V-01", "minimax-s2v-01"),
            ("minimax", "video", "acme-video", "minimax-hailuo-v1"),
            # 名字推出的是别的供应商的端点：落回这个供应商的默认端点
            ("grok", "video", "grok-imagine-video-2", "grok-video"),
            ("ark", "image", "kling-image-o2", "ark-seedream-4"),
            ("ark", "video", "doubao-seedance-2-5-260628", "ark-seedance-2-5"),
            ("ark-agent-plan", "video", "doubao-seedance-2.0-mini", "ark-seedance-2"),
            ("ark", "video", "doubao-seedance-1-5-pro-251215", "ark-seedance-1"),
            ("ark", "video", "ep-20261104-opaque", "ark-seedance-2"),
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
def test_official_image_selection_and_new_model_prefill_preserve_the_old_request_contract(
    provider: str, model: str, endpoint: str
):
    from lib.backends.backend_assembly.specs import official_model_endpoint

    assert official_model_endpoint(provider, "image", model) == endpoint
    assert infer_builtin_endpoint(provider, "image", model) == endpoint
