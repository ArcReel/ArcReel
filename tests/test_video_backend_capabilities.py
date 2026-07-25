from pathlib import Path

import pytest

from lib.video_backends.base import VideoCapabilities, VideoGenerationRequest


class TestVideoCapabilities:
    def test_defaults(self):
        caps = VideoCapabilities()
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.reference_images is False
        assert caps.max_reference_images == 0

    def test_first_last(self):
        caps = VideoCapabilities(last_frame=True)
        assert caps.last_frame is True

    def test_custom_values(self):
        caps = VideoCapabilities(last_frame=True, reference_images=True, max_reference_images=9)
        assert caps.last_frame is True
        assert caps.reference_images is True
        assert caps.max_reference_images == 9


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
    @pytest.mark.unit
    def test_no_start_frame_overlay_field(self):
        """Grok 同时下发 image_url 与 reference_image_urls，但字段已收敛，不再单独声明该组合能力。"""
        from unittest.mock import patch

        from lib.video_backends.grok import GrokVideoBackend

        with patch("lib.video_backends.grok.create_grok_client"):
            caps = GrokVideoBackend(api_key="test-key").video_capabilities
        assert caps.reference_images is True
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
        assert caps.reference_images is True

    def test_ark_non_seedance_2_returns_zero(self):
        from lib.video_backends.ark import ArkVideoBackend

        assert ArkVideoBackend.video_capabilities_for_model("doubao-seedance-1-0").max_reference_images == 0

    def test_vidu_returns_seven(self):
        from lib.video_backends.vidu import ViduVideoBackend

        assert ViduVideoBackend.video_capabilities_for_model("viduq3-turbo").max_reference_images == 7

    def test_v2_returns_four(self):
        from lib.video_backends.v2_video_generations import V2VideoGenerationsBackend

        assert V2VideoGenerationsBackend.video_capabilities_for_model("whatever").max_reference_images == 4

    def test_instance_property_delegates_to_static(self):
        """instance video_capabilities 委托至静态方法，保持 backend 为单一真相源。

        patch 掉 create_ark_client：本测试只验证 property→静态方法的委托，不应在 __init__ 里真实
        构造 Ark SDK client（caps 路径不依赖 client）。"""
        from unittest.mock import patch

        from lib.video_backends.ark import ArkVideoBackend

        with patch("lib.video_backends.ark.create_ark_client"):
            backend = ArkVideoBackend(api_key="k", model="doubao-seedance-2-0")
        assert backend.video_capabilities == ArkVideoBackend.video_capabilities_for_model("doubao-seedance-2-0")
