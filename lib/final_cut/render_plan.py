"""成片的渲染规划：把一个修订的剪辑片段落到输出帧网格上，并按硬切边界分段。

规划是纯函数。片段边界按时间线累计时长舍入到帧，而不是逐段舍入，因此总帧数与剪辑时间线
总时长相差不超过半帧，且不随片段数累积漂移；画面与整集混音都按同一组帧边界摆放。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from lib.edit_timeline.model import MICROSECONDS_PER_SECOND, EditClip, Transition
from lib.edit_timeline.readout import effective_source_range_us

FINAL_CUT_FPS = 30
"""成片固定帧率：不同帧率的素材一律规整到这一帧率。"""

_SHORT_EDGE = 1080


@dataclass(frozen=True, slots=True)
class OutputProfile:
    """成片的画布与帧率。"""

    width: int
    height: int
    fps: int

    def frame_at(self, microseconds: int) -> int:
        """时间线上某一时刻最近的帧边界。"""
        return round(Fraction(microseconds * self.fps, MICROSECONDS_PER_SECOND))

    def seconds(self, frames: int) -> float:
        return frames / self.fps


def output_profile_for_aspect_ratio(aspect_ratio: str) -> OutputProfile:
    """按项目画幅取成片画布：短边 1080，长边按比例取偶数；无法解析的比例按 16:9。"""
    width_part, _, height_part = aspect_ratio.partition(":")
    try:
        ratio = Fraction(int(width_part), int(height_part))
    except (ValueError, ZeroDivisionError):
        ratio = Fraction(16, 9)
    if ratio <= 0:
        ratio = Fraction(16, 9)
    if ratio >= 1:
        width, height = round(_SHORT_EDGE * ratio / 2) * 2, _SHORT_EDGE
    else:
        width, height = _SHORT_EDGE, round(_SHORT_EDGE / ratio / 2) * 2
    return OutputProfile(width=width, height=height, fps=FINAL_CUT_FPS)


@dataclass(frozen=True, slots=True)
class RenderMedia:
    """一个视频单元 current 视频的渲染素材：路径、版本、实测时长与是否带音频流。"""

    path: Path
    video_version: int
    duration_us: int
    has_audio: bool


@dataclass(frozen=True, slots=True)
class RenderClip:
    """一个剪辑片段实际取用的素材：源区间已按截取规则解析。"""

    clip_id: str
    unit_id: str
    video_path: Path
    source_in_us: int
    source_duration_us: int
    hold_us: int
    source_volume: float
    has_audio: bool
    transition_to_next: Transition | None


def render_clips(clips: Sequence[EditClip], media: Mapping[str, RenderMedia]) -> tuple[RenderClip, ...]:
    """按修订的片段顺序解析每个剪辑片段的素材；没有渲染素材的片段（视频单元已删除）跳过。"""
    resolved: list[RenderClip] = []
    for clip in clips:
        unit_media = media.get(clip.unit_id)
        if unit_media is None:
            continue
        source_in, source_out = effective_source_range_us(clip, unit_media.video_version, unit_media.duration_us)
        resolved.append(
            RenderClip(
                clip_id=clip.id,
                unit_id=clip.unit_id,
                video_path=unit_media.path,
                source_in_us=source_in,
                source_duration_us=source_out - source_in,
                hold_us=clip.hold_us,
                source_volume=clip.source_volume,
                has_audio=unit_media.has_audio,
                transition_to_next=clip.transition_to_next,
            )
        )
    return tuple(resolved)


@dataclass(frozen=True, slots=True)
class PlannedClip:
    """落到帧网格上的剪辑片段：先取 ``source_frames`` 帧源画面，再用出点帧补 ``hold_frames`` 帧。"""

    clip: RenderClip
    start_frame: int
    source_frames: int
    hold_frames: int

    @property
    def frames(self) -> int:
        return self.source_frames + self.hold_frames


@dataclass(frozen=True, slots=True)
class RenderSegment:
    """硬切边界之间的一段：段内相邻片段以转场衔接，段与段之间直接拼接。"""

    clips: tuple[PlannedClip, ...]

    @property
    def start_frame(self) -> int:
        return self.clips[0].start_frame

    @property
    def frames(self) -> int:
        return sum(planned.frames for planned in self.clips)

    @property
    def has_transitions(self) -> bool:
        return len(self.clips) > 1


@dataclass(frozen=True, slots=True)
class RenderPlan:
    profile: OutputProfile
    segments: tuple[RenderSegment, ...]
    total_frames: int

    @property
    def clips(self) -> tuple[PlannedClip, ...]:
        return tuple(planned for segment in self.segments for planned in segment.clips)

    @property
    def duration_seconds(self) -> float:
        return self.profile.seconds(self.total_frames)


def plan_render(clips: Sequence[RenderClip], profile: OutputProfile) -> RenderPlan:
    """把片段首尾相接地排到帧网格上，再按硬切边界分段；不足半帧的片段不进入成片。"""
    planned: list[PlannedClip] = []
    cursor_us = 0
    for clip in clips:
        start = profile.frame_at(cursor_us)
        source_end = profile.frame_at(cursor_us + clip.source_duration_us)
        cursor_us += clip.source_duration_us + clip.hold_us
        end = profile.frame_at(cursor_us)
        if end > start:
            planned.append(
                PlannedClip(
                    clip=clip, start_frame=start, source_frames=source_end - start, hold_frames=end - source_end
                )
            )
    segments: list[RenderSegment] = []
    current: list[PlannedClip] = []
    for index, item in enumerate(planned):
        current.append(item)
        joins_next = item.clip.transition_to_next is not None and index + 1 < len(planned)
        if not joins_next:
            segments.append(RenderSegment(clips=tuple(current)))
            current = []
    return RenderPlan(profile=profile, segments=tuple(segments), total_frames=profile.frame_at(cursor_us))


__all__ = [
    "FINAL_CUT_FPS",
    "OutputProfile",
    "PlannedClip",
    "RenderClip",
    "RenderMedia",
    "RenderPlan",
    "RenderSegment",
    "output_profile_for_aspect_ratio",
    "plan_render",
    "render_clips",
]
