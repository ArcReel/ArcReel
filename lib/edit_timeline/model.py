"""剪辑时间线的领域模型（``docs/adr/0090``）。

一集可以有多条具名剪辑时间线；每条按集存成一个文件，内含不可变修订的追加序列。修订内容由
剪辑片段排成的主轨与 BGM 轨组成；内部时间一律是整数微秒，秒制只出现在读取投影与工具入参上。
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

EDIT_TIMELINE_DOCUMENT_SCHEMA = 1

MICROSECONDS_PER_SECOND = 1_000_000

DEFAULT_SOURCE_VOLUME = 1.0
"""台词与无人声单位的剪辑片段原声默认音量：保持原音量。"""

VOICEOVER_SOURCE_VOLUME = 0.3
"""画外音单位的剪辑片段原声默认音量：整轨压低，作为旁白下的环境声垫底。"""

DEFAULT_BGM_VOLUME = 0.25
DEFAULT_BGM_FADE_MICROSECONDS = 1_000_000

TIMELINE_NAME_MAX_LENGTH = 40

TIMELINE_ID_PATTERN = r"^tl-[0-9a-f]{8}$"
CLIP_ID_PATTERN = r"^c[1-9][0-9]*$"
BGM_CLIP_ID_PATTERN = r"^b[1-9][0-9]*$"
ANY_CLIP_ID_PATTERN = r"^[cb][1-9][0-9]*$"
"""剪辑片段（``c``）或 BGM 片段（``b``）的 ID。"""

_TIMELINE_ID_RE = re.compile(TIMELINE_ID_PATTERN)

type Microseconds = Annotated[int, Field(ge=0, strict=True)]
type Volume = Annotated[float, Field(ge=0.0, le=1.0)]
type TimelineName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TIMELINE_NAME_MAX_LENGTH)
]


def is_timeline_id(value: object) -> bool:
    return isinstance(value, str) and _TIMELINE_ID_RE.fullmatch(value) is not None


def seconds_to_microseconds(seconds: float) -> int:
    """秒按毫秒精度规整后换算成整数微秒。"""
    return round(round(seconds, 3) * MICROSECONDS_PER_SECOND)


def microseconds_to_seconds(microseconds: int) -> float:
    """整数微秒换算成最多三位小数的秒。"""
    return round(microseconds / MICROSECONDS_PER_SECOND, 3)


class TransitionType(StrEnum):
    """ArcReel 转场词表：每一项同时对应一个剪映非 VIP 预设与一个 ffmpeg ``xfade`` 等效效果。

    硬切不在词表里，剪辑片段没有转场即为硬切。
    """

    DISSOLVE = "dissolve"
    FADE_BLACK = "fade_black"
    FADE_WHITE = "fade_white"
    PUSH_LEFT = "push_left"
    PUSH_RIGHT = "push_right"
    PUSH_UP = "push_up"
    PUSH_DOWN = "push_down"
    WIPE_LEFT = "wipe_left"
    WIPE_RIGHT = "wipe_right"
    WIPE_UP = "wipe_up"
    WIPE_DOWN = "wipe_down"
    CIRCLE = "circle"
    CURTAIN_HORIZONTAL = "curtain_horizontal"
    CURTAIN_VERTICAL = "curtain_vertical"
    MOSAIC = "mosaic"
    BLUR = "blur"
    RADIAL = "radial"
    GRADIENT_WIPE = "gradient_wipe"
    SQUEEZE = "squeeze"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Transition(_Frozen):
    """相邻两个剪辑片段之间的转场；窗口以切点为中心，前后各占一半，不改变总时长。"""

    type: TransitionType
    duration_us: Microseconds


class ClipTrim(_Frozen):
    """剪辑片段的截取：入出点只对 ``basis_version`` 这一视频版本有效。"""

    in_us: Microseconds
    out_us: Microseconds
    basis_version: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def _out_after_in(self) -> ClipTrim:
        if self.out_us <= self.in_us:
            raise ValueError("out_us must be greater than in_us")
        return self


class EditClip(_Frozen):
    """主轨上的一格：引用视频单元，画面一律取该单元的 current 视频版本。

    ``trim`` 为 None 时整段使用；``transition_to_next`` 为 None 时与下一片段硬切。
    """

    id: str = Field(pattern=CLIP_ID_PATTERN)
    unit_id: str = Field(min_length=1)
    trim: ClipTrim | None = None
    source_volume: Volume
    hold_us: Microseconds = 0
    carries_narration: bool = False
    reason: str | None = None
    transition_to_next: Transition | None = None


class BgmClip(_Frozen):
    """BGM 轨上的一段：同一时刻只有一首，超出时间线末尾的部分截断。"""

    id: str = Field(pattern=BGM_CLIP_ID_PATTERN)
    bgm_id: str = Field(min_length=1)
    start_us: Microseconds
    in_us: Microseconds
    out_us: Microseconds
    volume: Volume = DEFAULT_BGM_VOLUME
    fade_in_us: Microseconds = DEFAULT_BGM_FADE_MICROSECONDS
    fade_out_us: Microseconds = DEFAULT_BGM_FADE_MICROSECONDS

    @model_validator(mode="after")
    def _out_after_in(self) -> BgmClip:
        if self.out_us <= self.in_us:
            raise ValueError("out_us must be greater than in_us")
        return self


class EditTimelineContent(_Frozen):
    """一个修订的剪辑内容：主轨按相对顺序排列，绝对时间由读取时计算。"""

    clips: tuple[EditClip, ...] = ()
    bgm: tuple[BgmClip, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> EditTimelineContent:
        clip_ids = [clip.id for clip in self.clips]
        if len(set(clip_ids)) != len(clip_ids):
            raise ValueError("clip ids must be unique")
        bgm_ids = [clip.id for clip in self.bgm]
        if len(set(bgm_ids)) != len(bgm_ids):
            raise ValueError("bgm clip ids must be unique")
        return self

    @model_validator(mode="after")
    def _one_narration_carrier_per_unit(self) -> EditTimelineContent:
        carriers = [clip.unit_id for clip in self.clips if clip.carries_narration]
        if len(set(carriers)) != len(carriers):
            raise ValueError("a video unit's narration can be carried by at most one clip")
        return self


def clip_number(clip_id: str) -> int:
    """片段编号里的序号：``c12`` / ``b3`` → 12 / 3。"""
    return int(clip_id[1:])


type AuthorKind = Literal["creator", "arcreel_agent", "external_agent"]


class RevisionAuthor(_Frozen):
    """修订作者：创作者（Web）、ArcReel Agent 或外部 Agent。"""

    kind: AuthorKind
    user_id: str | None = None


class TimelineRevision(_Frozen):
    """不可变修订；回滚与复制同样追加新修订，不改写旧修订。"""

    number: int = Field(ge=1, strict=True)
    parent: int | None = Field(default=None, ge=1, strict=True)
    author: RevisionAuthor
    summary: str = Field(min_length=1)
    created_at: str
    content: EditTimelineContent
    changed_clip_ids: tuple[Annotated[str, Field(pattern=ANY_CLIP_ID_PATTERN)], ...] | None = None
    """本修订实际改动的剪辑片段与 BGM 片段（含批内改后恢复）；旧修订缺省时由内容差异推断。"""
    restored_from: int | None = Field(default=None, ge=1, strict=True)
    """回滚产生的修订记录它还原到的修订号；内容取自那一修订，历史不改写。"""


class EditTimelineDocument(_Frozen):
    """一条剪辑时间线的持久化形态。

    ``next_clip_number`` / ``next_bgm_number`` 是片段编号分配器：编号在这条时间线内稳定、不复用。
    """

    schema_version: Literal[1] = EDIT_TIMELINE_DOCUMENT_SCHEMA
    id: str = Field(pattern=TIMELINE_ID_PATTERN)
    episode: int = Field(ge=1, strict=True)
    name: TimelineName
    created_at: str
    next_clip_number: int = Field(ge=1, strict=True)
    next_bgm_number: int = Field(default=1, ge=1, strict=True)
    revisions: tuple[TimelineRevision, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _revision_chain(self) -> EditTimelineDocument:
        for index, revision in enumerate(self.revisions, start=1):
            if revision.number != index:
                raise ValueError("revisions must be numbered consecutively from 1")
        for revision in self.revisions:
            if any(clip_number(clip.id) >= self.next_clip_number for clip in revision.content.clips):
                raise ValueError("clip ids must be allocated below next_clip_number")
            if any(clip_number(clip.id) >= self.next_bgm_number for clip in revision.content.bgm):
                raise ValueError("bgm clip ids must be allocated below next_bgm_number")
        return self

    @property
    def latest(self) -> TimelineRevision:
        return self.revisions[-1]

    def revision(self, number: int) -> TimelineRevision | None:
        if 1 <= number <= len(self.revisions):
            return self.revisions[number - 1]
        return None


__all__ = [
    "ANY_CLIP_ID_PATTERN",
    "BGM_CLIP_ID_PATTERN",
    "CLIP_ID_PATTERN",
    "DEFAULT_BGM_FADE_MICROSECONDS",
    "DEFAULT_BGM_VOLUME",
    "DEFAULT_SOURCE_VOLUME",
    "EDIT_TIMELINE_DOCUMENT_SCHEMA",
    "MICROSECONDS_PER_SECOND",
    "TIMELINE_ID_PATTERN",
    "TIMELINE_NAME_MAX_LENGTH",
    "VOICEOVER_SOURCE_VOLUME",
    "AuthorKind",
    "BgmClip",
    "ClipTrim",
    "EditClip",
    "EditTimelineContent",
    "EditTimelineDocument",
    "RevisionAuthor",
    "TimelineName",
    "TimelineRevision",
    "Transition",
    "TransitionType",
    "clip_number",
    "is_timeline_id",
    "microseconds_to_seconds",
    "seconds_to_microseconds",
]
