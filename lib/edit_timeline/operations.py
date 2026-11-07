"""Batch editing-timeline operations: locate clips by ID and apply sequentially to in-memory editing content.

Application is pure: input one revision's content, clip-ID allocators, and episode media facts; output
new content. Invalid operations raise ``operation_invalid`` with operation index, clip ID, and allowed
ranges, so callers persist none of the batch.

Insert/delete/move changes adjacency: cuts whose neighbors change reset to hard cuts by clearing the
preceding clip's ``transition_to_next``; set again later in the same batch if needed. When narration's
carrier is deleted, reattach to that video unit's first remaining clip; narration placement reattaches
to a specified clip of the same unit.

BGM clips use absolute starts, unaffected by main-track insert/delete/move. After applying the batch,
check affected BGM clips: starts inside timeline, fades fit, only one BGM at a time. Do not reject
portions beyond timeline end here; truncate on rendering (:mod:`lib.edit_timeline.bgm`)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, TypeAdapter

from lib.edit_timeline.bgm import bgm_clip_length_us
from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.model import (
    DEFAULT_BGM_FADE_MICROSECONDS,
    DEFAULT_BGM_VOLUME,
    DEFAULT_SOURCE_VOLUME,
    VOICEOVER_SOURCE_VOLUME,
    BgmClip,
    ClipTrim,
    EditClip,
    EditTimelineContent,
    Transition,
    TransitionType,
    clip_number,
    microseconds_to_seconds,
    seconds_to_microseconds,
)
from lib.edit_timeline.readout import clip_source_duration_us
from lib.edit_timeline.sources import EpisodeSources
from lib.speech.speech_composition import SpeechMode

MIN_TRIM_MICROSECONDS = 100_000
"""Minimum visual duration retained after trimming."""

MAX_HOLD_MICROSECONDS = 10_000_000
MIN_TRANSITION_MICROSECONDS = 100_000
MAX_TRANSITION_MICROSECONDS = 2_000_000
REASON_MAX_LENGTH = 200
MIN_BGM_MICROSECONDS = 1_000_000
"""Minimum BGM-clip duration retained after trimming."""


def default_source_volume(speech_mode: SpeechMode | None) -> float:
    """Default source volume for new clips without explicit volume, based on video-unit speech attribution."""
    return VOICEOVER_SOURCE_VOLUME if speech_mode is SpeechMode.NARRATOR_VOICEOVER else DEFAULT_SOURCE_VOLUME


class _Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


_CLIP = Field(min_length=1, description="Edit clip ID, for example c3")
_AFTER = Field(description="Place after this edit clip; null means at the beginning")


class TrimSpec(_Operation):
    source_in: FiniteFloat = Field(
        description="Source in-point in seconds, measured from the beginning of this unit's current video"
    )
    source_out: FiniteFloat = Field(
        description="Source out-point in seconds; must exceed the in-point and not exceed video duration"
    )


class TransitionSpec(_Operation):
    type: TransitionType = Field(description="Transition type")
    duration: FiniteFloat = Field(description="Transition duration in seconds, 0.1–2")


class InsertClip(_Operation):
    op: Literal["insert"]
    unit_id: str = Field(min_length=1, description="Video-unit ID; the same unit may be inserted multiple times")
    after: str | None = _AFTER
    source_volume: FiniteFloat | None = Field(
        default=None,
        description="Original audio volume 0–1; default 0.3 for voiceover units, 1.0 for dialogue units and units without speech",
    )
    trim: TrimSpec | None = Field(default=None, description="Trim; omit to use the whole clip")
    hold: FiniteFloat = Field(default=0.0, description="End-frame hold extension in seconds, 0–10")
    reason: str | None = Field(default=None, description="Editing rationale, ignored during rendering")
    transition_to_next: TransitionSpec | None = Field(
        default=None, description="Transition to the next clip; omit for a hard cut"
    )


class DeleteClip(_Operation):
    op: Literal["delete"]
    clip: str = _CLIP


class MoveClip(_Operation):
    op: Literal["move"]
    clip: str = _CLIP
    after: str | None = _AFTER


class SetTrim(_Operation):
    op: Literal["set_trim"]
    clip: str = _CLIP
    trim: TrimSpec | None = Field(description="New source in/out points; null cancels trimming and uses the whole clip")


class SetVolume(_Operation):
    op: Literal["set_volume"]
    clip: str = _CLIP
    volume: FiniteFloat = Field(
        description="Original audio volume, linear multiplier 0–1; amplification is unsupported"
    )


class SetHold(_Operation):
    op: Literal["set_hold"]
    clip: str = _CLIP
    hold: FiniteFloat = Field(description="End-frame hold extension in seconds, 0–10; 0 means no extension")


class SetReason(_Operation):
    op: Literal["set_reason"]
    clip: str = _CLIP
    reason: str | None = Field(description="Editing rationale; null clears it")


class SetTransition(_Operation):
    op: Literal["set_transition"]
    clip: str = _CLIP
    transition: TransitionSpec | None = Field(description="Transition to the next clip; null restores a hard cut")


class PlaceNarration(_Operation):
    op: Literal["place_narration"]
    clip: str = Field(
        min_length=1,
        description="Edit clip ID carrying narration; must belong to a voiceover unit. Remove narration from the clip previously carrying it for the same video unit",
    )


_BGM_CLIP = Field(min_length=1, description="BGM clip ID, for example b2")


class InsertBgm(_Operation):
    op: Literal["insert_bgm"]
    bgm_id: str = Field(min_length=1, description="BGM ID (for example bgm-3f9a0c21), from list_bgm")
    start: FiniteFloat = Field(
        description="Absolute start on the edit timeline in seconds; must lie within the timeline"
    )
    source_in: FiniteFloat = Field(
        default=0.0, description="BGM source start in seconds; omit to start from the beginning"
    )
    source_out: FiniteFloat | None = Field(
        default=None,
        description="BGM source end in seconds; must exceed source_in and not exceed BGM duration; omit to use through the end",
    )
    volume: FiniteFloat = Field(
        default=DEFAULT_BGM_VOLUME,
        description="Volume 0–1, multiplying loudness already normalized to −16 LUFS; default 0.25",
    )
    fade_in: FiniteFloat = Field(
        default=DEFAULT_BGM_FADE_MICROSECONDS / 1_000_000, description="Fade-in seconds; default 1"
    )
    fade_out: FiniteFloat = Field(
        default=DEFAULT_BGM_FADE_MICROSECONDS / 1_000_000, description="Fade-out seconds; default 1"
    )


class SetBgm(_Operation):
    op: Literal["set_bgm"]
    clip: str = _BGM_CLIP
    start: FiniteFloat | None = Field(
        default=None, description="New absolute start in seconds; omit to leave unchanged"
    )
    source_in: FiniteFloat | None = Field(
        default=None, description="New source in-point in seconds; omit to leave unchanged"
    )
    source_out: FiniteFloat | None = Field(
        default=None, description="New source out-point in seconds; omit to leave unchanged"
    )
    volume: FiniteFloat | None = Field(default=None, description="New volume 0–1; omit to leave unchanged")
    fade_in: FiniteFloat | None = Field(default=None, description="New fade-in seconds; omit to leave unchanged")
    fade_out: FiniteFloat | None = Field(default=None, description="New fade-out seconds; omit to leave unchanged")


class DeleteBgm(_Operation):
    op: Literal["delete_bgm"]
    clip: str = _BGM_CLIP


type TimelineOperation = Annotated[
    InsertClip
    | DeleteClip
    | MoveClip
    | SetTrim
    | SetVolume
    | SetHold
    | SetReason
    | SetTransition
    | PlaceNarration
    | InsertBgm
    | SetBgm
    | DeleteBgm,
    Field(discriminator="op"),
]

TimelineOperationAdapter: TypeAdapter[TimelineOperation] = TypeAdapter(TimelineOperation)


@dataclass(frozen=True, slots=True)
class AppliedBatch:
    """Editing content after applying one operation batch.

    ``targets`` contains operation subjects; ``referenced`` additionally includes positional anchors.
    ``last_operation`` records the last zero-based operation changing each affected clip, including
    insertions, adjacency-triggered transition clearing, and narration reassignment."""

    content: EditTimelineContent
    next_clip_number: int
    next_bgm_number: int
    targets: frozenset[str]
    referenced: frozenset[str]
    last_operation: dict[str, int]


def _invalid(index: int, message: str, *, clip_id: str | None = None, **params: Any) -> EditTimelineError:
    location = f"第 {index + 1} 条操作（operations[{index}]）"
    subject = f"片段 {clip_id}：" if clip_id is not None else ""
    return EditTimelineError(
        "operation_invalid", f"{location}{subject}{message}", operation_index=index, clip_id=clip_id, **params
    )


class _Batch:
    def __init__(
        self, content: EditTimelineContent, next_clip_number: int, next_bgm_number: int, sources: EpisodeSources
    ) -> None:
        self.clips: list[EditClip] = list(content.clips)
        self.bgm: list[BgmClip] = list(content.bgm)
        self.next_clip_number = next_clip_number
        self.next_bgm_number = next_bgm_number
        self.sources = sources
        self.targets: set[str] = set()
        self.referenced: set[str] = set()
        self.last_operation: dict[str, int] = {}

    # ---- Location ----

    def _position(self, index: int, clip_id: str, *, field: str = "clip") -> int:
        self.referenced.add(clip_id)
        if field == "clip":
            self.targets.add(clip_id)
        for position, clip in enumerate(self.clips):
            if clip.id == clip_id:
                return position
        raise _invalid(
            index,
            "剪辑片段不存在（或已在本批前面的操作中删除）",
            clip_id=clip_id,
            field=field,
            allowed="当前修订里的片段 ID",
        )

    def _insertion_point(self, index: int, after: str | None) -> int:
        return 0 if after is None else self._position(index, after, field="after") + 1

    def _touch(self, index: int, clip: EditClip) -> EditClip:
        self.last_operation[clip.id] = index
        return clip

    def _replace(self, index: int, position: int, **changes: Any) -> None:
        updated = self.clips[position].model_copy(update=changes)
        if updated != self.clips[position]:
            self.clips[position] = self._touch(index, updated)

    def _current_video(self, index: int, clip_id: str | None, unit_id: str) -> tuple[int, int]:
        """Current video underlying trim: (version number, duration in microseconds)."""
        media = self.sources.media.get(unit_id)
        if self.sources.unit(unit_id) is None or media is None:
            raise _invalid(
                index,
                f"视频单元 {unit_id} 已从脚本删除，不能截取",
                clip_id=clip_id,
                field="trim",
                allowed="引用脚本中现有视频单元的片段",
            )
        if media.video_version is None or media.video_duration_us is None:
            raise _invalid(
                index,
                f"视频单元 {unit_id} 还没有可用视频，不能截取",
                clip_id=clip_id,
                field="trim",
                allowed="视频单元已有可用视频的片段",
            )
        return media.video_version, media.video_duration_us

    # ---- Field validation ----

    def _trim(self, index: int, clip_id: str | None, unit_id: str, spec: TrimSpec | None) -> ClipTrim | None:
        if spec is None:
            return None
        version, whole_us = self._current_video(index, clip_id, unit_id)
        whole = microseconds_to_seconds(whole_us)
        in_range = 0 <= spec.source_in < spec.source_out <= whole + 0.001
        in_us = seconds_to_microseconds(spec.source_in) if in_range else 0
        out_us = seconds_to_microseconds(spec.source_out) if in_range else 0
        # Millisecond rounding may put the out-point at most 1 ms beyond measured duration; clamp to video end.
        if whole_us < out_us <= whole_us + 1_000:
            out_us = whole_us
        allowed = (
            f"0 ≤ source_in < source_out ≤ {whole}，且至少保留 {microseconds_to_seconds(MIN_TRIM_MICROSECONDS)} 秒"
        )
        if not in_range or out_us > whole_us or out_us - in_us < MIN_TRIM_MICROSECONDS:
            raise _invalid(
                index,
                f"入出点 {spec.source_in}–{spec.source_out} 秒超出范围，current 视频（版本 {version}）"
                f"共 {whole} 秒；合法范围：{allowed}",
                clip_id=clip_id,
                field="trim",
                allowed=allowed,
            )
        return ClipTrim(in_us=in_us, out_us=out_us, basis_version=version)

    def _volume(self, index: int, clip_id: str | None, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise _invalid(
                index,
                f"原声音量 {value} 超出范围；合法范围：0–1",
                clip_id=clip_id,
                field="source_volume",
                allowed="0–1",
            )
        return value

    def _hold(self, index: int, clip_id: str | None, value: float) -> int:
        limit = microseconds_to_seconds(MAX_HOLD_MICROSECONDS)
        if not 0 <= value <= limit:
            raise _invalid(
                index,
                f"定格延长 {value} 秒超出范围；合法范围：0–{limit} 秒",
                clip_id=clip_id,
                field="hold",
                allowed=f"0–{limit}",
            )
        return seconds_to_microseconds(value)

    def _reason(self, index: int, clip_id: str | None, value: str | None) -> str | None:
        reason = value.strip() if value is not None else ""
        if not reason:
            return None
        if len(reason) > REASON_MAX_LENGTH:
            raise _invalid(
                index,
                f"剪辑理由超过 {REASON_MAX_LENGTH} 个字符",
                clip_id=clip_id,
                field="reason",
                allowed=f"1–{REASON_MAX_LENGTH} 个字符或 null",
            )
        return reason

    def _transition(self, index: int, clip_id: str | None, spec: TransitionSpec | None) -> Transition | None:
        if spec is None:
            return None
        low = microseconds_to_seconds(MIN_TRANSITION_MICROSECONDS)
        high = microseconds_to_seconds(MAX_TRANSITION_MICROSECONDS)
        if not low <= spec.duration <= high:
            raise _invalid(
                index,
                f"转场时长 {spec.duration} 秒超出范围；合法范围：{low}–{high} 秒",
                clip_id=clip_id,
                field="transition.duration",
                allowed=f"{low}–{high}",
            )
        return Transition(type=spec.type, duration_us=seconds_to_microseconds(spec.duration))

    def _require_next(self, index: int, position: int, transition: Transition | None) -> None:
        if transition is not None and position == len(self.clips) - 1:
            raise _invalid(
                index,
                "它是最后一个剪辑片段，后面没有下一片段，不能设置转场",
                clip_id=self.clips[position].id,
                field="transition",
                allowed="不是最后一个的片段",
            )

    # ---- Adjacency ----

    def _next_ids(self) -> dict[str, str | None]:
        return {
            clip.id: (self.clips[position + 1].id if position + 1 < len(self.clips) else None)
            for position, clip in enumerate(self.clips)
        }

    def _reset_broken_cuts(self, index: int, before: dict[str, str | None]) -> None:
        after = self._next_ids()
        for position, clip in enumerate(self.clips):
            if clip.transition_to_next is not None and before.get(clip.id, after[clip.id]) != after[clip.id]:
                self._replace(index, position, transition_to_next=None)

    # ---- Operations ----

    def apply(self, index: int, operation: TimelineOperation) -> None:
        match operation:
            case InsertClip():
                self._insert(index, operation)
            case DeleteClip():
                self._delete(index, operation)
            case MoveClip():
                self._move(index, operation)
            case SetTrim(clip=clip_id, trim=spec):
                position = self._position(index, clip_id)
                trim = self._trim(index, clip_id, self.clips[position].unit_id, spec)
                self._replace(index, position, trim=trim)
            case SetVolume(clip=clip_id, volume=volume):
                position = self._position(index, clip_id)
                self._replace(index, position, source_volume=self._volume(index, clip_id, volume))
            case SetHold(clip=clip_id, hold=hold):
                position = self._position(index, clip_id)
                self._replace(index, position, hold_us=self._hold(index, clip_id, hold))
            case SetReason(clip=clip_id, reason=reason):
                position = self._position(index, clip_id)
                self._replace(index, position, reason=self._reason(index, clip_id, reason))
            case SetTransition(clip=clip_id, transition=spec):
                position = self._position(index, clip_id)
                transition = self._transition(index, clip_id, spec)
                self._require_next(index, position, transition)
                self._replace(index, position, transition_to_next=transition)
            case PlaceNarration():
                self._place_narration(index, operation)
            case InsertBgm():
                self._insert_bgm(index, operation)
            case SetBgm():
                self._set_bgm(index, operation)
            case DeleteBgm(clip=clip_id):
                self._touch_bgm(index, self.bgm.pop(self._bgm_position(index, clip_id)))

    def _insert(self, index: int, operation: InsertClip) -> None:
        unit = self.sources.unit(operation.unit_id)
        if unit is None:
            raise _invalid(
                index,
                f"视频单元 {operation.unit_id} 不在集（id={self.sources.script.episode}）的脚本里",
                field="unit_id",
                allowed=f"集（id={self.sources.script.episode}）脚本中的视频单元 ID",
            )
        clip_id = f"c{self.next_clip_number}"
        clip = EditClip(
            id=clip_id,
            unit_id=unit.unit_id,
            trim=self._trim(index, None, unit.unit_id, operation.trim),
            source_volume=(
                self._volume(index, None, operation.source_volume)
                if operation.source_volume is not None
                else default_source_volume(unit.speech_mode)
            ),
            hold_us=self._hold(index, None, operation.hold),
            carries_narration=not any(item.carries_narration for item in self.clips if item.unit_id == unit.unit_id),
            reason=self._reason(index, None, operation.reason),
            transition_to_next=self._transition(index, None, operation.transition_to_next),
        )
        before = self._next_ids()
        position = self._insertion_point(index, operation.after)
        self.clips.insert(position, self._touch(index, clip))
        self.next_clip_number += 1
        self._require_next(index, position, clip.transition_to_next)
        self._reset_broken_cuts(index, before)

    def _place_narration(self, index: int, operation: PlaceNarration) -> None:
        position = self._position(index, operation.clip)
        unit_id = self.clips[position].unit_id
        unit = self.sources.unit(unit_id)
        if unit is None or unit.speech_mode is not SpeechMode.NARRATOR_VOICEOVER:
            raise _invalid(
                index,
                f"视频单元 {unit_id} 不是脚本里的画外音单位，没有旁白可挂",
                clip_id=operation.clip,
                field="clip",
                allowed="画外音单位的剪辑片段",
            )
        for other, clip in enumerate(self.clips):
            if clip.unit_id == unit_id and other != position:
                self._replace(index, other, carries_narration=False)
        self._replace(index, position, carries_narration=True)

    def _delete(self, index: int, operation: DeleteClip) -> None:
        position = self._position(index, operation.clip)
        before = self._next_ids()
        removed = self.clips.pop(position)
        self._touch(index, removed)
        if removed.carries_narration:
            heir = next((p for p, clip in enumerate(self.clips) if clip.unit_id == removed.unit_id), None)
            if heir is not None:
                self._replace(index, heir, carries_narration=True)
        self._reset_broken_cuts(index, before)

    def _move(self, index: int, operation: MoveClip) -> None:
        position = self._position(index, operation.clip)
        if operation.after == operation.clip:
            raise _invalid(
                index, "不能放到自己之后", clip_id=operation.clip, field="after", allowed="其他片段的 ID 或 null"
            )
        before = self._next_ids()
        moving = self.clips.pop(position)
        destination = self._insertion_point(index, operation.after)
        self.clips.insert(destination, self._touch(index, moving) if destination != position else moving)
        self._reset_broken_cuts(index, before)

    # ---- BGM ----

    def _bgm_position(self, index: int, clip_id: str) -> int:
        self.referenced.add(clip_id)
        self.targets.add(clip_id)
        for position, clip in enumerate(self.bgm):
            if clip.id == clip_id:
                return position
        raise _invalid(
            index,
            "BGM 片段不存在（或已在本批前面的操作中删除）",
            clip_id=clip_id,
            field="clip",
            allowed="当前修订里的 BGM 片段 ID",
        )

    def _touch_bgm(self, index: int, clip: BgmClip) -> BgmClip:
        self.last_operation[clip.id] = index
        return clip

    def _bgm_duration_us(self, index: int, clip_id: str | None, bgm_id: str) -> int:
        media = self.sources.bgm.get(bgm_id)
        if media is None:
            raise _invalid(
                index,
                f"BGM {bgm_id} 不在项目里（或文件已不在）",
                clip_id=clip_id,
                field="bgm_id",
                allowed="list_bgm 列出的 BGM ID",
            )
        return media.duration_us

    def _bgm_range(
        self, index: int, clip_id: str | None, bgm_id: str, source_in: float, source_out: float | None
    ) -> tuple[int, int]:
        whole_us = self._bgm_duration_us(index, clip_id, bgm_id)
        whole = microseconds_to_seconds(whole_us)
        out = whole if source_out is None else source_out
        in_range = 0 <= source_in < out <= whole + 0.001
        in_us = seconds_to_microseconds(source_in) if in_range else 0
        out_us = min(seconds_to_microseconds(out), whole_us) if in_range else 0
        minimum = microseconds_to_seconds(MIN_BGM_MICROSECONDS)
        allowed = f"0 ≤ source_in < source_out ≤ {whole}，且至少保留 {minimum} 秒"
        if not in_range or out_us - in_us < min(MIN_BGM_MICROSECONDS, whole_us):
            raise _invalid(
                index,
                f"入出点 {source_in}–{out} 秒超出范围，BGM {bgm_id} 共 {whole} 秒；合法范围：{allowed}",
                clip_id=clip_id,
                field="source_in/source_out",
                allowed=allowed,
            )
        return in_us, out_us

    def _bgm_volume(self, index: int, clip_id: str | None, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise _invalid(
                index, f"BGM 音量 {value} 超出范围；合法范围：0–1", clip_id=clip_id, field="volume", allowed="0–1"
            )
        return value

    def _seconds_at_least_zero(self, index: int, clip_id: str | None, field: str, value: float) -> int:
        if value < 0:
            raise _invalid(index, f"{field} 不能为负数", clip_id=clip_id, field=field, allowed="≥ 0")
        return seconds_to_microseconds(value)

    def _insert_bgm(self, index: int, operation: InsertBgm) -> None:
        in_us, out_us = self._bgm_range(index, None, operation.bgm_id, operation.source_in, operation.source_out)
        clip = BgmClip(
            id=f"b{self.next_bgm_number}",
            bgm_id=operation.bgm_id,
            start_us=self._seconds_at_least_zero(index, None, "start", operation.start),
            in_us=in_us,
            out_us=out_us,
            volume=self._bgm_volume(index, None, operation.volume),
            fade_in_us=self._seconds_at_least_zero(index, None, "fade_in", operation.fade_in),
            fade_out_us=self._seconds_at_least_zero(index, None, "fade_out", operation.fade_out),
        )
        self.next_bgm_number += 1
        self.bgm.append(self._touch_bgm(index, clip))

    def _set_bgm(self, index: int, operation: SetBgm) -> None:
        clip_id = operation.clip
        position = self._bgm_position(index, clip_id)
        current = self.bgm[position]
        changes: dict[str, Any] = {}
        if operation.source_in is not None or operation.source_out is not None:
            changes["in_us"], changes["out_us"] = self._bgm_range(
                index,
                clip_id,
                current.bgm_id,
                operation.source_in if operation.source_in is not None else microseconds_to_seconds(current.in_us),
                operation.source_out if operation.source_out is not None else microseconds_to_seconds(current.out_us),
            )
        if operation.start is not None:
            changes["start_us"] = self._seconds_at_least_zero(index, clip_id, "start", operation.start)
        if operation.volume is not None:
            changes["volume"] = self._bgm_volume(index, clip_id, operation.volume)
        if operation.fade_in is not None:
            changes["fade_in_us"] = self._seconds_at_least_zero(index, clip_id, "fade_in", operation.fade_in)
        if operation.fade_out is not None:
            changes["fade_out_us"] = self._seconds_at_least_zero(index, clip_id, "fade_out", operation.fade_out)
        updated = current.model_copy(update=changes)
        if updated != current:
            self.bgm[position] = self._touch_bgm(index, updated)

    # ---- Whole-batch checks ----

    def _duration_us(self, clip: EditClip) -> int | None:
        unit = self.sources.unit(clip.unit_id)
        media = self.sources.media.get(clip.unit_id)
        if unit is None or media is None:
            return None
        return clip_source_duration_us(clip, media, unit.scripted_duration_us) + clip.hold_us

    def check_transition_windows(self) -> None:
        """Clips changed by this batch must fit half of each adjacent transition; skip deleted units and clips with unknown media."""
        for position, clip in enumerate(self.clips):
            previous = self.clips[position - 1] if position > 0 else None
            owners = [item for item in (previous, clip) if item is not None]
            indexes = [self.last_operation[item.id] for item in owners if item.id in self.last_operation]
            if not indexes:
                continue
            incoming = previous.transition_to_next if previous is not None else None
            outgoing = clip.transition_to_next
            total_us = sum(t.duration_us for t in (incoming, outgoing) if t is not None)
            duration_us = self._duration_us(clip)
            if total_us == 0 or duration_us is None or total_us <= 2 * duration_us:
                continue
            limit = microseconds_to_seconds(2 * duration_us)
            raise _invalid(
                max(indexes),
                f"片段时长 {microseconds_to_seconds(duration_us)} 秒，容不下两侧转场各占的一半："
                f"进出两个转场的时长之和须 ≤ {limit} 秒，当前为 {microseconds_to_seconds(total_us)} 秒",
                clip_id=clip.id,
                field="transition.duration",
                allowed=f"两侧转场时长之和 ≤ {limit}",
            )

    def _timeline_duration_us(self) -> int:
        return sum(duration for clip in self.clips if (duration := self._duration_us(clip)) is not None)

    def check_bgm(self) -> None:
        """BGM clips changed by this batch must start within the timeline without overlapping other BGM clips."""
        touched = [clip for clip in self.bgm if clip.id in self.last_operation]
        if not touched:
            return
        total_us = self._timeline_duration_us()
        total = microseconds_to_seconds(total_us)
        for clip in touched:
            index = self.last_operation[clip.id]
            if clip.start_us >= total_us:
                raise _invalid(
                    index,
                    f"起点 {microseconds_to_seconds(clip.start_us)} 秒不在时间线内（时间线共 {total} 秒）",
                    clip_id=clip.id,
                    field="start",
                    allowed=f"0 ≤ start < {total}",
                )
        ordered = sorted(self.bgm, key=_bgm_order)
        for previous, following in pairwise(ordered):
            previous_end = previous.start_us + bgm_clip_length_us(previous)
            if previous_end <= following.start_us:
                continue
            indexes = [self.last_operation[clip.id] for clip in (previous, following) if clip.id in self.last_operation]
            if not indexes:
                continue
            subject = following if following.id in self.last_operation else previous
            other = previous if subject is following else following
            raise _invalid(
                max(indexes),
                f"与 BGM 片段 {other.id}（{microseconds_to_seconds(other.start_us)}–"
                f"{microseconds_to_seconds(other.start_us + bgm_clip_length_us(other))} 秒）重叠，同一时刻只能有一首 BGM",
                clip_id=subject.id,
                field="start",
                allowed=f"不与 {other.id} 重叠的起点或更短的截取",
            )


def _bgm_order(clip: BgmClip) -> tuple[int, int]:
    return clip.start_us, clip_number(clip.id)


def apply_operations(
    content: EditTimelineContent,
    next_clip_number: int,
    operations: Sequence[TimelineOperation],
    sources: EpisodeSources,
    *,
    next_bgm_number: int,
    check_windows: bool = True,
) -> AppliedBatch:
    """Apply operations sequentially; any invalid operation raises ``operation_invalid``.

    ``check_windows`` False skips whole-batch transition-window/BGM-placement checks, only for previews
    checking concurrent conflicts first."""
    batch = _Batch(content, next_clip_number, next_bgm_number, sources)
    for index, operation in enumerate(operations):
        batch.apply(index, operation)
    if check_windows:
        batch.check_transition_windows()
        batch.check_bgm()
    return AppliedBatch(
        content=EditTimelineContent(clips=tuple(batch.clips), bgm=tuple(sorted(batch.bgm, key=_bgm_order))),
        next_clip_number=batch.next_clip_number,
        next_bgm_number=batch.next_bgm_number,
        targets=frozenset(batch.targets),
        referenced=frozenset(batch.referenced),
        last_operation=dict(batch.last_operation),
    )


@dataclass(frozen=True, slots=True)
class ContentDiff:
    """Clips changed between two content snapshots: added, deleted, fields changed, or relative order changed (longest common subsequence)."""

    added: frozenset[str]
    removed: frozenset[str]
    modified: frozenset[str]
    moved: frozenset[str]

    @property
    def changed(self) -> frozenset[str]:
        return self.added | self.removed | self.modified | self.moved


def _longest_common_subsequence(left: list[str], right: list[str]) -> set[str]:
    lengths = [[0] * (len(right) + 1) for _ in range(len(left) + 1)]
    for i, a in enumerate(left):
        for j, b in enumerate(right):
            lengths[i + 1][j + 1] = lengths[i][j] + 1 if a == b else max(lengths[i][j + 1], lengths[i + 1][j])
    kept: set[str] = set()
    i, j = len(left), len(right)
    while i and j:
        if left[i - 1] == right[j - 1]:
            kept.add(left[i - 1])
            i, j = i - 1, j - 1
        elif lengths[i - 1][j] >= lengths[i][j - 1]:
            i -= 1
        else:
            j -= 1
    return kept


def diff_content(before: EditTimelineContent, after: EditTimelineContent) -> ContentDiff:
    """Compare editing/BGM clips together; BGM uses absolute starts without relative order, so does not count moves."""
    old: dict[str, EditClip | BgmClip] = {clip.id: clip for clip in (*before.clips, *before.bgm)}
    new: dict[str, EditClip | BgmClip] = {clip.id: clip for clip in (*after.clips, *after.bgm)}
    common_before = [clip.id for clip in before.clips if clip.id in new]
    common_after = [clip.id for clip in after.clips if clip.id in old]
    in_order = _longest_common_subsequence(common_before, common_after)
    return ContentDiff(
        added=frozenset(new.keys() - old.keys()),
        removed=frozenset(old.keys() - new.keys()),
        modified=frozenset(clip_id for clip_id in old.keys() & new.keys() if old[clip_id] != new[clip_id]),
        moved=frozenset(clip_id for clip_id in common_before if clip_id not in in_order),
    )


def restore_changed_clip_ids(before: EditTimelineContent, after: EditTimelineContent) -> frozenset[str]:
    """Clips affected by whole-content replacement.

    When relative order changes, there is no unique answer to which clip moved. Include all clips shared
    by both contents, preferring extra conflicts for old-revision edits over overlooking reordered clips."""
    diff = diff_content(before, after)
    if not diff.moved:
        return diff.changed
    return diff.changed | ({clip.id for clip in after.clips} & {clip.id for clip in before.clips})


def sorted_clip_ids(clip_ids: set[str] | frozenset[str]) -> tuple[str, ...]:
    """Editing clips first, BGM clips afterward, each ordered by sequence number."""
    return tuple(sorted(clip_ids, key=lambda clip_id: (clip_id.startswith("b"), clip_number(clip_id))))


__all__ = [
    "MAX_HOLD_MICROSECONDS",
    "MAX_TRANSITION_MICROSECONDS",
    "MIN_BGM_MICROSECONDS",
    "MIN_TRANSITION_MICROSECONDS",
    "MIN_TRIM_MICROSECONDS",
    "REASON_MAX_LENGTH",
    "AppliedBatch",
    "ContentDiff",
    "DeleteBgm",
    "DeleteClip",
    "InsertBgm",
    "InsertClip",
    "MoveClip",
    "PlaceNarration",
    "SetBgm",
    "SetHold",
    "SetReason",
    "SetTransition",
    "SetTrim",
    "SetVolume",
    "TimelineOperation",
    "TimelineOperationAdapter",
    "TransitionSpec",
    "TrimSpec",
    "apply_operations",
    "default_source_volume",
    "diff_content",
    "restore_changed_clip_ids",
    "sorted_clip_ids",
]
