"""Contact-sheet sampling plans: at least one frame per shot, dense samples around signals, remaining budget filled with uniform samples.

Work by frame index, corresponding to the nth frame returned by ``probe_video_frame_times``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

from lib.video_review.signals import VideoSignals, frame_index_at, last_frame_index_before


def sample_frame_indices(frame_count: int, count: int) -> list[int]:
    """Uniformly take ``count`` from ``frame_count`` frames (midpoints of equal intervals); take all if there are fewer."""
    if count >= frame_count:
        return list(range(frame_count))
    return [math.floor((index + 0.5) * frame_count / count) for index in range(count)]


def _shot_ranges(frame_times: Sequence[float], signals: VideoSignals | None) -> list[tuple[int, int]]:
    """Frame-index intervals [first frame, last frame] for each shot."""
    frame_count = len(frame_times)
    starts = [0]
    if signals is not None:
        starts += [index for cut in signals.cuts if 0 < (index := frame_index_at(frame_times, cut)) < frame_count]
    starts = sorted(set(starts))
    ends = [start - 1 for start in starts[1:]] + [frame_count - 1]
    return list(zip(starts, ends, strict=True))


def _signal_tiers(frame_times: Sequence[float], signals: VideoSignals) -> tuple[list[int], list[int]]:
    """Two signal-dense sampling tiers: signal-start frames, then frames on the other side (before cuts, final frames of spans)."""
    events: list[tuple[float, int, int]] = []
    for cut in signals.cuts:
        index = frame_index_at(frame_times, cut)
        events.append((cut, index, index - 1))
    for span in (*signals.black, *signals.freeze):
        first = frame_index_at(frame_times, span.start)
        last = max(first, last_frame_index_before(frame_times, span.end))
        events.append((span.start, first, last))
    events.sort()
    frame_count = len(frame_times)
    limit = range(frame_count)
    return (
        [first for _, first, _ in events if first in limit],
        [other for _, _, other in events if other in limit],
    )


def _add_evenly(selected: set[int], candidates: Sequence[int], room: int) -> None:
    fresh = list(dict.fromkeys(index for index in candidates if index not in selected))
    if len(fresh) <= room:
        selected.update(fresh)
    else:
        selected.update(fresh[position] for position in sample_frame_indices(len(fresh), room))


def _fill_uniformly(selected: set[int], even: Sequence[int], frame_count: int, room: int) -> None:
    """Add uniform samples farthest from selected frames in turn; after exhausting samples, fill the largest gaps if budget remains."""
    for _ in range(room):
        pool = [index for index in even if index not in selected]
        if pool:
            selected.add(max(pool, key=lambda index: min((abs(index - other) for other in selected), default=0)))
            continue
        anchors = [-1, *sorted(selected), frame_count]
        gap_start, gap_end = max(pairwise(anchors), key=lambda gap: gap[1] - gap[0])
        if gap_end - gap_start < 2:
            return
        selected.add((gap_start + gap_end) // 2)


def plan_frame_indices(
    frame_times: Sequence[float], signals: VideoSignals | None, count: int, *, max_count: int
) -> list[int]:
    """Choose frames from ``len(frame_times)`` video frames and return ascending indices.

    ``count`` is the budget. If shots exceed it, prioritize one frame per shot, raising the total
    to shot count but never above ``max_count``; beyond that, choose shots uniformly. Select in order:
    one frame per shot (a uniform sample within it, or its middle frame if none), signal-start frames,
    frames on the other side, then uniform samples for remaining budget. Without signals, sample uniformly.
    """
    frame_count = len(frame_times)
    shots = _shot_ranges(frame_times, signals)
    target = min(frame_count, max(count, min(len(shots), max_count)))
    even = sample_frame_indices(frame_count, target)
    anchors: list[int] = []
    for first, last in shots:
        middle = (first + last) // 2
        inside = [index for index in even if first <= index <= last]
        anchors.append(min(inside, key=lambda index: abs(index - middle)) if inside else middle)
    selected: set[int] = set()
    _add_evenly(selected, anchors, target)
    if signals is not None:
        for tier in _signal_tiers(frame_times, signals):
            if len(selected) >= target:
                break
            _add_evenly(selected, tier, target - len(selected))
    _fill_uniformly(selected, even, frame_count, target - len(selected))
    return sorted(selected)


__all__ = ["plan_frame_indices", "sample_frame_indices"]
