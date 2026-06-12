from pathlib import Path

from lib.video_backends.base import VideoCapabilities, VideoGenerationRequest


class TestVideoCapabilities:
    def test_defaults(self):
        caps = VideoCapabilities()
        assert caps.first_frame is True
        assert caps.last_frame is False
        assert caps.reference_images is False
        assert caps.max_reference_images == 0
        # 首帧叠加参考必须显式声明：默认 False 保证「reference_images=True 但参考与首帧
        # 互斥」的后端不会被产品参考注入等叠加场景误选
        assert caps.reference_images_with_start_frame is False

    def test_start_frame_overlay_declared_per_backend(self):
        """首帧叠加参考能力按后端 API 实际裁决声明：互斥实现（见图切端点/单槽合并/服务端拒绝）必须为 False。"""
        from lib.video_backends.ark import ArkVideoBackend
        from lib.video_backends.dashscope import DashScopeVideoBackend
        from lib.video_backends.v2_video_generations import V2VideoGenerationsBackend
        from lib.video_backends.vidu import ViduVideoBackend

        # Ark API 实测拒绝首帧与参考素材混合（InvalidParameter: first/last frame content
        # cannot be mixed with reference media content）——参考图是与首帧互斥的参考生视频模式
        assert ArkVideoBackend.video_capabilities_for_model("seedance-2.0").reference_images_with_start_frame is False
        assert V2VideoGenerationsBackend.video_capabilities_for_model("any").reference_images_with_start_frame is True
        # wan2.7-r2v 官方形态即「带首帧的参考生视频」；happyhorse-r2v 无首帧能力，叠加无从谈起
        assert (
            DashScopeVideoBackend.video_capabilities_for_model("wan2.7-r2v").reference_images_with_start_frame is True
        )
        assert (
            DashScopeVideoBackend.video_capabilities_for_model("happyhorse-1.0-r2v").reference_images_with_start_frame
            is False
        )
        # Vidu 见参考图即切 /reference2video 丢首帧——互斥模式，禁止叠加
        assert ViduVideoBackend.video_capabilities_for_model("viduq3-turbo").reference_images_with_start_frame is False

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
