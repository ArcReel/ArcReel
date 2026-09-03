"""分集规划有效目标体量的来源优先级与时长折算口径。"""

from __future__ import annotations

import pytest

from lib.episode_target_duration import EPISODE_TARGET_DURATION_FIELD
from lib.episode_target_volume import (
    EPISODE_TARGET_UNITS_FIELD,
    project_episode_target_units,
    resolve_episode_target_volume,
)
from lib.speech_rate import SPEECH_RATE_FIELD


class TestProjectEpisodeTargetUnits:
    def test_reads_a_valid_setting(self) -> None:
        assert project_episode_target_units({EPISODE_TARGET_UNITS_FIELD: 800}) == 800

    @pytest.mark.parametrize("value", [0, -1])
    def test_rejects_values_below_one(self, value: int) -> None:
        assert project_episode_target_units({EPISODE_TARGET_UNITS_FIELD: value}) is None

    def test_rejects_bool_even_though_it_is_an_int_subclass(self) -> None:
        """JSON 里的 true 被当成「每集 1 个字」会把整部作品切碎。"""
        assert project_episode_target_units({EPISODE_TARGET_UNITS_FIELD: True}) is None

    @pytest.mark.parametrize("value", ["800", 800.0, None, [800]])
    def test_rejects_non_integer_values(self, value: object) -> None:
        assert project_episode_target_units({EPISODE_TARGET_UNITS_FIELD: value}) is None

    def test_missing_field_and_non_mapping_read_as_unset(self) -> None:
        assert project_episode_target_units({}) is None
        assert project_episode_target_units(None) is None


class TestResolveEpisodeTargetVolume:
    def test_returns_none_when_neither_setting_is_present(self) -> None:
        assert resolve_episode_target_volume({}, language="zh") is None

    def test_explicit_units_win_over_target_duration(self) -> None:
        """显式体量是用户直接给的数，不再叠一层语速估算。"""
        volume = resolve_episode_target_volume(
            {EPISODE_TARGET_UNITS_FIELD: 800, EPISODE_TARGET_DURATION_FIELD: 90},
            language="zh",
        )

        assert volume is not None
        assert (volume.units, volume.source) == (800, "units")
        assert volume.seconds is None
        assert volume.units_per_second is None

    def test_derives_units_from_target_duration_at_the_language_speech_rate(self) -> None:
        volume = resolve_episode_target_volume({EPISODE_TARGET_DURATION_FIELD: 90}, language="zh")

        assert volume is not None
        assert (volume.units, volume.source, volume.seconds) == (450, "duration", 90)
        assert volume.units_per_second == 5.0
        assert volume.unit_noun == "字"

    def test_derivation_follows_the_language_reading_unit(self) -> None:
        """en 的阅读单位是词，语速与量词必须一起切换，否则折算出的是「450 个词」。"""
        volume = resolve_episode_target_volume({EPISODE_TARGET_DURATION_FIELD: 90}, language="en")

        assert volume is not None
        assert (volume.units, volume.unit_noun) == (225, "词")

    def test_project_speech_rate_override_applies_to_the_derivation(self) -> None:
        volume = resolve_episode_target_volume(
            {EPISODE_TARGET_DURATION_FIELD: 90, SPEECH_RATE_FIELD: 4},
            language="zh",
        )

        assert volume is not None
        assert (volume.units, volume.units_per_second) == (360, 4.0)

    def test_derived_units_never_fall_below_one(self) -> None:
        """合法区间的下界组合（10 秒 × 0.001 单位 / 秒）会舍到 0，目标体量 0 没有可用语义。"""
        volume = resolve_episode_target_volume(
            {EPISODE_TARGET_DURATION_FIELD: 10, SPEECH_RATE_FIELD: 0.001},
            language="zh",
        )

        assert volume is not None
        assert volume.units == 1

    def test_dirty_explicit_units_fall_through_to_the_duration_derivation(self) -> None:
        volume = resolve_episode_target_volume(
            {EPISODE_TARGET_UNITS_FIELD: 0, EPISODE_TARGET_DURATION_FIELD: 90},
            language="zh",
        )

        assert volume is not None
        assert (volume.units, volume.source) == (450, "duration")

    def test_out_of_range_target_duration_reads_as_unset(self) -> None:
        assert resolve_episode_target_volume({EPISODE_TARGET_DURATION_FIELD: 5}, language="zh") is None
