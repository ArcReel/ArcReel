"""BGM 轨的摆放：截到时间线末尾、截断处淡出与淡入淡出收进片段时长。"""

from __future__ import annotations

from lib.edit_timeline.bgm import place_bgm
from lib.edit_timeline.model import BgmClip

SECOND = 1_000_000


def _clip(clip_id: str, start: float, source_in: float, source_out: float, **fields: object) -> BgmClip:
    return BgmClip(
        id=clip_id,
        bgm_id="bgm-0000abcd",
        start_us=round(start * SECOND),
        in_us=round(source_in * SECOND),
        out_us=round(source_out * SECOND),
        **fields,
    )


def _rows(duration: float, *clips: BgmClip) -> list[tuple[str, int, int, int, int, int, bool]]:
    return [
        (p.clip_id, p.start_us, p.source_in_us, p.duration_us, p.fade_in_us, p.fade_out_us, p.truncated)
        for p in place_bgm(clips, round(duration * SECOND))
    ]


def test_clips_inside_the_timeline_keep_their_range_and_fades_in_start_order() -> None:
    later = _clip("b1", 5, 0, 3, fade_in_us=SECOND // 2, fade_out_us=SECOND)
    earlier = _clip("b2", 0, 1, 4)

    assert _rows(10, later, earlier) == [
        ("b2", 0, SECOND, 3 * SECOND, SECOND, SECOND, False),
        ("b1", 5 * SECOND, 0, 3 * SECOND, SECOND // 2, SECOND, False),
    ]


def test_a_clip_past_the_end_is_cut_there_with_a_one_second_fade_out() -> None:
    clip = _clip("b1", 7, 0, 5, fade_in_us=SECOND // 2, fade_out_us=3 * SECOND)

    assert _rows(10, clip) == [("b1", 7 * SECOND, 0, 3 * SECOND, SECOND // 2, SECOND, True)]


def test_a_cut_shorter_than_its_fades_keeps_the_cutoff_fade_and_shortens_the_fade_in() -> None:
    assert _rows(10, _clip("b1", 9.5, 0, 5)) == [("b1", 9_500_000, 0, SECOND // 2, 0, SECOND // 2, True)]
    assert _rows(10, _clip("b1", 8.5, 0, 5)) == [("b1", 8_500_000, 0, 1_500_000, SECOND // 2, SECOND, True)]


def test_fades_longer_than_an_uncut_clip_shrink_in_proportion() -> None:
    clip = _clip("b1", 0, 0, 1.5, fade_in_us=SECOND, fade_out_us=2 * SECOND)

    assert _rows(10, clip) == [("b1", 0, 0, 1_500_000, SECOND // 2, SECOND, False)]


def test_a_clip_starting_at_or_after_the_end_is_silent() -> None:
    assert _rows(10, _clip("b1", 10, 0, 3)) == []
