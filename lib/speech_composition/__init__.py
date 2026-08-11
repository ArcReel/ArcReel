"""Unified speech ownership for video generation units."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from lib.asset_types import normalize_asset_name
from lib.reference_video.shot_parser import (
    find_malformed_mention,
    leading_mention_before_colon,
    match_dialogue_line,
    match_voiceover_line,
)


class SpeechOwner(StrEnum):
    """The entity responsible for delivering one utterance."""

    CHARACTER = "character"
    NARRATOR = "narrator"


class SpeechMode(StrEnum):
    """The exclusive speech ownership mechanically derived for one video unit."""

    CHARACTER_SPEECH = "character_speech"
    NARRATOR_VOICEOVER = "narrator_voiceover"
    SILENT = "silent"


class SpeechProblemCode(StrEnum):
    """Stable problem codes shared by Web and Agent adapters."""

    MIXED_SPEECH = "mixed_speech"
    NEEDS_REPLAN = "needs_replan"
    PARSE_FAILED = "parse_failed"
    EMPTY_SPEAKER = "empty_speaker"


class SpeechProblemReason(StrEnum):
    """Closed reasons that explain why preparation is unsafe."""

    CHARACTER_AND_NARRATOR_MIXED = "character_and_narrator_mixed"
    UNIT_MARKED_NEEDS_REPLAN = "unit_marked_needs_replan"
    SPEECH_INPUT_UNPARSEABLE = "speech_input_unparseable"
    CHARACTER_SPEAKER_EMPTY = "character_speaker_empty"


class SpeechProblemAction(StrEnum):
    """Closed next actions for repairing a speech problem."""

    REPLAN_UNIT = "replan_unit"
    FIX_INPUT = "fix_input"
    ASSIGN_SPEAKER = "assign_speaker"


@dataclass(frozen=True, slots=True)
class SpeechFieldLocation:
    """A source field position; ``line`` is zero-based when present."""

    path: tuple[str | int, ...]
    line: int | None = None


@dataclass(frozen=True, slots=True)
class SpeechProblem:
    """A machine-readable blocker with unit and source-field locations."""

    code: SpeechProblemCode
    unit_id: str
    locations: tuple[SpeechFieldLocation, ...]
    reason: SpeechProblemReason
    action: SpeechProblemAction


@dataclass(frozen=True, slots=True)
class SpeechInputUtterance:
    """Structure-only utterance translated by an input adapter."""

    text: str
    speaker: str | None
    speaker_required: bool
    location: SpeechFieldLocation


@dataclass(frozen=True, slots=True)
class SpeechUtterance:
    """One ordered piece of spoken content with mechanically derived ownership."""

    owner: SpeechOwner
    text: str
    speaker: str | None
    location: SpeechFieldLocation


@dataclass(frozen=True, slots=True)
class SpeechUnitSnapshot:
    """Content-skeleton-neutral input consumed by :class:`SpeechComposition`."""

    unit_id: str
    entries: tuple[SpeechInputUtterance, ...]
    problems: tuple[SpeechProblem, ...] = ()


@dataclass(frozen=True, slots=True)
class SpeechPreparation:
    """Speech ownership result for one video generation unit."""

    unit_id: str
    mode: SpeechMode | None
    utterances: tuple[SpeechUtterance, ...]
    problems: tuple[SpeechProblem, ...] = ()


class SpeechComposition:
    """Derive unit-wide speech facts from a content-neutral snapshot."""

    @staticmethod
    def prepare(snapshot: SpeechUnitSnapshot) -> SpeechPreparation:
        problems = list(snapshot.problems)
        utterances: list[SpeechUtterance] = []
        for entry in snapshot.entries:
            speaker = entry.speaker.strip() if isinstance(entry.speaker, str) else ""
            if entry.speaker_required and not speaker:
                problems.append(_empty_speaker_problem(snapshot.unit_id, entry.location))
            owner = SpeechOwner.CHARACTER if speaker or entry.speaker_required else SpeechOwner.NARRATOR
            utterances.append(
                SpeechUtterance(
                    owner=owner,
                    text=entry.text,
                    speaker=speaker or None,
                    location=entry.location,
                )
            )

        owners = {entry.owner for entry in utterances}
        if owners == {SpeechOwner.CHARACTER, SpeechOwner.NARRATOR}:
            problems.append(
                SpeechProblem(
                    code=SpeechProblemCode.MIXED_SPEECH,
                    unit_id=snapshot.unit_id,
                    locations=tuple(entry.location for entry in utterances),
                    reason=SpeechProblemReason.CHARACTER_AND_NARRATOR_MIXED,
                    action=SpeechProblemAction.REPLAN_UNIT,
                )
            )

        if problems:
            mode = None
        elif owners == {SpeechOwner.CHARACTER}:
            mode = SpeechMode.CHARACTER_SPEECH
        elif owners == {SpeechOwner.NARRATOR}:
            mode = SpeechMode.NARRATOR_VOICEOVER
        else:
            mode = SpeechMode.SILENT
        return SpeechPreparation(snapshot.unit_id, mode, tuple(utterances), tuple(problems))


_EMPTY_SPEAKER_LINE = re.compile(r"^\s*@\[\s*\]\s*[:：]\s*\{([^{}]+)\}\s*$")
_MISSING = object()


def _empty_speaker_problem(unit_id: str, location: SpeechFieldLocation) -> SpeechProblem:
    return SpeechProblem(
        code=SpeechProblemCode.EMPTY_SPEAKER,
        unit_id=unit_id,
        locations=(location,),
        reason=SpeechProblemReason.CHARACTER_SPEAKER_EMPTY,
        action=SpeechProblemAction.ASSIGN_SPEAKER,
    )


def _parse_problem(unit_id: str, location: SpeechFieldLocation) -> SpeechProblem:
    return SpeechProblem(
        code=SpeechProblemCode.PARSE_FAILED,
        unit_id=unit_id,
        locations=(location,),
        reason=SpeechProblemReason.SPEECH_INPUT_UNPARSEABLE,
        action=SpeechProblemAction.FIX_INPUT,
    )


def _initial_problems(source: Mapping[str, object], unit_id: str, id_field: str) -> list[SpeechProblem]:
    problems: list[SpeechProblem] = []
    if not unit_id.strip():
        problems.append(_parse_problem(unit_id, SpeechFieldLocation((id_field,))))
    needs_replan = source.get("needs_replan")
    if needs_replan is True:
        problems.append(
            SpeechProblem(
                code=SpeechProblemCode.NEEDS_REPLAN,
                unit_id=unit_id,
                locations=(SpeechFieldLocation(("needs_replan",)),),
                reason=SpeechProblemReason.UNIT_MARKED_NEEDS_REPLAN,
                action=SpeechProblemAction.REPLAN_UNIT,
            )
        )
    elif needs_replan is not None and not isinstance(needs_replan, bool):
        problems.append(_parse_problem(unit_id, SpeechFieldLocation(("needs_replan",))))
    return problems


def _append_structured_entry(
    entries: list[SpeechInputUtterance],
    problems: list[SpeechProblem],
    unit_id: str,
    *,
    text: object,
    speaker: object,
    speaker_required: bool,
    text_location: SpeechFieldLocation,
    speaker_location: SpeechFieldLocation,
) -> None:
    """Translate one structured utterance without assigning its final owner."""

    if not isinstance(text, str) or not text.strip():
        problems.append(_parse_problem(unit_id, text_location))
        return
    if speaker is not None and not isinstance(speaker, str):
        problems.append(_parse_problem(unit_id, speaker_location))
        return
    named_speaker = speaker.strip() if isinstance(speaker, str) else ""
    if not speaker_required and named_speaker:
        problems.append(_parse_problem(unit_id, speaker_location))
        return
    entries.append(
        SpeechInputUtterance(
            speaker=named_speaker or None,
            speaker_required=speaker_required,
            text=text,
            location=speaker_location if speaker_required and not named_speaker else text_location,
        )
    )


def _non_character_reference_names(raw_references: object) -> set[str]:
    if not isinstance(raw_references, list):
        return set()
    return {
        normalize_asset_name(name.strip())
        for reference in raw_references
        if isinstance(reference, Mapping)
        and reference.get("type") in {"product", "scene", "prop"}
        and isinstance((name := reference.get("name")), str)
        and name.strip()
    }


def _append_video_prompt_dialogue(
    entries: list[SpeechInputUtterance],
    problems: list[SpeechProblem],
    unit_id: str,
    video_prompt: object,
) -> None:
    if not isinstance(video_prompt, Mapping):
        problems.append(_parse_problem(unit_id, SpeechFieldLocation(("video_prompt",))))
        return
    dialogue = video_prompt.get("dialogue")
    if isinstance(dialogue, list):
        for index, raw in enumerate(dialogue):
            if not isinstance(raw, Mapping):
                problems.append(_parse_problem(unit_id, SpeechFieldLocation(("video_prompt", "dialogue", index))))
                continue
            _append_structured_entry(
                entries,
                problems,
                unit_id,
                text=raw.get("line"),
                speaker=raw.get("speaker"),
                speaker_required=True,
                text_location=SpeechFieldLocation(("video_prompt", "dialogue", index, "line")),
                speaker_location=SpeechFieldLocation(("video_prompt", "dialogue", index, "speaker")),
            )
    elif dialogue is not None:
        problems.append(_parse_problem(unit_id, SpeechFieldLocation(("video_prompt", "dialogue"))))


def adapt_narration_segment(segment: Mapping[str, object]) -> SpeechUnitSnapshot:
    """Translate a narration segment without persisting a duplicate utterance."""

    unit_id = segment.get("segment_id")
    normalized_unit_id = unit_id if isinstance(unit_id, str) else ""
    text = segment.get("novel_text")
    problems = _initial_problems(segment, normalized_unit_id, "segment_id")
    entries: list[SpeechInputUtterance] = []
    _append_video_prompt_dialogue(entries, problems, normalized_unit_id, segment.get("video_prompt", _MISSING))
    if isinstance(text, str) and text.strip():
        entries.append(
            SpeechInputUtterance(
                speaker=None,
                speaker_required=False,
                text=text,
                location=SpeechFieldLocation(("novel_text",)),
            )
        )
    else:
        problems.append(_parse_problem(normalized_unit_id, SpeechFieldLocation(("novel_text",))))
    return SpeechUnitSnapshot(normalized_unit_id, tuple(entries), tuple(problems))


def adapt_drama_scene(scene: Mapping[str, object]) -> SpeechUnitSnapshot:
    """Translate the ordered spoken-content list of a drama scene."""

    unit_id = scene.get("scene_id")
    normalized_unit_id = unit_id if isinstance(unit_id, str) else ""
    entries: list[SpeechInputUtterance] = []
    problems = _initial_problems(scene, normalized_unit_id, "scene_id")
    raw_entries = scene.get("utterances", _MISSING)
    if isinstance(raw_entries, list):
        for index, raw in enumerate(raw_entries):
            if not isinstance(raw, Mapping):
                problems.append(_parse_problem(normalized_unit_id, SpeechFieldLocation(("utterances", index))))
                continue
            kind = raw.get("kind")
            if kind not in {"dialogue", "voiceover"}:
                problems.append(_parse_problem(normalized_unit_id, SpeechFieldLocation(("utterances", index, "kind"))))
                continue
            _append_structured_entry(
                entries,
                problems,
                normalized_unit_id,
                text=raw.get("text"),
                speaker=raw.get("speaker"),
                speaker_required=kind == "dialogue",
                text_location=SpeechFieldLocation(("utterances", index, "text")),
                speaker_location=SpeechFieldLocation(("utterances", index, "speaker")),
            )
    else:
        problems.append(_parse_problem(normalized_unit_id, SpeechFieldLocation(("utterances",))))
    return SpeechUnitSnapshot(normalized_unit_id, tuple(entries), tuple(problems))


def adapt_ad_shot(shot: Mapping[str, object]) -> SpeechUnitSnapshot:
    """Translate an ad storyboard shot's dialogue and voiceover fields."""

    unit_id = shot.get("shot_id")
    normalized_unit_id = unit_id if isinstance(unit_id, str) else ""
    entries: list[SpeechInputUtterance] = []
    problems = _initial_problems(shot, normalized_unit_id, "shot_id")
    _append_video_prompt_dialogue(entries, problems, normalized_unit_id, shot.get("video_prompt", _MISSING))
    voiceover = shot.get("voiceover_text", _MISSING)
    if isinstance(voiceover, str):
        if voiceover.strip():
            entries.append(
                SpeechInputUtterance(
                    speaker=None,
                    speaker_required=False,
                    text=voiceover,
                    location=SpeechFieldLocation(("voiceover_text",)),
                )
            )
    else:
        problems.append(_parse_problem(normalized_unit_id, SpeechFieldLocation(("voiceover_text",))))
    return SpeechUnitSnapshot(normalized_unit_id, tuple(entries), tuple(problems))


def adapt_video_unit(unit: Mapping[str, object]) -> SpeechUnitSnapshot:
    """Translate utterance lines from a self-contained reference-video unit."""

    unit_id = unit.get("unit_id")
    normalized_unit_id = unit_id if isinstance(unit_id, str) else ""
    entries: list[SpeechInputUtterance] = []
    problems = _initial_problems(unit, normalized_unit_id, "unit_id")
    non_character_names = _non_character_reference_names(unit.get("references"))
    shots = unit.get("shots")
    if isinstance(shots, list) and shots:
        for shot_index, shot in enumerate(shots):
            if not isinstance(shot, Mapping):
                problems.append(_parse_problem(normalized_unit_id, SpeechFieldLocation(("shots", shot_index))))
                continue
            text = shot.get("text")
            if not isinstance(text, str):
                problems.append(_parse_problem(normalized_unit_id, SpeechFieldLocation(("shots", shot_index, "text"))))
                continue
            for line_index, line in enumerate(text.splitlines()):
                location = SpeechFieldLocation(("shots", shot_index, "text"), line_index)
                dialogue = match_dialogue_line(line)
                if dialogue is not None:
                    speaker, spoken = dialogue
                    entries.append(
                        SpeechInputUtterance(
                            speaker=speaker,
                            speaker_required=True,
                            text=spoken,
                            location=location,
                        )
                    )
                    continue
                voiceover = match_voiceover_line(line)
                if voiceover is not None:
                    entries.append(
                        SpeechInputUtterance(
                            speaker=None,
                            speaker_required=False,
                            text=voiceover,
                            location=location,
                        )
                    )
                    continue
                empty_speaker = _EMPTY_SPEAKER_LINE.match(line.replace("\ufeff", ""))
                if empty_speaker is not None:
                    spoken = empty_speaker.group(1)
                    if not spoken.strip():
                        problems.append(_parse_problem(normalized_unit_id, location))
                        continue
                    entries.append(
                        SpeechInputUtterance(
                            speaker=None,
                            speaker_required=True,
                            text=spoken,
                            location=location,
                        )
                    )
                    continue
                leading_name = leading_mention_before_colon(line)
                if (
                    "{" in line
                    or "}" in line
                    or "｛" in line
                    or "｝" in line
                    or (
                        leading_name is not None
                        and normalize_asset_name(leading_name.strip()) not in non_character_names
                    )
                    or find_malformed_mention(line) is not None
                ):
                    problems.append(_parse_problem(normalized_unit_id, location))
    else:
        problems.append(_parse_problem(normalized_unit_id, SpeechFieldLocation(("shots",))))
    return SpeechUnitSnapshot(normalized_unit_id, tuple(entries), tuple(problems))


__all__ = [
    "SpeechComposition",
    "SpeechFieldLocation",
    "SpeechInputUtterance",
    "SpeechMode",
    "SpeechOwner",
    "SpeechPreparation",
    "SpeechProblem",
    "SpeechProblemAction",
    "SpeechProblemCode",
    "SpeechProblemReason",
    "SpeechUnitSnapshot",
    "SpeechUtterance",
    "adapt_ad_shot",
    "adapt_drama_scene",
    "adapt_narration_segment",
    "adapt_video_unit",
]
