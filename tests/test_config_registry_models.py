"""Test ProviderMeta with ModelInfo structure."""

import pytest

from lib.config.registry import PROVIDER_REGISTRY, ModelInfo, ProviderMeta

pytestmark = pytest.mark.unit


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

    def test_default_concurrency_valid_declaration(self):
        meta = ProviderMeta(
            display_name="T",
            description="T",
            required_keys=[],
            default_concurrency={"video": 1, "image": 5},
        )
        assert meta.default_concurrency == {"video": 1, "image": 5}

    def test_default_concurrency_for_unsupported_lane_is_allowed(self):
        # 声明合法 lane 名即便该 provider 不支持该 media_type 也无害（投影期再归零），不报错
        meta = ProviderMeta(
            display_name="T",
            description="T",
            required_keys=[],
            models={"vid": ModelInfo("V", "video", [])},
            default_concurrency={"image": 2},
        )
        assert meta.default_concurrency == {"image": 2}

    def test_default_concurrency_unknown_lane_raises(self):
        with pytest.raises(ValueError, match="未知 lane"):
            ProviderMeta(
                display_name="T",
                description="T",
                required_keys=[],
                default_concurrency={"vidoe": 1},
            )

    # True 是 int 子类、值等于 1，须显式拦截，否则配置笔误 default_concurrency={"video": True}
    # 会被静默当成并发 1。
    @pytest.mark.parametrize("bad_limit", [0, -1, "1", 3.0, True])
    def test_default_concurrency_non_positive_int_raises(self, bad_limit):
        with pytest.raises(ValueError, match=">=1 的整数"):
            ProviderMeta(
                display_name="T",
                description="T",
                required_keys=[],
                default_concurrency={"video": bad_limit},
            )


class TestProviderRegistry:
    # vidu 仅图片+视频、kling 为仅身份注册（无 models）、runware 仅图片、croco/doubao 无文本，均跳过文本相关断言
    _TEXT_PROVIDERS = [pid for pid in PROVIDER_REGISTRY if pid not in ("vidu", "kling", "runware", "croco", "doubao")]

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

    def test_dashscope_video_models_include_happyhorse_11(self):
        meta = PROVIDER_REGISTRY["dashscope"]
        video_models = {mid: m for mid, m in meta.models.items() if m.media_type == "video"}
        for mid in ("happyhorse-1.1-t2v", "happyhorse-1.1-i2v", "happyhorse-1.1-r2v"):
            assert mid in video_models
            # supported_durations 未登记即 fail loud（ADR 0018），三模态均为 3–15s 整数
            assert video_models[mid].supported_durations == list(range(3, 16))
            assert video_models[mid].resolutions == ["480p", "720p", "1080p"]
        # 默认视频模型是 1.1-i2v；1.0 三条仍在售，保留登记但非默认
        assert video_models["happyhorse-1.1-i2v"].default is True
        assert video_models["happyhorse-1.0-i2v"].default is False
        for mid in ("happyhorse-1.0-t2v", "happyhorse-1.0-i2v", "happyhorse-1.0-r2v"):
            assert video_models[mid].resolutions == ["720p", "1080p"]

    def test_dashscope_video_models_include_wan3(self):
        meta = PROVIDER_REGISTRY["dashscope"]
        wan3 = meta.models["wan3.0-video"]
        assert wan3.media_type == "video"
        assert wan3.supported_durations == list(range(2, 31))
        assert wan3.resolutions == ["480p", "720p", "1080p"]
        # 单模型通吃三条路径，音轨由请求参数控制 —— 与同门恒有声型号相反
        assert "generate_audio" in wan3.capabilities
        assert wan3.audio_always_on is False
        assert wan3.default is False

    def test_dashscope_declares_wan3_base_url_key(self):
        # wan3 专用 maas 域名单列一键：并进通用 base_url 会让其余型号也跟着改域名
        meta = PROVIDER_REGISTRY["dashscope"]
        assert "wan3_base_url" in meta.optional_keys
        assert "base_url" in meta.optional_keys

    def test_ark_video_models_include_seedance_2(self):
        meta = PROVIDER_REGISTRY["ark"]
        video_models = {mid: m for mid, m in meta.models.items() if m.media_type == "video"}
        assert len(video_models) == 5
        assert "doubao-seedance-2-0-260128" in video_models
        assert "doubao-seedance-2-0-fast-260128" in video_models
        assert "doubao-seedance-2-0-mini-260615" in video_models
        # 2.0 系列音轨开关可控
        for mid in (
            "doubao-seedance-2-0-260128",
            "doubao-seedance-2-0-fast-260128",
            "doubao-seedance-2-0-mini-260615",
        ):
            assert "generate_audio" in video_models[mid].capabilities
        # fast 与 mini 都只支持 480p/720p，不含 1080p/4k
        assert video_models["doubao-seedance-2-0-fast-260128"].resolutions == ["480p", "720p"]
        assert video_models["doubao-seedance-2-0-mini-260615"].resolutions == ["480p", "720p"]
        # mini 接管默认视频模型，1.5 Pro 不再默认
        assert video_models["doubao-seedance-2-0-mini-260615"].default is True
        assert video_models["doubao-seedance-1-5-pro-251215"].default is False
        assert video_models["doubao-seedance-2-0-260128"].default is False

    def test_ark_video_models_include_seedance_2_5(self):
        meta = PROVIDER_REGISTRY["ark"]
        seedance_25 = meta.models["doubao-seedance-2-5-260628"]
        assert seedance_25.media_type == "video"
        # 30 秒直出全展开为离散档；官方的 -1（模型自选时长）不登记
        assert seedance_25.supported_durations == list(range(4, 31))
        assert seedance_25.resolutions == ["480p", "720p"]
        assert "generate_audio" in seedance_25.capabilities
        # 2.0 mini 仍是默认，2.5 只是可选
        assert seedance_25.default is False

    def test_ark_agent_plan_has_no_seedance_2_5(self):
        # agent plan 套餐未纳入 2.5，registry 不得先于套餐登记
        meta = PROVIDER_REGISTRY["ark-agent-plan"]
        assert not [mid for mid in meta.models if "seedance-2.5" in mid or "seedance-2-5" in mid]
