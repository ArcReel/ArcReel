"""抽帧计划：每个镜头至少一帧、信号两侧加密、无信号时即均匀取样。"""

from __future__ import annotations

import pytest

from lib.video_review.sampling import plan_frame_indices
from lib.video_review.signals import TimeSpan, VideoSignals

_FRAME_TIMES = [index * 0.04 for index in range(100)]


def _signals(*, cuts: tuple[float, ...] = (), black: tuple[TimeSpan, ...] = ()) -> VideoSignals:
    return VideoSignals(duration_seconds=4.0, black=black, freeze=(), cuts=cuts)


def test_without_signals_the_plan_is_even_sampling() -> None:
    assert plan_frame_indices(_FRAME_TIMES, None, 4, max_count=24) == [12, 37, 62, 87]


def test_a_video_shorter_than_the_budget_uses_every_frame() -> None:
    assert plan_frame_indices(_FRAME_TIMES[:3], None, 8, max_count=24) == [0, 1, 2]


def test_every_shot_keeps_a_frame_when_shots_outnumber_the_budget() -> None:
    cuts = (0.4, 0.8, 1.2, 1.6, 2.0)
    indices = plan_frame_indices(_FRAME_TIMES, _signals(cuts=cuts), 2, max_count=24)

    starts = [0, *(round(cut / 0.04) for cut in cuts)]
    ends = [*(start - 1 for start in starts[1:]), 99]
    assert len(indices) == 6
    for start, end in zip(starts, ends, strict=True):
        assert any(start <= index <= end for index in indices)


def test_shots_beyond_the_cap_are_sampled_evenly_across_the_video() -> None:
    cuts = tuple(round(0.04 * step, 2) for step in range(2, 40, 2))
    indices = plan_frame_indices(_FRAME_TIMES, _signals(cuts=cuts), 2, max_count=6)

    assert len(indices) == 6
    assert indices == sorted(indices)
    assert indices[0] < 10
    assert indices[-1] > 30


def test_leftover_budget_goes_to_both_sides_of_each_signal() -> None:
    signals = _signals(cuts=(1.0, 2.0), black=(TimeSpan(1.0, 2.0),))

    indices = plan_frame_indices(_FRAME_TIMES, signals, 12, max_count=24)

    for expected in (24, 25, 49, 50):
        assert expected in indices


def test_a_small_budget_spreads_signal_frames_instead_of_clustering_early() -> None:
    cuts = (1.0, 2.0, 3.0)
    indices = plan_frame_indices(_FRAME_TIMES, _signals(cuts=cuts), 6, max_count=24)

    assert len(indices) == 6
    assert [index for index in indices if index >= 75] != []
    assert indices == sorted(set(indices))


@pytest.mark.parametrize("count", [1, 5, 24])
def test_the_plan_never_exceeds_the_target_or_repeats_a_frame(count: int) -> None:
    signals = _signals(cuts=(1.0, 2.0), black=(TimeSpan(1.0, 2.0),))

    indices = plan_frame_indices(_FRAME_TIMES, signals, count, max_count=24)

    assert len(indices) == len(set(indices)) == max(count, 3)
