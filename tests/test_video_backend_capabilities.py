from pathlib import Path

import pytest

from lib.video_backends.base import ReferenceAudioMode, VideoCapabilities, VideoGenerationRequest


class TestVideoCapabilities:
    @pytest.mark.unit
    def test_defaults(self):
        caps = VideoCapabilities()
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.max_reference_images == 0
        assert caps.reference_audio_mode is ReferenceAudioMode.NONE
        assert caps.max_reference_audio_count == 0

    @pytest.mark.unit
    def test_first_last(self):
        caps = VideoCapabilities(last_frame=True)
        assert caps.last_frame is True

    @pytest.mark.unit
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
    @pytest.mark.unit
    def test_end_image_default_none(self):
        req = VideoGenerationRequest(prompt="t", output_path=Path("/tmp/o.mp4"))
        assert req.end_image is None
        assert req.reference_images is None

    @pytest.mark.unit
    def test_end_image_set(self):
        req = VideoGenerationRequest(
            prompt="t",
            output_path=Path("/tmp/o.mp4"),
            start_image=Path("/tmp/f.png"),
            end_image=Path("/tmp/l.png"),
        )
        assert req.end_image == Path("/tmp/l.png")

    @pytest.mark.unit
    def test_reference_images(self):
        req = VideoGenerationRequest(
            prompt="t",
            output_path=Path("/tmp/o.mp4"),
            reference_images=[Path("/tmp/r1.png"), Path("/tmp/r2.png")],
        )
        assert len(req.reference_images) == 2

    @pytest.mark.unit
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
    @pytest.mark.unit
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

    @pytest.mark.unit
    def test_ark_seedance_2_returns_nine(self):
        from lib.video_backends.ark import ArkVideoBackend

        # 不构造实例（即不构造 Ark SDK client、不需 api_key）即可取得 caps
        caps = ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-0")
        assert caps.max_reference_images == 9
        assert caps.max_reference_images > 0

    @pytest.mark.unit
    def test_ark_non_seedance_2_returns_zero(self):
        from lib.video_backends.ark import ArkVideoBackend

        assert ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-0").max_reference_images == 0

    @pytest.mark.unit
    def test_vidu_returns_seven(self):
        from lib.video_backends.vidu import ViduVideoBackend

        assert ViduVideoBackend.video_capabilities_for_model("viduq3-turbo").max_reference_images == 7

    @pytest.mark.unit
    def test_v2_returns_four(self):
        from lib.video_backends.v2_video_generations import V2VideoGenerationsBackend

        assert V2VideoGenerationsBackend.video_capabilities_for_model("whatever").max_reference_images == 4

    @pytest.mark.unit
    def test_instance_property_delegates_to_static(self):
        """instance video_capabilities 委托至静态方法，保持 backend 为单一真相源。

        patch 掉 create_ark_client：本测试只验证 property→静态方法的委托，不应在 __init__ 里真实
        构造 Ark SDK client（caps 路径不依赖 client）。"""
        from unittest.mock import patch

        from lib.video_backends.ark import ArkVideoBackend

        with patch("lib.video_backends.ark.create_ark_client"):
            backend = ArkVideoBackend(api_key="k", model="doubao-seedance-2-0")
        assert backend.video_capabilities == ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-0")


class TestVideoCapabilitySingleSourceOfTruth:
    """全注册表扫描：内置视频模型的输入模式与参考图上限只有 backend 一处手写声明。

    registry `ModelInfo` 不描述视频输入模式与参考图上限——第二份手写声明没有比对方，两侧漂了
    也无人发现，还会把审查者引到不参与解析的那一份上。这三个用例守住单一真相源的形状。
    """

    @pytest.mark.unit
    def test_registry_declares_no_video_capability_bits(self):
        """视频模型的 capabilities 不得含输入模式 token——它们的真相源是 VideoCapabilities。"""
        from lib.config.registry import PROVIDER_REGISTRY

        banned = {"text_to_video", "image_to_video", "reference_to_video"}
        offenders = [
            f"{provider_id}/{model_id}: {sorted(banned & set(info.capabilities))}"
            for provider_id, meta in PROVIDER_REGISTRY.items()
            for model_id, info in meta.models.items()
            if info.media_type == "video" and banned & set(info.capabilities)
        ]
        assert offenders == []

    @pytest.mark.unit
    def test_model_info_has_no_reference_image_cap_field(self):
        """ModelInfo 不得重新长出参考图上限字段：加回去就等于把第二份手写来源请回来。"""
        from dataclasses import fields

        from lib.config.registry import ModelInfo

        assert "max_reference_images" not in {f.name for f in fields(ModelInfo)}

    @pytest.mark.unit
    def test_every_registry_video_model_resolves_backend_capabilities(self):
        """每个内置视频模型都能从 backend 取到能力声明——单一真相源须覆盖全注册表。"""
        from lib.backend_assembly.specs import get_provider_spec
        from lib.config.registry import PROVIDER_REGISTRY
        from lib.video_backends.registry import video_capabilities_for_model

        unresolved: list[str] = []
        for provider_id, meta in PROVIDER_REGISTRY.items():
            for model_id, info in meta.models.items():
                if info.media_type != "video":
                    continue
                try:
                    spec = get_provider_spec(provider_id, "video")
                    video_capabilities_for_model(spec.registry_backend, model_id)
                except ValueError as exc:
                    unresolved.append(f"{provider_id}/{model_id}: {exc}")
        assert unresolved == []
