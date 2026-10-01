"""成片渲染规划：剪辑片段如何落到输出帧网格上、如何展开转场、如何按硬切分段。"""

from __future__ import annotations

from pathlib import Path

from lib.edit_timeline.model import ClipTrim, EditClip, TransitionType
from lib.final_cut.render_plan import (
    Crossfade,
    Extension,
    Fade,
    OutputProfile,
    RenderMedia,
    borrow_frames,
    plan_render,
    render_clips,
)

PROFILE = OutputProfile(width=1080, height=1920, fps=30)


def _clip(clip_id: str, unit_id: str, **fields: object) -> EditClip:
    return EditClip.model_validate({"id": clip_id, "unit_id": unit_id, "source_volume": 1.0, **fields})


def _media(duration_us: int, *, version: int = 1, has_audio: bool = True) -> RenderMedia:
    return RenderMedia(
        path=Path("/p/videos/x.mp4"), video_version=version, duration_us=duration_us, has_audio=has_audio
    )


def test_clip_boundaries_round_on_the_cumulative_frame_grid() -> None:
    clips = render_clips(
        [_clip(f"c{i}", f"U{i}") for i in range(1, 6)],
        {f"U{i}": _media(1_020_000) for i in range(1, 6)},
    )

    plan = plan_render(clips, PROFILE)

    # 5 × 1.02 秒 = 5.1 秒 = 153 帧；逐段舍入会得到 155 帧。
    assert plan.total_frames == 153
    assert [planned.start_frame for planned in plan.clips] == [0, 31, 61, 92, 122]
    assert plan.duration_seconds == 5.1


def test_hold_extends_a_clip_with_frames_after_its_out_point() -> None:
    clips = render_clips([_clip("c1", "U1", hold_us=500_000)], {"U1": _media(1_000_000)})

    (planned,) = plan_render(clips, PROFILE).clips

    assert (planned.source_frames, planned.hold_frames) == (30, 15)


def test_hard_cuts_and_fades_split_segments_while_overlapping_transitions_keep_neighbours_together() -> None:
    clips = render_clips(
        [
            _clip("c1", "U1"),
            _clip("c2", "U2", transition_to_next={"type": TransitionType.DISSOLVE, "duration_us": 500_000}),
            _clip("c3", "U3", transition_to_next={"type": TransitionType.FADE_BLACK, "duration_us": 400_000}),
            _clip("c4", "U4"),
        ],
        {unit: _media(1_000_000) for unit in ("U1", "U2", "U3", "U4")},
    )

    plan = plan_render(clips, PROFILE)

    assert [[planned.clip.clip_id for planned in segment.clips] for segment in plan.segments] == [
        ["c1"],
        ["c2", "c3"],
        ["c4"],
    ]
    # 0.5 秒 = 15 帧，切点之前 7 帧、之后 8 帧；窗口起点相对段起点（第 30 帧）。
    assert [segment.crossfades for segment in plan.segments] == [(), (Crossfade("fade", 60 - 7 - 30, 15),), ()]
    assert plan.total_frames == 120


def test_overlapping_transition_borrows_half_a_window_of_source_on_each_side() -> None:
    clips = render_clips(
        [
            _clip(
                "c1",
                "U1",
                trim={"in_us": 0, "out_us": 1_000_000, "basis_version": 1},
                transition_to_next={"type": "push_left", "duration_us": 400_000},
            ),
            _clip("c2", "U2", trim={"in_us": 1_000_000, "out_us": 2_000_000, "basis_version": 1}),
        ],
        {"U1": _media(3_000_000), "U2": _media(3_000_000)},
    )

    first, second = plan_render(clips, PROFILE).clips

    # 0.4 秒 = 12 帧：前一片段在出点之后借 6 帧，后一片段在入点之前借 6 帧，片段边界不变。
    assert (first.start_frame, first.frames, first.trail, first.lead) == (0, 30, Extension(6, 0), Extension())
    assert (second.start_frame, second.frames, second.lead) == (30, 30, Extension(6, 0))
    assert (first.stream_frames, second.stream_frames) == (36, 36)


def test_borrowing_beyond_the_source_is_made_up_with_freeze_frames() -> None:
    clips = render_clips(
        [
            _clip(
                "c1",
                "U1",
                trim={"in_us": 0, "out_us": 900_000, "basis_version": 1},
                transition_to_next={"type": "dissolve", "duration_us": 400_000},
            ),
            _clip("c2", "U2", trim={"in_us": 100_000, "out_us": 1_000_000, "basis_version": 1}),
        ],
        {"U1": _media(1_000_000), "U2": _media(1_000_000)},
    )

    first, second = plan_render(clips, PROFILE).clips

    # 出点之后只剩 0.1 秒（3 帧）源素材、入点之前也只有 3 帧，各缺 3 帧用定格补齐。
    assert first.trail == Extension(source_frames=3, freeze_frames=3)
    assert second.lead == Extension(source_frames=3, freeze_frames=3)


def test_a_clip_ending_in_a_hold_continues_the_still_into_the_transition() -> None:
    clips = render_clips(
        [
            _clip(
                "c1",
                "U1",
                trim={"in_us": 0, "out_us": 1_000_000, "basis_version": 1},
                hold_us=500_000,
                transition_to_next={"type": "wipe_up", "duration_us": 200_000},
            ),
            _clip("c2", "U2"),
        ],
        {"U1": _media(5_000_000), "U2": _media(1_000_000)},
    )

    first, second = plan_render(clips, PROFILE).clips

    assert (first.source_frames, first.hold_frames) == (30, 15)
    assert first.trail == Extension(source_frames=0, freeze_frames=3)
    # 整段使用的片段入点在 0，没有可借的源素材。
    assert second.lead == Extension(source_frames=0, freeze_frames=3)


def test_borrow_frames_counts_only_whole_frames_of_remaining_source() -> None:
    assert borrow_frames(6, 1_000_000, 30) == Extension(6, 0)
    assert borrow_frames(6, 99_999, 30) == Extension(2, 4)
    assert borrow_frames(6, 0, 30) == Extension(0, 6)
    assert borrow_frames(6, -1, 30) == Extension(0, 6)


def test_fade_transitions_fade_out_and_in_around_the_cut_without_borrowing() -> None:
    clips = render_clips(
        [
            _clip("c1", "U1", transition_to_next={"type": "fade_white", "duration_us": 1_000_000}),
            _clip("c2", "U2"),
        ],
        {"U1": _media(2_000_000), "U2": _media(1_000_000)},
    )

    first, second = plan_render(clips, PROFILE).clips

    assert (first.fade_out, first.trail) == (Fade(15, "white"), Extension())
    assert (second.fade_in, second.lead) == (Fade(15, "white"), Extension())
    assert (first.stream_frames, second.stream_frames) == (60, 30)


def test_a_fade_longer_than_its_clip_is_capped_at_the_clip() -> None:
    clips = render_clips(
        [
            _clip("c1", "U1", transition_to_next={"type": "fade_black", "duration_us": 2_000_000}),
            _clip("c2", "U2"),
        ],
        {"U1": _media(3_000_000), "U2": _media(500_000)},
    )

    first, second = plan_render(clips, PROFILE).clips

    assert (first.fade_out, second.fade_in) == (Fade(30, "black"), Fade(15, "black"))


def test_a_transition_on_the_last_clip_has_no_neighbour_to_join() -> None:
    clips = render_clips(
        [_clip("c1", "U1"), _clip("c2", "U2", transition_to_next={"type": "dissolve", "duration_us": 500_000})],
        {"U1": _media(1_000_000), "U2": _media(1_000_000)},
    )

    plan = plan_render(clips, PROFILE)

    assert [segment.crossfades for segment in plan.segments] == [(), ()]
    assert plan.clips[-1].trail == Extension()


def test_a_transition_before_a_skipped_clip_joins_the_next_rendered_clip() -> None:
    clips = render_clips(
        [
            _clip("c1", "U1", transition_to_next={"type": "dissolve", "duration_us": 200_000}),
            _clip("c2", "GONE"),
            _clip("c3", "U3"),
        ],
        {"U1": _media(1_000_000), "U3": _media(1_000_000)},
    )

    plan = plan_render(clips, PROFILE)

    assert [[planned.clip.clip_id for planned in segment.clips] for segment in plan.segments] == [["c1", "c3"]]


def test_clips_shorter_than_half_a_frame_do_not_reach_the_output() -> None:
    clips = render_clips(
        [
            _clip("c1", "U1"),
            _clip("c2", "U2", trim={"in_us": 0, "out_us": 10_000, "basis_version": 1}),
            _clip("c3", "U1"),
        ],
        {"U1": _media(1_000_000), "U2": _media(1_000_000)},
    )

    plan = plan_render(clips, PROFILE)

    assert [planned.clip.clip_id for planned in plan.clips] == ["c1", "c3"]
    assert plan.total_frames == 60


def test_trim_applies_only_to_the_video_version_it_was_cut_against() -> None:
    trim = ClipTrim(in_us=500_000, out_us=1_500_000, basis_version=2)
    clips = render_clips(
        [_clip("c1", "U1", trim=trim), _clip("c2", "U2", trim=trim)],
        {"U1": _media(4_000_000, version=2), "U2": _media(4_000_000, version=3)},
    )

    assert [(clip.source_in_us, clip.source_duration_us) for clip in clips] == [(500_000, 1_000_000), (0, 4_000_000)]


def test_trim_out_point_is_clamped_to_the_current_video_length() -> None:
    clips = render_clips(
        [_clip("c1", "U1", trim={"in_us": 1_000_000, "out_us": 9_000_000, "basis_version": 1})],
        {"U1": _media(3_000_000)},
    )

    assert [(clip.source_in_us, clip.source_duration_us) for clip in clips] == [(1_000_000, 2_000_000)]


def test_clips_of_units_without_render_media_are_skipped() -> None:
    clips = render_clips([_clip("c1", "U1"), _clip("c2", "GONE")], {"U1": _media(1_000_000)})

    assert [clip.clip_id for clip in clips] == ["c1"]
