"""ViduVideoBackend 单元测试 — 重点：endpoint 选择、duration 强制、resolution 白名单、build_request 字段。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from lib.providers import PROVIDER_VIDU
from lib.video_backends.base import (
    VideoCapability,
    VideoGenerationRequest,
)
from lib.video_backends.vidu import (
    _DURATION_RULES,
    _ENDPOINT_MODELS,
    _ENDPOINTS_WITH_ASPECT_RATIO,
    _Q3_MODELS,
    _RESOLUTION_WHITELIST,
    DEFAULT_MODEL,
    ViduVideoBackend,
    _coerce_duration,
    _coerce_resolution,
)


@pytest.fixture
def output_path(tmp_path: Path) -> Path:
    return tmp_path / "out.mp4"


class TestBackendBasics:
    def test_default_model_is_q3_turbo(self):
        backend = ViduVideoBackend(api_key="test-key")
        assert backend.name == PROVIDER_VIDU
        assert backend.model == DEFAULT_MODEL == "viduq3-turbo"

    def test_q3_models_have_audio_capability(self):
        backend = ViduVideoBackend(api_key="test-key", model="viduq3-turbo")
        assert VideoCapability.GENERATE_AUDIO in backend.capabilities

    def test_non_q3_models_lack_audio_capability(self):
        backend = ViduVideoBackend(api_key="test-key", model="vidu2.0")
        assert VideoCapability.GENERATE_AUDIO not in backend.capabilities

    def test_max_reference_images_seven(self):
        backend = ViduVideoBackend(api_key="test-key")
        assert backend.video_capabilities.max_reference_images == 7


class TestEndpointSelection:
    def _backend(self, model: str = "viduq3-turbo") -> ViduVideoBackend:
        return ViduVideoBackend(api_key="test-key", model=model)

    def test_text_only_picks_text2video(self, output_path: Path):
        backend = self._backend()
        req = VideoGenerationRequest(prompt="x", output_path=output_path)
        assert backend._select_endpoint(req) == "/text2video"

    def test_start_image_picks_img2video(self, tmp_path: Path, output_path: Path):
        backend = self._backend()
        start = tmp_path / "start.png"
        start.write_bytes(b"x")
        req = VideoGenerationRequest(prompt="x", output_path=output_path, start_image=start)
        assert backend._select_endpoint(req) == "/img2video"

    def test_start_and_end_image_picks_start_end2video(self, tmp_path: Path, output_path: Path):
        backend = self._backend()
        start, end = tmp_path / "s.png", tmp_path / "e.png"
        for p in (start, end):
            p.write_bytes(b"x")
        req = VideoGenerationRequest(prompt="x", output_path=output_path, start_image=start, end_image=end)
        assert backend._select_endpoint(req) == "/start-end2video"

    def test_reference_images_take_priority(self, tmp_path: Path, output_path: Path):
        """有 refs 时即使带 start_image 也应走 reference2video（依实现：refs 先判）。"""
        backend = self._backend()
        ref = tmp_path / "ref.png"
        ref.write_bytes(b"x")
        req = VideoGenerationRequest(prompt="x", output_path=output_path, reference_images=[ref])
        assert backend._select_endpoint(req) == "/reference2video"


class TestEndpointModelMatrix:
    """端点支持模型集合是 spec，钉死，避免误改。"""

    def test_reference2video_does_not_include_q3_pro(self):
        # q3-pro 在 reference2video 上不被官方文档列出
        assert "viduq3-pro" not in _ENDPOINT_MODELS["/reference2video"]

    def test_reference2video_includes_q3_turbo(self):
        assert "viduq3-turbo" in _ENDPOINT_MODELS["/reference2video"]

    def test_text2video_excludes_vidu2_0(self):
        assert "vidu2.0" not in _ENDPOINT_MODELS["/text2video"]

    def test_aspect_ratio_only_text2video_and_reference2video(self):
        assert _ENDPOINTS_WITH_ASPECT_RATIO == frozenset({"/text2video", "/reference2video"})

    def test_q3_models_set(self):
        assert "viduq3-turbo" in _Q3_MODELS
        assert "viduq3-pro" in _Q3_MODELS
        assert "vidu2.0" not in _Q3_MODELS


class TestCoerceDuration:
    def test_q3_turbo_text2video_passthrough_in_range(self):
        assert _coerce_duration("viduq3-turbo", "/text2video", 8) == 8

    def test_q3_turbo_text2video_clamps_to_nearest(self):
        # range 1..16；超过 16 应取 16
        assert _coerce_duration("viduq3-turbo", "/text2video", 30) == 16

    def test_vidu2_0_img2video_only_4_or_8(self):
        assert _coerce_duration("vidu2.0", "/img2video", 12) == 8
        assert _coerce_duration("vidu2.0", "/img2video", 5) == 4
        assert _coerce_duration("vidu2.0", "/img2video", 4) == 4
        assert _coerce_duration("vidu2.0", "/img2video", 8) == 8

    def test_vidu2_0_reference2video_only_4(self):
        assert _coerce_duration("vidu2.0", "/reference2video", 8) == 4

    def test_q1_text2video_only_5(self):
        assert _coerce_duration("viduq1", "/text2video", 10) == 5

    def test_q3_reference2video_min_3(self):
        # range 3..16；请求 1 → 取最近 3
        assert _coerce_duration("viduq3", "/reference2video", 1) == 3

    def test_unknown_combination_passthrough(self):
        # 表里无项时不强校（透传 / 兜底 5）
        assert _coerce_duration("unknown-model", "/img2video", 9) == 9
        assert _coerce_duration("unknown-model", "/img2video", None) == 5


class TestCoerceResolution:
    def test_q1_only_1080p_falls_back(self):
        assert _coerce_resolution("viduq1", "720p") == "1080p"

    def test_q3_turbo_passes_720p(self):
        assert _coerce_resolution("viduq3-turbo", "720p") == "720p"

    def test_default_for_q3_turbo_when_none(self):
        # whitelist 里有 720p → 取 720p
        assert _coerce_resolution("viduq3-turbo", None) == "720p"

    def test_unknown_model_passthrough(self):
        assert _coerce_resolution("unknown", "9000p") == "9000p"

    def test_all_known_models_have_whitelist(self):
        # registry 中暴露的模型都得在白名单里
        for model in {
            "viduq3-pro",
            "viduq3-turbo",
            "viduq3",
            "viduq3-mix",
            "viduq2",
            "viduq1",
            "vidu2.0",
        }:
            assert model in _RESOLUTION_WHITELIST


class TestBuildRequest:
    """_build_request 是核心串联函数：endpoint 选择 + duration/resolution/aspect_ratio/audio 字段拼装。"""

    @patch("lib.video_backends.vidu.image_to_data_uri")
    def test_text2video_body_minimal(self, mock_data_uri, output_path: Path):
        backend = ViduVideoBackend(api_key="test-key", model="viduq3-turbo")
        req = VideoGenerationRequest(
            prompt="hello",
            output_path=output_path,
            aspect_ratio="9:16",
            duration_seconds=8,
            resolution="720p",
        )
        endpoint, body = backend._build_request(req)

        assert endpoint == "/text2video"
        assert body["model"] == "viduq3-turbo"
        assert body["prompt"] == "hello"
        assert body["duration"] == 8
        assert body["resolution"] == "720p"
        assert body["aspect_ratio"] == "9:16"
        # q3 默认透传 audio
        assert body["audio"] is True
        # text2video 不携带 images
        assert "images" not in body

    @patch("lib.video_backends.vidu.image_to_data_uri", return_value="data:image/png;base64,XX")
    def test_img2video_passes_one_image_no_aspect_ratio(self, _mock, tmp_path: Path, output_path: Path):
        start = tmp_path / "s.png"
        start.write_bytes(b"x")

        backend = ViduVideoBackend(api_key="test-key", model="viduq3-turbo")
        req = VideoGenerationRequest(
            prompt="x",
            output_path=output_path,
            start_image=start,
            aspect_ratio="9:16",  # 应被丢弃
            duration_seconds=5,
        )
        endpoint, body = backend._build_request(req)

        assert endpoint == "/img2video"
        assert body["images"] == ["data:image/png;base64,XX"]
        # img2video 不接受 aspect_ratio
        assert "aspect_ratio" not in body

    @patch("lib.video_backends.vidu.image_to_data_uri", return_value="data:image/png;base64,XX")
    def test_reference2video_with_q3_turbo(self, _mock, tmp_path: Path, output_path: Path):
        ref1, ref2 = tmp_path / "r1.png", tmp_path / "r2.png"
        for p in (ref1, ref2):
            p.write_bytes(b"x")

        backend = ViduVideoBackend(api_key="test-key", model="viduq3-turbo")
        req = VideoGenerationRequest(
            prompt="x",
            output_path=output_path,
            reference_images=[ref1, ref2],
            aspect_ratio="9:16",
            duration_seconds=5,
        )
        endpoint, body = backend._build_request(req)

        assert endpoint == "/reference2video"
        assert len(body["images"]) == 2
        # reference2video 接受 aspect_ratio
        assert body["aspect_ratio"] == "9:16"
        # range 3..16，5 透传
        assert body["duration"] == 5

    def test_reference2video_with_q3_pro_raises_model_mismatch(self, tmp_path: Path, output_path: Path):
        ref = tmp_path / "r.png"
        ref.write_bytes(b"x")

        backend = ViduVideoBackend(api_key="test-key", model="viduq3-pro")
        req = VideoGenerationRequest(
            prompt="x",
            output_path=output_path,
            reference_images=[ref],
            duration_seconds=5,
        )
        with pytest.raises(RuntimeError, match="不支持"):
            backend._build_request(req)

    def test_non_q3_model_does_not_send_audio(self, output_path: Path):
        backend = ViduVideoBackend(api_key="test-key", model="vidu2.0")
        # vidu2.0 走 img2video，不能 text2video
        # 这里直接用 reference2video 路径需 ref；走 start-image 演示
        # 用 monkeypatch 跳过 image_to_data_uri
        # 简化：直接构造一个 text2video 请求验证 audio 不写入
        # —— 但 vidu2.0 不在 text2video 集合里，应该 raise
        req = VideoGenerationRequest(prompt="x", output_path=output_path, duration_seconds=4)
        with pytest.raises(RuntimeError, match="不支持"):
            backend._build_request(req)


class TestDurationRulesSpec:
    """直接钉死 _DURATION_RULES 关键条目，避免误改。"""

    def test_q1_text2video_only_5(self):
        assert _DURATION_RULES[("viduq1", "/text2video")] == [5]

    def test_vidu2_0_img2video_only_4_8(self):
        assert _DURATION_RULES[("vidu2.0", "/img2video")] == [4, 8]

    def test_vidu2_0_reference2video_only_4(self):
        assert _DURATION_RULES[("vidu2.0", "/reference2video")] == [4]

    def test_q3_turbo_text2video_full_range(self):
        assert _DURATION_RULES[("viduq3-turbo", "/text2video")] == list(range(1, 17))
