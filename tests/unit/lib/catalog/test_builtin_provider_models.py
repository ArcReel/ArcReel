"""出厂快照里各内置供应商的模型清单与模型事实：默认模型、能力、可选时长与分辨率等对照官方文档的登记。"""

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
    # vidu 仅图片+视频、kling 仅图片+视频，均跳过文本相关断言
    _TEXT_PROVIDERS: ClassVar[list[str]] = [pid for pid in PROVIDER_REGISTRY if pid not in ("vidu", "kling")]

    def test_text_providers_have_text_models(self):
        for provider_id in self._TEXT_PROVIDERS:
            assert _models_of(provider_id, "text"), f"{provider_id} has no text models"
            assert "text" in catalog.provider_media_types(provider_id), f"{provider_id} missing 'text'"

    @pytest.mark.parametrize("media_type", ["image", "video"])
    def test_core_providers_have_image_and_video_models(self, media_type):
        for provider_id in ("gemini-aistudio", "gemini-vertex", "ark", "grok"):
            assert _models_of(provider_id, media_type), f"{provider_id} has no {media_type} models"

    def test_each_media_type_has_one_default(self):
        for provider_id in PROVIDER_REGISTRY:
            for media_type in catalog.provider_media_types(provider_id):
                defaults = [mid for mid, m in _models_of(provider_id, media_type).items() if m.default]
                assert len(defaults) == 1, f"{provider_id} has {len(defaults)} default {media_type} models"

    def test_api_model_name_only_differs_for_amphibious_aliases(self):
        # 只有两栖模型沿用的旧模型键与 API 模型名不同，其余模型键就是 API 模型名。
        declared = {f"{pid}/{mid}": m.api_model_name for pid, mid, m in _all_models() if m.api_model_name is not None}
        assert declared == {"kling/kling-v3-omni-image": "kling-v3-omni"}

    def test_video_capability_tokens_are_not_declared(self):
        # 视频模型的输入模式与音轨形态都不进能力 token，真相源是 backend 的 VideoCapabilities。
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
        """Ark Seedream 当前不传分辨率，留空 → UI 不展示下拉。"""
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
        """两侧 Veo 模型每个可选高分辨率档都声明「仅 8s」，UI 才能据此收窄时长选项。"""
        for model_id, model in _models_of(provider_id, "video").items():
            for resolution in model.resolutions:
                if resolution in ("1080p", "4k"):
                    assert model.duration_resolution_constraints.get(resolution) == [8], (
                        f"{provider_id}/{model_id} 的 {resolution} 未声明仅 8s"
                    )

    @pytest.mark.parametrize("provider_id", ["gemini-aistudio", "gemini-vertex"])
    def test_veo_declares_reference_image_durations(self, provider_id):
        """Veo 带参考图时只接受 8s，两侧全系声明，与 backend 的执行期拒绝对齐。"""
        for model_id, model in _models_of(provider_id, "video").items():
            assert model.reference_image_durations == [8], f"{provider_id}/{model_id} 参考图时长约束缺失"

    def test_veo_4k_only_where_officially_supported(self):
        """4k 仅 Veo 3.1 Standard 两侧 + AI Studio 的 Fast 支持；Lite 与 Vertex Fast 不支持。"""
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
    def test_agnes_25_models_are_listed_and_legacy_models_are_hidden(self):
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

        for model_id in ("agnes-2.0-flash", "agnes-image-2.1-flash", "agnes-video-v2.0"):
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
            # 三模态均为 3–15s 整数
            assert video_models[mid].supported_durations == list(range(3, 16))
            assert video_models[mid].resolutions == ["480p", "720p", "1080p"]
        # 默认视频模型是 1.1-i2v；1.0 三条仍在售，保留但非默认
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
        # fast 与 mini 都只支持 480p/720p，不含 1080p/4k
        assert video_models["doubao-seedance-2-0-fast-260128"].resolutions == ["480p", "720p"]
        assert video_models["doubao-seedance-2-0-mini-260615"].resolutions == ["480p", "720p"]
        # mini 是默认视频模型
        assert catalog.default_model("ark", "video") == "doubao-seedance-2-0-mini-260615"

    def test_ark_video_models_include_seedance_2_5(self):
        seedance_25 = _model("ark", "doubao-seedance-2-5-260628")
        assert seedance_25.media_type == "video"
        # 30 秒直出全展开为离散档；官方的 -1（模型自选时长）不登记
        assert seedance_25.supported_durations == list(range(4, 31))
        assert seedance_25.resolutions == ["480p", "720p"]
        # 2.0 mini 仍是默认，2.5 只是可选
        assert seedance_25.default is False

    def test_ark_agent_plan_has_no_seedance_2_5(self):
        # agent plan 套餐未纳入 2.5，清单不得先于套餐收录
        models = catalog.list_models("ark-agent-plan")
        assert not [mid for mid in models if "seedance-2.5" in mid or "seedance-2-5" in mid]
