"""Effective episode target-volume precedence and duration conversion."""

from __future__ import annotations

import pytest

from lib.episode.episode_target_duration import EPISODE_TARGET_DURATION_FIELD
from lib.episode.episode_target_volume import (
    EPISODE_TARGET_UNITS_FIELD,
    project_episode_target_units,
    resolve_episode_target_volume,
)
from lib.speech.speech_rate import SPEECH_RATE_FIELD


class TestProjectEpisodeTargetUnits:
    def test_reads_a_valid_setting(self) -> None:
        assert project_episode_target_units({EPISODE_TARGET_UNITS_FIELD: 800}) == 800

    @pytest.mark.parametrize("value", [0, -1])
    def test_rejects_values_below_one(self, value: int) -> None:
        assert project_episode_target_units({EPISODE_TARGET_UNITS_FIELD: value}) is None

    def test_rejects_bool_even_though_it_is_an_int_subclass(self) -> None:
        """Treating JSON true as one character per episode would fragment an entire work."""
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
        """Explicit volume is user-provided, without an extra speech-rate estimate."""
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
        """English reading units are words; rate and unit labels must switch together, avoiding 450-word misconversion."""
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
        """Lower-bound combination (10 seconds × 0.001 units/second) rounds to 0, which has no usable target-volume meaning."""
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
