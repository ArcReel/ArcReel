"""验证 _format_duration_constraint 按连续性切换文案，且不允许空 supported_durations。"""

from __future__ import annotations

import pytest

from lib.prompt_builders_script import _format_duration_constraint


class TestFormatDurationConstraint:
    def test_discrete_set(self):
        text = _format_duration_constraint([4, 6, 8], default_duration=None)
        assert "[4, 6, 8]" in text
        assert "根据内容节奏自行决定" in text

    def test_discrete_set_with_default(self):
        text = _format_duration_constraint([4, 6, 8], default_duration=6)
        assert "[4, 6, 8]" in text
        assert "默认使用 6 秒" in text

    def test_default_duration_must_be_in_supported(self):
        """default_duration 不在 supported 集合时应抛错，避免 prompt 自相矛盾。"""
        with pytest.raises(ValueError, match="default_duration=6 不在"):
            _format_duration_constraint([4, 8], default_duration=6)

    def test_continuous_range_uses_min_max_phrasing(self):
        """长度 ≥5 且连续整数 → 用 'min 到 max 整数任选' 文案。"""
        text = _format_duration_constraint([3, 4, 5, 6, 7, 8, 9, 10], default_duration=None)
        assert "3 到 10 秒间" in text or "3-10" in text
        # 不再列举每个数
        assert "[3, 4, 5, 6, 7, 8, 9, 10]" not in text

    def test_short_continuous_still_uses_list(self):
        """长度 <5 即使连续，仍走列举形态（避免简短列表强行变成 '4 到 6'）。"""
        text = _format_duration_constraint([4, 5, 6], default_duration=None)
        assert "[4, 5, 6]" in text


class TestBuildersRequireDurations:
    """删除 fallback 后，传 None / 空 list 不应再被静默回填。"""

    def test_format_constraint_rejects_empty(self):
        with pytest.raises(ValueError, match="supported_durations 不能为空"):
            _format_duration_constraint([], default_duration=None)
