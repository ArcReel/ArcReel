import pytest

from lib.config.registry import PROVIDER_REGISTRY, ModelInfo, ProviderMeta


def test_all_providers_registered():
    assert set(PROVIDER_REGISTRY.keys()) == {
        "gemini-aistudio",
        "gemini-vertex",
        "ark",
        "ark-agent-plan",
        "grok",
        "openai",
        "vidu",
        "dashscope",
        "minimax",
        "kling",
        "agnes",
    }


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


# 媒体 lane → 并发上限可选键：凡 provider 模型覆盖某条 lane，设置页就应能配该 lane 的并发。
_LANE_WORKER_KEY = {
    "image": "image_max_workers",
    "video": "video_max_workers",
    "audio": "audio_max_workers",
}


def test_optional_keys_cover_every_supported_lane_worker():
    """每个 provider 支持的每条媒体 lane 都须在 optional_keys 声明对应 *_max_workers。"""
    for name, meta in PROVIDER_REGISTRY.items():
        optional = set(meta.optional_keys)
        for media_type in meta.media_types:
            worker_key = _LANE_WORKER_KEY.get(media_type)
            if worker_key is None:
                continue
            assert worker_key in optional, f"{name}: 支持 {media_type} lane 但 optional_keys 未声明 {worker_key}"


def test_optional_keys_have_no_worker_for_unsupported_lane():
    """provider 不支持的 lane 不应声明其 *_max_workers，避免设置页渲染无效字段。"""
    for name, meta in PROVIDER_REGISTRY.items():
        supported_worker_keys = {_LANE_WORKER_KEY[m] for m in meta.media_types if m in _LANE_WORKER_KEY}
        for key in meta.optional_keys:
            if key in _LANE_WORKER_KEY.values():
                assert key in supported_worker_keys, f"{name}: optional_keys 含 {key} 但 provider 不支持对应 lane"


class TestModelInfoDurations:
    def test_video_models_have_supported_durations(self):
        """所有预置视频模型必须声明 supported_durations。"""
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, model_info in meta.models.items():
                if model_info.media_type == "video":
                    assert len(model_info.supported_durations) > 0, (
                        f"{provider_id}/{model_id} 是视频模型但未声明 supported_durations"
                    )

    def test_non_video_models_have_empty_durations(self):
        """非视频模型的 supported_durations 应为空列表。"""
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, model_info in meta.models.items():
                if model_info.media_type != "video":
                    assert model_info.supported_durations == [], (
                        f"{provider_id}/{model_id} 不是视频模型但有 supported_durations"
                    )

    def test_aistudio_veo_has_resolution_constraints(self):
        """AI Studio Veo 模型在 1080p 下只支持 8s。"""
        meta = PROVIDER_REGISTRY["gemini-aistudio"]
        for model_id, model_info in meta.models.items():
            if model_info.media_type == "video":
                assert "1080p" in model_info.duration_resolution_constraints
                assert model_info.duration_resolution_constraints["1080p"] == [8]

    def test_vertex_veo_has_no_resolution_constraints(self):
        """Vertex Veo 模型无分辨率约束。"""
        meta = PROVIDER_REGISTRY["gemini-vertex"]
        for model_id, model_info in meta.models.items():
            if model_info.media_type == "video":
                assert model_info.duration_resolution_constraints == {}

    def test_model_info_default_values(self):
        """ModelInfo 新字段的默认值。"""
        mi = ModelInfo(display_name="test", media_type="text", capabilities=[])
        assert mi.supported_durations == []
        assert mi.duration_resolution_constraints == {}


class TestCredentialGroups:
    """凭证「二选一」分组声明的 fail-fast 校验。"""

    def test_default_empty(self):
        meta = ProviderMeta(display_name="t", description="t", required_keys=["api_key"], secret_keys=["api_key"])
        assert meta.credential_groups == []

    def test_group_keys_must_be_subset_of_required_and_secret(self):
        with pytest.raises(ValueError, match="credential_groups"):
            ProviderMeta(
                display_name="t",
                description="t",
                required_keys=["api_key"],
                secret_keys=["api_key"],
                credential_groups=[["api_key"], ["access_key"]],
            )

    def test_valid_groups_accepted(self):
        meta = ProviderMeta(
            display_name="t",
            description="t",
            required_keys=["api_key", "access_key", "secret_key"],
            secret_keys=["api_key", "access_key", "secret_key"],
            credential_groups=[["api_key"], ["access_key", "secret_key"]],
        )
        assert meta.credential_groups == [["api_key"], ["access_key", "secret_key"]]

    def test_empty_group_rejected(self):
        with pytest.raises(ValueError, match="空分组"):
            ProviderMeta(
                display_name="t",
                description="t",
                required_keys=["api_key", "access_key", "secret_key"],
                secret_keys=["api_key", "access_key", "secret_key"],
                credential_groups=[["api_key"], []],
            )

    def test_uncovered_key_rejected(self):
        with pytest.raises(ValueError, match="未覆盖"):
            ProviderMeta(
                display_name="t",
                description="t",
                required_keys=["api_key", "access_key", "secret_key"],
                secret_keys=["api_key", "access_key", "secret_key"],
                credential_groups=[["api_key"]],
            )
