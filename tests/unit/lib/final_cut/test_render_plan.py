"""成片渲染规划：剪辑片段如何落到输出帧网格上、如何按硬切分段。"""

from __future__ import annotations

from pathlib import Path

from lib.edit_timeline.model import ClipTrim, EditClip, Transition, TransitionType
from lib.final_cut.render_plan import OutputProfile, RenderMedia, plan_render, render_clips

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


def test_hard_cuts_split_segments_and_transitions_keep_neighbours_together() -> None:
    dissolve = {"type": TransitionType.DISSOLVE, "duration_us": 500_000}
    clips = render_clips(
        [
            _clip("c1", "U1"),
            _clip("c2", "U2", transition_to_next=Transition.model_validate(dissolve)),
            _clip("c3", "U3"),
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
    assert [segment.has_transitions for segment in plan.segments] == [False, True, False]


def test_a_transition_on_the_last_clip_has_no_neighbour_to_join() -> None:
    clips = render_clips(
        [_clip("c1", "U1"), _clip("c2", "U2", transition_to_next={"type": "dissolve", "duration_us": 500_000})],
        {"U1": _media(1_000_000), "U2": _media(1_000_000)},
    )

    plan = plan_render(clips, PROFILE)

    assert [segment.has_transitions for segment in plan.segments] == [False, False]


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
