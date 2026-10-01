"""联系表的抽帧计划：每个镜头至少一帧，信号两侧加密，余量补均匀取样点。

按帧索引工作；索引对应 ``probe_video_frame_times`` 给出的第 n 帧。
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from itertools import pairwise

from lib.video_review.signals import VideoSignals, frame_index_at, last_frame_index_before


def sample_frame_indices(frame_count: int, count: int) -> list[int]:
    """在 ``frame_count`` 帧里均匀取 ``count`` 帧（各取所在等分区间的中点）；帧数不够时全取。"""
    if count >= frame_count:
        return list(range(frame_count))
    return [math.floor((index + 0.5) * frame_count / count) for index in range(count)]


def _shot_ranges(frame_times: Sequence[float], signals: VideoSignals | None) -> list[tuple[int, int]]:
    """各镜头的帧索引区间 [首帧, 末帧]。"""
    frame_count = len(frame_times)
    starts = [0]
    if signals is not None:
        starts += [index for cut in signals.cuts if 0 < (index := frame_index_at(frame_times, cut)) < frame_count]
    starts = sorted(set(starts))
    ends = [start - 1 for start in starts[1:]] + [frame_count - 1]
    return list(zip(starts, ends, strict=True))


def _signal_tiers(frame_times: Sequence[float], signals: VideoSignals) -> tuple[list[int], list[int]]:
    """信号加密帧的两个梯队：第一梯队是信号起点帧，第二梯队是起点的另一侧（切点前一帧、区间末帧）。"""
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
    """依次补入离已选帧最远的均匀取样点；取样点用完仍有余量时，补进最大的空隙。"""
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
    """从视频的 ``len(frame_times)`` 帧里挑要抽的帧，返回升序索引。

    ``count`` 是帧数预算；镜头数比预算多时，每个镜头至少一帧优先，总帧数提到镜头数，但不超过 ``max_count``，
    镜头更多时在各镜头里均匀挑。取帧依次为：每个镜头一帧（该镜头里的均匀取样点，没有则取中间帧）、
    各信号起点帧、起点另一侧的帧，余量补均匀取样点；没有信号时结果就是均匀取样。
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
