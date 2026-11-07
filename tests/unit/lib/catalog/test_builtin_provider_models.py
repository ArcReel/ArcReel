"""Shipped built-in model lists/facts: defaults, capabilities, durations, and resolutions registered against official docs."""

from typing import ClassVar

import pytest

from lib.catalog import query as catalog
from lib.catalog.query import CatalogModel
from lib.config.registry import PROVIDER_REGISTRY


def _all_models() -> list[tuple[str, str, CatalogModel]]:
    return [
        (provider_id, model_id, model)
        for provider_id in PROVIDER_REGISTRY
        for model_id, model in catalog.list_models(provider_id).items()
    ]


def _model(provider_id: str, model_id: str) -> CatalogModel:
    model = catalog.get_model(provider_id, model_id)
    assert model is not None, f"{provider_id}/{model_id} 不在目录中"
    return model


def _models_of(provider_id: str, media_type: str) -> dict[str, CatalogModel]:
    return {mid: m for mid, m in catalog.list_models(provider_id).items() if m.media_type == media_type}


class TestListings:
    # vidu and kling support images/videos only; skip text assertions for both.
    _TEXT_PROVIDERS: ClassVar[list[str]] = [pid for pid in PROVIDER_REGISTRY if pid not in ("vidu", "kling")]

    def test_text_providers_have_text_models(self):
        for provider_id in self._TEXT_PROVIDERS:
            assert _models_of(provider_id, "text"), f"{provider_id} has no text models"
            assert "text" in catalog.provider_media_types(provider_id), f"{provider_id} missing 'text'"

    @pytest.mark.parametrize("media_type", ["image", "video"])
    def test_core_providers_have_image_and_video_models(self, media_type):
        for provider_id in ("gemini-aistudio", "gemini-vertex", "ark", "grok"):
            assert _models_of(provider_id, media_type), f"{provider_id} has no {media_type} models"

    def test_declared_defaults_are_callable_and_other_media_types_have_no_default(self):
        for provider_id, listing in catalog.factory_snapshot().builtin_providers.items():
            for media_type in catalog.provider_media_types(provider_id):
                declared = listing.defaults.get(media_type)
                model = catalog.get_model(provider_id, declared) if declared is not None else None
                expected = declared if model is not None and not model.hidden else None
                assert catalog.default_model(provider_id, media_type) == expected

    def test_api_model_name_only_differs_for_amphibious_aliases(self):
        # Only multimodal models retain legacy keys differing from API names; all other keys equal API names.
        declared = {f"{pid}/{mid}": m.api_model_name for pid, mid, m in _all_models() if m.api_model_name is not None}
        assert declared == {"kling/kling-v3-omni-image": "kling-v3-omni"}

    def test_video_capability_tokens_are_not_declared(self):
        # Video input modes/audio forms are excluded from capability tokens; backend VideoCapabilities is authoritative.
        assert "generate_audio" not in catalog.provider_capabilities("gemini-aistudio")


class TestTextModels:
    @pytest.mark.parametrize(
        ("provider_id", "model_id"),
        [
            ("gemini-aistudio", "gemini-3.1-flash-lite"),
            ("gemini-vertex", "gemini-3.1-flash-lite"),
            ("dashscope", "qwen3.6-plus"),
            ("dashscope", "qwen3.6-flash"),
        ],
    )
    def test_multimodal_text_models_declare_vision(self, provider_id, model_id):
        assert "vision" in _model(provider_id, model_id).capabilities

    @pytest.mark.parametrize("provider_id", ["gemini-aistudio", "gemini-vertex"])
    def test_retired_flash_lite_preview_is_absent(self, provider_id):
        models = catalog.list_models(provider_id)
        assert "gemini-3.1-flash-lite" in models
        assert "gemini-3.1-flash-lite-preview" not in models

    def test_text_generation_models_register_max_output_tokens(self):
        unregistered = [
            f"{pid}/{mid}"
            for pid, mid, m in _all_models()
            if "text_generation" in m.capabilities and not (m.max_output_tokens and m.max_output_tokens > 0)
        ]
        assert unregistered == [], f"带 text_generation 能力的内置模型须登记最大输出长度：{unregistered}"

    def test_dashscope_qwen_long_does_not_declare_structured_output(self):
        assert "structured_output" not in _model("dashscope", "qwen-long").capabilities

    @pytest.mark.parametrize("model_id", ["qwen-plus", "qwen3.6-plus", "qwen3-max", "qwen3.7-max", "qwen3.6-flash"])
    def test_dashscope_native_structured_models_declare_structured_output(self, model_id):
        assert "structured_output" in _model("dashscope", model_id).capabilities


class TestResolutions:
    def test_image_and_video_models_have_resolutions(self):
        missing = [
            f"{pid}/{mid}"
            for pid, mid, m in _all_models()
            if m.media_type in ("image", "video")
            and not m.resolutions
            and not (pid in ("ark", "ark-agent-plan") and mid.startswith("doubao-seedream"))
        ]
        assert missing == [], f"以下 image/video 模型缺少 resolutions: {missing}"

    def test_text_models_have_no_resolutions(self):
        for pid, mid, m in _all_models():
            if m.media_type == "text":
                assert m.resolutions == [], f"{pid}/{mid}: text 模型不应声明 resolutions"

    def test_ark_seedream_image_resolutions_empty(self):
        """Ark Seedream currently sends no resolution; unset means no UI dropdown."""
        for pid in ("ark", "ark-agent-plan"):
            for mid, m in _models_of(pid, "image").items():
                assert m.resolutions == [], f"{pid}/{mid}: Ark Seedream 应留空"


class TestVideoDurations:
    def test_video_models_have_supported_durations(self):
        for pid, mid, m in _all_models():
            if m.media_type == "video":
                assert m.supported_durations, f"{pid}/{mid} 是视频模型但未声明 supported_durations"
            else:
                assert m.supported_durations == [], f"{pid}/{mid} 不是视频模型但有 supported_durations"

    @pytest.mark.parametrize("provider_id", ["gemini-aistudio", "gemini-vertex"])
    def test_veo_declares_high_resolution_constraints(self, provider_id):
        """Both Veo providers declare 8s-only for every optional high-resolution tier, enabling UI duration narrowing."""
        for model_id, model in _models_of(provider_id, "video").items():
            for resolution in model.resolutions:
                if resolution in ("1080p", "4k"):
                    assert model.duration_resolution_constraints.get(resolution) == [8], (
                        f"{provider_id}/{model_id} 的 {resolution} 未声明仅 8s"
                    )

    @pytest.mark.parametrize("provider_id", ["gemini-aistudio", "gemini-vertex"])
    def test_veo_declares_reference_image_durations(self, provider_id):
        """Reference-image Veo accepts only 8s; both providers declare this across all models, matching runtime rejection."""
        for model_id, model in _models_of(provider_id, "video").items():
            assert model.reference_image_durations == [8], f"{provider_id}/{model_id} 参考图时长约束缺失"

    def test_veo_4k_only_where_officially_supported(self):
        """4k is supported by both Veo 3.1 Standard providers and AI Studio Fast, not Lite or Vertex Fast."""
        expected_4k = {
            ("gemini-aistudio", "veo-3.1-generate-preview"): True,
            ("gemini-aistudio", "veo-3.1-fast-generate-preview"): True,
            ("gemini-aistudio", "veo-3.1-lite-generate-preview"): False,
            ("gemini-vertex", "veo-3.1-generate-001"): True,
            ("gemini-vertex", "veo-3.1-fast-generate-001"): False,
        }
        for (provider_id, model_id), supported in expected_4k.items():
            resolutions = _model(provider_id, model_id).resolutions
            assert ("4k" in resolutions) is supported, f"{provider_id}/{model_id} 的 4k 支持性与官方文档不符"


class TestProviderListings:
    def test_agnes_models_and_their_declared_lifecycle_are_listed(self):
        models = catalog.list_models("agnes")
        for model_id in (
            "agnes-3.0-flash",
            "agnes-2.5-flash",
            "agnes-2.5-pro",
            "agnes-image-2.5-flash",
            "agnes-video-2.5",
            "agnes-video-2.5-flash",
        ):
            assert model_id in models
        assert "agnes-image-2.0-flash" not in models

        assert models["agnes-image-2.1-flash"].hidden is False
        for model_id in ("agnes-2.0-flash", "agnes-video-v2.0"):
            assert models[model_id].hidden is True

        assert models["agnes-image-2.5-flash"].resolutions == ["1K", "2K", "3K", "4K"]
        assert models["agnes-image-2.1-flash"].resolutions == ["1K", "2K"]
        assert models["agnes-video-2.5-flash"].supported_durations == list(range(4, 13))
        assert models["agnes-video-2.5-flash"].resolutions == ["720p"]
        assert models["agnes-video-2.5"].supported_durations == list(range(4, 13))
        assert models["agnes-video-2.5"].resolutions == ["720p", "1080p", "2K"]

        assert catalog.default_model("agnes", "text") == "agnes-3.0-flash"
        assert catalog.default_model("agnes", "image") == "agnes-image-2.5-flash"
        assert catalog.default_model("agnes", "video") == "agnes-video-2.5-flash"

    def test_dashscope_video_models_include_happyhorse_11(self):
        video_models = _models_of("dashscope", "video")
        for mid in ("happyhorse-1.1-t2v", "happyhorse-1.1-i2v", "happyhorse-1.1-r2v"):
            # All three modalities use integer 3–15s.
            assert video_models[mid].supported_durations == list(range(3, 16))
            assert video_models[mid].resolutions == ["480p", "720p", "1080p"]
        # Default video is 1.1-i2v; all three 1.0 models remain available, nondefault.
        assert catalog.default_model("dashscope", "video") == "happyhorse-1.1-i2v"
        for mid in ("happyhorse-1.0-t2v", "happyhorse-1.0-i2v", "happyhorse-1.0-r2v"):
            assert video_models[mid].resolutions == ["720p", "1080p"]

    def test_dashscope_video_models_include_wan3(self):
        wan3 = _model("dashscope", "wan3.0-video")
        assert wan3.media_type == "video"
        assert wan3.supported_durations == list(range(2, 31))
        assert wan3.resolutions == ["480p", "720p", "1080p"]
        assert wan3.default is False

    def test_ark_video_models_include_seedance_2(self):
        video_models = _models_of("ark", "video")
        assert len(video_models) == 4
        for mid in ("doubao-seedance-2-0-260128", "doubao-seedance-2-0-fast-260128", "doubao-seedance-2-0-mini-260615"):
            assert mid in video_models
        # fast and mini support only 480p/720p, excluding 1080p/4k.
        assert video_models["doubao-seedance-2-0-fast-260128"].resolutions == ["480p", "720p"]
        assert video_models["doubao-seedance-2-0-mini-260615"].resolutions == ["480p", "720p"]
        # mini is the default video model.
        assert catalog.default_model("ark", "video") == "doubao-seedance-2-0-mini-260615"

    def test_ark_video_models_include_seedance_2_5(self):
        seedance_25 = _model("ark", "doubao-seedance-2-5-260628")
        assert seedance_25.media_type == "video"
        # Expand 30-second direct output to discrete options; omit official -1 automatic duration.
        assert seedance_25.supported_durations == list(range(4, 31))
        assert seedance_25.resolutions == ["480p", "720p", "1080p"]
        # 2.0 mini remains default; 2.5 is optional only.
        assert seedance_25.default is False

    def test_ark_agent_plan_includes_the_declared_seedance_2_5_model(self):
        model = _model("ark-agent-plan", "doubao-seedance-2.5")
        assert model.supported_durations == list(range(4, 31))
