"""剪辑时间线的读取投影：服务端算好的时长、绝对起点、旁白起止与结构类 issues。

投影是纯函数：输入一个修订与一集的素材事实，输出以秒为单位（最多三位小数）的读取结果。
字幕缺字按随包字幕字体的字符覆盖表判断，覆盖表只读一次。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from lib.edit_timeline.bgm import place_bgm
from lib.edit_timeline.model import (
    VOICEOVER_SOURCE_VOLUME,
    EditClip,
    EditTimelineContent,
    EditTimelineDocument,
    TimelineRevision,
    microseconds_to_seconds,
)
from lib.edit_timeline.sources import EpisodeSources, UnitMedia
from lib.speech.speech_composition import SpeechMode
from lib.subtitle_style.font import missing_glyphs


class IssueSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    BLOCKING = "blocking"


class IssueScope(StrEnum):
    """issue 影响的渲染版本：全部版本，或只影响带旁白版本。"""

    ALL = "all"
    WITH_NARRATION = "with_narration"


class IssueCode(StrEnum):
    TRIM_IGNORED = "trim_ignored"
    UNIT_DELETED = "unit_deleted"
    UNIT_UNUSED = "unit_unused"
    VIDEO_MISSING = "video_missing"
    HOLD_TOO_LONG = "hold_too_long"
    NARRATION_MISSING = "narration_missing"
    NARRATION_OVERRUN = "narration_overrun"
    NARRATION_SOURCE_COLLISION = "narration_source_collision"
    SUBTITLE_MISSING_GLYPHS = "subtitle_missing_glyphs"
    BGM_MISSING = "bgm_missing"


ISSUE_LEVELS: dict[IssueCode, tuple[IssueSeverity, IssueScope]] = {
    IssueCode.TRIM_IGNORED: (IssueSeverity.INFO, IssueScope.ALL),
    IssueCode.UNIT_DELETED: (IssueSeverity.INFO, IssueScope.ALL),
    IssueCode.UNIT_UNUSED: (IssueSeverity.INFO, IssueScope.ALL),
    IssueCode.VIDEO_MISSING: (IssueSeverity.BLOCKING, IssueScope.ALL),
    IssueCode.HOLD_TOO_LONG: (IssueSeverity.WARNING, IssueScope.ALL),
    IssueCode.NARRATION_MISSING: (IssueSeverity.BLOCKING, IssueScope.WITH_NARRATION),
    IssueCode.NARRATION_OVERRUN: (IssueSeverity.WARNING, IssueScope.WITH_NARRATION),
    IssueCode.NARRATION_SOURCE_COLLISION: (IssueSeverity.WARNING, IssueScope.WITH_NARRATION),
    IssueCode.SUBTITLE_MISSING_GLYPHS: (IssueSeverity.WARNING, IssueScope.ALL),
    IssueCode.BGM_MISSING: (IssueSeverity.BLOCKING, IssueScope.ALL),
}
"""每种 issue 的固定级别与影响范围；读取结果与出片前的阻断检查共用这张表。"""

HOLD_WARNING_MICROSECONDS = 2_000_000
"""单个剪辑片段的定格延长超过这个时长即报定格过长。"""


class _View(BaseModel):
    model_config = ConfigDict(frozen=True)


class TimelineIssue(_View):
    code: IssueCode
    severity: IssueSeverity
    applies_to: IssueScope
    clip_ids: tuple[str, ...] = ()
    unit_id: str | None = None
    params: dict[str, Any] = {}


def timeline_issue(
    code: IssueCode, *, clip_ids: tuple[str, ...] = (), unit_id: str | None = None, **params: Any
) -> TimelineIssue:
    severity, scope = ISSUE_LEVELS[code]
    return TimelineIssue(
        code=code, severity=severity, applies_to=scope, clip_ids=clip_ids, unit_id=unit_id, params=params
    )


type ClipStatus = Literal["ready", "video_missing", "unit_deleted"]


class TrimView(_View):
    source_in: float
    source_out: float
    basis_version: int


class TransitionView(_View):
    type: str
    duration: float


class NarrationView(_View):
    """旁白从承载片段的起点开始；配音时长未知（没有旁白配音）时 ``end`` 为 None。"""

    start: float
    end: float | None


class ClipView(_View):
    """剪辑片段的读取形态。

    ``status`` 为 ``unit_deleted`` 时渲染跳过，时长计 0；为 ``video_missing`` 时时长暂按编排时长占位。
    ``source_duration`` 是 current 视频的全长，没有可用视频或时长探测不出时为 None。
    """

    id: str
    unit_id: str
    status: ClipStatus
    start: float
    duration: float
    video_version: int | None
    source_duration: float | None
    trim: TrimView | None
    source_volume: float
    hold: float
    carries_narration: bool
    narration: NarrationView | None
    reason: str | None
    transition_to_next: TransitionView | None


class BgmView(_View):
    """BGM 片段的读取形态。``end`` 是截到时间线末尾后的实际结束时间，片段完全落在末尾之后时等于 ``start``；
    ``fade_in`` / ``fade_out`` 是摆放后实际生效的淡入淡出（截断处固定淡出、超出片段时长时缩短），片段完全落在
    末尾之后时是所设的值；``name`` 是所引用 BGM 的名称，BGM 不在项目里时为 None。"""

    id: str
    bgm_id: str
    name: str | None
    start: float
    end: float
    source_in: float
    source_out: float
    volume: float
    fade_in: float
    fade_out: float


class TimelineIdentity(_View):
    id: str
    name: str
    episode: int


class EditTimelineReadout(_View):
    timeline: TimelineIdentity
    revision: int
    latest_revision: int
    duration: float
    clips: tuple[ClipView, ...]
    bgm: tuple[BgmView, ...]
    issues: tuple[TimelineIssue, ...]


def trim_applies(clip: EditClip, media: UnitMedia) -> bool:
    """截取只对其依据的视频版本有效；current 已换版本或没有可用视频时整段使用。"""
    return clip.trim is not None and media.video_version is not None and clip.trim.basis_version == media.video_version


def effective_source_range_us(clip: EditClip, video_version: int | None, whole_us: int) -> tuple[int, int]:
    """片段实际取用的源素材区间；截取只对其依据的视频版本有效，入点与出点都不超过视频全长。"""
    trim = clip.trim
    if video_version is None or trim is None or trim.basis_version != video_version:
        return 0, whole_us
    source_in = min(trim.in_us, whole_us)
    return source_in, max(source_in, min(trim.out_us, whole_us))


def clip_source_duration_us(clip: EditClip, media: UnitMedia, scripted_us: int) -> int:
    """片段截取后的画面时长（不含定格延长）；没有可用视频时按编排时长占位。"""
    whole = media.video_duration_us if media.video_duration_us is not None else scripted_us
    source_in, source_out = effective_source_range_us(clip, media.video_version, whole)
    return source_out - source_in


def _clip_view(clip: EditClip, sources: EpisodeSources, start_us: int) -> tuple[ClipView, int]:
    unit = sources.unit(clip.unit_id)
    media = sources.media.get(clip.unit_id)
    if unit is None or media is None:
        status: ClipStatus = "unit_deleted"
        duration_us = 0
        narration = None
    else:
        status = "ready" if media.video_version is not None else "video_missing"
        duration_us = clip_source_duration_us(clip, media, unit.scripted_duration_us) + clip.hold_us
        narration = None
        if clip.carries_narration and unit.speech_mode is SpeechMode.NARRATOR_VOICEOVER:
            end_us = start_us + media.narration_duration_us if media.narration_duration_us is not None else None
            narration = NarrationView(
                start=microseconds_to_seconds(start_us),
                end=microseconds_to_seconds(end_us) if end_us is not None else None,
            )
    trim = clip.trim
    view = ClipView(
        id=clip.id,
        unit_id=clip.unit_id,
        status=status,
        start=microseconds_to_seconds(start_us),
        duration=microseconds_to_seconds(duration_us),
        video_version=media.video_version if media is not None else None,
        source_duration=(
            microseconds_to_seconds(media.video_duration_us)
            if media is not None and media.video_version is not None and media.video_duration_us is not None
            else None
        ),
        trim=(
            TrimView(
                source_in=microseconds_to_seconds(trim.in_us),
                source_out=microseconds_to_seconds(trim.out_us),
                basis_version=trim.basis_version,
            )
            if trim is not None
            else None
        ),
        source_volume=clip.source_volume,
        hold=microseconds_to_seconds(clip.hold_us),
        carries_narration=clip.carries_narration,
        narration=narration,
        reason=clip.reason,
        transition_to_next=(
            TransitionView(
                type=clip.transition_to_next.type.value,
                duration=microseconds_to_seconds(clip.transition_to_next.duration_us),
            )
            if clip.transition_to_next is not None
            else None
        ),
    )
    return view, duration_us


def _structural_issues(revision: TimelineRevision, sources: EpisodeSources) -> list[TimelineIssue]:
    issues: list[TimelineIssue] = []
    used: dict[str, list[str]] = {}
    for clip in revision.content.clips:
        if sources.unit(clip.unit_id) is None:
            issues.append(timeline_issue(IssueCode.UNIT_DELETED, clip_ids=(clip.id,), unit_id=clip.unit_id))
            continue
        used.setdefault(clip.unit_id, []).append(clip.id)
        media = sources.media.get(clip.unit_id)
        if (
            clip.trim is not None
            and media is not None
            and media.video_version is not None
            and not trim_applies(clip, media)
        ):
            issues.append(
                timeline_issue(
                    IssueCode.TRIM_IGNORED,
                    clip_ids=(clip.id,),
                    unit_id=clip.unit_id,
                    basis_version=clip.trim.basis_version,
                    current_version=media.video_version,
                )
            )
        if clip.hold_us > HOLD_WARNING_MICROSECONDS:
            issues.append(
                timeline_issue(
                    IssueCode.HOLD_TOO_LONG,
                    clip_ids=(clip.id,),
                    unit_id=clip.unit_id,
                    hold=microseconds_to_seconds(clip.hold_us),
                    limit=microseconds_to_seconds(HOLD_WARNING_MICROSECONDS),
                )
            )
    for unit_id, clip_ids in used.items():
        media = sources.media.get(unit_id)
        if media is None or media.video_version is None:
            issues.append(timeline_issue(IssueCode.VIDEO_MISSING, clip_ids=tuple(clip_ids), unit_id=unit_id))
        unit = sources.unit(unit_id)
        missing = missing_glyphs(unit.subtitle_text) if unit is not None else ""
        if missing:
            issues.append(
                timeline_issue(
                    IssueCode.SUBTITLE_MISSING_GLYPHS, clip_ids=tuple(clip_ids), unit_id=unit_id, characters=missing
                )
            )
    issues.extend(
        timeline_issue(IssueCode.UNIT_UNUSED, unit_id=unit.unit_id)
        for unit in sources.script.units
        if unit.unit_id not in used
    )
    return issues


@dataclass(frozen=True, slots=True)
class _Placed:
    clip: EditClip
    start_us: int
    duration_us: int


@dataclass(frozen=True, slots=True)
class _Narration:
    carrier: _Placed
    position: int
    end_us: int


def _narration_issues(placed: list[_Placed], sources: EpisodeSources, total_us: int) -> list[TimelineIssue]:
    """旁白配音缺失、越界与可能与原声相撞；只在 TTS 配音项目里检查。

    旁白从承载片段的起点开始，按配音实测时长延伸，可以覆盖后续片段。越界指与下一段旁白重叠或超出时间线末尾；
    相撞指旁白延伸到台词单位的片段，或原声音量高于画外音默认值的片段上。
    """
    if not sources.tts_narration:
        return []
    issues: list[TimelineIssue] = []
    narrations: list[_Narration] = []
    for position, item in enumerate(placed):
        clip = item.clip
        unit = sources.unit(clip.unit_id)
        media = sources.media.get(clip.unit_id)
        if not clip.carries_narration or unit is None or media is None:
            continue
        if unit.speech_mode is not SpeechMode.NARRATOR_VOICEOVER:
            continue
        if media.narration_duration_us is None:
            issues.append(timeline_issue(IssueCode.NARRATION_MISSING, clip_ids=(clip.id,), unit_id=clip.unit_id))
            continue
        narrations.append(_Narration(item, position, item.start_us + media.narration_duration_us))
    for index, narration in enumerate(narrations):
        carrier = narration.carrier.clip
        following = narrations[index + 1] if index + 1 < len(narrations) else None
        if following is not None and narration.end_us > following.carrier.start_us:
            overlap_us = min(narration.end_us, following.end_us) - following.carrier.start_us
            issues.append(
                timeline_issue(
                    IssueCode.NARRATION_OVERRUN,
                    clip_ids=(carrier.id, following.carrier.clip.id),
                    unit_id=carrier.unit_id,
                    cause="next_narration",
                    next_unit_id=following.carrier.clip.unit_id,
                    overlap=microseconds_to_seconds(overlap_us),
                )
            )
        if narration.end_us > total_us:
            issues.append(
                timeline_issue(
                    IssueCode.NARRATION_OVERRUN,
                    clip_ids=(carrier.id,),
                    unit_id=carrier.unit_id,
                    cause="timeline_end",
                    overflow=microseconds_to_seconds(narration.end_us - total_us),
                )
            )
        for item in placed[narration.position + 1 :]:
            if item.start_us >= narration.end_us:
                break
            unit = sources.unit(item.clip.unit_id)
            if item.duration_us == 0 or unit is None:
                continue
            if unit.speech_mode is SpeechMode.CHARACTER_SPEECH:
                cause = "dialogue"
            elif item.clip.source_volume > VOICEOVER_SOURCE_VOLUME:
                cause = "source_volume"
            else:
                continue
            issues.append(
                timeline_issue(
                    IssueCode.NARRATION_SOURCE_COLLISION,
                    clip_ids=(carrier.id, item.clip.id),
                    unit_id=carrier.unit_id,
                    cause=cause,
                    other_unit_id=item.clip.unit_id,
                    source_volume=item.clip.source_volume,
                    overlap=microseconds_to_seconds(
                        min(narration.end_us, item.start_us + item.duration_us) - item.start_us
                    ),
                )
            )
    return issues


def bgm_missing_issues(content: EditTimelineContent, available: Collection[str]) -> list[TimelineIssue]:
    """BGM 片段引用的 BGM 不在 ``available`` 里（已不在项目里或文件已不在）时阻断出片。"""
    return [
        timeline_issue(IssueCode.BGM_MISSING, clip_ids=(item.id,), bgm_id=item.bgm_id)
        for item in sorted(content.bgm, key=lambda item: item.start_us)
        if item.bgm_id not in available
    ]


def project_readout(
    document: EditTimelineDocument, revision: TimelineRevision, sources: EpisodeSources
) -> EditTimelineReadout:
    clips: list[ClipView] = []
    placed: list[_Placed] = []
    cursor_us = 0
    for clip in revision.content.clips:
        view, duration_us = _clip_view(clip, sources, cursor_us)
        clips.append(view)
        placed.append(_Placed(clip, cursor_us, duration_us))
        cursor_us += duration_us
    placements = {placed.clip_id: placed for placed in place_bgm(revision.content.bgm, cursor_us)}
    bgm = tuple(
        BgmView(
            id=item.id,
            bgm_id=item.bgm_id,
            name=media.name if (media := sources.bgm.get(item.bgm_id)) is not None else None,
            start=microseconds_to_seconds(item.start_us),
            end=microseconds_to_seconds(placed.end_us if placed is not None else item.start_us),
            source_in=microseconds_to_seconds(item.in_us),
            source_out=microseconds_to_seconds(item.out_us),
            volume=item.volume,
            fade_in=microseconds_to_seconds(placed.fade_in_us if placed is not None else item.fade_in_us),
            fade_out=microseconds_to_seconds(placed.fade_out_us if placed is not None else item.fade_out_us),
        )
        for item in sorted(revision.content.bgm, key=lambda item: item.start_us)
        for placed in (placements.get(item.id),)
    )
    return EditTimelineReadout(
        timeline=TimelineIdentity(id=document.id, name=document.name, episode=document.episode),
        revision=revision.number,
        latest_revision=document.latest.number,
        duration=microseconds_to_seconds(cursor_us),
        clips=tuple(clips),
        bgm=bgm,
        issues=(
            *_structural_issues(revision, sources),
            *_narration_issues(placed, sources, cursor_us),
            *bgm_missing_issues(revision.content, sources.bgm),
        ),
    )


__all__ = [
    "HOLD_WARNING_MICROSECONDS",
    "ISSUE_LEVELS",
    "BgmView",
    "ClipStatus",
    "ClipView",
    "EditTimelineReadout",
    "IssueCode",
    "IssueScope",
    "IssueSeverity",
    "NarrationView",
    "TimelineIdentity",
    "TimelineIssue",
    "TransitionView",
    "TrimView",
    "bgm_missing_issues",
    "clip_source_duration_us",
    "effective_source_range_us",
    "project_readout",
    "timeline_issue",
    "trim_applies",
]
