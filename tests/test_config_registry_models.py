"""Test ProviderMeta with ModelInfo structure."""

from lib.config.registry import PROVIDER_REGISTRY, ModelInfo, ProviderMeta


class TestModelInfo:
    def test_basic(self):
        m = ModelInfo(
            display_name="Test Model",
            media_type="text",
            capabilities=["text_generation"],
            default=True,
        )
        assert m.display_name == "Test Model"
        assert m.media_type == "text"
        assert m.default is True

    def test_api_model_name_defaults_none(self):
        m = ModelInfo(display_name="X", media_type="image", capabilities=["text_to_image"])
        assert m.api_model_name is None

    def test_api_model_name_only_declared_for_amphibious_aliases(self):
        # 解耦字段对存量模型零影响：仅两栖别名键显式声明 api_model_name，其余一律回退键名。
        declared = {
            f"{pid}/{mid}": minfo.api_model_name
            for pid, meta in PROVIDER_REGISTRY.items()
            for mid, minfo in meta.models.items()
            if minfo.api_model_name is not None
        }
        assert declared == {"kling/kling-v3-omni-image": "kling-v3-omni"}


class TestProviderMeta:
    def test_media_types_derived_from_models(self):
        meta = ProviderMeta(
            display_name="Test",
            description="Test provider",
            required_keys=["api_key"],
            models={
                "text-model": ModelInfo("TM", "text", ["text_generation"], default=True),
                "image-model": ModelInfo("IM", "image", ["text_to_image"], default=True),
            },
        )
        assert sorted(meta.media_types) == ["image", "text"]

    def test_capabilities_derived_from_models(self):
        meta = ProviderMeta(
            display_name="Test",
            description="Test provider",
            required_keys=["api_key"],
            models={
                "m1": ModelInfo("M1", "text", ["text_generation", "vision"]),
                "m2": ModelInfo("M2", "image", ["text_to_image"]),
            },
        )
        assert sorted(meta.capabilities) == ["text_generation", "text_to_image", "vision"]

    def test_empty_models(self):
        meta = ProviderMeta(
            display_name="T",
            description="T",
            required_keys=[],
        )
        assert meta.media_types == []
        assert meta.capabilities == []


class TestProviderRegistry:
    # vidu 仅图片+视频、kling 为仅身份注册（无 models），均跳过文本相关断言
    _TEXT_PROVIDERS = [pid for pid in PROVIDER_REGISTRY if pid not in ("vidu", "kling")]

    def test_all_providers_have_text_models(self):
        for provider_id in self._TEXT_PROVIDERS:
            meta = PROVIDER_REGISTRY[provider_id]
            text_models = [mid for mid, m in meta.models.items() if m.media_type == "text"]
            assert len(text_models) > 0, f"{provider_id} has no text models"

    def test_all_providers_have_image_models(self):
        for provider_id in ("gemini-aistudio", "gemini-vertex", "ark", "grok"):
            meta = PROVIDER_REGISTRY[provider_id]
            image_models = [mid for mid, m in meta.models.items() if m.media_type == "image"]
            assert len(image_models) > 0, f"{provider_id} has no image models"

    def test_all_providers_have_video_models(self):
        for provider_id in ("gemini-aistudio", "gemini-vertex", "ark", "grok"):
            meta = PROVIDER_REGISTRY[provider_id]
            video_models = [mid for mid, m in meta.models.items() if m.media_type == "video"]
            assert len(video_models) > 0, f"{provider_id} has no video models"

    def test_each_media_type_has_default(self):
        for provider_id, meta in PROVIDER_REGISTRY.items():
            by_type: dict[str, list[ModelInfo]] = {}
            for m in meta.models.values():
                by_type.setdefault(m.media_type, []).append(m)
            for mt, models in by_type.items():
                defaults = [m for m in models if m.default]
                assert len(defaults) == 1, f"{provider_id} has {len(defaults)} default {mt} models, expected 1"

    def test_media_types_property_includes_text(self):
        for provider_id in self._TEXT_PROVIDERS:
            meta = PROVIDER_REGISTRY[provider_id]
            assert "text" in meta.media_types, f"{provider_id} missing 'text'"

    def test_ark_video_models_include_seedance_2(self):
        meta = PROVIDER_REGISTRY["ark"]
        video_models = {mid: m for mid, m in meta.models.items() if m.media_type == "video"}
        assert len(video_models) == 4
        assert "doubao-seedance-2-0-260128" in video_models
        assert "doubao-seedance-2-0-fast-260128" in video_models
        assert "doubao-seedance-2-0-mini-260615" in video_models
        # 2.0 系列应声明 video_extend 但不声明 flex_tier
        for mid in (
            "doubao-seedance-2-0-260128",
            "doubao-seedance-2-0-fast-260128",
            "doubao-seedance-2-0-mini-260615",
        ):
            caps = video_models[mid].capabilities
            assert "video_extend" in caps
            assert "flex_tier" not in caps
        # fast 与 mini 都只支持 480p/720p，不含 1080p/4k
        assert video_models["doubao-seedance-2-0-fast-260128"].resolutions == ["480p", "720p"]
        assert video_models["doubao-seedance-2-0-mini-260615"].resolutions == ["480p", "720p"]
        # mini 接管默认视频模型，1.5 Pro 不再默认
        assert video_models["doubao-seedance-2-0-mini-260615"].default is True
        assert video_models["doubao-seedance-1-5-pro-251215"].default is False
        assert video_models["doubao-seedance-2-0-260128"].default is False
