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
    UNIT_DELETED = "unit_deleted"
    UNIT_UNUSED = "unit_unused"
    VIDEO_MISSING = "video_missing"


ISSUE_LEVELS: dict[IssueCode, tuple[IssueSeverity, IssueScope]] = {
    IssueCode.UNIT_DELETED: (IssueSeverity.INFO, IssueScope.ALL),
    IssueCode.UNIT_UNUSED: (IssueSeverity.INFO, IssueScope.ALL),
    IssueCode.VIDEO_MISSING: (IssueSeverity.BLOCKING, IssueScope.ALL),
}
"""每种 issue 的固定级别与影响范围；读取结果与出片前的阻断检查共用这张表。"""


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
    """

    id: str
    unit_id: str
    status: ClipStatus
    start: float
    duration: float
    video_version: int | None
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


def _clip_source_duration_us(clip: EditClip, media: UnitMedia, scripted_us: int) -> int:
    """片段截取后的画面时长：截取只对其依据的视频版本有效，current 已换版本时整段使用。"""
    whole = media.video_duration_us if media.video_duration_us is not None else scripted_us
    if media.video_version is None or clip.trim is None or clip.trim.basis_version != media.video_version:
        return whole
    return max(0, min(clip.trim.out_us, whole) - clip.trim.in_us)


def _clip_view(clip: EditClip, sources: EpisodeSources, start_us: int) -> tuple[ClipView, int]:
    unit = sources.unit(clip.unit_id)
    media = sources.media.get(clip.unit_id)
    if unit is None or media is None:
        status: ClipStatus = "unit_deleted"
        duration_us = 0
        narration = None
    else:
        status = "ready" if media.video_version is not None else "video_missing"
        duration_us = _clip_source_duration_us(clip, media, unit.scripted_duration_us) + clip.hold_us
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


__all__ = [
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
    "project_readout",
    "timeline_issue",
]
