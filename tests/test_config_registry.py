from lib.config.registry import PROVIDER_REGISTRY, ModelInfo, ProviderMeta


def test_all_providers_registered():
    assert set(PROVIDER_REGISTRY.keys()) == {"gemini-aistudio", "gemini-vertex", "ark", "grok", "openai"}


def test_provider_meta_fields():
    meta = PROVIDER_REGISTRY["gemini-aistudio"]
    assert isinstance(meta, ProviderMeta)
    assert meta.display_name == "AI Studio"
    assert "video" in meta.media_types
    assert "image" in meta.media_types
    assert "api_key" in meta.required_keys
    assert "api_key" in meta.secret_keys
    assert "text_to_video" in meta.capabilities


def test_ark_supports_video_and_image():
    meta = PROVIDER_REGISTRY["ark"]
    assert "video" in meta.media_types
    assert "image" in meta.media_types


def test_required_keys_are_subset_of_all_keys():
    for name, meta in PROVIDER_REGISTRY.items():
        all_keys = set(meta.required_keys) | set(meta.optional_keys)
        for rk in meta.required_keys:
            assert rk in all_keys, f"{name}: required key {rk} not in all keys"


def test_secret_keys_are_subset_of_required_or_optional():
    for name, meta in PROVIDER_REGISTRY.items():
        all_keys = set(meta.required_keys) | set(meta.optional_keys)
        for sk in meta.secret_keys:
            assert sk in all_keys, f"{name}: secret key {sk} not in all keys"


class TestModelInfoDurations:
    def test_video_models_have_supported_durations(self):
        """All built-in video models must declare supported_durations."""
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, model_info in meta.models.items():
                if model_info.media_type == "video":
                    assert len(model_info.supported_durations) > 0, (
                        f"{provider_id}/{model_id} is a video model but did not declare supported_durations"
                    )

    def test_non_video_models_have_empty_durations(self):
        """Non-video models should have empty supported_durations list."""
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, model_info in meta.models.items():
                if model_info.media_type != "video":
                    assert model_info.supported_durations == [], (
                        f"{provider_id}/{model_id} is not a video model but has supported_durations"
                    )

    def test_aistudio_veo_has_resolution_constraints(self):
        """AI Studio Veo models only support 8s at 1080p."""
        meta = PROVIDER_REGISTRY["gemini-aistudio"]
        for model_id, model_info in meta.models.items():
            if model_info.media_type == "video":
                assert "1080p" in model_info.duration_resolution_constraints
                assert model_info.duration_resolution_constraints["1080p"] == [8]

    def test_vertex_veo_has_no_resolution_constraints(self):
        """Vertex Veo models have no resolution constraints."""
        meta = PROVIDER_REGISTRY["gemini-vertex"]
        for model_id, model_info in meta.models.items():
            if model_info.media_type == "video":
                assert model_info.duration_resolution_constraints == {}

    def test_model_info_default_values(self):
        """Default values for new ModelInfo fields."""
        mi = ModelInfo(display_name="test", media_type="text", capabilities=[])
        assert mi.supported_durations == []
        assert mi.duration_resolution_constraints == {}
