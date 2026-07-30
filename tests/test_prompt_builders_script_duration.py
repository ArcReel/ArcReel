"""验证 _format_duration_constraint 按连续性切换文案，且不允许空 supported_durations。"""

from __future__ import annotations

import pytest

from lib.prompt_builders_script import _format_duration_constraint


class TestFormatDurationConstraint:
    def test_discrete_set(self):
        text = _format_duration_constraint([4, 6, 8], default_duration=None)
        for duration in (4, 6, 8):
            assert str(duration) in text

    def test_discrete_set_with_default(self):
        text = _format_duration_constraint([4, 6, 8], default_duration=6)
        assert "6" in text
        assert text != _format_duration_constraint([4, 6, 8], default_duration=None)

    def test_default_duration_must_be_in_supported(self):
        """default_duration 不在 supported 集合时应抛错，避免 prompt 自相矛盾。"""
        with pytest.raises(ValueError, match="default_duration=6 不在"):
            _format_duration_constraint([4, 8], default_duration=6)

    def test_continuous_range_uses_min_max_phrasing(self):
        """长度 ≥5 且连续整数时压缩为只包含边界的区间。"""
        text = _format_duration_constraint([3, 4, 5, 6, 7, 8, 9, 10], default_duration=None)
        assert "3" in text
        assert "10" in text
        assert "[3, 4, 5, 6, 7, 8, 9, 10]" not in text

    def test_short_continuous_still_uses_list(self):
        """长度 <5 即使连续，仍保留中间值。"""
        text = _format_duration_constraint([4, 5, 6], default_duration=None)
        assert "5" in text


class TestBuildersRequireDurations:
    """删除 fallback 后，传 None / 空 list 不应再被静默回填。"""

    def test_format_constraint_rejects_empty(self):
        with pytest.raises(ValueError, match="supported_durations 不能为空"):
            _format_duration_constraint([], default_duration=None)
