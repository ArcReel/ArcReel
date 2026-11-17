from pathlib import Path

import pytest

from arcreel_market_core.video_backend_contract import (
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

        from lib.backends.video_backends.grok import GrokVideoBackend

        with patch("lib.backends.video_backends.grok.create_grok_client"):
            caps = GrokVideoBackend(api_key="test-key").video_capabilities
        # 通道实现了参考图且不设上限，各型号的上限来自条目
        assert caps.max_reference_images is None
        assert not hasattr(caps, "reference_images_with_start_frame")


class TestVideoCapabilitiesForModel:
    """各 backend 的 client-free 静态 caps 方法给出通道声明，不构造实例 / 不需 api_key；生效能力再与条目两侧求值。

    resolver 解析参考图上限走这条纯函数路径，故不应触发 SDK client 构造或 api_key 校验。"""

    def test_ark_seedance_2_returns_nine(self):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.backends.video_backends.ark import Seedance2VideoBackend

        # 不构造实例（即不构造 Ark SDK client、不需 api_key）即可取得通道声明：不设参考图上限
        assert (
            Seedance2VideoBackend.video_capabilities_for_model("doubao-seedance-2-0-260128").max_reference_images
            is None
        )
        # 生效上限来自条目
        assert builtin_video_capabilities_for_model("ark", "doubao-seedance-2-0-260128").max_reference_images == 9

    def test_vidu_returns_seven(self):
        from lib.backends.video_backends.vidu import ViduVideoBackend

        assert ViduVideoBackend.video_capabilities_for_model("viduq3-turbo").max_reference_images == 7

    def test_minimax_h3_declares_multimodal_limits(self):
        from arcreel_market_core.video_backend_contract import ReferenceAudioMode
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

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
            ("MiniMax-H3", True),
        ],
    )
    def test_minimax_declares_text_to_video(self, model: str, expected: bool):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        assert builtin_video_capabilities_for_model("minimax", model).text_to_video is expected

    @pytest.mark.parametrize(
        ("model", "expected"),
        [("kling-v3", True), ("kling-video-o1", False)],
    )
    def test_kling_declares_text_to_video(self, model: str, expected: bool):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        assert builtin_video_capabilities_for_model("kling", model).text_to_video is expected

    @pytest.mark.parametrize(
        ("model", "expected"),
        [("viduq3-pro", True), ("viduq3", False), ("vidu2.0", False)],
    )
    def test_vidu_declares_text_to_video(self, model: str, expected: bool):
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model

        assert builtin_video_capabilities_for_model("vidu", model).text_to_video is expected

    def test_v2_returns_four(self):
        from lib.custom_provider.endpoints import ENDPOINT_REGISTRY

        caps = ENDPOINT_REGISTRY["v2-video-generations"].video_caps_for_model
        assert caps is not None
        assert caps("whatever", None).max_reference_images == 4

    def test_instance_property_delegates_to_static(self):
        """instance video_capabilities 委托至静态方法，保持 backend 为单一真相源。

        patch 掉 create_ark_client：本测试只验证 property→静态方法的委托，不应在 __init__ 里真实
        构造 Ark SDK client（caps 路径不依赖 client）。"""
        from unittest.mock import patch

        from lib.backends.video_backends.ark import Seedance25VideoBackend

        with patch("lib.backends.video_backends.ark.create_ark_client"):
            backend = Seedance25VideoBackend(api_key="k", model="doubao-seedance-2-0")
        assert backend.video_capabilities == Seedance25VideoBackend.video_capabilities_for_model("doubao-seedance-2-0")


class TestVideoAudioTrack:
    """音轨形态：逐路径声明、按路径取值，以及配置侧的派生查询与 backend 声明同值。"""

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

    def test_video_route_vocabulary_matches_generation_type_buckets(self):
        """VideoRoute 与 lib.config.resolver.VideoGenerationType 是同一份桶名词汇表。

        分层契约不允许 backend 层导入 config 层，故两侧各声明一次；取值一旦漂开，
        ``audio_track_for_route`` 会对 r2v 静默按 i2v 取值。
        """
        from typing import get_args

        from arcreel_market_core.video_backend_contract import VideoRoute
        from lib.config.resolver import VideoGenerationType

        assert get_args(VideoRoute) == get_args(VideoGenerationType)

    def test_registry_lookup_matches_backend_declaration(self):
        """lib.config 侧的派生查询与 backend 声明同值——展示层与入队预检读的就是这一份。"""
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.catalog import query as catalog
        from lib.config.registry import PROVIDER_REGISTRY
        from lib.config.resolver import builtin_video_audio_track

        video_models = [
            (provider_id, model_id)
            for provider_id in PROVIDER_REGISTRY
            for model_id, info in catalog.list_models(provider_id).items()
            if info.media_type == "video"
        ]
        assert video_models
        for provider_id, model_id in video_models:
            caps = builtin_video_capabilities_for_model(provider_id, model_id)
            for route in ("i2v", "r2v"):
                assert builtin_video_audio_track(provider_id, model_id, generation_type=route) == (
                    caps.audio_track_for_route(route).value
                ), (provider_id, model_id, route)

    def test_lookup_returns_none_without_signal(self):
        """非视频 model / 未知供应商没有逐模型声明，返回 None 交调用方按无信号不收紧处理。"""
        from lib.config.resolver import builtin_video_audio_track

        assert builtin_video_audio_track("dashscope", "wan2.7-image", generation_type="i2v") is None
        assert builtin_video_audio_track("custom-1", "whatever", generation_type="i2v") is None
        assert builtin_video_audio_track("kling", "not-a-registered-model", generation_type="i2v") is None


class TestVideoCapabilitySingleSourceOfTruth:
    """全目录扫描：内置视频模型的输入模式、参考图上限与音轨形态只有 backend 一处手写声明。

    目录的模型信息 `CatalogModel` 不描述这些维度——第二份声明没有比对方，两侧漂了也无人发现，还会
    把审查者引到不参与解析的那一份上。这几个用例守住单一真相源的形状。
    """

    def test_registry_declares_no_video_capability_bits(self):
        """视频模型的 capabilities 不得含输入模式或音轨 token——真相源是 VideoCapabilities。"""
        from lib.catalog import query as catalog
        from lib.config.registry import PROVIDER_REGISTRY

        banned = {"text_to_video", "image_to_video", "reference_to_video", "generate_audio"}
        offenders = [
            f"{provider_id}/{model_id}: {sorted(banned & set(info.capabilities))}"
            for provider_id in PROVIDER_REGISTRY
            for model_id, info in catalog.list_models(provider_id).items()
            if info.media_type == "video" and banned & set(info.capabilities)
        ]
        assert offenders == []

    def test_model_info_has_no_audio_cap_field(self):
        """同上：CatalogModel 不得长出音轨字段。

        逐模型的单一声明表达不了「同一 model 内按执行子路径分叉」，加上就会长出
        「界面允许关音频、执行期静默丢弃」的分裂。
        """
        from dataclasses import fields

        from lib.catalog.query import CatalogModel

        names = {f.name for f in fields(CatalogModel)}
        assert not {n for n in names if "audio" in n}

    def test_every_registry_video_model_resolves_backend_capabilities(self):
        """每个内置视频模型都能从 backend 取到能力声明——单一真相源须覆盖全部内置供应商。"""
        from lib.backends.backend_assembly.specs import builtin_video_capabilities_for_model
        from lib.catalog import query as catalog
        from lib.config.registry import PROVIDER_REGISTRY

        unresolved: list[str] = []
        for provider_id in PROVIDER_REGISTRY:
            for model_id, info in catalog.list_models(provider_id).items():
                if info.media_type != "video":
                    continue
                try:
                    builtin_video_capabilities_for_model(provider_id, model_id)
                except ValueError as exc:
                    unresolved.append(f"{provider_id}/{model_id}: {exc}")
        assert unresolved == []
