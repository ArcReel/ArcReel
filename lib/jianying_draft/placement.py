"""把剪辑时间线的一个修订摆成剪映草稿里的片段：纯函数，时间一律是整数微秒。

剪辑片段首尾相接排在主视频轨上，截取只对其依据的视频版本有效；定格延长以出点帧静帧接在片段之后。
转场挂在前一片段上，只保留到下一个参与导出的片段之间的切点：最后一个参与导出的片段上的转场没有效果。
旁白从承载片段的起点开始；字幕在带旁白的单元上跟随旁白，否则按源素材时间保留、只显示落在入出点之内的部分。
草稿只有一条旁白轨和一条字幕轨，同一条轨内的片段不能重叠：后一段开始时前一段截止，超出时间线末尾的部分截断。
剪辑视图预览的字幕摆放（frontend/src/components/canvas/edit/preview-tracks.ts ``placeSubtitles``）与这里同口径，改动摆放规则时一并修改。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from lib.edit_timeline.model import EditClip, EditTimelineContent, Transition
from lib.edit_timeline.readout import effective_source_range_us


@dataclass(frozen=True, slots=True)
class UnitCue:
    """素材层里的一条字幕：时间相对旁白起点（跟随旁白时）或视频源起点。"""

    start_us: int
    duration_us: int
    text: str

    @property
    def end_us(self) -> int:
        return self.start_us + self.duration_us


@dataclass(frozen=True, slots=True)
class UnitMaterial:
    """一个视频单元作为剪辑片段素材层的事实，取自它当前的呈现模型。

    ``source_gain`` 是供应商原声的开关（生成时未带原声为 0）；``narration_*`` 只在该单元按带旁白版本呈现时给出。
    """

    unit_id: str
    video_path: str
    video_version: int
    video_duration_us: int
    source_gain: float
    subtitles: tuple[UnitCue, ...]
    narration_path: str | None = None
    narration_duration_us: int | None = None

    @property
    def subtitles_follow_narration(self) -> bool:
        return self.narration_path is not None


@dataclass(frozen=True, slots=True)
class PlacedClip:
    """主视频轨上的一个剪辑片段；``hold_us`` 大于 0 时其后接一段出点帧静帧，``transition_to_next`` 是到下一片段的转场。"""

    clip_id: str
    unit_id: str
    video_path: str
    start_us: int
    source_in_us: int
    source_duration_us: int
    volume: float
    hold_us: int
    transition_to_next: Transition | None = None

    @property
    def source_out_us(self) -> int:
        return self.source_in_us + self.source_duration_us

    @property
    def hold_start_us(self) -> int:
        return self.start_us + self.source_duration_us


@dataclass(frozen=True, slots=True)
class PlacedNarration:
    clip_id: str
    audio_path: str
    start_us: int
    duration_us: int


@dataclass(frozen=True, slots=True)
class PlacedSubtitle:
    start_us: int
    duration_us: int
    text: str


@dataclass(frozen=True, slots=True)
class DraftPlacement:
    duration_us: int
    clips: tuple[PlacedClip, ...]
    narrations: tuple[PlacedNarration, ...]
    subtitles: tuple[PlacedSubtitle, ...]


def _source_window(clip: EditClip, unit: UnitMaterial) -> tuple[int, int]:
    """截取后的源素材区间 ``[in, out)``：current 已不是截取所依据的版本时整段使用。"""
    return effective_source_range_us(clip, unit.video_version, unit.video_duration_us)


def _clip_subtitles(clip: PlacedClip, unit: UnitMaterial, *, carries: bool) -> list[PlacedSubtitle]:
    if unit.subtitles_follow_narration:
        if not carries:
            return []
        return [PlacedSubtitle(clip.start_us + cue.start_us, cue.duration_us, cue.text) for cue in unit.subtitles]
    placed: list[PlacedSubtitle] = []
    for cue in unit.subtitles:
        start = max(cue.start_us, clip.source_in_us)
        end = min(cue.end_us, clip.source_out_us)
        if end > start:
            placed.append(PlacedSubtitle(clip.start_us + start - clip.source_in_us, end - start, cue.text))
    return placed


def _without_overlap[T: (PlacedNarration, PlacedSubtitle)](items: list[T], duration_us: int) -> tuple[T, ...]:
    """同一条轨内按起点排列，后一段开始时前一段截止，超出时间线末尾的部分截断，截成空的丢弃。"""
    ordered = sorted(items, key=lambda item: item.start_us)
    kept: list[T] = []
    for index, item in enumerate(ordered):
        limit = ordered[index + 1].start_us if index + 1 < len(ordered) else duration_us
        end = min(item.start_us + item.duration_us, limit, duration_us)
        if end > item.start_us:
            kept.append(_with_duration(item, end - item.start_us))
    return tuple(kept)


def _with_duration[T: (PlacedNarration, PlacedSubtitle)](item: T, duration_us: int) -> T:
    if isinstance(item, PlacedNarration):
        return PlacedNarration(item.clip_id, item.audio_path, item.start_us, duration_us)
    return PlacedSubtitle(item.start_us, duration_us, item.text)


def place_timeline(content: EditTimelineContent, units: Mapping[str, UnitMaterial]) -> DraftPlacement:
    """按修订内容摆放片段；``units`` 里没有的视频单元（已从脚本删除）渲染时跳过。"""
    clips: list[PlacedClip] = []
    narrations: list[PlacedNarration] = []
    subtitles: list[PlacedSubtitle] = []
    cursor = 0
    for clip in content.clips:
        unit = units.get(clip.unit_id)
        if unit is None:
            continue
        source_in, source_out = _source_window(clip, unit)
        placed = PlacedClip(
            clip_id=clip.id,
            unit_id=clip.unit_id,
            video_path=unit.video_path,
            start_us=cursor,
            source_in_us=source_in,
            source_duration_us=source_out - source_in,
            volume=clip.source_volume * unit.source_gain,
            hold_us=clip.hold_us,
            transition_to_next=clip.transition_to_next,
        )
        clips.append(placed)
        if clip.carries_narration and unit.narration_path is not None and unit.narration_duration_us is not None:
            narrations.append(PlacedNarration(clip.id, unit.narration_path, cursor, unit.narration_duration_us))
        subtitles.extend(_clip_subtitles(placed, unit, carries=clip.carries_narration))
        cursor += placed.source_duration_us + placed.hold_us
    if clips and clips[-1].transition_to_next is not None:
        clips[-1] = replace(clips[-1], transition_to_next=None)
    return DraftPlacement(
        duration_us=cursor,
        clips=tuple(clips),
        narrations=_without_overlap(narrations, cursor),
        subtitles=_without_overlap(subtitles, cursor),
    )


__all__ = [
    "DraftPlacement",
    "PlacedClip",
    "PlacedNarration",
    "PlacedSubtitle",
    "UnitCue",
    "UnitMaterial",
    "place_timeline",
]
