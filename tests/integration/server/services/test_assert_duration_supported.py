"""Tests for assert_duration_supported (split from test_generation_tasks_service.py)."""

import pytest

from lib.video_backends.base import VideoCapabilityError
from server.services.generation_tasks import assert_duration_supported


class TestAssertDurationSupported:
    def test_supported_duration_passes(self):
        assert_duration_supported(8, [4, 6, 8])  # no raise

    def test_unsupported_duration_rejected(self):
        # 抛带稳定 code 的能力错误（与 ImageCapabilityError 对称），细节在 params。
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported(5, [4, 6, 8])
        assert exc.value.code == "video_duration_not_supported"
        assert exc.value.params["duration"] == 5

    def test_empty_supported_list_passes(self):
        # 能力不可解析时空列表放行，保持宽容行为。
        assert_duration_supported(99, [])  # no raise

    def test_integer_like_string_and_float_accepted(self):
        # 外部配置可能给字符串 / 浮点，可解析为整数秒的归一化后通过，不抛裸异常。
        assert_duration_supported("6", [4, 6, 8])  # no raise
        assert_duration_supported(6.0, [4, 6, 8])  # no raise

    def test_fractional_duration_rejected_not_truncated(self):
        # 非整数秒一律拒绝，绝不截断成「碰巧合法」的 4。
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported(4.5, [4, 6, 8])
        assert exc.value.code == "video_duration_invalid"
        with pytest.raises(VideoCapabilityError):
            assert_duration_supported("4.5", [4, 6, 8])

    def test_non_numeric_duration_rejected(self):
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported("abc", [4, 6, 8])
        assert exc.value.code == "video_duration_invalid"
