"""视频信号：对随包 ffmpeg 合成的带黑屏、定格与切点的素材断言三类信号的区间，以及联系表的抽帧与标注。"""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.infra.media_probe import probe_video_frame_times
from lib.video_review.contact_sheet import build_contact_sheets
from lib.video_review.signals import TAG_BLACK, TAG_CUT, TAG_FREEZE, VideoSignals, detect_signals, signals_for
from tests.factories import make_signal_clip


@pytest.fixture
def clip(tmp_path: Path) -> Path:
    path = tmp_path / "signal.mp4"
    make_signal_clip(path)
    return path


async def _signals(clip: Path) -> VideoSignals:
    return await detect_signals(clip, await probe_video_frame_times(clip))


async def test_black_freeze_and_cut_signals_cover_the_synthesised_intervals(clip: Path) -> None:
    signals = await _signals(clip)

    assert signals.duration_seconds == pytest.approx(5.0)
    assert [(span.start, span.end) for span in signals.black] == [pytest.approx((1.0, 2.0))]
    # 黑屏必然静止，但不重复计入卡帧段；视频以定格收尾，卡帧延续到片尾。
    assert [(span.start, span.end) for span in signals.freeze] == [pytest.approx((3.0, 5.0))]
    assert signals.cuts == pytest.approx((1.0, 2.0, 3.0))
    assert [(shot.start, shot.end) for shot in signals.shots] == pytest.approx(
        [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 5.0)]
    )


async def test_a_plain_moving_clip_has_no_signals(tmp_path: Path) -> None:
    from tests.factories import make_test_clip

    clip = tmp_path / "plain.mp4"
    make_test_clip(clip, size="320x180", fps=25, seconds=2, tone=False)

    signals = await _signals(clip)

    assert (signals.black, signals.freeze, signals.cuts) == ((), (), ())
    assert len(signals.shots) == 1


async def test_second_lookup_of_the_same_video_hits_the_cache(clip: Path, tmp_path: Path) -> None:
    frame_times = await probe_video_frame_times(clip)
    cache_file = tmp_path / "cache" / "signals.json"

    first, first_cached = await signals_for(clip, frame_times, cache_file=cache_file)
    written = cache_file.read_bytes()
    second, second_cached = await signals_for(clip, frame_times, cache_file=cache_file)

    assert (first_cached, second_cached) == (False, True)
    assert second == first
    assert cache_file.read_bytes() == written


async def test_replacing_the_video_invalidates_the_cache(clip: Path, tmp_path: Path) -> None:
    from tests.factories import make_test_clip

    frame_times = await probe_video_frame_times(clip)
    cache_file = tmp_path / "signals.json"
    await signals_for(clip, frame_times, cache_file=cache_file)

    make_test_clip(clip, size="320x180", fps=25, seconds=2, tone=False)
    replaced, cached = await signals_for(clip, await probe_video_frame_times(clip), cache_file=cache_file)

    assert cached is False
    assert replaced.cuts == ()


async def test_a_corrupt_cache_is_recomputed(clip: Path, tmp_path: Path) -> None:
    cache_file = tmp_path / "signals.json"
    cache_file.write_text("{not json", encoding="utf-8")

    signals, cached = await signals_for(clip, await probe_video_frame_times(clip), cache_file=cache_file)

    assert cached is False
    assert signals.cuts == pytest.approx((1.0, 2.0, 3.0))


async def test_every_shot_gets_a_frame_even_when_the_budget_is_smaller(clip: Path) -> None:
    frame_times = await probe_video_frame_times(clip)
    signals = await detect_signals(clip, frame_times)

    sheets = await build_contact_sheets(
        clip, unit_id="E1S01", version=1, frames=2, frame_times=frame_times, signals=signals
    )

    times = [frame.time_seconds for sheet in sheets for frame in sheet.frames]
    assert len(times) >= len(signals.shots)
    for shot in signals.shots:
        assert any(shot.start - 1e-6 <= time < shot.end for time in times), (shot, times)


async def test_frames_around_each_signal_are_densified_and_marked(clip: Path) -> None:
    frame_times = await probe_video_frame_times(clip)
    signals = await detect_signals(clip, frame_times)

    sheets = await build_contact_sheets(
        clip, unit_id="E1S01", version=1, frames=12, frame_times=frame_times, signals=signals
    )

    tags = {frame.time_seconds: frame.tags for sheet in sheets for frame in sheet.frames}

    def frame_before(time_seconds: float) -> float:
        return max(time for time in frame_times if time < time_seconds - 1e-6)

    def tags_at(time_seconds: float) -> tuple[str, ...]:
        return tags[next(time for time in tags if time == pytest.approx(time_seconds))]

    # 切点两侧：切点帧本身与它前一帧。
    for cut in (1.0, 2.0, 3.0):
        assert TAG_CUT in tags_at(cut)
        assert frame_before(cut) in tags
    # 黑屏段与卡帧段各自的首尾帧都被抽到并标注。
    assert tags_at(1.0) == (TAG_CUT, TAG_BLACK)
    assert tags[frame_before(2.0)] == (TAG_BLACK,)
    assert tags_at(3.0) == (TAG_CUT, TAG_FREEZE)
    assert tags[frame_times[-1]] == (TAG_FREEZE,)
    assert all(TAG_BLACK not in found for time, found in tags.items() if not 1.0 <= time < 2.0)


async def test_without_signals_the_sheet_is_unmarked(clip: Path) -> None:
    sheets = await build_contact_sheets(clip, unit_id="E1S01", version=1, frames=6)

    assert all(frame.tags == () for sheet in sheets for frame in sheet.frames)
    assert sum(len(sheet.frames) for sheet in sheets) == 6
