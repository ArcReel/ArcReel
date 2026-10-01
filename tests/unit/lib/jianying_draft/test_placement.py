"""剪映草稿片段摆放：截取依据版本、定格、旁白落点与字幕窗口，以及单轨不重叠的截断规则。"""

from __future__ import annotations

from lib.edit_timeline.model import ClipTrim, EditClip, EditTimelineContent
from lib.jianying_draft.placement import (
    PlacedNarration,
    PlacedSubtitle,
    UnitCue,
    UnitMaterial,
    place_timeline,
)

S = 1_000_000


def _unit(
    unit_id: str,
    *,
    version: int = 2,
    duration_us: int = 4 * S,
    gain: float = 1.0,
    cues: tuple[UnitCue, ...] = (),
    narration_us: int | None = None,
) -> UnitMaterial:
    return UnitMaterial(
        unit_id=unit_id,
        video_path=f"versions/videos/{unit_id}_v{version}.mp4",
        video_version=version,
        video_duration_us=duration_us,
        source_gain=gain,
        subtitles=cues,
        narration_path=f"versions/audio/{unit_id}_v1.wav" if narration_us is not None else None,
        narration_duration_us=narration_us,
    )


def _clip(clip_id: str, unit_id: str, **fields: object) -> EditClip:
    return EditClip.model_validate({"id": clip_id, "unit_id": unit_id, "source_volume": 1.0, **fields})


def test_trim_applies_only_to_its_basis_version_and_hold_follows_the_clip() -> None:
    content = EditTimelineContent(
        clips=(
            _clip("c1", "U1", trim=ClipTrim(in_us=500_000, out_us=2_500_000, basis_version=2), hold_us=700_000),
            _clip("c2", "U2", trim=ClipTrim(in_us=1 * S, out_us=2 * S, basis_version=1)),
        )
    )

    placement = place_timeline(content, {"U1": _unit("U1"), "U2": _unit("U2", duration_us=3 * S)})

    first, second = placement.clips
    assert (first.start_us, first.source_in_us, first.source_duration_us) == (0, 500_000, 2 * S)
    assert (first.hold_start_us, first.hold_us, first.source_out_us) == (2 * S, 700_000, 2_500_000)
    # current 已换成版本 2，依据版本 1 的截取作废，整段使用
    assert (second.start_us, second.source_in_us, second.source_duration_us) == (2_700_000, 0, 3 * S)
    assert placement.duration_us == 5_700_000


def test_trim_out_point_is_clamped_to_the_current_video() -> None:
    content = EditTimelineContent(clips=(_clip("c1", "U1", trim=ClipTrim(in_us=1 * S, out_us=9 * S, basis_version=2)),))

    placement = place_timeline(content, {"U1": _unit("U1", duration_us=3 * S)})

    assert (placement.clips[0].source_in_us, placement.clips[0].source_duration_us) == (1 * S, 2 * S)


def test_deleted_units_are_skipped_and_volume_follows_the_provider_audio_switch() -> None:
    content = EditTimelineContent(
        clips=(
            _clip("c1", "U1", source_volume=0.3),
            _clip("c2", "GONE"),
            _clip("c3", "U3", source_volume=0.8),
        )
    )

    placement = place_timeline(content, {"U1": _unit("U1"), "U3": _unit("U3", gain=0.0)})

    assert [(clip.clip_id, clip.start_us, clip.volume) for clip in placement.clips] == [
        ("c1", 0, 0.3),
        ("c3", 4 * S, 0.0),
    ]


def test_narration_starts_at_its_carrying_clip_and_is_cut_at_the_next_one_and_the_end() -> None:
    content = EditTimelineContent(
        clips=(
            _clip("c1", "U1", carries_narration=True),
            _clip("c2", "U2", carries_narration=True),
            _clip("c3", "U1"),
        )
    )
    units = {
        "U1": _unit("U1", duration_us=2 * S, narration_us=3 * S),
        "U2": _unit("U2", duration_us=1 * S, narration_us=5 * S),
    }

    placement = place_timeline(content, units)

    assert placement.duration_us == 5 * S
    assert placement.narrations == (
        PlacedNarration("c1", "versions/audio/U1_v1.wav", 0, 2 * S),
        PlacedNarration("c2", "versions/audio/U2_v1.wav", 2 * S, 3 * S),
    )


def test_subtitles_follow_narration_only_on_the_carrying_clip() -> None:
    cues = (UnitCue(0, 1 * S, "一"), UnitCue(1 * S, 2 * S, "二二"))
    content = EditTimelineContent(
        clips=(
            _clip("c1", "U1", trim=ClipTrim(in_us=1 * S, out_us=2 * S, basis_version=2), carries_narration=True),
            _clip("c2", "U1"),
        )
    )

    placement = place_timeline(content, {"U1": _unit("U1", cues=cues, narration_us=3 * S)})

    assert placement.subtitles == (PlacedSubtitle(0, 1 * S, "一"), PlacedSubtitle(1 * S, 2 * S, "二二"))


def test_source_time_subtitles_show_only_inside_each_clips_window() -> None:
    cues = (UnitCue(0, 1 * S, "甲"), UnitCue(1 * S, 2 * S, "乙"), UnitCue(3 * S, 1 * S, "丙"))
    content = EditTimelineContent(
        clips=(
            _clip("c1", "U1", trim=ClipTrim(in_us=1_500_000, out_us=3_500_000, basis_version=2), hold_us=S),
            _clip("c2", "U1", trim=ClipTrim(in_us=0, out_us=500_000, basis_version=2)),
        )
    )

    placement = place_timeline(content, {"U1": _unit("U1", cues=cues)})

    assert placement.subtitles == (
        PlacedSubtitle(0, 1_500_000, "乙"),
        PlacedSubtitle(1_500_000, 500_000, "丙"),
        PlacedSubtitle(3 * S, 500_000, "甲"),
    )
