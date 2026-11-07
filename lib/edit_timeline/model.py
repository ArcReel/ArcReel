"""Editing-timeline domain model (``docs/adr/0090``).

One episode may have multiple named timelines, each stored in its own file under the episode with
an appended sequence of immutable revisions. Revision content consists of an ordered main clip track
and a BGM track. All internal times are integer microseconds; seconds appear only in read projections
and tool arguments."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

EDIT_TIMELINE_DOCUMENT_SCHEMA = 1

MICROSECONDS_PER_SECOND = 1_000_000

DEFAULT_SOURCE_VOLUME = 1.0
"""Default clip source volume for dialogue units and units without speech: preserve original volume."""

VOICEOVER_SOURCE_VOLUME = 0.3
"""Default clip source volume for voiceover units: lower the whole track to provide ambient sound beneath narration."""

DEFAULT_BGM_VOLUME = 0.25
DEFAULT_BGM_FADE_MICROSECONDS = 1_000_000

TIMELINE_NAME_MAX_LENGTH = 40

TIMELINE_ID_PATTERN = r"^tl-[0-9a-f]{8}$"
CLIP_ID_PATTERN = r"^c[1-9][0-9]*$"
BGM_CLIP_ID_PATTERN = r"^b[1-9][0-9]*$"
ANY_CLIP_ID_PATTERN = r"^[cb][1-9][0-9]*$"
"""ID for an editing clip (``c``) or BGM clip (``b``)."""

_TIMELINE_ID_RE = re.compile(TIMELINE_ID_PATTERN)

type Microseconds = Annotated[int, Field(ge=0, strict=True)]
type Volume = Annotated[float, Field(ge=0.0, le=1.0)]
type TimelineName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=TIMELINE_NAME_MAX_LENGTH)
]


def is_timeline_id(value: object) -> bool:
    return isinstance(value, str) and _TIMELINE_ID_RE.fullmatch(value) is not None


def seconds_to_microseconds(seconds: float) -> int:
    """Normalize seconds to millisecond precision, then convert to integer microseconds."""
    return round(round(seconds, 3) * MICROSECONDS_PER_SECOND)


def microseconds_to_seconds(microseconds: int) -> float:
    """Convert integer microseconds to seconds with at most three decimal places."""
    return round(microseconds / MICROSECONDS_PER_SECOND, 3)


class TransitionType(StrEnum):
    """ArcReel transition vocabulary: every entry maps to both a non-VIP Jianying preset and an equivalent ffmpeg ``xfade`` effect.

    Hard cuts are outside this vocabulary: a clip without a transition uses a hard cut.
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
    """Transition between adjacent editing clips; its window is centered on the cut, half on each side, without changing total duration."""

    type: TransitionType
    duration_us: Microseconds


class ClipTrim(_Frozen):
    """Clip trim whose in/out points apply only to video version ``basis_version``."""

    in_us: Microseconds
    out_us: Microseconds
    basis_version: int = Field(ge=1, strict=True)

    @model_validator(mode="after")
    def _out_after_in(self) -> ClipTrim:
        if self.out_us <= self.in_us:
            raise ValueError("out_us must be greater than in_us")
        return self


class EditClip(_Frozen):
    """One main-track slot referencing a video unit, always using its current video version.

    ``trim`` None uses the entire video; ``transition_to_next`` None makes a hard cut to the next clip."""

    id: str = Field(pattern=CLIP_ID_PATTERN)
    unit_id: str = Field(min_length=1)
    trim: ClipTrim | None = None
    source_volume: Volume
    hold_us: Microseconds = 0
    carries_narration: bool = False
    reason: str | None = None
    transition_to_next: Transition | None = None


class BgmClip(_Frozen):
    """One BGM-track clip: only one plays at a time, with portions beyond timeline end truncated."""

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
    """One revision's editing content: relative main-track order, with absolute times computed on read."""

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
    """Sequence number in clip IDs: ``c12`` / ``b3`` → 12 / 3."""
    return int(clip_id[1:])


type AuthorKind = Literal["creator", "arcreel_agent", "external_agent"]


class RevisionAuthor(_Frozen):
    """Revision author: creator (Web), ArcReel Agent, or external Agent."""

    kind: AuthorKind
    user_id: str | None = None


class TimelineRevision(_Frozen):
    """Immutable revision; rollback and copy likewise append new revisions without rewriting old ones."""

    number: int = Field(ge=1, strict=True)
    parent: int | None = Field(default=None, ge=1, strict=True)
    author: RevisionAuthor
    summary: str = Field(min_length=1)
    created_at: str
    content: EditTimelineContent
    changed_clip_ids: tuple[Annotated[str, Field(pattern=ANY_CLIP_ID_PATTERN)], ...] | None = None
    """Editing/BGM clips actually changed by this revision, including changes reverted within the batch; infer from content differences when absent in old revisions."""
    restored_from: int | None = Field(default=None, ge=1, strict=True)
    """Rollback-created revisions record the restored revision number, taking its content without rewriting history."""


class EditTimelineDocument(_Frozen):
    """Persisted form of one editing timeline.

    ``next_clip_number`` / ``next_bgm_number`` allocate clip IDs, stable and never reused within this timeline."""

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
