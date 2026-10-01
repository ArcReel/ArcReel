"""剪辑时间线的读取投影：服务端算好的时长、绝对起点、旁白起止与结构类 issues。

投影是纯函数：输入一个修订与一集的素材事实，输出以秒为单位（最多三位小数）的读取结果。
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from lib.edit_timeline.model import (
    EditClip,
    EditTimelineDocument,
    TimelineRevision,
    microseconds_to_seconds,
)
from lib.edit_timeline.sources import EpisodeSources, UnitMedia
from lib.speech.speech_composition import SpeechMode


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


ISSUE_LEVELS: dict[IssueCode, tuple[IssueSeverity, IssueScope]] = {
    IssueCode.TRIM_IGNORED: (IssueSeverity.INFO, IssueScope.ALL),
    IssueCode.UNIT_DELETED: (IssueSeverity.INFO, IssueScope.ALL),
    IssueCode.UNIT_UNUSED: (IssueSeverity.INFO, IssueScope.ALL),
    IssueCode.VIDEO_MISSING: (IssueSeverity.BLOCKING, IssueScope.ALL),
    IssueCode.HOLD_TOO_LONG: (IssueSeverity.WARNING, IssueScope.ALL),
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
    id: str
    bgm_id: str
    start: float
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
    issues.extend(
        timeline_issue(IssueCode.UNIT_UNUSED, unit_id=unit.unit_id)
        for unit in sources.script.units
        if unit.unit_id not in used
    )
    return issues


def project_readout(
    document: EditTimelineDocument, revision: TimelineRevision, sources: EpisodeSources
) -> EditTimelineReadout:
    clips: list[ClipView] = []
    cursor_us = 0
    for clip in revision.content.clips:
        view, duration_us = _clip_view(clip, sources, cursor_us)
        clips.append(view)
        cursor_us += duration_us
    bgm = tuple(
        BgmView(
            id=item.id,
            bgm_id=item.bgm_id,
            start=microseconds_to_seconds(item.start_us),
            source_in=microseconds_to_seconds(item.in_us),
            source_out=microseconds_to_seconds(item.out_us),
            volume=item.volume,
            fade_in=microseconds_to_seconds(item.fade_in_us),
            fade_out=microseconds_to_seconds(item.fade_out_us),
        )
        for item in revision.content.bgm
    )
    return EditTimelineReadout(
        timeline=TimelineIdentity(id=document.id, name=document.name, episode=document.episode),
        revision=revision.number,
        latest_revision=document.latest.number,
        duration=microseconds_to_seconds(cursor_us),
        clips=tuple(clips),
        bgm=bgm,
        issues=tuple(_structural_issues(revision, sources)),
    )


def unrendered_effects(readout: EditTimelineReadout) -> list[str]:
    """成片与剪映草稿都还不能渲染的内容：BGM 轨上的 BGM 片段 ID。"""
    return [item.id for item in readout.bgm]


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
    "clip_source_duration_us",
    "effective_source_range_us",
    "project_readout",
    "timeline_issue",
    "trim_applies",
    "unrendered_effects",
]
