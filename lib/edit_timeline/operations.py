"""剪辑时间线的批量编辑操作：按片段 ID 定位，依次作用于一份内存中的剪辑内容。

应用是纯函数：输入一个修订的内容、片段编号分配器与一集的素材事实，输出新的内容；任一条操作
非法即抛出带操作序号、片段 ID 与合法取值范围的 ``operation_invalid``，调用方整批不写。

插入、删除、移动会改变相邻关系：前后相邻片段变了的切点一律恢复硬切（清掉前一片段上的
``transition_to_next``），需要时在同一批里随后重新设置。旁白承载片段被删除时，旁白改挂到该
视频单元剩下的第一个片段上。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, TypeAdapter

from lib.edit_timeline.errors import EditTimelineError
from lib.edit_timeline.model import (
    DEFAULT_SOURCE_VOLUME,
    VOICEOVER_SOURCE_VOLUME,
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
"""截取后至少保留的画面时长。"""

MAX_HOLD_MICROSECONDS = 10_000_000
MIN_TRANSITION_MICROSECONDS = 100_000
MAX_TRANSITION_MICROSECONDS = 2_000_000
REASON_MAX_LENGTH = 200


def default_source_volume(speech_mode: SpeechMode | None) -> float:
    """新建剪辑片段未指定原声音量时，按视频单元的发声归属取默认值。"""
    return VOICEOVER_SOURCE_VOLUME if speech_mode is SpeechMode.NARRATOR_VOICEOVER else DEFAULT_SOURCE_VOLUME


class _Operation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


_CLIP = Field(min_length=1, description="剪辑片段 ID，如 c3")
_AFTER = Field(description="放到这个剪辑片段之后；null 表示放到最前面")


class TrimSpec(_Operation):
    source_in: FiniteFloat = Field(description="入点，秒，从该视频单元 current 视频的开头算起")
    source_out: FiniteFloat = Field(description="出点，秒，须大于入点且不超过视频时长")


class TransitionSpec(_Operation):
    type: TransitionType = Field(description="转场类型")
    duration: FiniteFloat = Field(description="转场时长，秒，0.1–2")


class InsertClip(_Operation):
    op: Literal["insert"]
    unit_id: str = Field(min_length=1, description="视频单元 ID；同一视频单元可以插入多次")
    after: str | None = _AFTER
    source_volume: FiniteFloat | None = Field(
        default=None, description="原声音量 0–1；省略时画外音单位 0.3，台词与无人声单位 1.0"
    )
    trim: TrimSpec | None = Field(default=None, description="截取；省略时整段使用")
    hold: FiniteFloat = Field(default=0.0, description="尾部定格延长，秒，0–10")
    reason: str | None = Field(default=None, description="剪辑理由，渲染时忽略")
    transition_to_next: TransitionSpec | None = Field(default=None, description="到下一片段的转场；省略为硬切")


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
    trim: TrimSpec | None = Field(description="新的入出点；null 表示取消截取、整段使用")


class SetVolume(_Operation):
    op: Literal["set_volume"]
    clip: str = _CLIP
    volume: FiniteFloat = Field(description="原声音量，线性倍数 0–1，不支持放大")


class SetHold(_Operation):
    op: Literal["set_hold"]
    clip: str = _CLIP
    hold: FiniteFloat = Field(description="尾部定格延长，秒，0–10；0 表示不延长")


class SetReason(_Operation):
    op: Literal["set_reason"]
    clip: str = _CLIP
    reason: str | None = Field(description="剪辑理由；null 表示清除")


class SetTransition(_Operation):
    op: Literal["set_transition"]
    clip: str = _CLIP
    transition: TransitionSpec | None = Field(description="到下一片段的转场；null 表示恢复硬切")


type TimelineOperation = Annotated[
    InsertClip | DeleteClip | MoveClip | SetTrim | SetVolume | SetHold | SetReason | SetTransition,
    Field(discriminator="op"),
]

TimelineOperationAdapter: TypeAdapter[TimelineOperation] = TypeAdapter(TimelineOperation)


@dataclass(frozen=True, slots=True)
class AppliedBatch:
    """一批操作应用后的剪辑内容。

    ``targets`` 是操作的作用对象，``referenced`` 另含用作位置锚点的片段；``last_operation`` 记录
    每个被改动片段最后一次被哪条操作（从 0 起）改动，含插入、跟随相邻关系清掉转场与旁白改挂。
    """

    content: EditTimelineContent
    next_clip_number: int
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
    def __init__(self, content: EditTimelineContent, next_clip_number: int, sources: EpisodeSources) -> None:
        self.clips: list[EditClip] = list(content.clips)
        self.next_clip_number = next_clip_number
        self.sources = sources
        self.targets: set[str] = set()
        self.referenced: set[str] = set()
        self.last_operation: dict[str, int] = {}

    # ---- 定位 ----

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
        """截取所依据的 current 视频：（版本号, 时长微秒）。"""
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

    # ---- 字段校验 ----

    def _trim(self, index: int, clip_id: str | None, unit_id: str, spec: TrimSpec | None) -> ClipTrim | None:
        if spec is None:
            return None
        version, whole_us = self._current_video(index, clip_id, unit_id)
        whole = microseconds_to_seconds(whole_us)
        in_range = 0 <= spec.source_in < spec.source_out <= whole + 0.001
        in_us = seconds_to_microseconds(spec.source_in) if in_range else 0
        out_us = seconds_to_microseconds(spec.source_out) if in_range else 0
        # 出点按毫秒规整后可能比实测时长多出至多 1 毫秒，规整回视频末尾。
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

    # ---- 相邻关系 ----

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

    # ---- 操作 ----

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

    # ---- 整批检查 ----

    def _duration_us(self, clip: EditClip) -> int | None:
        unit = self.sources.unit(clip.unit_id)
        media = self.sources.media.get(clip.unit_id)
        if unit is None or media is None:
            return None
        return clip_source_duration_us(clip, media, unit.scripted_duration_us) + clip.hold_us

    def check_transition_windows(self) -> None:
        """被本批改动过的片段须容得下两侧转场各占的一半；已删除单元与素材未知的片段不查。"""
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


def apply_operations(
    content: EditTimelineContent,
    next_clip_number: int,
    operations: Sequence[TimelineOperation],
    sources: EpisodeSources,
    *,
    check_windows: bool = True,
) -> AppliedBatch:
    """依次应用一批操作；任一条非法即抛出 ``operation_invalid``。BGM 轨原样保留。

    ``check_windows`` 为 False 时跳过整批的转场窗口检查，只用于先判断并发冲突的预演。
    """
    batch = _Batch(content, next_clip_number, sources)
    for index, operation in enumerate(operations):
        batch.apply(index, operation)
    if check_windows:
        batch.check_transition_windows()
    return AppliedBatch(
        content=EditTimelineContent(clips=tuple(batch.clips), bgm=content.bgm),
        next_clip_number=batch.next_clip_number,
        targets=frozenset(batch.targets),
        referenced=frozenset(batch.referenced),
        last_operation=dict(batch.last_operation),
    )


@dataclass(frozen=True, slots=True)
class ContentDiff:
    """两份剪辑内容之间改动过的片段：新增、删除、字段变化，以及相对顺序变化（按最长公共子序列判定）。"""

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
    old = {clip.id: clip for clip in before.clips}
    new = {clip.id: clip for clip in after.clips}
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
    """整段内容替换改动过的片段。

    相对顺序变了时，哪个片段「被移动」没有唯一答案，因此两份内容共有的片段一律计入，宁可让基于旧修订的
    编辑多报冲突，也不放过被改排的片段。
    """
    diff = diff_content(before, after)
    if not diff.moved:
        return diff.changed
    return diff.changed | ({clip.id for clip in after.clips} & {clip.id for clip in before.clips})


def sorted_clip_ids(clip_ids: set[str] | frozenset[str]) -> tuple[str, ...]:
    return tuple(sorted(clip_ids, key=clip_number))


__all__ = [
    "MAX_HOLD_MICROSECONDS",
    "MAX_TRANSITION_MICROSECONDS",
    "MIN_TRANSITION_MICROSECONDS",
    "MIN_TRIM_MICROSECONDS",
    "REASON_MAX_LENGTH",
    "AppliedBatch",
    "ContentDiff",
    "DeleteClip",
    "InsertClip",
    "MoveClip",
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
