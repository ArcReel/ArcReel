"""成片的渲染规划：把一个修订的剪辑片段落到输出帧网格上，并按硬切边界分段。

规划是纯函数。片段边界按时间线累计时长舍入到帧，而不是逐段舍入，因此总帧数与剪辑时间线
总时长相差不超过半帧，且不随片段数累积漂移；画面与整集混音都按同一组帧边界摆放。

转场不改变片段的帧边界，窗口以切点为中心，前一半在切点之前、后一半在切点之后：

- 重叠型转场：前一片段在出点之后、后一片段在入点之前各多取半个窗口的源素材，两者在窗口内交叉过渡；
  源素材余量不足（或前一片段带定格延长）时，用边缘帧定格补齐。相邻片段因此同属一段。
- 非重叠型转场（闪黑、闪白）：不借素材，前一片段在最后半个窗口淡出，后一片段在开头半个窗口淡入，
  切点两侧仍按硬切分段。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from pathlib import Path

from lib.edit_timeline.model import MICROSECONDS_PER_SECOND, EditClip, Transition
from lib.edit_timeline.readout import effective_source_range_us
from lib.edit_timeline.transitions import transition_preset

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
    media_duration_us: int

    @property
    def source_out_us(self) -> int:
        return self.source_in_us + self.source_duration_us


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
                media_duration_us=unit_media.duration_us,
            )
        )
    return tuple(resolved)


@dataclass(frozen=True, slots=True)
class Extension:
    """片段为重叠型转场在自身区间之外多出的画面：先取 ``source_frames`` 帧源素材，再补 ``freeze_frames`` 帧定格。"""

    source_frames: int = 0
    freeze_frames: int = 0

    @property
    def frames(self) -> int:
        return self.source_frames + self.freeze_frames


def borrow_frames(needed: int, available_us: int, fps: int) -> Extension:
    """从余量 ``available_us`` 的源素材里借 ``needed`` 帧；余量不足一帧的部分用边缘帧定格补齐。"""
    available = max(available_us, 0) * fps // MICROSECONDS_PER_SECOND
    source = min(needed, available)
    return Extension(source_frames=source, freeze_frames=needed - source)


@dataclass(frozen=True, slots=True)
class Fade:
    """非重叠型转场在片段一端的淡入或淡出。"""

    frames: int
    color: str


@dataclass(frozen=True, slots=True)
class PlannedClip:
    """落到帧网格上的剪辑片段：先取 ``source_frames`` 帧源画面，再用出点帧补 ``hold_frames`` 帧。

    ``lead`` / ``trail`` 是为前后重叠型转场在片段区间之外多取的画面，``fade_in`` / ``fade_out`` 是
    前后非重叠型转场的淡入、淡出；它们都不改变片段在时间线上的帧边界。
    """

    clip: RenderClip
    start_frame: int
    source_frames: int
    hold_frames: int
    lead: Extension = Extension()
    trail: Extension = Extension()
    fade_in: Fade | None = None
    fade_out: Fade | None = None

    @property
    def frames(self) -> int:
        return self.source_frames + self.hold_frames

    @property
    def end_frame(self) -> int:
        return self.start_frame + self.frames

    @property
    def stream_frames(self) -> int:
        """渲染这一片段要产出的画面帧数：前借、自身与后借之和。"""
        return self.lead.frames + self.frames + self.trail.frames


@dataclass(frozen=True, slots=True)
class Crossfade:
    """段内一次重叠型转场：``offset_frames`` 是窗口起点相对段起点的帧数。"""

    effect: str
    offset_frames: int
    frames: int


@dataclass(frozen=True, slots=True)
class RenderSegment:
    """硬切边界之间的一段：段内相邻片段以重叠型转场衔接，段与段之间直接拼接。"""

    clips: tuple[PlannedClip, ...]
    crossfades: tuple[Crossfade, ...] = ()

    @property
    def start_frame(self) -> int:
        return self.clips[0].start_frame

    @property
    def frames(self) -> int:
        return sum(planned.frames for planned in self.clips)


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


def _window_frames(transition: Transition, profile: OutputProfile) -> tuple[int, int]:
    """转场窗口在切点之前、之后各占的帧数。"""
    frames = max(profile.frame_at(transition.duration_us), 1)
    before = frames // 2
    return before, frames - before


def _lay_out(clips: Sequence[RenderClip], profile: OutputProfile) -> tuple[list[PlannedClip], int]:
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
    return planned, profile.frame_at(cursor_us)


def _join(previous: PlannedClip, following: PlannedClip, profile: OutputProfile) -> tuple[PlannedClip, PlannedClip]:
    """按前一片段上的转场改写切点两侧的片段。"""
    transition = previous.clip.transition_to_next
    if transition is None:
        return previous, following
    preset = transition_preset(transition.type)
    before, after = _window_frames(transition, profile)
    if preset.fade_color is not None:
        return (
            replace(previous, fade_out=Fade(min(before, previous.frames), preset.fade_color)),
            replace(following, fade_in=Fade(min(after, following.frames), preset.fade_color)),
        )
    # 带定格延长的片段以静帧收尾，转场窗口里延续这一静帧。
    tail_us = 0 if previous.hold_frames else previous.clip.media_duration_us - previous.clip.source_out_us
    return (
        replace(previous, trail=borrow_frames(after, tail_us, profile.fps)),
        replace(following, lead=borrow_frames(before, following.clip.source_in_us, profile.fps)),
    )


def _overlaps(planned: PlannedClip) -> bool:
    transition = planned.clip.transition_to_next
    return transition is not None and transition_preset(transition.type).overlaps


def plan_render(clips: Sequence[RenderClip], profile: OutputProfile) -> RenderPlan:
    """把片段首尾相接地排到帧网格上，展开转场，再按硬切边界分段；不足半帧的片段不进入成片。

    转场只作用于到下一个进入成片的片段之间的切点，最后一个片段上的转场没有效果。
    """
    planned, total_frames = _lay_out(clips, profile)
    for index in range(len(planned) - 1):
        planned[index], planned[index + 1] = _join(planned[index], planned[index + 1], profile)
    segments: list[RenderSegment] = []
    current: list[PlannedClip] = []
    crossfades: list[Crossfade] = []
    for index, item in enumerate(planned):
        current.append(item)
        transition = item.clip.transition_to_next
        if transition is not None and _overlaps(item) and index + 1 < len(planned):
            before, after = _window_frames(transition, profile)
            crossfades.append(
                Crossfade(
                    effect=transition_preset(transition.type).xfade,
                    offset_frames=item.end_frame - before - current[0].start_frame,
                    frames=before + after,
                )
            )
            continue
        segments.append(RenderSegment(clips=tuple(current), crossfades=tuple(crossfades)))
        current, crossfades = [], []
    return RenderPlan(profile=profile, segments=tuple(segments), total_frames=total_frames)


__all__ = [
    "FINAL_CUT_FPS",
    "Crossfade",
    "Extension",
    "Fade",
    "OutputProfile",
    "PlannedClip",
    "RenderClip",
    "RenderMedia",
    "RenderPlan",
    "RenderSegment",
    "borrow_frames",
    "output_profile_for_aspect_ratio",
    "plan_render",
    "render_clips",
]
