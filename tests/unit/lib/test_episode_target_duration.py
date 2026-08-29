"""单集目标时长的区间守卫与 project.json 读时解析。"""

from __future__ import annotations

import pytest

from lib.episode_target_duration import (
    EPISODE_TARGET_DURATION_FIELD,
    MAX_EPISODE_TARGET_DURATION,
    MIN_EPISODE_TARGET_DURATION,
    is_valid_episode_target_duration,
    project_episode_target_duration,
)


class TestIsValidEpisodeTargetDuration:
    @pytest.mark.parametrize("value", [MIN_EPISODE_TARGET_DURATION, MAX_EPISODE_TARGET_DURATION, 90])
    def test_accepts_integers_inside_the_range(self, value: int) -> None:
        assert is_valid_episode_target_duration(value) is True

    @pytest.mark.parametrize("value", [MIN_EPISODE_TARGET_DURATION - 1, MAX_EPISODE_TARGET_DURATION + 1, 0, -30])
    def test_rejects_values_outside_the_range(self, value: int) -> None:
        assert is_valid_episode_target_duration(value) is False

    def test_rejects_floats_instead_of_rounding_them(self) -> None:
        """秒数是整数量纲：静默取整会让用户看到的设置与生效值不同。"""
        assert is_valid_episode_target_duration(90.0) is False
        assert is_valid_episode_target_duration(90.5) is False

    def test_rejects_bool_even_though_it_is_an_int_subclass(self) -> None:
        assert is_valid_episode_target_duration(True) is False

    @pytest.mark.parametrize("value", ["90", None, [90]])
    def test_rejects_non_numeric_values(self, value: object) -> None:
        assert is_valid_episode_target_duration(value) is False


class TestProjectEpisodeTargetDuration:
    def test_reads_a_valid_setting(self) -> None:
        assert project_episode_target_duration({EPISODE_TARGET_DURATION_FIELD: 120}) == 120

    def test_missing_field_is_no_target(self) -> None:
        assert project_episode_target_duration({}) is None

    @pytest.mark.parametrize("raw", [5, 900, "120", 120.0, True])
    def test_dirty_or_out_of_range_values_degrade_to_no_target(self, raw: object) -> None:
        """手改 project.json / 历史脏数据不应让一次生成崩在偏好字段上。"""
        assert project_episode_target_duration({EPISODE_TARGET_DURATION_FIELD: raw}) is None

    def test_non_mapping_project_is_no_target(self) -> None:
        assert project_episode_target_duration(None) is None
