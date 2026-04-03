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
    def test_all_providers_have_text_models(self):
        for provider_id, meta in PROVIDER_REGISTRY.items():
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
        for provider_id, meta in PROVIDER_REGISTRY.items():
            assert "text" in meta.media_types, f"{provider_id} missing 'text'"

    def test_ark_video_models_include_seedance_2(self):
        meta = PROVIDER_REGISTRY["ark"]
        video_models = {mid: m for mid, m in meta.models.items() if m.media_type == "video"}
        assert len(video_models) == 3
        assert "doubao-seedance-2-0-260128" in video_models
        assert "doubao-seedance-2-0-fast-260128" in video_models
        # 2.0 系列应声明 video_extend 但不声明 flex_tier
        for mid in ("doubao-seedance-2-0-260128", "doubao-seedance-2-0-fast-260128"):
            caps = video_models[mid].capabilities
            assert "video_extend" in caps
            assert "flex_tier" not in caps
        # 1.5 Pro 仍然是默认模型
        assert video_models["doubao-seedance-1-5-pro-251215"].default is True
        assert video_models["doubao-seedance-2-0-260128"].default is False
