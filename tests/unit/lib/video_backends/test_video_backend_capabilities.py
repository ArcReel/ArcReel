from pathlib import Path

import pytest

from lib.video_backends.base import (
    ReferenceAudioMode,
    VideoAudioMode,
    VideoCapabilities,
    VideoGenerationRequest,
)


class TestVideoCapabilities:
    def test_defaults(self):
        caps = VideoCapabilities()
        assert caps.text_to_video is True
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.max_reference_images == 0
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0
        assert caps.first_frame_ratio_adaptive_only is False

    def test_first_frame_ratio_adaptive_only_declared(self):
        caps = VideoCapabilities(first_frame_ratio_adaptive_only=True)
        assert caps.first_frame_ratio_adaptive_only is True

    def test_first_last(self):
        caps = VideoCapabilities(last_frame=True)
        assert caps.last_frame is True

    def test_custom_values(self):
        caps = VideoCapabilities(
            last_frame=True,
            max_reference_images=9,
            reference_audio_mode=ReferenceAudioMode.DIRECT,
            max_reference_audio_count=3,
        )
        assert caps.last_frame is True
        assert caps.max_reference_images == 9
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 3


class TestVideoGenerationRequestNewFields:
    def test_end_image_default_none(self):
        req = VideoGenerationRequest(prompt="t", output_path=Path("/tmp/o.mp4"))
        assert req.end_image is None
        assert req.reference_images is None

    def test_end_image_set(self):
        req = VideoGenerationRequest(
            prompt="t",
            output_path=Path("/tmp/o.mp4"),
            start_image=Path("/tmp/f.png"),
            end_image=Path("/tmp/l.png"),
        )
        assert req.end_image == Path("/tmp/l.png")

    def test_reference_images(self):
        req = VideoGenerationRequest(
            prompt="t",
            output_path=Path("/tmp/o.mp4"),
            reference_images=[Path("/tmp/r1.png"), Path("/tmp/r2.png")],
        )
        assert len(req.reference_images) == 2

    def test_existing_fields_unchanged(self):
        """Ensure existing fields still work as before."""
        req = VideoGenerationRequest(
            prompt="test prompt",
            output_path=Path("/tmp/out.mp4"),
            aspect_ratio="16:9",
            duration_seconds=5,
            resolution="720p",
            start_image=Path("/tmp/start.png"),
            generate_audio=False,
            project_name="my_project",
            service_tier="flex",
            seed=42,
        )
        assert req.prompt == "test prompt"
        assert req.start_image == Path("/tmp/start.png")
        assert req.generate_audio is False
        assert req.seed == 42


class TestGrokVideoCapabilities:
    def test_no_start_frame_overlay_field(self):
        """Grok 同时下发 image_url 与 reference_image_urls，但字段已收敛，不再单独声明该组合能力。"""
        from unittest.mock import patch

        from lib.video_backends.grok import GrokVideoBackend

        with patch("lib.video_backends.grok.create_grok_client"):
            caps = GrokVideoBackend(api_key="test-key").video_capabilities
        assert caps.max_reference_images > 0
        assert caps.max_reference_images == 7
        assert not hasattr(caps, "reference_images_with_start_frame")


class TestVideoCapabilitiesForModel:
    """各 backend 的 client-free 静态 caps 方法：按 model_id 纯计算，不构造实例 / 不需 api_key。

    resolver 解析参考图上限走这条纯函数路径，故不应触发 SDK client 构造或 api_key 校验。"""

    def test_ark_seedance_2_returns_nine(self):
        from lib.video_backends.ark import ArkVideoBackend

        # 不构造实例（即不构造 Ark SDK client、不需 api_key）即可取得 caps
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-0")
        assert caps.max_reference_images == 9
        assert caps.max_reference_images > 0

    def test_ark_non_seedance_2_returns_zero(self):
        from lib.video_backends.ark import ArkVideoBackend

        assert ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-0").max_reference_images == 0

    def test_vidu_returns_seven(self):
        from lib.video_backends.vidu import ViduVideoBackend

        assert ViduVideoBackend.video_capabilities_for_model("viduq3-turbo").max_reference_images == 7

    def test_minimax_h3_declares_multimodal_limits(self):
        from lib.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.video_backends.base import ReferenceAudioMode

        caps = builtin_video_capabilities_for_model("minimax", "MiniMax-H3")
        assert caps.max_reference_images == 9
        assert caps.last_frame is True
        assert caps.reference_audio_mode is ReferenceAudioMode.DIRECT
        assert caps.max_reference_audio_count == 3
        assert caps.max_prompt_chars == 7000
        assert caps.first_frame_ratio_adaptive_only is True

    @pytest.mark.parametrize(
        ("model", "expected"),
        [
            ("MiniMax-Hailuo-2.3", True),
            # Fast 仅图生视频：这条收窄由 minimax-hailuo-v1-fast 定义里必需的首帧输入承担。
            ("MiniMax-Hailuo-2.3-Fast", False),
            ("S2V-01", False),
            ("MiniMax-H3", True),
        ],
    )
    def test_minimax_declares_text_to_video(self, model: str, expected: bool):
        from lib.backend_assembly.specs import builtin_video_capabilities_for_model

        assert builtin_video_capabilities_for_model("minimax", model).text_to_video is expected

    @pytest.mark.parametrize(
        ("model", "expected"),
        [("kling-v3", True), ("kling-video-o1", False)],
    )
    def test_kling_declares_text_to_video(self, model: str, expected: bool):
        from lib.video_backends.kling import KlingVideoBackend

        assert KlingVideoBackend.video_capabilities_for_model(model).text_to_video is expected

    @pytest.mark.parametrize(
        ("model", "expected"),
        [("viduq3-pro", True), ("viduq3-pro-fast", False), ("vidu2.0", False)],
    )
    def test_vidu_declares_text_to_video(self, model: str, expected: bool):
        from lib.video_backends.vidu import ViduVideoBackend

        assert ViduVideoBackend.video_capabilities_for_model(model).text_to_video is expected

    def test_v2_returns_four(self):
        from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

        caps = ENDPOINT_REGISTRY["v2-video-generations"].video_caps_for_model
        assert caps is not None
        assert caps("whatever").max_reference_images == 4

    def test_instance_property_delegates_to_static(self):
        """instance video_capabilities 委托至静态方法，保持 backend 为单一真相源。

        patch 掉 create_ark_client：本测试只验证 property→静态方法的委托，不应在 __init__ 里真实
        构造 Ark SDK client（caps 路径不依赖 client）。"""
        from unittest.mock import patch

        from lib.video_backends.ark import ArkVideoBackend

        with patch("lib.video_backends.ark.create_ark_client"):
            backend = ArkVideoBackend(api_key="k", model="doubao-seedance-2-0")
        assert backend.video_capabilities == ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-0")


#: 全部内置视频 model 的音轨立场逐条钉死，按执行路径各给一份：``controllable`` = 请求带音轨开关；
#: ``always_on`` = 恒有声、开关不可控；``always_off`` = 该路径不产音轨。值为 ``(i2v, r2v)``。
#:
#: 新增视频型号必须在此登记，登记时即被迫表态其音轨立场——backend 漏声明的新型号会以
#: ``VideoCapabilities`` 的默认值（controllable）落到这张表上，与作者的登记意图对不上而在 CI 暴露。
#: 表放在本文件而非注册表测试里：音轨形态的真相源是 backend 的 VideoCapabilities，守卫应贴着真相源。
_VIDEO_AUDIO_STANCES: dict[tuple[str, str], tuple[str, str]] = {
    ("agnes", "agnes-video-v2.0"): ("always_off", "always_off"),
    ("ark", "doubao-seedance-1-5-pro-251215"): ("controllable", "controllable"),
    ("ark", "doubao-seedance-2-0-260128"): ("controllable", "controllable"),
    ("ark", "doubao-seedance-2-0-fast-260128"): ("controllable", "controllable"),
    ("ark", "doubao-seedance-2-0-mini-260615"): ("controllable", "controllable"),
    ("ark", "doubao-seedance-2-5-260628"): ("controllable", "controllable"),
    ("ark-agent-plan", "doubao-seedance-1.5-pro"): ("controllable", "controllable"),
    ("ark-agent-plan", "doubao-seedance-2.0"): ("controllable", "controllable"),
    ("ark-agent-plan", "doubao-seedance-2.0-fast"): ("controllable", "controllable"),
    ("ark-agent-plan", "doubao-seedance-2.0-mini"): ("controllable", "controllable"),
    ("dashscope", "happyhorse-1.0-i2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.0-r2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.0-t2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.1-i2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.1-r2v"): ("always_on", "always_on"),
    ("dashscope", "happyhorse-1.1-t2v"): ("always_on", "always_on"),
    ("dashscope", "wan2.7-i2v"): ("always_on", "always_on"),
    ("dashscope", "wan2.7-r2v"): ("always_on", "always_on"),
    ("dashscope", "wan2.7-t2v"): ("always_on", "always_on"),
    ("dashscope", "wan3.0-video"): ("controllable", "controllable"),
    ("gemini-aistudio", "veo-3.1-fast-generate-preview"): ("always_on", "always_on"),
    ("gemini-aistudio", "veo-3.1-generate-preview"): ("always_on", "always_on"),
    ("gemini-aistudio", "veo-3.1-lite-generate-preview"): ("always_on", "always_on"),
    ("gemini-vertex", "veo-3.1-fast-generate-001"): ("controllable", "controllable"),
    ("gemini-vertex", "veo-3.1-generate-001"): ("controllable", "controllable"),
    ("grok", "grok-imagine-video"): ("always_on", "always_on"),
    ("kling", "kling-v2-5-turbo"): ("always_off", "always_off"),
    # 可灵有音频能力的三档：图生/文生子路径带 sound 开关，多图主体（R2V）子路径的原生 schema
    # 不含该字段，成片必然无声。
    ("kling", "kling-v2-6"): ("controllable", "always_off"),
    ("kling", "kling-v3"): ("controllable", "always_off"),
    ("kling", "kling-v3-omni"): ("controllable", "always_off"),
    ("kling", "kling-video-o1"): ("always_off", "always_off"),
    ("minimax", "MiniMax-H3"): ("always_on", "always_on"),
    ("minimax", "MiniMax-Hailuo-2.3"): ("always_off", "always_off"),
    ("minimax", "MiniMax-Hailuo-2.3-Fast"): ("always_off", "always_off"),
    ("minimax", "S2V-01"): ("always_off", "always_off"),
    ("openai", "sora-2"): ("always_on", "always_on"),
    ("openai", "sora-2-pro"): ("always_on", "always_on"),
    ("vidu", "vidu2.0"): ("always_off", "always_off"),
    ("vidu", "viduq3"): ("controllable", "controllable"),
    ("vidu", "viduq3-pro"): ("controllable", "controllable"),
    ("vidu", "viduq3-turbo"): ("controllable", "controllable"),
}


class TestVideoAudioTrack:
    """音轨形态：逐路径声明、按路径取值，以及全注册表的立场守卫。"""

    def test_default_is_controllable_and_shared_by_both_routes(self):
        """未声明即「无信号不收紧」：两条路径都按开关可控处理。"""
        caps = VideoCapabilities()
        assert caps.audio_track is VideoAudioMode.CONTROLLABLE
        assert caps.reference_route_audio_track is None
        assert caps.audio_track_for_route("i2v") is VideoAudioMode.CONTROLLABLE
        assert caps.audio_track_for_route("r2v") is VideoAudioMode.CONTROLLABLE

    def test_reference_route_declaration_narrows_only_that_route(self):
        caps = VideoCapabilities(
            audio_track=VideoAudioMode.CONTROLLABLE,
            reference_route_audio_track=VideoAudioMode.ALWAYS_OFF,
        )
        assert caps.audio_track_for_route("i2v") is VideoAudioMode.CONTROLLABLE
        assert caps.audio_track_for_route("r2v") is VideoAudioMode.ALWAYS_OFF

    def test_video_route_vocabulary_matches_capability_buckets(self):
        """VideoRoute 与 lib.config.resolver.VideoCapability 是同一份桶名词汇表。

        分层契约不允许 backend 层导入 config 层，故两侧各声明一次；取值一旦漂开，
        ``audio_track_for_route`` 会对 r2v 静默按 i2v 取值。
        """
        from typing import get_args

        from lib.config.resolver import VideoCapability
        from lib.video_backends.base import VideoRoute

        assert get_args(VideoRoute) == get_args(VideoCapability)

    def test_every_video_model_matches_declared_stance(self):
        """backend 声明在全部内置视频 model 上的逐路径取值与上表相等（整表相等，非子集）。"""
        from lib.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.config.registry import PROVIDER_REGISTRY

        actual: dict[tuple[str, str], tuple[str, str]] = {}
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, info in meta.models.items():
                if info.media_type != "video":
                    continue
                caps = builtin_video_capabilities_for_model(provider_id, model_id)
                actual[(provider_id, model_id)] = (
                    caps.audio_track_for_route("i2v").value,
                    caps.audio_track_for_route("r2v").value,
                )
        assert actual == _VIDEO_AUDIO_STANCES

    def test_registry_lookup_matches_backend_declaration(self):
        """lib.config 侧的派生查询与 backend 声明同值——展示层与入队预检读的就是这一份。"""
        from lib.config.resolver import builtin_video_audio_track

        for (provider_id, model_id), (i2v, r2v) in _VIDEO_AUDIO_STANCES.items():
            assert builtin_video_audio_track(provider_id, model_id, capability="i2v") == i2v
            assert builtin_video_audio_track(provider_id, model_id, capability="r2v") == r2v

    def test_lookup_returns_none_without_signal(self):
        """非视频 model / 未知供应商没有逐模型声明，返回 None 交调用方按无信号不收紧处理。"""
        from lib.config.resolver import builtin_video_audio_track

        assert builtin_video_audio_track("dashscope", "wan2.7-image", capability="i2v") is None
        assert builtin_video_audio_track("custom-1", "whatever", capability="i2v") is None
        assert builtin_video_audio_track("kling", "not-a-registered-model", capability="i2v") is None


class TestVideoCapabilitySingleSourceOfTruth:
    """全注册表扫描：内置视频模型的输入模式、参考图上限与音轨形态只有 backend 一处手写声明。

    registry `ModelInfo` 不描述这些维度——第二份手写声明没有比对方，两侧漂了也无人发现，还会
    把审查者引到不参与解析的那一份上。这几个用例守住单一真相源的形状。
    """

    def test_registry_declares_no_video_capability_bits(self):
        """视频模型的 capabilities 不得含输入模式或音轨 token——真相源是 VideoCapabilities。"""
        from lib.config.registry import PROVIDER_REGISTRY

        banned = {"text_to_video", "image_to_video", "reference_to_video", "generate_audio"}
        offenders = [
            f"{provider_id}/{model_id}: {sorted(banned & set(info.capabilities))}"
            for provider_id, meta in PROVIDER_REGISTRY.items()
            for model_id, info in meta.models.items()
            if info.media_type == "video" and banned & set(info.capabilities)
        ]
        assert offenders == []

    def test_model_info_has_no_reference_image_cap_field(self):
        """ModelInfo 不得重新长出参考图上限字段：加回去就等于把第二份手写来源请回来。"""
        from dataclasses import fields

        from lib.config.registry import ModelInfo

        assert "max_reference_images" not in {f.name for f in fields(ModelInfo)}

    def test_model_info_has_no_audio_cap_field(self):
        """同上：ModelInfo 不得长出音轨字段。

        registry 的单一声明表达不了「同一 model 内按执行子路径分叉」，加回去就会长出
        「界面允许关音频、执行期静默丢弃」的分裂。
        """
        from dataclasses import fields

        from lib.config.registry import ModelInfo

        names = {f.name for f in fields(ModelInfo)}
        assert not {n for n in names if "audio" in n}

    def test_every_registry_video_model_resolves_backend_capabilities(self):
        """每个内置视频模型都能从 backend 取到能力声明——单一真相源须覆盖全注册表。"""
        from lib.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.config.registry import PROVIDER_REGISTRY

        unresolved: list[str] = []
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, info in meta.models.items():
                if info.media_type != "video":
                    continue
                try:
                    builtin_video_capabilities_for_model(provider_id, model_id)
                except ValueError as exc:
                    unresolved.append(f"{provider_id}/{model_id}: {exc}")
        assert unresolved == []
