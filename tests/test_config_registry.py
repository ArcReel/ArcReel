import pytest

from lib.config.registry import (
    PROVIDER_REGISTRY,
    ModelInfo,
    ProviderMeta,
    model_audio_always_on,
    model_audio_switch_controllable,
    model_has_audio_track,
)


@pytest.mark.unit
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


@pytest.mark.unit
def test_provider_meta_fields():
    meta = PROVIDER_REGISTRY["gemini-aistudio"]
    assert isinstance(meta, ProviderMeta)
    assert meta.display_name == "AI Studio"
    assert "video" in meta.media_types
    assert "image" in meta.media_types
    assert "api_key" in meta.required_keys
    assert "api_key" in meta.secret_keys
    # 视频模型只声明与输入模式无关的特性 token（输入模式的真相源是 backend VideoCapabilities）；
    # AI Studio 的 Veo 恒有声但开关不可控，不声明 generate_audio（见 registry 的 token 语义注）。
    assert "generate_audio" not in meta.capabilities


@pytest.mark.unit
def test_ark_supports_video_and_image():
    meta = PROVIDER_REGISTRY["ark"]
    assert "video" in meta.media_types
    assert "image" in meta.media_types


@pytest.mark.unit
def test_required_keys_are_subset_of_all_keys():
    for name, meta in PROVIDER_REGISTRY.items():
        all_keys = set(meta.required_keys) | set(meta.optional_keys)
        for rk in meta.required_keys:
            assert rk in all_keys, f"{name}: required key {rk} not in all keys"


@pytest.mark.unit
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


@pytest.mark.unit
def test_optional_keys_cover_every_supported_lane_worker():
    """每个 provider 支持的每条媒体 lane 都须在 optional_keys 声明对应 *_max_workers。"""
    for name, meta in PROVIDER_REGISTRY.items():
        optional = set(meta.optional_keys)
        for media_type in meta.media_types:
            worker_key = _LANE_WORKER_KEY.get(media_type)
            if worker_key is None:
                continue
            assert worker_key in optional, f"{name}: 支持 {media_type} lane 但 optional_keys 未声明 {worker_key}"


@pytest.mark.unit
def test_optional_keys_have_no_worker_for_unsupported_lane():
    """provider 不支持的 lane 不应声明其 *_max_workers，避免设置页渲染无效字段。"""
    for name, meta in PROVIDER_REGISTRY.items():
        supported_worker_keys = {_LANE_WORKER_KEY[m] for m in meta.media_types if m in _LANE_WORKER_KEY}
        for key in meta.optional_keys:
            if key in _LANE_WORKER_KEY.values():
                assert key in supported_worker_keys, f"{name}: optional_keys 含 {key} 但 provider 不支持对应 lane"


class TestModelInfoDurations:
    @pytest.mark.unit
    def test_video_models_have_supported_durations(self):
        """所有预置视频模型必须声明 supported_durations。"""
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, model_info in meta.models.items():
                if model_info.media_type == "video":
                    assert len(model_info.supported_durations) > 0, (
                        f"{provider_id}/{model_id} 是视频模型但未声明 supported_durations"
                    )

    @pytest.mark.unit
    def test_non_video_models_have_empty_durations(self):
        """非视频模型的 supported_durations 应为空列表。"""
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, model_info in meta.models.items():
                if model_info.media_type != "video":
                    assert model_info.supported_durations == [], (
                        f"{provider_id}/{model_id} 不是视频模型但有 supported_durations"
                    )

    @pytest.mark.unit
    @pytest.mark.parametrize("provider_id", ["gemini-aistudio", "gemini-vertex"])
    def test_veo_declares_high_resolution_constraints(self, provider_id):
        """两侧 Veo 模型每个可选高分辨率档都声明「仅 8s」，UI 才能据此收窄时长选项。"""
        meta = PROVIDER_REGISTRY[provider_id]
        for model_id, model_info in meta.models.items():
            if model_info.media_type != "video":
                continue
            for resolution in model_info.resolutions:
                if resolution in ("1080p", "4k"):
                    assert model_info.duration_resolution_constraints.get(resolution) == [8], (
                        f"{provider_id}/{model_id} 的 {resolution} 未声明仅 8s"
                    )

    @pytest.mark.unit
    @pytest.mark.parametrize("provider_id", ["gemini-aistudio", "gemini-vertex"])
    def test_veo_declares_reference_image_durations(self, provider_id):
        """Veo 带参考图时只接受 8s，两侧全系声明，与 backend 的执行期拒绝对齐。"""
        meta = PROVIDER_REGISTRY[provider_id]
        for model_id, model_info in meta.models.items():
            if model_info.media_type == "video":
                assert model_info.reference_image_durations == [8], f"{provider_id}/{model_id} 参考图时长约束缺失"

    @pytest.mark.unit
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
            resolutions = PROVIDER_REGISTRY[provider_id].models[model_id].resolutions
            assert ("4k" in resolutions) is supported, f"{provider_id}/{model_id} 的 4k 支持性与官方文档不符"

    @pytest.mark.unit
    def test_model_info_default_values(self):
        """ModelInfo 新字段的默认值。"""
        mi = ModelInfo(display_name="test", media_type="text", capabilities=[])
        assert mi.supported_durations == []
        assert mi.duration_resolution_constraints == {}
        assert mi.reference_image_durations == []


class TestCredentialGroups:
    """凭证「二选一」分组声明的 fail-fast 校验。"""

    @pytest.mark.unit
    def test_default_empty(self):
        meta = ProviderMeta(display_name="t", description="t", required_keys=["api_key"], secret_keys=["api_key"])
        assert meta.credential_groups == []

    @pytest.mark.unit
    def test_group_keys_must_be_subset_of_required_and_secret(self):
        with pytest.raises(ValueError, match="credential_groups"):
            ProviderMeta(
                display_name="t",
                description="t",
                required_keys=["api_key"],
                secret_keys=["api_key"],
                credential_groups=[["api_key"], ["access_key"]],
            )

    @pytest.mark.unit
    def test_valid_groups_accepted(self):
        meta = ProviderMeta(
            display_name="t",
            description="t",
            required_keys=["api_key", "access_key", "secret_key"],
            secret_keys=["api_key", "access_key", "secret_key"],
            credential_groups=[["api_key"], ["access_key", "secret_key"]],
        )
        assert meta.credential_groups == [["api_key"], ["access_key", "secret_key"]]

    @pytest.mark.unit
    def test_empty_group_rejected(self):
        with pytest.raises(ValueError, match="空分组"):
            ProviderMeta(
                display_name="t",
                description="t",
                required_keys=["api_key", "access_key", "secret_key"],
                secret_keys=["api_key", "access_key", "secret_key"],
                credential_groups=[["api_key"], []],
            )

    @pytest.mark.unit
    def test_uncovered_key_rejected(self):
        with pytest.raises(ValueError, match="未覆盖"):
            ProviderMeta(
                display_name="t",
                description="t",
                required_keys=["api_key", "access_key", "secret_key"],
                secret_keys=["api_key", "access_key", "secret_key"],
                credential_groups=[["api_key"]],
            )


class TestFullyCoveredCredentialGroups:
    """ProviderMeta.fully_covered_credential_groups —— 切组判定的核心真值表。"""

    def _kling_meta(self) -> ProviderMeta:
        return ProviderMeta(
            display_name="t",
            description="t",
            required_keys=["api_key", "access_key", "secret_key"],
            secret_keys=["api_key", "access_key", "secret_key"],
            credential_groups=[["api_key"], ["access_key", "secret_key"]],
        )

    @pytest.mark.unit
    def test_no_groups_declared_always_empty(self):
        meta = ProviderMeta(display_name="t", description="t", required_keys=["api_key"], secret_keys=["api_key"])
        assert meta.fully_covered_credential_groups({"api_key": "k"}) == []

    @pytest.mark.unit
    def test_single_group_fully_submitted(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"api_key": "k"}) == [["api_key"]]

    @pytest.mark.unit
    def test_dual_key_group_fully_submitted(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"access_key": "ak", "secret_key": "sk"}) == [
            ["access_key", "secret_key"]
        ]

    @pytest.mark.unit
    def test_dual_key_group_partially_submitted_not_matched(self):
        """只提交组内一个 key（如仅轮换 secret_key）不算完整覆盖该组。"""
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"secret_key": "sk"}) == []

    @pytest.mark.unit
    def test_both_groups_fully_submitted(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"api_key": "k", "access_key": "ak", "secret_key": "sk"}) == [
            ["api_key"],
            ["access_key", "secret_key"],
        ]

    @pytest.mark.unit
    def test_empty_string_not_counted_as_covering(self):
        """空字符串视同未提交，不满足组覆盖。"""
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"api_key": ""}) == []

    @pytest.mark.unit
    def test_none_not_counted_as_covering(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({"api_key": None}) == []

    @pytest.mark.unit
    def test_nothing_submitted(self):
        meta = self._kling_meta()
        assert meta.fully_covered_credential_groups({}) == []


@pytest.mark.unit
class TestModelHasAudioTrack:
    """model_has_audio_track —— voice_consistency 派生所依据的音轨判定。"""

    def _model(self, provider_id: str, model_id: str) -> ModelInfo:
        return PROVIDER_REGISTRY[provider_id].models[model_id]

    def test_ark_seedance_declares_token(self):
        """声明了 generate_audio token 的模型直接判定有音轨。"""
        assert model_has_audio_track("ark", self._model("ark", "doubao-seedance-2-0-260128")) is True

    def test_aistudio_veo_always_audible_without_token(self):
        """AI Studio Veo 恒有声但请求参数不可控，未声明 token 也须判定有音轨（不得直推无声）。"""
        model = self._model("gemini-aistudio", "veo-3.1-generate-preview")
        assert "generate_audio" not in model.capabilities
        assert model_has_audio_track("gemini-aistudio", model) is True

    def test_grok_imagine_always_audible_without_token(self):
        """Grok Imagine 同 AI Studio Veo：恒有声、开关不可控、不补 token，仍判定有音轨。"""
        model = self._model("grok", "grok-imagine-video")
        assert "generate_audio" not in model.capabilities
        assert model_has_audio_track("grok", model) is True

    def test_dashscope_video_always_audible_without_token(self):
        """DashScope 视频全家族恒有声：_build_payload 不下传音频开关，故不声明 token，
        由恒有声例外表判定有音轨。"""
        for model_id in (
            "happyhorse-1.1-i2v",
            "happyhorse-1.1-t2v",
            "happyhorse-1.1-r2v",
            "happyhorse-1.0-i2v",
            "happyhorse-1.0-t2v",
            "happyhorse-1.0-r2v",
            "wan2.7-i2v",
            "wan2.7-t2v",
            "wan2.7-r2v",
        ):
            model = self._model("dashscope", model_id)
            assert "generate_audio" not in model.capabilities
            assert model_has_audio_track("dashscope", model) is True

    def test_dashscope_image_model_not_audible(self):
        """恒有声例外表是 provider 级，但图像 model 仍恒 False——不因同 provider 被误判。"""
        model = self._model("dashscope", "wan2.7-image")
        assert model.media_type != "video"
        assert model_has_audio_track("dashscope", model) is False

    def test_sora_2_declares_token(self):
        """Sora 2 声明修正随本票落地：目录已补 generate_audio（官方原生含对话音轨）。"""
        model = self._model("openai", "sora-2")
        assert "generate_audio" in model.capabilities
        assert model_has_audio_track("openai", model) is True

    def test_kling_audio_capable_models_declare_token(self):
        """可灵支持音画同出的三档均声明 token；能力地图的「声音控制（人声）」列是指定音色通道，
        不是音频能力本身，不据此判定音轨。"""
        for model_id in ("kling-v2-6", "kling-v3", "kling-v3-omni"):
            model = self._model("kling", model_id)
            assert "generate_audio" in model.capabilities
            assert model_has_audio_track("kling", model) is True

    def test_kling_silent_models_omit_token(self):
        """kling-v2-5-turbo 无音频开关、kling-video-o1 只能保留参考视频原声（ArcReel 不下发参考
        视频）——两档均不声明 token。"""
        for model_id in ("kling-v2-5-turbo", "kling-video-o1"):
            model = self._model("kling", model_id)
            assert "generate_audio" not in model.capabilities
            assert model_has_audio_track("kling", model) is False

    def test_minimax_true_silent(self):
        """MiniMax 未声明 token 且不在恒有声例外表内——真无声模型。"""
        model = self._model("minimax", "MiniMax-Hailuo-2.3")
        assert model_has_audio_track("minimax", model) is False

    def test_agnes_true_silent(self):
        """Agnes 同 MiniMax：真无声模型。"""
        model = self._model("agnes", "agnes-video-v2.0")
        assert model_has_audio_track("agnes", model) is False

    def test_non_video_model_always_false(self):
        """非视频 model（如文本模型）音轨判定无意义，恒 False，即便所属 provider 在恒有声例外表内。"""
        model = self._model("gemini-aistudio", "gemini-3-flash-preview")
        assert model.media_type != "video"
        assert model_has_audio_track("gemini-aistudio", model) is False


@pytest.mark.unit
class TestAudioSwitchControllable:
    """音轨的第二位描述：开关是否可控，及由两位合成的「恒有声」判定。"""

    def _model(self, provider_id: str, model_id: str) -> ModelInfo:
        return PROVIDER_REGISTRY[provider_id].models[model_id]

    def test_token_declared_is_controllable(self):
        model = self._model("ark", "doubao-seedance-2-0-260128")
        assert model_audio_switch_controllable(model) is True
        assert model_audio_always_on("ark", model) is False

    @pytest.mark.parametrize(
        ("provider_id", "model_id"),
        [
            ("gemini-aistudio", "veo-3.1-generate-preview"),
            ("grok", "grok-imagine-video"),
            ("dashscope", "wan2.7-i2v"),
        ],
    )
    def test_always_audible_families_are_not_controllable(self, provider_id: str, model_id: str):
        """恒有声家族：请求里没有开关可下发，故关闭意图必然落空。"""
        model = self._model(provider_id, model_id)
        assert model_audio_switch_controllable(model) is False
        assert model_audio_always_on(provider_id, model) is True

    def test_silent_model_is_neither_controllable_nor_always_on(self):
        model = self._model("minimax", "MiniMax-Hailuo-2.3")
        assert model_audio_switch_controllable(model) is False
        assert model_audio_always_on("minimax", model) is False

    def test_non_video_model_is_not_controllable(self):
        model = self._model("dashscope", "wan2.7-image")
        assert model_audio_switch_controllable(model) is False
        assert model_audio_always_on("dashscope", model) is False
