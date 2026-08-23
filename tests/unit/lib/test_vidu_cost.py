"""Vidu 计费单元测试。"""

from __future__ import annotations

import pytest

from lib.vidu_shared import (
    DEFAULT_VIDU_IMAGE_MODEL,
    DEFAULT_VIDU_VIDEO_MODEL,
    VIDU_CREDIT_TO_CNY,
    calculate_vidu_cost,
)


class TestViduMainPath:
    """主路径：响应里有 credits（=usage_tokens），按 ¥0.03125/credit 折算。"""

    def test_credits_to_cny(self):
        amount, currency = calculate_vidu_cost(call_type="video", usage_tokens=120, model="viduq3-turbo")
        assert amount == pytest.approx(120 * 0.03125)
        assert currency == "CNY"

    def test_image_credits_to_cny(self):
        amount, currency = calculate_vidu_cost(call_type="image", usage_tokens=8, model="viduq2")
        assert amount == pytest.approx(8 * 0.03125)
        assert currency == "CNY"

    def test_zero_tokens_is_legal_credits(self):
        # credits=0 是合法值（如 off_peak / 平台促销），应直接计为 0 而不是回退表
        amount, _ = calculate_vidu_cost(call_type="image", usage_tokens=0, model="viduq2", resolution="1080p")
        assert amount == pytest.approx(0.0)


class TestViduFallbackTable:
    def test_video_q3_turbo_720p_5s(self):
        # viduq3-turbo/720p = 12/sec
        amount, currency = calculate_vidu_cost(
            call_type="video",
            usage_tokens=None,
            model="viduq3-turbo",
            resolution="720p",
            duration_seconds=5,
        )
        assert amount == pytest.approx(5 * 12 * 0.03125)
        assert currency == "CNY"

    def test_video_unknown_model_falls_back_to_default(self):
        # 未知模型 → 用 DEFAULT_VIDU_VIDEO_MODEL = "viduq3-turbo"
        amount, _ = calculate_vidu_cost(
            call_type="video",
            usage_tokens=None,
            model="some-future-model",
            resolution="720p",
            duration_seconds=5,
        )
        assert amount == pytest.approx(5 * 12 * 0.03125)

    def test_video_unknown_resolution_falls_back_to_720p(self):
        # resolution=8K 不在 viduq3-turbo 表里 → 退回 720p=12/sec
        amount, _ = calculate_vidu_cost(
            call_type="video",
            usage_tokens=None,
            model="viduq3-turbo",
            resolution="8K",
            duration_seconds=5,
        )
        assert amount == pytest.approx(5 * 12 * 0.03125)

    def test_image_q1_only_1080p(self):
        amount, _ = calculate_vidu_cost(
            call_type="image",
            usage_tokens=None,
            model="viduq1",
            resolution="1080p",
        )
        # viduq1/1080p = 20 credits
        assert amount == pytest.approx(20 * 0.03125)

    def test_image_resolution_case_insensitive(self):
        # viduq2 表里是小写 2k；调用方传 "2K" 也应命中
        amount, _ = calculate_vidu_cost(
            call_type="image",
            usage_tokens=None,
            model="viduq2",
            resolution="2K",
        )
        # viduq2/2k = 12 credits
        assert amount == pytest.approx(12 * 0.03125)


class TestViduUnknownCallType:
    def test_unknown_call_type_returns_zero(self):
        amount, currency = calculate_vidu_cost(
            call_type="audio",
            usage_tokens=None,
            model="viduq3-turbo",
        )
        assert amount == 0.0
        assert currency == "CNY"


class TestViduDefaultsConstants:
    def test_default_video_model_matches_registry(self):
        # 防止 vidu_shared 里的 default 与 registry 的 default 漂移
        assert DEFAULT_VIDU_VIDEO_MODEL == "viduq3-turbo"

    def test_default_image_model_matches_registry(self):
        assert DEFAULT_VIDU_IMAGE_MODEL == "viduq2"

    def test_credit_to_cny_rate(self):
        # ¥500/16000 credits = ¥0.03125
        assert VIDU_CREDIT_TO_CNY == 0.03125
