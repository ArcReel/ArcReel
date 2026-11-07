"""Mechanical validation of flat reference-to-video script_plan / prompt_authoring drafts.

LLM output and human editor input use the same format, so validation operates on the same text.
This module provides the post-parser validation layer: schemas already constrain enums and outer
structure; remaining syntax/content constraints are checked per unit here. Any violation fails loudly
with :class:`DraftViolation`, never writing invalid output as a successful result.

Not writing as a successful result does not mean discarding it: callers use :func:`collect_violations`
to gather per-unit violations into one report. Output enters the repairable draft area
(``lib.script.draft_quarantine``) for Agent repair and revalidation, without resampling.
Every violation has a ``code`` (category) and ``label`` (unit location), enabling item-level locations
and category-based assertions.

Violation types and report rendering (:class:`DraftViolation` / :func:`collect_violations` /
:func:`render_violation_report`) are shared by all three routes and defined in route-neutral
``lib.script.draft_violation``. This module reexports them unchanged, preserving existing
reference-to-video import paths.

Editor-side tolerance differs: ``lib.script.reference_video.script_preview`` only warns on the same
human-written text and still saves it, protecting author intent. This module validates machine output,
which has no author intent to protect, and rejects every violation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Container
from typing import Any

from lib.project.asset_types import asset_name_comparison_key
from lib.references.reference_catalog import build_reference_catalog
from lib.script.draft_violation import (
    DraftViolation,
    DraftViolations,
    collect_violations,
    render_violation_report,
    violation_items,
)
from lib.script.reference_video.text_parser import (
    SpeechMark,
    derive_references_from_text,
    find_malformed_mention,
    leading_mention_before_colon,
    line_speech_marks,
    speech_line_description,
    speech_speaker_references,
    split_speech_line,
    strip_speech_marks,
)
from lib.script.script_models import ReferenceResource
from lib.speech.speech_composition import SpeechProblem, admit_script_unit
from lib.speech.speech_rate import estimate_spoken_seconds

#: Dialogue-duration tolerance relative to unit duration: overload requires exceeding this ratio.
#: Rates are statistical estimates (``lib.speech.speech_rate``); character counts inevitably differ
#: from actual delivery. Without tolerance, ordinary output that just fills its budget would violate.
#: Match the drama save-time upper-bound warning's 20%: both compare the same rate estimates against
#: fixed durations, so there is no reason to use different tolerances.
SPEECH_OVERFLOW_TOLERANCE = 0.20


def _normalize_for_anchor(text: str) -> str:
    """Normalize Unicode to NFC and collapse consecutive whitespace into single spaces.

    Remove only encoding/whitespace differences, not whitespace itself. Like narration coverage's
    ``_normalize_for_coverage``, this tolerates unreliable line breaks/indentation when models copy
    source text, while catching deleted or changed characters. NFC matches the source coordinate system
    defined by ``lib.episode.episode_ledger.normalize_source_text``: languages with combining marks
    (such as vi) may be stored as NFD and returned as NFC. Encoding differences alone are not rewrites.
    """
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def is_verbatim_source_anchor(source_text: str, novel_text: str) -> bool:
    """Whether ``source_text`` is a verbatim source substring after whitespace normalization (empty anchors never match)."""
    anchor = _normalize_for_anchor(source_text)
    return bool(anchor) and anchor in _normalize_for_anchor(novel_text)


def validate_source_text_anchor(label: str, source_text: str, novel_text: str) -> None:
    """Validate that ``source_text`` is a verbatim source substring after whitespace normalization.

    script_plan unit boundaries must trace back to the source. Mismatched anchors mean the model
    rewrote or invented source text while splitting, a fundamental content violation to stop before
    any downstream visual issue. Check substrings only, not ordering or complete coverage: units are
    visual rather than reading units, so dialogue tags or reported passages may belong to no anchor.
    """
    anchor = _normalize_for_anchor(source_text)
    if not anchor:
        raise DraftViolation(
            f"{label} 的 source_text 为空：每个 unit 必须摘录其所依据的原文片段作为追溯锚",
            code="source_text_empty",
            label=label,
        )
    if not is_verbatim_source_anchor(source_text, novel_text):
        raise DraftViolation(
            f"{label} 的 source_text 不是小说原文的逐字片段（存在改写、翻译或杜撰）："
            f"{source_text.strip()[:40]!r}；请原样复制原文，不要转述",
            code="source_text_not_verbatim",
            label=label,
        )


#: Fullwidth braces. Syntax recognizes ASCII braces only, but Chinese input methods make fullwidth
#: forms easy to produce. They create no speech marks, allowing dialogue through as visual description
#: while deriving speakers as reference images. Explicitly recognize and reject them during syntax
#: validation, without silent acceptance or rewriting on the model's behalf.
_FULLWIDTH_BRACES = "｛｝"


def _assert_line_syntax(label: str, text: str, character_references: Container[str]) -> None:
    """Check reference syntax line by line: brace usage, malformed ``@[`` references, and unbraced dialogue.

    All three can pass the parser while reversing intent: dialogue becomes visual description,
    speakers become reference images, or broken tokens reach provider requests verbatim. Machine
    output has no author intent to protect; reject loudly at syntax validation, without silence or
    rewriting on the model's behalf.
    """
    for idx, line in enumerate(text.splitlines()):
        if any(ch in line for ch in _FULLWIDTH_BRACES):
            raise DraftViolation(
                f"{label} 使用了全角花括号：{line.strip()[:40]!r}；"
                "台词与画外音的花括号必须是半角 `{}`，全角形不会被识别为台词",
                code="fullwidth_braces",
                label=label,
                line=idx,
            )
        malformed = find_malformed_mention(line)
        if malformed is not None:
            raise DraftViolation(
                f"{label} 有写坏的资产引用：{malformed!r}；"
                "引用须写成 `@[资产名]`，方括号要成对闭合、名称非空，否则既不进 references，"
                "又会原样进入视频请求",
                code="malformed_mention",
                label=label,
                line=idx,
            )
        parts = split_speech_line(line)
        leads_with_speech = bool(parts) and isinstance(parts[0], SpeechMark) and bool(parts[0].speaker)
        # Only registered characters plus a colon imply malformed dialogue. Scene/prop subheadings
        # (``@[酒馆]：木门被风吹开``) are valid visual descriptions; rejecting every such shape would
        # reject normal script_plan output.
        if not leads_with_speech and (leading_mention_before_colon(line) or "") in character_references:
            raise DraftViolation(
                f"{label} 的台词写法不合法：{line.strip()[:40]!r}；"
                "台词须写成 `@[角色]{台词}`——说话人非空、台词由半角花括号成对包裹，"
                "否则这段会被当成画面描述、台词整句丢失",
                code="dialogue_line_syntax",
                label=label,
                line=idx,
            )
        # Check only text outside marks: other braces on a line do not invalidate recognized dialogue.
        rest = speech_line_description(parts)
        if "{" not in rest and "}" not in rest:
            continue
        excerpt = line.strip()[:40]
        if rest.count("{") != rest.count("}"):
            raise DraftViolation(f"{label} 有未闭合的花括号：{excerpt!r}", code="unclosed_brace", label=label, line=idx)
        raise DraftViolation(
            f"{label} 在画面描述里使用了花括号：{excerpt!r}；"
            "花括号是发声保留语法，台词写作 `@[角色]{台词}`、画外音写作 `{画外音}`，"
            "说话人须非空、花括号不得嵌套",
            code="braces_in_description",
            label=label,
            line=idx,
        )


def _has_description_line(text: str) -> bool:
    """Whether a unit contains visual description: some line retains nonempty text after all speech marks are removed."""
    return any(strip_speech_marks(line).strip() for line in text.splitlines())


def dialogue_speakers(text: str) -> list[str]:
    """Return deduplicated dialogue-mark speakers in appearance order for registered-character checks.

    Speakers come from ``split_speech_line``, already normalized to asset-name comparison coordinates
    at the parser entry (``_normalize_source`` in ``lib.script.reference_video.text_parser``).
    They match normalized asset-table keys, so use them directly without additional normalization.

    A speaker written as ``@[角色/衍生]`` yields the base name: voice belongs to identity, and derivatives
    share the base's reference audio and voice characteristics (see ``docs/adr/0072``).
    See ``speech_speaker_references`` for the appearance actually written.
    """
    seen: set[str] = set()
    speakers: list[str] = []
    for line in text.splitlines():
        for mark in line_speech_marks(line):
            if mark.speaker and mark.speaker not in seen:
                seen.add(mark.speaker)
                speakers.append(mark.speaker)
    return speakers


def normative_lines(text: str) -> list[tuple[str, str, str]]:
    """Return all speech marks in appearance order: ``(kind, speaker, 台词)``, with ``kind`` dialogue / voiceover.

    prompt_authoring's structure-preserving diff compares these: visual descriptions may expand freely,
    but speech marks must remain verbatim.

    Dialogue and speakers are already normalized to NFC at the parser entry (``_normalize_source`` in
    ``lib.script.reference_video.text_parser``). Source text may be stored as NFD and returned as NFC:
    visually identical forms compare differently character by character, causing the structural diff
    to report encoding differences as rewrites. Speech estimates also require NFC: combining marks
    otherwise split words into multiple reading units in word-counted languages. Normalizing once in
    the parser keeps both consumers consistent.
    """
    result: list[tuple[str, str, str]] = []
    for line in text.splitlines():
        result.extend(
            ("dialogue" if mark.speaker else "voiceover", mark.speaker, mark.text) for mark in line_speech_marks(line)
        )
    return result


def validate_unit_text(
    label: str,
    text: str,
    project: dict[str, Any],
    *,
    unit_id: str | None = None,
    max_refs: int | None,
    tolerated_speakers: Collection[str] = (),
) -> list[ReferenceResource]:
    """Validate one unit's body and mechanically derive reference-image references.

    Cover four blocking violations: empty bodies or speech-only bodies; reference-syntax misuse
    (braces, malformed references, unbraced dialogue); unregistered ``@[名称]`` references, including
    speech-mark speakers and their written derivatives; and reference counts over model limits.
    Only the body is persisted. Derived results serve this validation/capability check and are not written back.

    Speakers in ``tolerated_speakers`` need not be registered: confirmed speakers in formal bodies
    may be unregistered extras. Execution binds no voice to those lines and sends them verbatim.
    """
    if not text.strip():
        raise DraftViolation(f"{label} 的正文为空", code="empty_text", label=label)

    # Always get registered names from the reference catalog, already in comparison coordinates.
    # The parser normalizes body names at entry; both sides must match for accurate checks.
    catalog = build_reference_catalog(project)
    _assert_line_syntax(label, text, catalog.reference_names("character"))

    # Dialogue/voiceover-only bodies have no generatable visuals; nonempty bodies pass the empty check.
    if not _has_description_line(text):
        raise DraftViolation(
            f"{label} 没有画面描述；只有台词与画外音的单元没有可生成的画面",
            code="blank_description",
            label=label,
        )

    # Share ``derive_references_from_text`` with editor previews. Strictness differs (reject missing
    # references and limit violations here), but derivation must match or one body gets different `图N` indices.
    refs, missing = derive_references_from_text(text, project)
    if missing:
        raise DraftViolation(
            f"{label} 引用了未登记的资产名: {missing}；资产名必须逐字取自 project.json 三张表",
            code="unregistered_asset",
            label=label,
        )

    # The speaker slot asks whether this is a character asset, beyond reference eligibility:
    # it determines which reference audio binds to the dialogue.
    tolerated = {asset_name_comparison_key(name) for name in tolerated_speakers}
    registered_speakers = catalog.asset_names("character")
    bad_speakers = sorted({s for s in dialogue_speakers(text) if s not in registered_speakers and s not in tolerated})
    if bad_speakers:
        raise DraftViolation(
            f"{label} 的台词说话人未登记为角色资产: {bad_speakers}；说话人决定该句台词绑哪段参考音频，必须是登记角色",
            code="unregistered_speaker",
            label=label,
        )

    # If the base is registered but the speaker is absent from reference names, its derivative is missing.
    # Separate violations guide registration of the character versus registration/renaming of a derivative;
    # combining them would direct repairs incorrectly.
    character_references = catalog.reference_names("character")
    bad_forms = sorted(
        {name for name in speech_speaker_references(text) if name not in character_references and name not in tolerated}
    )
    if bad_forms:
        raise DraftViolation(
            f"{label} 的台词说话人写了该角色没有的衍生: {bad_forms}；"
            "衍生须先登记在该角色下，或改写成本体名 `@[角色]{台词}`",
            code="unregistered_speaker_derivative",
            label=label,
        )

    if max_refs is not None and len(refs) > max_refs:
        raise DraftViolation(
            f"{label} 的参考图数 {len(refs)} 超过模型上限 {max_refs}；请把次要角色融入背景描述（不用 `@` 引用）",
            code="refs_over_limit",
            label=label,
        )
    canonical_unit_id = unit_id if unit_id is not None else label.removeprefix("unit ").strip()
    admission = admit_script_unit("video_units", {"unit_id": canonical_unit_id, "text": text})
    if not admission.allowed:
        raise DraftViolations([_speech_problem_violation(problem) for problem in admission.problems])
    return refs


def _speech_problem_violation(problem: SpeechProblem) -> DraftViolation:
    locations = ", ".join(
        ".".join(str(part) for part in location.path) + (f" line {location.line}" if location.line is not None else "")
        for location in problem.locations
    )
    line = problem.locations[0].line if len(problem.locations) == 1 else None
    return DraftViolation(
        f"unit {problem.unit_id} 发声准入未通过（{problem.reason.value}，定位：{locations}）；"
        f"下一步：{problem.action.value}",
        code=problem.code.value,
        label=f"unit {problem.unit_id}",
        line=line,
        locations=tuple({"path": list(location.path), "line": location.line} for location in problem.locations),
        reason=problem.reason.value,
        action=problem.action.value,
    )


def dialogue_spoken_seconds(text: str, language: object, speech_rate_override: float | None = None) -> float:
    """Estimated spoken seconds for all dialogue in this unit; None ``speech_rate_override`` uses the language's default rate."""
    # project.json language may contain nonstring dirty data. Fall back to None (default rate),
    # matching prompt construction; otherwise ``count_reading_units`` calls ``language.strip()``
    # and raises AttributeError after a paid call, losing the draft too.
    language = language if isinstance(language, str) else None
    # normative_lines dialogue is already NFC: en / vi ``count_reading_units`` branches count words
    # using ``\b\w+\b``. NFD combining marks are not word characters, splitting one Vietnamese word
    # into several units (nine words become sixteen), inflating estimates and falsely rejecting dialogue that fits.
    return sum(estimate_spoken_seconds(line[2], language, speech_rate_override) for line in normative_lines(text))


def validate_dialogue_load(
    label: str,
    text: str,
    duration_seconds: int,
    language: object,
    speech_rate_override: float | None = None,
) -> None:
    """Validate that dialogue fits: estimates exceeding unit duration plus tolerance violate the contract.

    Duration determines billing and is finalized in script_plan. Excess dialogue inevitably causes
    dropped words or rushed timing in output. At script_plan it can still be fixed by splitting units
    or cutting dialogue; discovering it after generation requires starting over.
    ``speech_rate_override`` is the project override; None falls back to the language default, sharing
    the prompt builder's source so its lower bound and this upper bound always use the same measure.
    """
    spoken = dialogue_spoken_seconds(text, language, speech_rate_override)
    budget = duration_seconds * (1 + SPEECH_OVERFLOW_TOLERANCE)
    if spoken > budget:
        raise DraftViolation(
            f"{label} 的台词念完约需 {spoken:.1f} 秒，超过该 unit 的 {duration_seconds} 秒"
            f"（宽容 {SPEECH_OVERFLOW_TOLERANCE:.0%} 后上限 {budget:.1f} 秒）；"
            "请改取更长的时长档、把该 unit 拆开，或精简台词",
            code="dialogue_overload",
            label=label,
        )


def assert_dialogue_preserved(label: str, script_plan_text: str, prompt_authoring_text: str) -> None:
    """Structure-preserving prompt_authoring diff: speech-mark sequences must exactly match script_plan.

    prompt_authoring writes prompts; dialogue is the content contract already confirmed by the user
    in script_plan. Rewording, additions, deletions, or reordering fail loudly, never silently pass.
    When dialogue does not fit visuals, report an error and return to script_plan rather than letting
    prompt_authoring rewrite it to fit more convenient visuals.
    """
    before = normative_lines(script_plan_text)
    after = normative_lines(prompt_authoring_text)
    if before == after:
        return
    if len(before) != len(after):
        raise DraftViolation(
            f"{label} 的台词条数被改动（script_plan 有 {len(before)} 条，prompt_authoring 产出 {len(after)} 条）；"
            "prompt_authoring 只做提示词编写，台词须逐字保留",
            code="dialogue_line_count_changed",
            label=label,
        )
    for index, (old, new) in enumerate(zip(before, after, strict=True), start=1):
        if old != new:
            raise DraftViolation(
                f"{label} 第 {index} 条台词被改写（原：{old[1] or '画外音'}「{old[2]}」，"
                f"现：{new[1] or '画外音'}「{new[2]}」）；prompt_authoring 只做提示词编写，台词须逐字保留",
                code="dialogue_rewritten",
                label=label,
            )


__all__ = [
    "SPEECH_OVERFLOW_TOLERANCE",
    "DraftViolation",
    "DraftViolations",
    "assert_dialogue_preserved",
    "collect_violations",
    "dialogue_speakers",
    "dialogue_spoken_seconds",
    "is_verbatim_source_anchor",
    "normative_lines",
    "render_violation_report",
    "validate_dialogue_load",
    "validate_source_text_anchor",
    "validate_unit_text",
    "violation_items",
]
